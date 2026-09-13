#!/usr/bin/env python3
"""Publish one bounded ARC Improve change from an isolated Codex worktree."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import signal
import stat
import subprocess
import sys
from typing import Any, ContextManager

import automation_control


ALLOWED_REMOTES = {
    "git@github.com:arc-edge/arc-uas.git",
    "https://github.com/arc-edge/arc-uas.git",
}
BRANCH_RE = re.compile(r"^codex/arc-improve/[a-z0-9][a-z0-9._-]{2,60}$")
COMMIT_RE = re.compile(
    r"^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(?:\([a-z0-9._-]+\))?!?: .{1,72}$"
)
RISK_EVIDENCE = {
    "build-system": "canonical build or contract checks for the changed build behavior",
    "deployment": "render, dry-run, or static deployment validation with no live deployment",
    "filesystem-link": "link-target review and repository-boundary validation",
    "flight-critical": "OpenSpec, regression-relevant SITL evidence, and a named human flight owner",
    "hardware": "platform-specific static validation or disconnected simulation with no hardware operation",
    "policy": "independent review for weakened safety, authority, or validation rules",
    "secret-handling": "secret scan plus security review with no credential or identity mutation",
    "submodule": "resolved commit, reachability, owning-repository validation, and rollback evidence",
    "workflow": "workflow lint plus trigger, permission, secret, and required-check review",
}
SECRET_MATERIAL_NAMES = {
    ".env",
    ".netrc",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
SECRET_MATERIAL_SUFFIXES = {".key", ".p12", ".pfx"}
SECRET_TEMPLATE_SUFFIXES = {".example", ".sample", ".template"}
SECRET_LINK_COMPONENTS = {".aws", ".gnupg", ".ssh", "credentials", "secrets"}
SECRET_DIFF_RES = (
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}"),
)
REQUIRED_BODY_HEADINGS = ("## Evidence", "## Not verified", "## Review focus")
READ_TIMEOUT_SECONDS = 120
MUTATION_TIMEOUT_SECONDS = 300
LEASE_GUARD_SECONDS = 15 * 60
GITHUB_REPOSITORY = "arc-edge/arc-uas"
GITHUB_REPOSITORY_SPEC = f"github.com/{GITHUB_REPOSITORY}"
GITHUB_OWNER = "arc-edge"
RUN_CONTEXT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_PR_BODY_BYTES = 256 * 1024
SITL_EVIDENCE_RE = re.compile(
    r"^SITL:\s*PASS(?:ED)?\s*;\s*scenario=(?P<scenario>[^;\n]+)"
    r"\s*;\s*result=(?P<result>[^\n]+)$",
    re.MULTILINE,
)
FLIGHT_OWNER_RE = re.compile(r"^Flight owner:\s*(?P<owner>[^\n]+)$", re.MULTILINE)
EVIDENCE_PLACEHOLDERS = {
    "none",
    "not run",
    "n/a",
    "pending",
    "required",
    "required before merge",
    "tbd",
    "todo",
    "unassigned",
    "unknown",
}
NEGATIVE_EVIDENCE_RE = re.compile(
    r"\b(?:blocked|fail(?:ed|ure)?|not\s+run|pending|skipped|tbd|todo|unavailable)\b",
    re.IGNORECASE,
)


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class BodySnapshot:
    """The exact, validated bytes that will be sent to GitHub."""

    text: str
    sha256: str
    size: int


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate and reap a command plus any descendants it started."""
    try:
        # The group can outlive its leader, so signal it even when the direct
        # child has already exited while a descendant still owns a pipe.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        else:
            process.wait()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def run(
    repo: Path,
    *args: str,
    check: bool = True,
    timeout_seconds: int = READ_TIMEOUT_SECONDS,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(args),
        cwd=repo,
        text=True,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise PublishError(
            f"{' '.join(args)} timed out after {timeout_seconds} seconds"
        ) from exc
    except BaseException:
        _terminate_process_group(process)
        raise
    result = subprocess.CompletedProcess(list(args), process.returncode, stdout, stderr)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise PublishError(f"{' '.join(args)} failed: {detail}")
    return result


def git(
    repo: Path,
    *args: str,
    check: bool = True,
    timeout_seconds: int = READ_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    return run(repo, "git", *args, check=check, timeout_seconds=timeout_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--branch")
    parser.add_argument("--commit-message")
    parser.add_argument("--title")
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--base", default="main", choices=("main",))
    parser.add_argument("--max-files", type=int, default=24)
    parser.add_argument("--max-changed-lines", type=int, default=1600)
    parser.add_argument(
        "--evidence-profile",
        action="append",
        default=[],
        choices=tuple(sorted(RISK_EVIDENCE)),
        help="risk profile whose required evidence was obtained and recorded",
    )
    parser.add_argument(
        "--preflight-path",
        action="append",
        default=[],
        help="likely changed path to classify before implementation; repeat as needed",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--require-lease",
        choices=("delivery",),
        help="require the current CODEX_THREAD_ID to own this scheduled lease",
    )
    parser.add_argument(
        "--run-context-token",
        help="32-character invocation token returned by start-scheduled-run",
    )
    parser.add_argument(
        "--lease-owner-id",
        help="optional explicit owner; must match the active per-invocation context",
    )
    parser.add_argument(
        "--lease-generation",
        type=int,
        help="optional explicit generation; must match the active per-invocation context",
    )
    return parser.parse_args()


def _is_well_formed_repo_path(path: str) -> bool:
    parts = path.split("/")
    normalized = PurePosixPath(path).as_posix()
    return (
        bool(path)
        and path != "."
        and not path.startswith("/")
        and normalized == path
        and all(part not in {"", ".", ".."} for part in parts)
    )


def is_secret_material_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in SECRET_MATERIAL_NAMES:
        return True
    if name.startswith(".env."):
        return not any(name.endswith(suffix) for suffix in SECRET_TEMPLATE_SUFFIXES)
    return any(name.endswith(suffix) for suffix in SECRET_MATERIAL_SUFFIXES)


def risk_profiles_for_paths(
    paths: list[str], modes: dict[str, str] | None = None
) -> set[str]:
    profiles: set[str] = set()
    modes = modes or {}

    for path in paths:
        lowered = path.lower()
        name = PurePosixPath(path).name.lower()

        if path.startswith((".github/workflows/", ".github/actions/", ".github/scripts/")):
            profiles.add("workflow")
        if path == "justfile" or name in {"cargo.toml", "cargo.lock"} or "dockerfile" in name:
            profiles.add("build-system")
        if path.startswith("services/flight-control/"):
            profiles.add("flight-critical")
        if path.startswith(("infra/ansible/", "infra/pimod/")) or any(
            part in {"deploy", "deployment"} for part in lowered.split("/")
        ):
            profiles.add("deployment")
        if path.startswith(("infra/platforms/", "infra/jetson/", "infra/hardware/")):
            profiles.add("hardware")
        if path == "AGENTS.md" or path.endswith("/AGENTS.md") or path.startswith(".codex/"):
            profiles.add("policy")
        if re.search(r"(?:^|[._/-])(?:credential|secret|identity|auth)(?:[._/-]|$)", lowered):
            profiles.add("secret-handling")
        if path == ".gitmodules" or modes.get(path) == "160000":
            profiles.add("submodule")
        if modes.get(path) == "120000":
            profiles.add("filesystem-link")

    return profiles


def required_evidence_for(profiles: set[str]) -> dict[str, str]:
    return {profile: RISK_EVIDENCE[profile] for profile in sorted(profiles)}


def validate_risk_evidence(
    required: set[str], provided: list[str], body: str
) -> None:
    missing = sorted(required - set(provided))
    if missing:
        raise PublishError(
            "missing validated risk evidence: "
            + ", ".join(missing)
            + "; run preflight and record the required checks"
        )

    if required:
        absent_headings = [heading for heading in REQUIRED_BODY_HEADINGS if heading not in body]
        if absent_headings:
            raise PublishError(
                "risk-bearing pull-request body is missing: " + ", ".join(absent_headings)
            )

    if "flight-critical" in required:
        normalized = body.lstrip()
        if not normalized.startswith("FLIGHT-CRITICAL CHANGE"):
            raise PublishError("flight-critical pull requests must lead with FLIGHT-CRITICAL CHANGE")
        sitl_matches = list(SITL_EVIDENCE_RE.finditer(body))
        if len(sitl_matches) != 1:
            raise PublishError(
                "flight-critical evidence requires exactly one 'SITL: PASS; "
                "scenario=<regression scenario>; result=<observed result>' line"
            )
        scenario = sitl_matches[0].group("scenario").strip()
        result = sitl_matches[0].group("result").strip()
        if (
            len(scenario) < 8
            or len(result) < 4
            or scenario.lower() in EVIDENCE_PLACEHOLDERS
            or result.lower() in EVIDENCE_PLACEHOLDERS
            or NEGATIVE_EVIDENCE_RE.search(scenario)
            or NEGATIVE_EVIDENCE_RE.search(result)
        ):
            raise PublishError("flight-critical SITL evidence is empty or a placeholder")

        owner_matches = list(FLIGHT_OWNER_RE.finditer(body))
        if len(owner_matches) != 1:
            raise PublishError(
                "flight-critical evidence requires exactly one named 'Flight owner:' line"
            )
        owner = owner_matches[0].group("owner").strip()
        named_owner = bool(
            re.search(r"@[A-Za-z0-9][A-Za-z0-9_-]{1,38}\b", owner)
            or re.fullmatch(
                r"[A-Z][A-Za-z'.-]{1,63}(?:\s+[A-Z][A-Za-z'.-]{1,63})*",
                owner,
            )
        )
        if owner.lower() in EVIDENCE_PLACEHOLDERS or not named_owner:
            raise PublishError("flight-critical evidence must identify a named human flight owner")


def validate_location(repo: Path) -> None:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    expected_root = (codex_home / "worktrees").resolve()
    try:
        repo.relative_to(expected_root)
    except ValueError as exc:
        raise PublishError(f"scheduled publication requires a worktree under {expected_root}") from exc

    top = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != repo:
        raise PublishError(f"--repo must be the repository root ({top})")
    remote = git(repo, "remote", "get-url", "origin").stdout.strip()
    if remote not in ALLOWED_REMOTES:
        raise PublishError(f"unexpected origin remote: {remote}")


def lease_identity(
    args: argparse.Namespace, repo: Path
) -> tuple[Path, str, str, int, str]:
    scope = args.require_lease
    if scope is None:
        raise PublishError("actual publication requires --require-lease delivery")
    run_id = os.environ.get("CODEX_THREAD_ID", "")
    if not run_id:
        raise PublishError("scheduled publication requires CODEX_THREAD_ID")
    if not isinstance(args.run_context_token, str) or not RUN_CONTEXT_TOKEN_RE.fullmatch(
        args.run_context_token
    ):
        raise PublishError(
            "scheduled publication requires --run-context-token as 32 lowercase hex characters"
        )
    root = automation_control.state_root()
    try:
        active = automation_control.context_read(
            root,
            args.run_context_token,
            run_id=run_id,
            scope=scope,
            cwd=repo,
        )["context"]
    except (OSError, KeyError, TypeError, ValueError, automation_control.ControlError) as exc:
        raise PublishError(f"cannot resolve scheduled-run context token: {exc}") from exc
    owner_id = active.get("owner_id")
    generation = active.get("generation")
    if (
        not isinstance(owner_id, str)
        or not owner_id
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        raise PublishError("active scheduled-run context has invalid fencing identity")
    if args.lease_owner_id is not None and args.lease_owner_id != owner_id:
        raise PublishError("explicit lease owner does not match active scheduled-run context")
    if args.lease_generation is not None and args.lease_generation != generation:
        raise PublishError("explicit lease generation does not match active scheduled-run context")
    return root, run_id, owner_id, generation, args.run_context_token


def validate_lease(args: argparse.Namespace, repo: Path) -> None:
    if args.require_lease is None:
        if args.lease_owner_id is not None or args.lease_generation is not None:
            raise PublishError("lease owner/generation require --require-lease")
        return
    root, run_id, owner_id, generation, context_token = lease_identity(args, repo)
    try:
        automation_control.lease_assert(
            root,
            args.require_lease,
            run_id,
            owner_id=owner_id,
            generation=generation,
            context_token=context_token,
        )
    except (OSError, automation_control.ControlError) as exc:
        raise PublishError(f"scheduled publication lease check failed: {exc}") from exc


def capture_pr_body(path: Path) -> BodySnapshot:
    """Open the body once without following links and retain an immutable snapshot."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path.expanduser(), flags)
    except OSError as exc:
        raise PublishError(f"cannot securely open pull-request body: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise PublishError("pull-request body must be a regular non-symlink file")
        if before.st_uid != os.getuid():
            raise PublishError("pull-request body must be owned by the current user")
        if before.st_nlink != 1:
            raise PublishError("pull-request body must not have multiple hard links")
        if before.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PublishError("pull-request body must not be group/world writable")
        if before.st_size > MAX_PR_BODY_BYTES:
            raise PublishError("pull-request body exceeds the 256 KiB limit")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_PR_BODY_BYTES + 1)
        after = os.fstat(fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise PublishError("pull-request body changed while it was being captured")
        if len(raw) > MAX_PR_BODY_BYTES:
            raise PublishError("pull-request body exceeds the 256 KiB limit")
    finally:
        os.close(fd)
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublishError("pull-request body must be valid UTF-8") from exc
    if "\0" in body:
        raise PublishError("pull-request body contains a NUL byte")
    if len(body) < 80:
        raise PublishError("pull-request body is too short to carry evidence")
    for pattern in SECRET_DIFF_RES:
        if pattern.search(body):
            raise PublishError("pull-request body contains a secret-like value")
    return BodySnapshot(
        text=body,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
    )


def validate_inputs(args: argparse.Namespace) -> BodySnapshot | None:
    if args.require_lease is not None:
        if not isinstance(args.run_context_token, str) or not RUN_CONTEXT_TOKEN_RE.fullmatch(
            args.run_context_token
        ):
            raise PublishError(
                "--run-context-token must be 32 lowercase hex characters with --require-lease"
            )
        if (args.lease_owner_id is None) != (args.lease_generation is None):
            raise PublishError("lease owner and generation must be supplied together")
        if args.lease_owner_id == "":
            raise PublishError("lease owner cannot be empty")
        if args.lease_generation is not None and args.lease_generation < 1:
            raise PublishError("explicit lease generation must be positive")
    elif any(
        value is not None
        for value in (
            args.run_context_token,
            args.lease_owner_id,
            args.lease_generation,
        )
    ):
        raise PublishError("run context and lease identity require --require-lease")

    if args.preflight_path:
        if args.validate_only:
            raise PublishError("--preflight-path cannot be combined with --validate-only")
        malformed = [path for path in args.preflight_path if not _is_well_formed_repo_path(path)]
        if malformed:
            raise PublishError(f"invalid preflight path: {malformed[0]!r}")
        prohibited = [path for path in args.preflight_path if is_secret_material_path(path)]
        if prohibited:
            raise PublishError(f"preflight identifies secret material path: {prohibited[0]}")
        return None

    if not args.validate_only and args.require_lease is None:
        raise PublishError("actual publication requires --require-lease delivery")

    missing = [
        name
        for name in ("branch", "commit_message", "title", "body_file")
        if getattr(args, name) is None
    ]
    if missing:
        raise PublishError("missing publication arguments: " + ", ".join(missing))

    assert args.branch is not None
    assert args.commit_message is not None
    assert args.title is not None
    assert args.body_file is not None
    if not BRANCH_RE.fullmatch(args.branch):
        raise PublishError("branch must match codex/arc-improve/<short-kebab-name>")
    if not COMMIT_RE.fullmatch(args.commit_message):
        raise PublishError("commit message must be a short Conventional Commit")
    if not (8 <= len(args.title) <= 100):
        raise PublishError("pull-request title must contain 8-100 characters")
    if args.max_files < 1 or args.max_files > 40:
        raise PublishError("max-files must be between 1 and 40")
    if args.max_changed_lines < 1 or args.max_changed_lines > 3000:
        raise PublishError("max-changed-lines must be between 1 and 3000")
    return capture_pr_body(args.body_file)


def staged_paths(repo: Path) -> list[str]:
    raw = git(repo, "diff", "--cached", "--no-renames", "--name-only", "-z").stdout
    return [path for path in raw.split("\0") if path]


def validate_symlink_target(path: str, target: str) -> None:
    """Require a staged symlink to resolve to a non-secret path inside the repo."""
    if not target or any(character in target for character in ("\0", "\r", "\n")):
        raise PublishError(f"symlink {path} has an invalid target")
    if (
        PurePosixPath(target).is_absolute()
        or re.match(r"^[A-Za-z]:", target)
        or target.startswith("\\")
        or "://" in target
    ):
        raise PublishError(f"symlink {path} has an absolute target: {target!r}")

    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if normalized == ".." or normalized.startswith("../"):
        raise PublishError(f"symlink {path} escapes the repository: {target!r}")
    parts = PurePosixPath(normalized).parts
    if any(part.lower() == ".git" for part in parts):
        raise PublishError(f"symlink {path} targets Git metadata: {target!r}")
    if is_secret_material_path(normalized) or any(
        part.lower() in SECRET_LINK_COMPONENTS or is_secret_material_path(part)
        for part in parts
    ):
        raise PublishError(f"symlink {path} targets likely secret material: {target!r}")


def validate_staged_change(
    repo: Path, args: argparse.Namespace, body_snapshot: BodySnapshot
) -> tuple[list[str], set[str], str]:
    git(repo, "add", "--all")
    git(repo, "diff", "--cached", "--check")
    paths = staged_paths(repo)
    if not paths:
        raise PublishError("no staged change to publish")
    if len(paths) > args.max_files:
        raise PublishError(f"change touches {len(paths)} files; limit is {args.max_files}")

    for raw_path in paths:
        path = PurePosixPath(raw_path).as_posix()
        if not _is_well_formed_repo_path(path):
            raise PublishError(f"invalid repository path: {raw_path!r}")
        if is_secret_material_path(path):
            raise PublishError(f"publication denies likely secret material: {path}")

    numstat = git(repo, "diff", "--cached", "--no-renames", "--numstat").stdout.splitlines()
    changed_lines = 0
    for row in numstat:
        added, deleted, _ = row.split("\t", 2)
        if added == "-" or deleted == "-":
            raise PublishError("scheduled publication denies binary changes")
        changed_lines += int(added) + int(deleted)
    if changed_lines > args.max_changed_lines:
        raise PublishError(
            f"change has {changed_lines} added/deleted lines; limit is {args.max_changed_lines}"
        )

    staged_diff = git(
        repo,
        "diff",
        "--cached",
        "--no-ext-diff",
        "--no-renames",
        "--unified=0",
    ).stdout
    for pattern in SECRET_DIFF_RES:
        if pattern.search(staged_diff):
            raise PublishError("staged diff contains a secret-like value")

    modes: dict[str, str] = {}
    for row in git(repo, "ls-files", "--stage", "-z", "--", *paths).stdout.split("\0"):
        if not row:
            continue
        metadata, path = row.split("\t", 1)
        modes[path] = metadata.split(" ", 1)[0]

    for path, mode in modes.items():
        if mode == "120000":
            target = git(repo, "show", f":{path}").stdout
            validate_symlink_target(path, target)

    profiles = risk_profiles_for_paths(paths, modes)
    validate_risk_evidence(profiles, args.evidence_profile, body_snapshot.text)
    staged_tree = git(repo, "write-tree").stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", staged_tree):
        raise PublishError("git write-tree returned an invalid object id")
    return paths, profiles, staged_tree


def ensure_fresh_base(repo: Path, base: str, *, exact: bool) -> str:
    base_oid = git(repo, "rev-parse", "--verify", f"refs/remotes/origin/{base}").stdout.strip()
    head_oid = git(repo, "rev-parse", "HEAD").stdout.strip()
    if exact:
        if head_oid != base_oid:
            raise PublishError(
                f"scheduled publication requires HEAD exactly at origin/{base}; "
                f"found {head_oid[:12]} vs {base_oid[:12]}"
            )
    else:
        ancestor = git(
            repo,
            "merge-base",
            "--is-ancestor",
            f"origin/{base}",
            "HEAD",
            check=False,
        )
        if ancestor.returncode != 0:
            raise PublishError(f"HEAD is not based on origin/{base}; start a fresh worktree")
    return base_oid


def remote_ref_sha(repo: Path, ref: str) -> str | None:
    result = git(repo, "ls-remote", "--refs", "origin", ref)
    rows = [row for row in result.stdout.splitlines() if row.strip()]
    if not rows:
        return None
    if len(rows) != 1:
        raise PublishError(f"origin returned multiple objects for {ref}")
    fields = rows[0].split("\t")
    if len(fields) != 2 or fields[1] != ref or not re.fullmatch(r"[0-9a-f]{40,64}", fields[0]):
        raise PublishError(f"origin returned malformed data for {ref}")
    return fields[0]


def origin_branch_sha(repo: Path, branch: str) -> str | None:
    return remote_ref_sha(repo, f"refs/heads/{branch}")


def require_expected_remote(repo: Path) -> None:
    remote = git(repo, "remote", "get-url", "origin").stdout.strip()
    if remote not in ALLOWED_REMOTES:
        raise PublishError(f"origin remote changed before publication: {remote}")


def require_remote_base_unchanged(repo: Path, base: str, expected: str) -> None:
    actual = remote_ref_sha(repo, f"refs/heads/{base}")
    if actual != expected:
        rendered = actual[:12] if actual else "missing"
        raise PublishError(
            f"origin/{base} moved or disappeared before publication: "
            f"expected {expected[:12]}, found {rendered}"
        )


def validate_created_commit(repo: Path, commit: str, base_oid: str, staged_tree: str) -> None:
    parent_row = git(repo, "rev-list", "--parents", "-n", "1", commit).stdout.strip().split()
    if parent_row != [commit, base_oid]:
        raise PublishError("publication commit must have exactly origin/main as its sole parent")
    actual_tree = git(repo, "rev-parse", f"{commit}^{{tree}}").stdout.strip()
    if actual_tree != staged_tree:
        raise PublishError("publication commit tree differs from the validated staged tree")


def _decode_pr_list(output: str) -> list[dict[str, Any]]:
    value = json.loads(output or "[]")
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise PublishError("GitHub returned malformed pull-request list data")
    return value


def open_prs_for_branch(repo: Path, branch: str) -> list[dict[str, Any]]:
    result = run(
        repo,
        "gh",
        "pr",
        "list",
        "--repo",
        GITHUB_REPOSITORY_SPEC,
        "--head",
        branch,
        "--state",
        "open",
        "--json",
        "number,url,baseRefName,headRefName,headRefOid,headRepositoryOwner,state,isDraft",
    )
    return _decode_pr_list(result.stdout)


def canonical_pr(repo: Path, number: int) -> dict[str, Any]:
    result = run(
        repo,
        "gh",
        "pr",
        "view",
        str(number),
        "--repo",
        GITHUB_REPOSITORY_SPEC,
        "--json",
        "number,url,baseRefName,headRefName,headRefOid,headRepositoryOwner,state,isDraft",
    )
    value = json.loads(result.stdout or "{}")
    if not isinstance(value, dict):
        raise PublishError("GitHub returned malformed pull-request data")
    return value


def require_exact_pr(
    pr: dict[str, Any], commit: str, branch: str, base: str
) -> tuple[int, str]:
    number = pr.get("number")
    url = pr.get("url")
    head = pr.get("headRefOid")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise PublishError("GitHub pull request has no valid number")
    expected_url = f"https://github.com/{GITHUB_REPOSITORY}/pull/{number}"
    if url != expected_url:
        raise PublishError("GitHub pull request URL does not match its canonical number")
    if head != commit:
        raise PublishError(
            f"pull request #{number} points to {str(head)[:12]}, not {commit[:12]}"
        )
    if pr.get("state") != "OPEN":
        raise PublishError(f"pull request #{number} is not open")
    if pr.get("headRefName") != branch:
        raise PublishError(f"pull request #{number} has an unexpected head branch")
    if pr.get("baseRefName") != base:
        raise PublishError(f"pull request #{number} has an unexpected base branch")
    owner = pr.get("headRepositoryOwner")
    if not isinstance(owner, dict) or owner.get("login") != GITHUB_OWNER:
        raise PublishError(f"pull request #{number} has an unexpected head repository owner")
    if pr.get("isDraft") is not True:
        raise PublishError(f"pull request #{number} is not a draft")
    return number, url


def reconcile_remote_branch(repo: Path, branch: str, commit: str) -> None:
    actual = origin_branch_sha(repo, branch)
    if actual != commit:
        rendered = actual[:12] if actual else "missing"
        raise PublishError(
            f"origin branch reconciliation failed: expected {commit[:12]}, found {rendered}"
        )


def push_with_reconciliation(repo: Path, branch: str, commit: str) -> None:
    existing = origin_branch_sha(repo, branch)
    if existing is not None:
        raise PublishError(
            f"origin branch already exists at {existing[:12]}; refusing to adopt or overwrite it"
        )
    push_error = ""
    try:
        push = run(
            repo,
            "gh",
            "api",
            "--hostname",
            "github.com",
            "-X",
            "POST",
            f"repos/{GITHUB_REPOSITORY}/git/refs",
            "-f",
            f"ref=refs/heads/{branch}",
            "-f",
            f"sha={commit}",
            check=False,
            timeout_seconds=MUTATION_TIMEOUT_SECONDS,
        )
        push_error = push.stderr.strip() or push.stdout.strip() or f"exit {push.returncode}"
    except PublishError as exc:
        # A timeout or broken output channel is ambiguous: query the remote before
        # deciding whether the mutation succeeded.
        push_error = str(exc)
    try:
        reconcile_remote_branch(repo, branch, commit)
    except PublishError as exc:
        raise PublishError(
            f"remote branch creation was not reconciled ({push_error}): {exc}"
        ) from exc


def create_pr_with_reconciliation(
    repo: Path, args: argparse.Namespace, commit: str, body_snapshot: BodySnapshot
) -> tuple[int, str, dict[str, Any]]:
    assert args.branch is not None
    assert args.title is not None
    existing = open_prs_for_branch(repo, args.branch)
    if existing:
        if len(existing) != 1:
            raise PublishError("multiple open pull requests exist for the publication branch")
        # This permits recovery from a successful create whose command output was lost,
        # but it never adopts a PR pointing at a different object.
        number, _ = require_exact_pr(existing[0], commit, args.branch, args.base)
    else:
        creation_error = ""
        try:
            creation = run(
                repo,
                "gh",
                "pr",
                "create",
                "--repo",
                GITHUB_REPOSITORY_SPEC,
                "--draft",
                "--base",
                args.base,
                "--head",
                args.branch,
                "--title",
                args.title,
                "--body-file",
                "-",
                check=False,
                timeout_seconds=MUTATION_TIMEOUT_SECONDS,
                input_text=body_snapshot.text,
            )
            creation_error = (
                creation.stderr.strip()
                or creation.stdout.strip()
                or f"exit {creation.returncode}"
            )
        except PublishError as exc:
            creation_error = str(exc)
        reconciled = open_prs_for_branch(repo, args.branch)
        if len(reconciled) != 1:
            raise PublishError(
                f"pull-request creation was not reconciled ({creation_error}); "
                f"found {len(reconciled)} matching open pull requests"
            )
        number, _ = require_exact_pr(reconciled[0], commit, args.branch, args.base)

    authoritative = canonical_pr(repo, number)
    canonical_number, canonical_url = require_exact_pr(
        authoritative, commit, args.branch, args.base
    )
    return canonical_number, canonical_url, authoritative


def _publication_guard(
    repo: Path, args: argparse.Namespace
) -> ContextManager[dict[str, Any]]:
    root, run_id, owner_id, generation, context_token = lease_identity(args, repo)
    assert args.require_lease is not None
    return automation_control.lease_guard(
        root,
        args.require_lease,
        run_id,
        owner_id=owner_id,
        generation=generation,
        context_token=context_token,
        hold_seconds=LEASE_GUARD_SECONDS,
    )


def publish(
    repo: Path,
    args: argparse.Namespace,
    profiles: set[str],
    body_snapshot: BodySnapshot,
    *,
    base_oid: str,
    staged_tree: str,
) -> dict[str, object]:
    assert args.branch is not None
    assert args.commit_message is not None
    assert args.title is not None
    if origin_branch_sha(repo, args.branch) is not None:
        raise PublishError(f"origin branch already exists: {args.branch}")
    current = git(repo, "branch", "--show-current").stdout.strip()
    if current and current != args.branch:
        raise PublishError(f"worktree is already on unexpected branch: {current}")
    if not current:
        git(repo, "switch", "-c", args.branch)
    git(repo, "commit", "-m", args.commit_message)
    commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    validate_created_commit(repo, commit, base_oid, staged_tree)

    with _publication_guard(repo, args):
        if git(repo, "branch", "--show-current").stdout.strip() != args.branch:
            raise PublishError("publication branch changed before remote mutation")
        if git(repo, "rev-parse", "HEAD").stdout.strip() != commit:
            raise PublishError("HEAD changed before remote mutation")
        validate_created_commit(repo, commit, base_oid, staged_tree)
        if git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout:
            raise PublishError("worktree changed after validation and before publication")
        require_expected_remote(repo)
        require_remote_base_unchanged(repo, args.base, base_oid)
        push_with_reconciliation(repo, args.branch, commit)
        number, pr_url, authoritative_pr = create_pr_with_reconciliation(
            repo, args, commit, body_snapshot
        )
        try:
            queue = automation_control.review_record(
                automation_control.state_root(),
                GITHUB_REPOSITORY,
                number,
                commit,
                "queued",
                {"pull_request_url": pr_url, "source": "publish_draft_pr"},
                None,
            )
            review_queue = queue["state"]
            review_queue_warning = None
        except (OSError, automation_control.ControlError) as exc:
            # The PR is authoritative once exact-head reconciliation succeeds.
            # Preserve that outcome while making the queue failure explicit.
            review_queue = "failed"
            review_queue_warning = str(exc)

    result: dict[str, object] = {
        "state": "published",
        "branch": args.branch,
        "commit": commit,
        "pull_request": pr_url,
        "pull_request_number": number,
        "pull_request_head": authoritative_pr["headRefOid"],
        "risk_profiles": sorted(profiles),
        "review_queue": review_queue,
    }
    if review_queue_warning is not None:
        result["review_queue_warning"] = review_queue_warning
    return result


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    try:
        body_snapshot = validate_inputs(args)
        validate_location(repo)
        validate_lease(args, repo)
        if args.preflight_path:
            profiles = risk_profiles_for_paths(args.preflight_path)
            print(
                json.dumps(
                    {
                        "state": "preflight",
                        "paths": args.preflight_path,
                        "risk_profiles": sorted(profiles),
                        "required_evidence": required_evidence_for(profiles),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        if body_snapshot is None:
            raise PublishError("publication body snapshot is unavailable")

        scheduled_publication = bool(args.require_lease and not args.validate_only)
        base_oid = ensure_fresh_base(repo, args.base, exact=scheduled_publication)
        paths, profiles, staged_tree = validate_staged_change(
            repo, args, body_snapshot
        )
        validate_lease(args, repo)
        if args.validate_only:
            result: dict[str, object] = {
                "state": "validated",
                "branch": args.branch,
                "files": paths,
                "risk_profiles": sorted(profiles),
                "required_evidence": required_evidence_for(profiles),
                "staged_tree": staged_tree,
            }
        else:
            result = publish(
                repo,
                args,
                profiles,
                body_snapshot,
                base_oid=base_oid,
                staged_tree=staged_tree,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, PublishError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"state": "denied", "reason": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
