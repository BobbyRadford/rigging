#!/usr/bin/env python3
"""Fenced, non-force push for one scheduled ARC Improve review remediation."""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Any, Callable, Sequence


SKILLS_DIR = Path(__file__).resolve().parents[2]
CONTROL_DIR = SKILLS_DIR / "arc-improve" / "scripts"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

import automation_control as control  # noqa: E402


ALLOWED_REPOSITORY = "arc-edge/arc-uas"
ALLOWED_BRANCH_PREFIX = "codex/arc-improve/"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CONTEXT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
READ_TIMEOUT_SECONDS = 60
PUSH_TIMEOUT_SECONDS = 300


class GuardedPushError(RuntimeError):
    """A deterministic refusal or failed guarded push."""


class CommandTimeout(GuardedPushError):
    """A subprocess exceeded its deadline after its process group was reaped."""

    def __init__(self, command: Sequence[str], stdout: str, stderr: str) -> None:
        super().__init__(f"command timed out: {format_command(command)}")
        self.command = tuple(command)
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PullRequestState:
    state: str
    head_branch: str
    head_sha: str
    head_repository: str
    base_repository: str
    base_branch: str


Runner = Callable[..., CommandResult]
PrFetcher = Callable[[str, int], PullRequestState]
LeaseFactory = Callable[..., AbstractContextManager[dict[str, Any]]]
ClaimReader = Callable[..., dict[str, Any]]


def format_command(command: Sequence[str]) -> str:
    return " ".join(command)


def _terminate_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Terminate and reap a subprocess group, escalating once if necessary."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def run_command(
    command: Sequence[str],
    *,
    timeout: int = READ_TIMEOUT_SECONDS,
    check: bool = True,
) -> CommandResult:
    """Run a bounded command in a new process group and always reap it."""
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _terminate_group(process)
        raise CommandTimeout(command, stdout, stderr) from exc
    except BaseException:
        _terminate_group(process)
        raise

    result = CommandResult(process.returncode, stdout, stderr)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise GuardedPushError(
            f"command failed ({result.returncode}): {format_command(command)}: {detail}"
        )
    return result


def positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def lowercase_sha(raw: str) -> str:
    if not SHA_RE.fullmatch(raw):
        raise argparse.ArgumentTypeError("must be a full lowercase 40-character SHA")
    return raw


def context_token(raw: str) -> str:
    if not CONTEXT_TOKEN_RE.fullmatch(raw):
        raise argparse.ArgumentTypeError("must be a 32-character lowercase hex run-context token")
    return raw


def validate_repository(raw: str) -> str:
    if raw != ALLOWED_REPOSITORY:
        raise argparse.ArgumentTypeError(
            f"only {ALLOWED_REPOSITORY} is allowed for scheduled remediation pushes"
        )
    return raw


def resolve_repo(repo: Path, codex_home: Path) -> Path:
    resolved_repo = repo.expanduser().resolve(strict=True)
    worktrees = (codex_home.expanduser().resolve(strict=True) / "worktrees").resolve(
        strict=True
    )
    try:
        resolved_repo.relative_to(worktrees)
    except ValueError as exc:
        raise GuardedPushError(
            f"repository must be inside the Codex worktree root: {worktrees}"
        ) from exc
    if not resolved_repo.is_dir():
        raise GuardedPushError(f"repository is not a directory: {resolved_repo}")
    return resolved_repo


def allowed_origin_urls(repository: str) -> set[str]:
    return {
        f"https://github.com/{repository}.git",
        f"git@github.com:{repository}.git",
        f"ssh://git@github.com/{repository}.git",
    }


def git(repo: Path, args: Sequence[str], *, runner: Runner = run_command, check: bool = True) -> CommandResult:
    return runner(
        ["git", "-c", "submodule.recurse=false", "-C", str(repo), *args],
        timeout=READ_TIMEOUT_SECONDS,
        check=check,
    )


def validate_local_repo(
    repo: Path,
    repository: str,
    expected_remote_head: str,
    expected_new_head: str,
    *,
    runner: Runner = run_command,
) -> str:
    if expected_remote_head == expected_new_head:
        raise GuardedPushError("expected new head must advance beyond the remote head")
    origin = git(repo, ["remote", "get-url", "origin"], runner=runner).stdout.strip()
    if origin not in allowed_origin_urls(repository):
        raise GuardedPushError(f"origin is not the exact allowed ARC repository URL: {origin!r}")
    push_origin = git(
        repo, ["remote", "get-url", "--push", "origin"], runner=runner
    ).stdout.strip()
    if push_origin not in allowed_origin_urls(repository):
        raise GuardedPushError(
            f"origin push URL is not the exact allowed ARC repository URL: {push_origin!r}"
        )

    dirty = git(
        repo,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        runner=runner,
    ).stdout
    if dirty:
        raise GuardedPushError("worktree must be clean before a scheduled remediation push")

    branch = git(repo, ["branch", "--show-current"], runner=runner).stdout.strip()
    if not branch.startswith(ALLOWED_BRANCH_PREFIX) or branch == ALLOWED_BRANCH_PREFIX:
        raise GuardedPushError(
            f"current branch must match {ALLOWED_BRANCH_PREFIX}*: {branch!r}"
        )

    local_head = git(repo, ["rev-parse", "HEAD"], runner=runner).stdout.strip()
    if local_head != expected_new_head:
        raise GuardedPushError(
            f"local HEAD mismatch: expected {expected_new_head}, found {local_head}"
        )

    git(repo, ["cat-file", "-e", f"{expected_remote_head}^{{commit}}"], runner=runner)
    git(repo, ["cat-file", "-e", f"{expected_new_head}^{{commit}}"], runner=runner)
    ancestry = git(
        repo,
        ["merge-base", "--is-ancestor", expected_remote_head, expected_new_head],
        runner=runner,
        check=False,
    )
    if ancestry.returncode != 0:
        raise GuardedPushError("expected remote head is not an ancestor of expected new head")

    merges = git(
        repo,
        ["rev-list", "--merges", f"{expected_remote_head}..{expected_new_head}"],
        runner=runner,
    ).stdout.strip()
    if merges:
        raise GuardedPushError("review remediation range contains a merge commit")
    return branch


def parse_pull_request(payload: dict[str, Any]) -> PullRequestState:
    try:
        head = payload["head"]
        base = payload["base"]
        head_repo = head["repo"]["full_name"]
        base_repo = base["repo"]["full_name"]
        result = PullRequestState(
            state=str(payload["state"]),
            head_branch=str(head["ref"]),
            head_sha=str(head["sha"]),
            head_repository=str(head_repo),
            base_repository=str(base_repo),
            base_branch=str(base["ref"]),
        )
    except (KeyError, TypeError) as exc:
        raise GuardedPushError("GitHub returned malformed pull-request state") from exc
    if not SHA_RE.fullmatch(result.head_sha):
        raise GuardedPushError("GitHub returned a malformed pull-request head SHA")
    return result


def fetch_pull_request(
    repository: str,
    pr: int,
    *,
    runner: Runner = run_command,
) -> PullRequestState:
    result = runner(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"repos/{repository}/pulls/{pr}",
        ],
        timeout=READ_TIMEOUT_SECONDS,
        check=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GuardedPushError("GitHub returned invalid JSON for the pull request") from exc
    if not isinstance(payload, dict):
        raise GuardedPushError("GitHub returned a non-object pull-request response")
    return parse_pull_request(payload)


def require_pr_identity(
    state: PullRequestState,
    *,
    repository: str,
    branch: str,
    expected_head: str,
) -> None:
    if state.state != "open":
        raise GuardedPushError(f"pull request is not open: {state.state!r}")
    if state.base_repository != repository or state.head_repository != repository:
        raise GuardedPushError(
            "pull request must be owned by and target the exact ARC repository"
        )
    if state.base_branch != "main":
        raise GuardedPushError(
            f"pull-request base branch must be 'main', found {state.base_branch!r}"
        )
    if state.head_branch != branch or not state.head_branch.startswith(ALLOWED_BRANCH_PREFIX):
        raise GuardedPushError(
            f"pull-request branch mismatch: expected {branch!r}, found {state.head_branch!r}"
        )
    if state.head_sha != expected_head:
        raise GuardedPushError(
            f"pull-request head mismatch: expected {expected_head}, found {state.head_sha}"
        )


def guarded_push(
    *,
    repo: Path,
    repository: str,
    pr: int,
    expected_remote_head: str,
    expected_new_head: str,
    run_context_token: str,
    run_id: str,
    root: Path,
    runner: Runner = run_command,
    pr_fetcher: PrFetcher | None = None,
    context_reader: Callable[..., dict[str, Any]] = control.context_read,
    claim_reader: ClaimReader = control.active_review_claim,
    lease_factory: LeaseFactory = control.lease_guard,
) -> dict[str, Any]:
    context = context_reader(
        root,
        run_context_token,
        run_id=run_id,
        scope="review",
        cwd=repo,
    )["context"]
    owner_id = str(context["owner_id"])
    generation = int(context["generation"])
    fetcher = pr_fetcher or (
        lambda selected_repository, selected_pr: fetch_pull_request(
            selected_repository, selected_pr, runner=runner
        )
    )

    with lease_factory(
        root,
        "review",
        run_id,
        owner_id=owner_id,
        generation=generation,
        context_token=run_context_token,
        hold_seconds=PUSH_TIMEOUT_SECONDS + 60,
    ):
        claim = claim_reader(
            root,
            repository,
            pr,
            expected_remote_head,
            run_id,
            repo,
            run_context_token,
        )
        if claim.get("context") != context:
            raise GuardedPushError("active review claim context changed before push")
        branch = validate_local_repo(
            repo,
            repository,
            expected_remote_head,
            expected_new_head,
            runner=runner,
        )
        before = fetcher(repository, pr)
        require_pr_identity(
            before,
            repository=repository,
            branch=branch,
            expected_head=expected_remote_head,
        )

        push_command = [
            "git",
            "-c",
            "submodule.recurse=false",
            "-c",
            "push.followTags=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repo),
            "push",
            "--no-verify",
            "origin",
            f"HEAD:refs/heads/{branch}",
        ]
        timed_out = False
        try:
            push_result = runner(
                push_command,
                timeout=PUSH_TIMEOUT_SECONDS,
                check=False,
            )
        except CommandTimeout:
            timed_out = True
            push_result = None

        after = fetcher(repository, pr)
        if (
            after.state == "open"
            and after.base_repository == repository
            and after.head_repository == repository
            and after.base_branch == "main"
            and after.head_branch == branch
            and after.head_sha == expected_new_head
        ):
            return {
                "state": "pushed-reconciled" if timed_out else "pushed",
                "repository": repository,
                "pr": pr,
                "branch": branch,
                "previous_head": expected_remote_head,
                "head": expected_new_head,
                "lease_generation": generation,
            }

        if timed_out:
            raise GuardedPushError(
                "push timed out and canonical PR state did not reconcile to the expected new head"
            )
        assert push_result is not None
        if push_result.returncode != 0:
            detail = push_result.stderr.strip() or push_result.stdout.strip() or "no command output"
            raise GuardedPushError(
                f"non-force push failed ({push_result.returncode}) and did not reconcile: {detail}"
            )
        require_pr_identity(
            after,
            repository=repository,
            branch=branch,
            expected_head=expected_new_head,
        )
        raise GuardedPushError("push returned success but the canonical PR head did not advance")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument(
        "--repository",
        default=ALLOWED_REPOSITORY,
        type=validate_repository,
    )
    parser.add_argument("--pr", required=True, type=positive_int)
    parser.add_argument("--expected-remote-head", required=True, type=lowercase_sha)
    parser.add_argument("--expected-new-head", required=True, type=lowercase_sha)
    parser.add_argument("--run-context-token", required=True, type=context_token)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_id = os.environ.get("CODEX_THREAD_ID")
    if not run_id:
        parser.error("CODEX_THREAD_ID is required")
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    try:
        repo = resolve_repo(args.repo, codex_home)
        result = guarded_push(
            repo=repo,
            repository=args.repository,
            pr=args.pr,
            expected_remote_head=args.expected_remote_head,
            expected_new_head=args.expected_new_head,
            run_context_token=args.run_context_token,
            run_id=run_id,
            root=control.state_root(),
        )
    except (GuardedPushError, control.ControlError, OSError, ValueError) as exc:
        print(json.dumps({"state": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
