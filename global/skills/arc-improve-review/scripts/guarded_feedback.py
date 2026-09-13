#!/usr/bin/env python3
"""Fenced, exact-head GitHub review-feedback mutations for scheduled runs."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SKILLS_DIR = Path(__file__).resolve().parents[2]
CONTROL_DIR = SKILLS_DIR / "arc-improve" / "scripts"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

import automation_control as control  # noqa: E402


ALLOWED_REPOSITORY = "arc-edge/arc-uas"
ALLOWED_BASE = "main"
ALLOWED_BRANCH_PREFIX = "codex/arc-improve/"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
THREAD_RE = re.compile(r"^PRRT_[A-Za-z0-9_-]+$")
MAX_BODY_BYTES = 64 * 1024
READ_TIMEOUT_SECONDS = 30.0
WRITE_TIMEOUT_SECONDS = 60.0
LEASE_HOLD_SECONDS = 15 * 60
SECRET_PATTERNS = (
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(
        r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token)"
        r"\s*[:=]\s*['\"][^'\"]{12,}"
    ),
)


class FeedbackError(RuntimeError):
    """A denied or failed guarded-feedback operation."""


class CommandTimeout(FeedbackError):
    """A subprocess exceeded its bounded runtime."""


class HeadChanged(FeedbackError):
    """The pull request head changed around a guarded mutation."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__(str(result.get("reason", "pull request head changed")))
        self.result = dict(result)


class PartialApplication(FeedbackError):
    """A dispatched mutation cannot be reported as fully successful."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__(str(result.get("reason", "mutation may have partially applied")))
        self.result = dict(result)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PullRequest:
    repository: str
    number: int
    state: str
    head_repository: str
    head_branch: str
    head_sha: str
    base_repository: str
    base_branch: str


Runner = Callable[..., CommandResult]
PullRequestFetcher = Callable[[str, int], PullRequest]
TransitionValidator = Callable[..., str]


def _terminate_process_group(
    process: subprocess.Popen[str], process_group: int
) -> None:
    """Terminate and reap a process group even if its original leader exited."""
    for sig, grace in ((signal.SIGTERM, 0.25), (signal.SIGKILL, 0.5)):
        try:
            os.killpg(process_group, sig)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.01)

    try:
        process.communicate(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            # Do not let inherited pipe descriptors turn cleanup into an
            # unbounded wait. The group has already received SIGKILL.
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass


def run_command(
    command: Sequence[str],
    *,
    timeout: float,
    input_text: str | None = None,
) -> CommandResult:
    """Run one argv-only command in a bounded, independently killable group."""
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise FeedbackError("command must be a non-empty argv sequence")
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise FeedbackError(f"cannot start command {command[0]}: {exc}") from exc
    # start_new_session makes the child's PID its process-group ID. Capture it
    # now so cleanup remains possible after the group leader exits.
    process_group = process.pid
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process, process_group)
        raise CommandTimeout(f"command timed out after {timeout:g}s: {command[0]}") from exc
    except BaseException:
        _terminate_process_group(process, process_group)
        raise
    return CommandResult(process.returncode, stdout, stderr)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _repository(value: str) -> str:
    if value != ALLOWED_REPOSITORY:
        raise argparse.ArgumentTypeError(f"repository must be {ALLOWED_REPOSITORY}")
    return value


def _sha(value: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("SHA must be exactly 40 lowercase hexadecimal characters")
    return value


def _token(value: str) -> str:
    if not TOKEN_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("run-context token must be 32 lowercase hexadecimal characters")
    return value


def _thread(value: str) -> str:
    if not THREAD_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("thread id must start with PRRT_ and contain only safe characters")
    return value


def _json_object(result: CommandResult, operation: str) -> dict[str, Any]:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise FeedbackError(f"{operation} failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FeedbackError(f"{operation} returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise FeedbackError(f"{operation} did not return a JSON object")
    return payload


def parse_pull_request(payload: Mapping[str, Any], repository: str, number: int) -> PullRequest:
    try:
        head = payload["head"]
        base = payload["base"]
        head_repo = head["repo"]["full_name"]
        base_repo = base["repo"]["full_name"]
        state = payload["state"]
        head_branch = head["ref"]
        head_sha = head["sha"]
        base_branch = base["ref"]
    except (KeyError, TypeError) as exc:
        raise FeedbackError("pull request payload is malformed") from exc
    values = (head_repo, base_repo, state, head_branch, head_sha, base_branch)
    if any(not isinstance(value, str) for value in values):
        raise FeedbackError("pull request payload contains invalid field types")
    return PullRequest(
        repository=repository,
        number=number,
        state=state,
        head_repository=head_repo,
        head_branch=head_branch,
        head_sha=head_sha,
        base_repository=base_repo,
        base_branch=base_branch,
    )


def fetch_pull_request(
    repository: str,
    number: int,
    *,
    runner: Runner = run_command,
) -> PullRequest:
    result = runner(
        ["gh", "api", "--hostname", "github.com", f"repos/{repository}/pulls/{number}"],
        timeout=READ_TIMEOUT_SECONDS,
    )
    return parse_pull_request(_json_object(result, "pull request read"), repository, number)


def require_current_head(pr: PullRequest, expected_head: str) -> None:
    if pr.repository != ALLOWED_REPOSITORY:
        raise FeedbackError("pull request repository is not authorized")
    if pr.state != "open":
        raise FeedbackError("pull request is not open")
    if pr.head_repository != ALLOWED_REPOSITORY or pr.base_repository != ALLOWED_REPOSITORY:
        raise FeedbackError("pull request must use the canonical repository on both sides")
    if pr.base_branch != ALLOWED_BASE:
        raise FeedbackError("pull request base branch must be main")
    if not pr.head_branch.startswith(ALLOWED_BRANCH_PREFIX):
        raise FeedbackError("pull request head branch is outside the ARC Improve prefix")
    if pr.head_sha != expected_head:
        raise FeedbackError(
            f"pull request head mismatch: expected {expected_head}, found {pr.head_sha}"
        )


def _git(
    repo: Path,
    args: Sequence[str],
    *,
    runner: Runner = run_command,
    check: bool = True,
) -> CommandResult:
    result = runner(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "submodule.recurse=false",
            "-C",
            str(repo),
            *args,
        ],
        timeout=READ_TIMEOUT_SECONDS,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise FeedbackError(f"local remediation proof failed ({args[0]}): {detail}")
    return result


def _allowed_origin_urls(repository: str) -> set[str]:
    return {
        f"https://github.com/{repository}.git",
        f"git@github.com:{repository}.git",
        f"ssh://git@github.com/{repository}.git",
    }


def validate_local_remediation_transition(
    repo: Path,
    repository: str,
    claim_head: str,
    expected_current_head: str,
    *,
    runner: Runner = run_command,
) -> str:
    """Prove that a claimed old head advanced locally to one clean, linear new head."""
    if claim_head == expected_current_head:
        raise FeedbackError("local remediation proof requires two distinct heads")

    inside = _git(repo, ["rev-parse", "--is-inside-work-tree"], runner=runner).stdout.strip()
    if inside != "true":
        raise FeedbackError("repository path is not a Git worktree")
    top_level = _git(repo, ["rev-parse", "--show-toplevel"], runner=runner).stdout.strip()
    try:
        resolved_top_level = Path(top_level).resolve(strict=True)
    except OSError as exc:
        raise FeedbackError(f"cannot resolve Git worktree root: {exc}") from exc
    if resolved_top_level != repo:
        raise FeedbackError(
            f"repository path must be the exact Git worktree root: {resolved_top_level}"
        )

    allowed_origins = _allowed_origin_urls(repository)
    origin = _git(repo, ["remote", "get-url", "origin"], runner=runner).stdout.strip()
    if origin not in allowed_origins:
        raise FeedbackError(f"origin is not the exact allowed ARC repository URL: {origin!r}")
    push_origin = _git(
        repo, ["remote", "get-url", "--push", "origin"], runner=runner
    ).stdout.strip()
    if push_origin not in allowed_origins:
        raise FeedbackError(
            f"origin push URL is not the exact allowed ARC repository URL: {push_origin!r}"
        )

    dirty = _git(
        repo,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        runner=runner,
    ).stdout
    if dirty:
        raise FeedbackError("worktree must be clean for post-remediation feedback")

    branch = _git(repo, ["branch", "--show-current"], runner=runner).stdout.strip()
    if not branch.startswith(ALLOWED_BRANCH_PREFIX) or branch == ALLOWED_BRANCH_PREFIX:
        raise FeedbackError(
            f"current branch must match {ALLOWED_BRANCH_PREFIX}*: {branch!r}"
        )

    local_head = _git(repo, ["rev-parse", "HEAD"], runner=runner).stdout.strip()
    if local_head != expected_current_head:
        raise FeedbackError(
            f"local HEAD mismatch: expected {expected_current_head}, found {local_head}"
        )

    _git(repo, ["cat-file", "-e", f"{claim_head}^{{commit}}"], runner=runner)
    _git(
        repo,
        ["cat-file", "-e", f"{expected_current_head}^{{commit}}"],
        runner=runner,
    )
    ancestry = _git(
        repo,
        ["merge-base", "--is-ancestor", claim_head, expected_current_head],
        runner=runner,
        check=False,
    )
    if ancestry.returncode != 0:
        raise FeedbackError("claimed head is not an ancestor of the current remediation head")

    merges = _git(
        repo,
        ["rev-list", "--merges", f"{claim_head}..{expected_current_head}"],
        runner=runner,
    ).stdout.strip()
    if merges:
        raise FeedbackError("local remediation range contains a merge commit")
    return branch


def read_body_file(path: Path, *, expected_uid: int | None = None) -> str:
    """Capture a bounded UTF-8 body from a user-owned regular file without following links."""
    expected_uid = os.getuid() if expected_uid is None else expected_uid
    expanded = path.expanduser()
    try:
        before = expanded.lstat()
    except OSError as exc:
        raise FeedbackError(f"cannot inspect body file: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise FeedbackError("body file must be a regular non-symlink file")
    if before.st_uid != expected_uid:
        raise FeedbackError("body file must be owned by the current user")
    if before.st_nlink != 1:
        raise FeedbackError("body file must not have multiple hard links")
    if before.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise FeedbackError("body file must not be group/world writable")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(expanded, flags)
    except OSError as exc:
        raise FeedbackError(f"cannot open body file safely: {exc}") from exc
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or after.st_uid != expected_uid:
            raise FeedbackError("opened body file is not a user-owned regular file")
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise FeedbackError("body file changed while it was being opened")
        chunks: list[bytes] = []
        remaining = MAX_BODY_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)

    if len(raw) > MAX_BODY_BYTES:
        raise FeedbackError(f"body file exceeds {MAX_BODY_BYTES} bytes")
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FeedbackError("body file must contain UTF-8 text") from exc
    if not body.strip():
        raise FeedbackError("body file must not be empty")
    if "\x00" in body:
        raise FeedbackError("body file must not contain NUL characters")
    if any(pattern.search(body) for pattern in SECRET_PATTERNS):
        raise FeedbackError("body file contains a secret-like value")
    return body


def fetch_review_comment(
    repository: str,
    comment_id: int,
    *,
    runner: Runner = run_command,
) -> dict[str, Any]:
    result = runner(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"repos/{repository}/pulls/comments/{comment_id}",
        ],
        timeout=READ_TIMEOUT_SECONDS,
    )
    return _json_object(result, "review comment read")


def require_top_level_comment(
    payload: Mapping[str, Any], repository: str, pr_number: int, comment_id: int
) -> None:
    if payload.get("id") != comment_id:
        raise FeedbackError("review comment response did not match the requested comment")
    expected_url = f"https://api.github.com/repos/{repository}/pulls/{pr_number}"
    if payload.get("pull_request_url") != expected_url:
        raise FeedbackError("review comment does not belong to the authorized pull request")
    if payload.get("in_reply_to_id") is not None:
        raise FeedbackError("review comment must be top-level")


THREAD_QUERY = """query($id: ID!) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      id
      isResolved
      pullRequest { number repository { nameWithOwner } }
      comments(first: 100) { nodes { databaseId } }
    }
  }
}"""


RESOLVE_MUTATION = """mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}"""


def fetch_review_thread(thread_id: str, *, runner: Runner = run_command) -> dict[str, Any]:
    result = runner(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "graphql",
            "-f",
            f"query={THREAD_QUERY}",
            "-F",
            f"id={thread_id}",
        ],
        timeout=READ_TIMEOUT_SECONDS,
    )
    payload = _json_object(result, "review thread read")
    node = payload.get("data", {}).get("node") if isinstance(payload.get("data"), dict) else None
    if not isinstance(node, dict):
        raise FeedbackError("review thread read did not return a thread")
    return node


def require_review_thread(
    payload: Mapping[str, Any],
    repository: str,
    pr_number: int,
    comment_id: int,
    thread_id: str,
) -> bool:
    if payload.get("id") != thread_id:
        raise FeedbackError("review thread response did not match the requested thread")
    pull_request = payload.get("pullRequest")
    if not isinstance(pull_request, dict):
        raise FeedbackError("review thread payload is malformed")
    repo = pull_request.get("repository")
    if (
        pull_request.get("number") != pr_number
        or not isinstance(repo, dict)
        or repo.get("nameWithOwner") != repository
    ):
        raise FeedbackError("review thread does not belong to the authorized pull request")
    comments = payload.get("comments")
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    if not isinstance(nodes, list) or comment_id not in {
        node.get("databaseId") for node in nodes if isinstance(node, dict)
    }:
        raise FeedbackError("review thread does not contain the requested top-level comment")
    if not isinstance(payload.get("isResolved"), bool):
        raise FeedbackError("review thread resolution state is malformed")
    return bool(payload["isResolved"])


def _head_changed_result(
    *,
    operation: str,
    phase: str,
    expected_head: str,
    observed_head: str | None,
    reply_applied: bool,
    resolution_applied: bool,
    mutation_dispatched: bool,
) -> dict[str, Any]:
    partial = reply_applied or resolution_applied or mutation_dispatched
    return {
        "state": "partial" if partial else "denied",
        "reason": f"pull request head changed {phase} {operation}",
        "operation": operation,
        "phase": phase,
        "expected_head": expected_head,
        "observed_head": observed_head,
        "reply_applied": reply_applied,
        "resolution_applied": resolution_applied,
        "mutation_dispatched": mutation_dispatched,
    }


def _guard_head(
    *,
    fetcher: PullRequestFetcher,
    repository: str,
    pr_number: int,
    expected_head: str,
    operation: str,
    phase: str,
    reply_applied: bool,
    resolution_applied: bool,
    mutation_dispatched: bool,
) -> PullRequest:
    try:
        current = fetcher(repository, pr_number)
    except Exception as exc:
        reason = f"cannot verify pull request head {phase} {operation}: {exc}"
        if reply_applied or resolution_applied or mutation_dispatched:
            raise PartialApplication(
                {
                    "state": "partial",
                    "reason": reason,
                    "operation": operation,
                    "phase": phase,
                    "expected_head": expected_head,
                    "observed_head": None,
                    "reply_applied": reply_applied,
                    "resolution_applied": resolution_applied,
                    "mutation_dispatched": mutation_dispatched,
                }
            ) from exc
        raise FeedbackError(reason) from exc
    try:
        require_current_head(current, expected_head)
    except FeedbackError as exc:
        if current.head_sha == expected_head:
            # Canonical identity/state failures are authorization failures, not
            # evidence of a concurrent head change.
            if reply_applied or resolution_applied or mutation_dispatched:
                raise PartialApplication(
                    {
                        "state": "partial",
                        "reason": f"pull request authorization changed {phase} {operation}",
                        "operation": operation,
                        "phase": phase,
                        "expected_head": expected_head,
                        "observed_head": current.head_sha,
                        "reply_applied": reply_applied,
                        "resolution_applied": resolution_applied,
                        "mutation_dispatched": mutation_dispatched,
                        "detail": str(exc),
                    }
                ) from exc
            raise
        result = _head_changed_result(
            operation=operation,
            phase=phase,
            expected_head=expected_head,
            observed_head=current.head_sha,
            reply_applied=reply_applied,
            resolution_applied=resolution_applied,
            mutation_dispatched=mutation_dispatched,
        )
        result["detail"] = str(exc)
        raise HeadChanged(result) from exc
    return current


def _guard_local_transition(
    *,
    validator: TransitionValidator,
    repo: Path,
    repository: str,
    claim_head: str,
    expected_current_head: str,
    runner: Runner,
    operation: str,
    phase: str,
    reply_applied: bool,
    resolution_applied: bool,
    mutation_dispatched: bool,
) -> str:
    try:
        return validator(
            repo,
            repository,
            claim_head,
            expected_current_head,
            runner=runner,
        )
    except Exception as exc:
        if reply_applied or resolution_applied or mutation_dispatched:
            raise PartialApplication(
                {
                    "state": "partial",
                    "reason": f"local remediation proof changed {phase} {operation}",
                    "operation": operation,
                    "phase": phase,
                    "expected_head": expected_current_head,
                    "observed_head": expected_current_head,
                    "reply_applied": reply_applied,
                    "resolution_applied": resolution_applied,
                    "mutation_dispatched": mutation_dispatched,
                    "detail": str(exc),
                }
            ) from exc
        if isinstance(exc, FeedbackError):
            raise
        raise FeedbackError(f"cannot prove local remediation transition: {exc}") from exc


def _require_pr_branch_matches_worktree(
    current: PullRequest,
    branch: str,
    *,
    operation: str,
    phase: str,
    expected_head: str,
    reply_applied: bool,
    resolution_applied: bool,
    mutation_dispatched: bool,
) -> None:
    if current.head_branch == branch:
        return
    detail = (
        f"pull request branch {current.head_branch!r} does not match "
        f"the proved local worktree branch {branch!r}"
    )
    if reply_applied or resolution_applied or mutation_dispatched:
        raise PartialApplication(
            {
                "state": "partial",
                "reason": f"pull request authorization changed {phase} {operation}",
                "operation": operation,
                "phase": phase,
                "expected_head": expected_head,
                "observed_head": current.head_sha,
                "reply_applied": reply_applied,
                "resolution_applied": resolution_applied,
                "mutation_dispatched": mutation_dispatched,
                "detail": detail,
            }
        )
    raise FeedbackError(detail)


def _post_reply(
    repository: str,
    pr_number: int,
    comment_id: int,
    body: str,
    *,
    runner: Runner,
) -> dict[str, Any]:
    result = runner(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "-X",
            "POST",
            f"repos/{repository}/pulls/{pr_number}/comments/{comment_id}/replies",
            "--input",
            "-",
        ],
        timeout=WRITE_TIMEOUT_SECONDS,
        input_text=json.dumps({"body": body}),
    )
    return _json_object(result, "review comment reply")


def _resolve_thread(thread_id: str, *, runner: Runner) -> dict[str, Any]:
    result = runner(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "graphql",
            "-f",
            f"query={RESOLVE_MUTATION}",
            "-F",
            f"threadId={thread_id}",
        ],
        timeout=WRITE_TIMEOUT_SECONDS,
    )
    return _json_object(result, "review thread resolution")


def guarded_feedback(
    *,
    repository: str,
    pr_number: int,
    claim_head: str,
    expected_current_head: str,
    comment_id: int,
    body: str,
    repo: Path,
    run_context_token: str,
    resolve_thread_id: str | None = None,
    runner: Runner = run_command,
    pr_fetcher: PullRequestFetcher | None = None,
    context_reader: Callable[..., Mapping[str, Any]] = control.context_read,
    claim_reader: Callable[..., Mapping[str, Any]] = control.active_review_claim,
    lease_factory: Callable[..., AbstractContextManager[Any]] = control.lease_guard,
    state_root_factory: Callable[[], Path] = control.state_root,
    local_runner: Runner = run_command,
    transition_validator: TransitionValidator = validate_local_remediation_transition,
) -> dict[str, Any]:
    """Reply, then optionally resolve, while holding the exact scheduled-run lease."""
    thread_run_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if not thread_run_id:
        raise FeedbackError("CODEX_THREAD_ID is required")
    if repository != ALLOWED_REPOSITORY:
        raise FeedbackError("repository is not authorized")
    if not SHA_RE.fullmatch(claim_head) or not SHA_RE.fullmatch(expected_current_head):
        raise FeedbackError("claim and expected heads must be lowercase 40-character SHAs")
    is_remediation_transition = claim_head != expected_current_head
    if not TOKEN_RE.fullmatch(run_context_token):
        raise FeedbackError("run-context token is malformed")
    if resolve_thread_id is not None and not THREAD_RE.fullmatch(resolve_thread_id):
        raise FeedbackError("review thread id is malformed")
    if pr_number <= 0 or comment_id <= 0:
        raise FeedbackError("pull request and comment ids must be positive")
    if not body.strip() or "\x00" in body or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise FeedbackError("reply body is empty or invalid")

    try:
        resolved_repo = repo.expanduser().resolve(strict=True)
    except OSError as exc:
        raise FeedbackError(f"repository path is invalid: {exc}") from exc
    if not resolved_repo.is_dir():
        raise FeedbackError("repository path must be a directory")

    root = state_root_factory()
    context_result = context_reader(
        root,
        run_context_token,
        run_id=thread_run_id,
        scope="review",
        cwd=resolved_repo,
    )
    context = context_result.get("context")
    if not isinstance(context, dict):
        raise FeedbackError("run context is missing")
    run_id = context.get("run_id")
    owner_id = context.get("owner_id")
    generation = context.get("generation")
    if not isinstance(run_id, str) or not run_id:
        raise FeedbackError("run context has an invalid run id")
    if run_id != thread_run_id:
        raise FeedbackError("run context does not belong to CODEX_THREAD_ID")
    if not isinstance(owner_id, str) or not owner_id or not isinstance(generation, int):
        raise FeedbackError("run context has invalid lease ownership")

    claim_result = claim_reader(
        root,
        repository,
        pr_number,
        claim_head,
        run_id=run_id,
        cwd=resolved_repo,
        context_token=run_context_token,
    )
    if claim_result.get("state") != "ok":
        raise FeedbackError("active exact-head review claim is required")
    claim_context = claim_result.get("context")
    if not isinstance(claim_context, dict) or (
        claim_context.get("owner_id") != owner_id
        or claim_context.get("generation") != generation
        or claim_context.get("run_id") != run_id
    ):
        raise FeedbackError("review claim is not owned by this run context")

    fetcher = pr_fetcher or (
        lambda repo_name, number: fetch_pull_request(repo_name, number, runner=runner)
    )
    with lease_factory(
        root,
        "review",
        run_id,
        owner_id=owner_id,
        generation=generation,
        context_token=run_context_token,
        hold_seconds=LEASE_HOLD_SECONDS,
    ):
        transition_branch = None
        if is_remediation_transition:
            transition_branch = _guard_local_transition(
                validator=transition_validator,
                repo=resolved_repo,
                repository=repository,
                claim_head=claim_head,
                expected_current_head=expected_current_head,
                runner=local_runner,
                operation="reply",
                phase="before",
                reply_applied=False,
                resolution_applied=False,
                mutation_dispatched=False,
            )

        comment = fetch_review_comment(repository, comment_id, runner=runner)
        require_top_level_comment(comment, repository, pr_number, comment_id)

        thread_already_resolved = False
        if resolve_thread_id is not None:
            thread = fetch_review_thread(resolve_thread_id, runner=runner)
            thread_already_resolved = require_review_thread(
                thread, repository, pr_number, comment_id, resolve_thread_id
            )

        current = _guard_head(
            fetcher=fetcher,
            repository=repository,
            pr_number=pr_number,
            expected_head=expected_current_head,
            operation="reply",
            phase="before",
            reply_applied=False,
            resolution_applied=False,
            mutation_dispatched=False,
        )
        if transition_branch is not None:
            _require_pr_branch_matches_worktree(
                current,
                transition_branch,
                operation="reply",
                phase="before",
                expected_head=expected_current_head,
                reply_applied=False,
                resolution_applied=False,
                mutation_dispatched=False,
            )
        reply_dispatched = False
        try:
            reply_dispatched = True
            reply = _post_reply(
                repository, pr_number, comment_id, body, runner=runner
            )
        except Exception as exc:
            try:
                _guard_head(
                    fetcher=fetcher,
                    repository=repository,
                    pr_number=pr_number,
                    expected_head=expected_current_head,
                    operation="reply",
                    phase="after",
                    reply_applied=False,
                    resolution_applied=False,
                    mutation_dispatched=reply_dispatched,
                )
            except HeadChanged:
                raise
            raise PartialApplication(
                {
                    "state": "partial",
                    "reason": f"reply mutation may have partially applied: {exc}",
                    "operation": "reply",
                    "phase": "mutation",
                    "expected_head": expected_current_head,
                    "observed_head": expected_current_head,
                    "reply_applied": False,
                    "resolution_applied": False,
                    "mutation_dispatched": True,
                }
            ) from exc

        current = _guard_head(
            fetcher=fetcher,
            repository=repository,
            pr_number=pr_number,
            expected_head=expected_current_head,
            operation="reply",
            phase="after",
            reply_applied=True,
            resolution_applied=False,
            mutation_dispatched=True,
        )
        if transition_branch is not None:
            _require_pr_branch_matches_worktree(
                current,
                transition_branch,
                operation="reply",
                phase="after",
                expected_head=expected_current_head,
                reply_applied=True,
                resolution_applied=False,
                mutation_dispatched=True,
            )
            transition_branch = _guard_local_transition(
                validator=transition_validator,
                repo=resolved_repo,
                repository=repository,
                claim_head=claim_head,
                expected_current_head=expected_current_head,
                runner=local_runner,
                operation="reply",
                phase="after",
                reply_applied=True,
                resolution_applied=False,
                mutation_dispatched=True,
            )
        reply_id = reply.get("id")
        if not isinstance(reply_id, int) or reply_id <= 0 or reply.get("in_reply_to_id") != comment_id:
            raise PartialApplication(
                {
                    "state": "partial",
                    "reason": "reply response did not confirm the dispatched mutation",
                    "operation": "reply",
                    "phase": "confirmation",
                    "expected_head": expected_current_head,
                    "observed_head": expected_current_head,
                    "reply_applied": False,
                    "resolution_applied": False,
                    "mutation_dispatched": True,
                }
            )

        resolution_applied = False
        if resolve_thread_id is not None and not thread_already_resolved:
            if is_remediation_transition:
                transition_branch = _guard_local_transition(
                    validator=transition_validator,
                    repo=resolved_repo,
                    repository=repository,
                    claim_head=claim_head,
                    expected_current_head=expected_current_head,
                    runner=local_runner,
                    operation="resolve",
                    phase="before",
                    reply_applied=True,
                    resolution_applied=False,
                    mutation_dispatched=False,
                )
            current = _guard_head(
                fetcher=fetcher,
                repository=repository,
                pr_number=pr_number,
                expected_head=expected_current_head,
                operation="resolve",
                phase="before",
                reply_applied=True,
                resolution_applied=False,
                mutation_dispatched=False,
            )
            if transition_branch is not None:
                _require_pr_branch_matches_worktree(
                    current,
                    transition_branch,
                    operation="resolve",
                    phase="before",
                    expected_head=expected_current_head,
                    reply_applied=True,
                    resolution_applied=False,
                    mutation_dispatched=False,
                )
            resolution_dispatched = False
            try:
                resolution_dispatched = True
                resolution = _resolve_thread(resolve_thread_id, runner=runner)
            except Exception as exc:
                try:
                    _guard_head(
                        fetcher=fetcher,
                        repository=repository,
                        pr_number=pr_number,
                        expected_head=expected_current_head,
                        operation="resolve",
                        phase="after",
                        reply_applied=True,
                        resolution_applied=False,
                        mutation_dispatched=resolution_dispatched,
                    )
                except HeadChanged:
                    raise
                raise PartialApplication(
                    {
                        "state": "partial",
                        "reason": (
                            "thread resolution may have partially applied after reply: "
                            f"{exc}"
                        ),
                        "operation": "resolve",
                        "phase": "mutation",
                        "expected_head": expected_current_head,
                        "observed_head": expected_current_head,
                        "reply_applied": True,
                        "resolution_applied": False,
                        "mutation_dispatched": True,
                    }
                ) from exc

            current = _guard_head(
                fetcher=fetcher,
                repository=repository,
                pr_number=pr_number,
                expected_head=expected_current_head,
                operation="resolve",
                phase="after",
                reply_applied=True,
                resolution_applied=True,
                mutation_dispatched=True,
            )
            if transition_branch is not None:
                _require_pr_branch_matches_worktree(
                    current,
                    transition_branch,
                    operation="resolve",
                    phase="after",
                    expected_head=expected_current_head,
                    reply_applied=True,
                    resolution_applied=True,
                    mutation_dispatched=True,
                )
                transition_branch = _guard_local_transition(
                    validator=transition_validator,
                    repo=resolved_repo,
                    repository=repository,
                    claim_head=claim_head,
                    expected_current_head=expected_current_head,
                    runner=local_runner,
                    operation="resolve",
                    phase="after",
                    reply_applied=True,
                    resolution_applied=True,
                    mutation_dispatched=True,
                )
            resolved = resolution.get("data", {}).get("resolveReviewThread")
            thread_result = resolved.get("thread") if isinstance(resolved, dict) else None
            if (
                not isinstance(thread_result, dict)
                or thread_result.get("id") != resolve_thread_id
                or thread_result.get("isResolved") is not True
            ):
                raise PartialApplication(
                    {
                        "state": "partial",
                        "reason": "thread response did not confirm the dispatched resolution",
                        "operation": "resolve",
                        "phase": "confirmation",
                        "expected_head": expected_current_head,
                        "observed_head": expected_current_head,
                        "reply_applied": True,
                        "resolution_applied": False,
                        "mutation_dispatched": True,
                    }
                )
            resolution_applied = True

        return {
            "state": "ok",
            "repository": repository,
            "pr": pr_number,
            "claim_head": claim_head,
            "head": expected_current_head,
            "comment_id": comment_id,
            "reply_id": reply_id,
            "thread_id": resolve_thread_id,
            "thread_resolution": (
                "already-resolved"
                if thread_already_resolved
                else "resolved"
                if resolution_applied
                else "not-requested"
            ),
            "claim_id": claim_result.get("claim_id"),
            "run_id": run_id,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=_repository, default=ALLOWED_REPOSITORY)
    parser.add_argument("--pr", type=_positive_int, required=True)
    parser.add_argument("--claim-head", type=_sha, required=True)
    parser.add_argument("--expected-current-head", type=_sha, required=True)
    parser.add_argument("--review-comment-id", type=_positive_int, required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--run-context-token", type=_token, required=True)
    parser.add_argument("--resolve-thread", type=_thread)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        body = read_body_file(args.body_file)
        result = guarded_feedback(
            repository=args.repository,
            pr_number=args.pr,
            claim_head=args.claim_head,
            expected_current_head=args.expected_current_head,
            comment_id=args.review_comment_id,
            body=body,
            repo=args.repo,
            run_context_token=args.run_context_token,
            resolve_thread_id=args.resolve_thread,
        )
    except (HeadChanged, PartialApplication) as exc:
        print(json.dumps(exc.result, sort_keys=True))
        return 3
    except (FeedbackError, control.ControlError) as exc:
        print(json.dumps({"state": "error", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
