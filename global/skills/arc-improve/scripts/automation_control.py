#!/usr/bin/env python3
"""Coordinate Bobby's local ARC Improvement delivery and review automations."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import posixpath
import re
import socket
import stat
import sys
import tempfile
from typing import Any, Iterator
import uuid


VERSION = 1
DEFAULT_LEASE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_REVIEW_RETRY_SECONDS = 4 * 60 * 60
EXIT_BUSY = 75
EXIT_NOT_OWNER = 76
EXIT_NOT_ELIGIBLE = 77
COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TERMINAL_REVIEW_STATES = {"posted", "remediated", "superseded", "needs-human"}
RETRYABLE_REVIEW_STATES = {"timeout", "failed"}
RUNTIME_SIGNAL_KINDS = {
    "current-runtime",
    "current-deployment",
    "current-build",
    "reachable-caller",
}
AUTHORITY_SIGNAL_KINDS = {
    "owner-confirmation",
    "approved-spec",
    "accepted-linear",
    "recent-decision",
}
IMPACT_CLASS_VALUES = {
    "flight-safety-trust": 4,
    "mission-runtime": 3,
    "systemic-engineering": 2,
    "local-maintenance": 1,
    "cosmetic": 0,
}
SIGNAL_SOURCE_RES = (
    re.compile(r"^repo:[A-Za-z0-9._@+-][A-Za-z0-9._/@+-]{0,255}$"),
    re.compile(r"^linear:[A-Z][A-Z0-9]+-[1-9][0-9]*$"),
    re.compile(r"^openspec:[a-z0-9][a-z0-9._/-]{0,127}$"),
    re.compile(r"^decision:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"),
    re.compile(r"^owner:@?[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"),
)


class ControlError(RuntimeError):
    """A deterministic automation-control failure."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def parse_json_object(raw: str | None, label: str) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControlError(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ControlError(f"{label} must be a JSON object")
    return parsed


def validate_component(value: str, label: str) -> str:
    if not COMPONENT_RE.fullmatch(value):
        raise ControlError(f"invalid {label}: {value!r}")
    return value


def validate_scope(scope: str) -> str:
    if not SCOPE_RE.fullmatch(scope):
        raise ControlError(f"invalid lease scope: {scope!r}")
    return scope


def validate_run_token(token: str) -> str:
    if not RUN_TOKEN_RE.fullmatch(token):
        raise ControlError("run token must be exactly 32 lowercase hexadecimal characters")
    return token


def run_token_digest(token: str) -> str:
    return hashlib.sha256(validate_run_token(token).encode("ascii")).hexdigest()


def state_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        requested = explicit.expanduser()
    elif os.environ.get("ARC_IMPROVE_STATE_DIR"):
        requested = Path(os.environ["ARC_IMPROVE_STATE_DIR"]).expanduser()
    else:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        requested = (codex_home / "arc-improve").expanduser()
    _reject_symlink_components(requested)
    root = requested.resolve()
    secure_directory(root)
    return root


def _reject_symlink_components(path: Path) -> None:
    lexical = path.absolute()
    components = [lexical, *lexical.parents]
    macos_system_links = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
        Path("/etc"): Path("/private/etc"),
    }
    for component in reversed(components):
        if component.exists() or component.is_symlink():
            try:
                info = component.lstat()
            except OSError as exc:
                raise ControlError(f"cannot inspect state path {component}: {exc}") from exc
            if stat.S_ISLNK(info.st_mode):
                if macos_system_links.get(component) == component.resolve():
                    continue
                raise ControlError(f"state path contains a symlink: {component}")


def secure_directory(path: Path) -> Path:
    _reject_symlink_components(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ControlError(f"state path is not a real directory: {path}")
    if info.st_uid != os.getuid():
        raise ControlError(f"state directory is not owned by the current user: {path}")
    path.chmod(0o700)
    return path


def _reject_unsafe_file(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ControlError(f"state file is not a regular non-symlink file: {path}")
    if info.st_uid != os.getuid():
        raise ControlError(f"state file is not owned by the current user: {path}")


@contextmanager
def locked(path: Path) -> Iterator[None]:
    secure_directory(path.parent)
    _reject_unsafe_file(path)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a+", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    secure_directory(path.parent)
    _reject_unsafe_file(path)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    _reject_unsafe_file(path)
    try:
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        with os.fdopen(os.open(path, flags), "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError(f"cannot read state file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlError(f"state file is not a JSON object: {path}")
    return value


def lease_paths(root: Path, scope: str) -> tuple[Path, Path]:
    validate_scope(scope)
    directory = secure_directory(root / "leases")
    return directory / f"{scope}.json", directory / f"{scope}.lock"


def owner_id_for(run_id: str, cwd: Path | str, nonce: str | None = None) -> str:
    validate_component(run_id, "run id")
    resolved = Path(cwd).expanduser().resolve()
    invocation_nonce = nonce or uuid.uuid4().hex
    validate_component(invocation_nonce, "invocation nonce")
    return hashlib.sha256(
        f"{run_id}\0{resolved}\0{invocation_nonce}".encode("utf-8")
    ).hexdigest()


def context_path(root: Path, token: str) -> Path:
    # The token is a capability. Persist only a one-way digest so another
    # scheduled invocation cannot recover it by enumerating local state.
    token_digest = run_token_digest(token)
    return secure_directory(root / "contexts") / f"context-{token_digest}.json"


def context_create(
    root: Path,
    token: str,
    run_id: str,
    owner_id: str,
    generation: int,
    ledger_run_id: str,
    scope: str,
    cwd: Path | str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_component(run_id, "run id")
    validate_component(owner_id, "owner id")
    validate_component(ledger_run_id, "ledger run id")
    validate_scope(scope)
    if generation < 1:
        raise ControlError("lease generation must be positive")
    resolved_cwd = str(Path(cwd).expanduser().resolve())
    expected_owner = owner_id_for(run_id, resolved_cwd, token)
    if not hmac.compare_digest(owner_id, expected_owner):
        raise ControlError("run context token does not derive the supplied owner id")
    path = context_path(root, token)
    contexts = secure_directory(root / "contexts")
    with locked(contexts / "contexts.lock"):
        if path.exists():
            raise ControlError("run token already has a persisted context")
        lease_assert(
            root,
            scope,
            run_id,
            owner_id=owner_id,
            generation=generation,
            context_token=token,
            now=now,
        )
        for existing_path in contexts.glob("context-*.json"):
            existing = read_json(existing_path)
            if not existing:
                continue
            existing_identity = (
                existing.get("run_id"),
                existing.get("owner_id"),
                existing.get("generation"),
                existing.get("scope"),
                existing.get("cwd"),
            )
            requested_identity = (
                run_id,
                owner_id,
                generation,
                scope,
                resolved_cwd,
            )
            if existing_identity == requested_identity:
                raise ControlError("lease identity already has a persisted run context")
        value = {
            "version": VERSION,
            "token_digest": run_token_digest(token),
            "run_id": run_id,
            "owner_id": owner_id,
            "generation": generation,
            "ledger_run_id": ledger_run_id,
            "scope": scope,
            "cwd": resolved_cwd,
        }
        atomic_write_json(path, value)
        return {"state": "created", "context": value}


def context_read(
    root: Path,
    token: str,
    *,
    run_id: str | None = None,
    scope: str | None = None,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    value = read_json(context_path(root, token))
    if not value:
        raise ControlError("run token does not exist")
    required = {
        "version": int,
        "token_digest": str,
        "run_id": str,
        "owner_id": str,
        "generation": int,
        "ledger_run_id": str,
        "scope": str,
        "cwd": str,
    }
    for key, expected in required.items():
        if key not in value or not isinstance(value[key], expected) or isinstance(value[key], bool):
            raise ControlError(f"malformed run context field: {key}")
    expected_digest = run_token_digest(token)
    if not hmac.compare_digest(value["token_digest"], expected_digest):
        raise ControlError("run context token mismatch")
    if run_id is not None and value["run_id"] != run_id:
        raise ControlError("run context belongs to a different thread")
    if scope is not None and value["scope"] != scope:
        raise ControlError("run context belongs to a different lease scope")
    if cwd is not None and value["cwd"] != str(Path(cwd).expanduser().resolve()):
        raise ControlError("run context belongs to a different worktree")
    return {"state": "ok", "context": value}


def context_delete(root: Path, token: str) -> dict[str, Any]:
    path = context_path(root, token)
    contexts = secure_directory(root / "contexts")
    with locked(contexts / "contexts.lock"):
        if not path.exists():
            return {"state": "already-deleted"}
        _reject_unsafe_file(path)
        path.unlink()
        return {"state": "deleted"}


def has_active_context(
    root: Path,
    run_id: str,
    scope: str,
    cwd: Path | str,
) -> bool:
    """Report scheduled ownership without revealing its capability token."""
    validate_component(run_id, "run id")
    validate_scope(scope)
    expected_cwd = str(Path(cwd).expanduser().resolve())
    directory = root / "contexts"
    for path in directory.glob("*.json") if directory.exists() else []:
        try:
            value = read_json(path)
        except (ControlError, TypeError, ValueError):
            continue
        if not value or (
            value.get("run_id") != run_id
            or value.get("scope") != scope
            or value.get("cwd") != expected_cwd
        ):
            continue
        required = {"owner_id": str, "generation": int, "token_digest": str}
        for key, expected_type in required.items():
            if (
                not isinstance(value.get(key), expected_type)
                or isinstance(value.get(key), bool)
            ):
                raise ControlError("matching run context has invalid fencing identity")
        lease_path, lock_path = lease_paths(root, scope)
        with locked(lock_path):
            lease = read_json(lease_path)
            if not lease:
                raise ControlError(f"no active {scope} lease")
            _validate_lease_document(lease, scope)
            expected = (
                run_id,
                value["owner_id"],
                value["generation"],
                value["token_digest"],
                expected_cwd,
            )
            actual = (
                lease["run_id"],
                lease["owner_id"],
                lease["generation"],
                lease["token_digest"],
                lease["cwd"],
            )
            if actual != expected:
                raise ControlError("matching run context is not bound to the active lease")
            if parse_time(lease["expires_at"]) <= utc_now():
                raise ControlError(f"{scope} lease for {run_id} expired")
            return True
    return False


def _generation_path(root: Path, scope: str) -> Path:
    validate_scope(scope)
    return secure_directory(root / "leases") / f"{scope}.generation.json"


def _next_generation(root: Path, scope: str) -> int:
    path = _generation_path(root, scope)
    current = read_json(path)
    if current:
        if (
            current.get("version") != VERSION
            or current.get("scope") != scope
            or not isinstance(current.get("generation"), int)
            or isinstance(current.get("generation"), bool)
            or current["generation"] < 1
        ):
            raise ControlError(f"malformed {scope} lease generation document")
        generation = current["generation"] + 1
    else:
        generation = 1
    atomic_write_json(path, {"version": VERSION, "scope": scope, "generation": generation})
    return generation


def _validate_lease_document(lease: dict[str, Any], scope: str) -> None:
    required = {
        "version": int,
        "scope": str,
        "run_id": str,
        "owner_id": str,
        "token_digest": str,
        "cwd": str,
        "generation": int,
        "acquired_at": str,
        "heartbeat_at": str,
        "expires_at": str,
        "ttl_seconds": int,
        "metadata": dict,
    }
    for key, expected in required.items():
        if key not in lease or not isinstance(lease[key], expected) or isinstance(lease[key], bool):
            raise ControlError(f"malformed {scope} lease field: {key}")
    if lease["scope"] != scope:
        raise ControlError(f"lease scope mismatch: expected {scope}, found {lease['scope']}")
    validate_component(lease["run_id"], "lease run id")
    validate_component(lease["owner_id"], "lease owner id")
    if not re.fullmatch(r"[0-9a-f]{64}", lease["token_digest"]):
        raise ControlError(f"malformed {scope} lease token digest")
    if lease["cwd"] != str(Path(lease["cwd"]).expanduser().resolve()):
        raise ControlError(f"malformed {scope} lease cwd")
    for key in ("acquired_at", "heartbeat_at", "expires_at"):
        try:
            parse_time(lease[key])
        except (TypeError, ValueError) as exc:
            raise ControlError(f"malformed {scope} lease timestamp: {key}") from exc


def _ownership(
    owner_id: str | None,
    generation: int | None,
    context_token: str | None,
) -> tuple[str, int, str]:
    if owner_id is None or generation is None or context_token is None:
        raise ControlError("lease owner id, generation, and context token are required")
    owner = owner_id
    validate_component(owner, "owner id")
    if generation < 1:
        raise ControlError("lease generation must be positive")
    return owner, generation, run_token_digest(context_token)


def lease_acquire(
    root: Path,
    scope: str,
    run_id: str,
    ttl_seconds: int,
    metadata: dict[str, Any],
    *,
    owner_id: str,
    cwd: Path | str,
    context_token: str,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any]]:
    validate_component(run_id, "run id")
    owner = owner_id
    validate_component(owner, "owner id")
    resolved_cwd = str(Path(cwd).expanduser().resolve())
    expected_owner = owner_id_for(run_id, resolved_cwd, context_token)
    if not hmac.compare_digest(owner, expected_owner):
        raise ControlError("lease context token does not derive the supplied owner id")
    token_digest = run_token_digest(context_token)
    if ttl_seconds < 60 or ttl_seconds > 24 * 60 * 60:
        raise ControlError("lease TTL must be between 60 seconds and 24 hours")
    now = now or utc_now()
    lease_path, lock_path = lease_paths(root, scope)
    with locked(lock_path):
        existing = read_json(lease_path)
        reclaimed: dict[str, Any] | None = None
        if existing:
            _validate_lease_document(existing, scope)
            expires_at = parse_time(existing["expires_at"])
            heartbeat_at = parse_time(existing["heartbeat_at"])
            if heartbeat_at > now + timedelta(minutes=5):
                raise ControlError(f"{scope} lease heartbeat is in the future; refusing recovery")
            if (
                expires_at > now
                and existing.get("run_id") == run_id
                and existing.get("owner_id") == owner
                and hmac.compare_digest(str(existing.get("token_digest", "")), token_digest)
                and existing.get("cwd") == resolved_cwd
            ):
                existing["heartbeat_at"] = isoformat(now)
                existing["expires_at"] = isoformat(now + timedelta(seconds=ttl_seconds))
                existing["metadata"] = {**existing.get("metadata", {}), **metadata}
                atomic_write_json(lease_path, existing)
                return True, {"state": "owned", "lease": existing}
            if expires_at > now:
                return False, {"state": "busy", "lease": existing}
            reclaimed = existing

        generation = _next_generation(root, scope)
        lease = {
            "version": VERSION,
            "scope": scope,
            "run_id": run_id,
            "owner_id": owner,
            "token_digest": token_digest,
            "cwd": resolved_cwd,
            "generation": generation,
            "acquired_at": isoformat(now),
            "heartbeat_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(seconds=ttl_seconds)),
            "ttl_seconds": ttl_seconds,
            "metadata": metadata,
        }
        if reclaimed:
            lease["reclaimed"] = {
                "run_id": reclaimed.get("run_id"),
                "owner_id": reclaimed.get("owner_id"),
                "generation": reclaimed.get("generation"),
                "expires_at": reclaimed.get("expires_at"),
            }
        atomic_write_json(lease_path, lease)
        return True, {"state": "acquired", "lease": lease}


def _assert_lease_unlocked(
    root: Path,
    scope: str,
    run_id: str,
    owner_id: str | None,
    generation: int | None,
    context_token: str | None,
    now: datetime,
) -> dict[str, Any]:
    owner, expected_generation, expected_token_digest = _ownership(
        owner_id, generation, context_token
    )
    lease_path, _ = lease_paths(root, scope)
    lease = read_json(lease_path)
    if not lease:
        raise ControlError(f"no active {scope} lease")
    _validate_lease_document(lease, scope)
    if lease.get("run_id") != run_id or lease.get("owner_id") != owner:
        raise ControlError(
            f"{scope} lease is owned by {lease.get('run_id')}/{lease.get('owner_id')}"
        )
    if lease.get("generation") != expected_generation:
        raise ControlError(
            f"{scope} lease generation changed from {expected_generation} to {lease.get('generation')}"
        )
    if not hmac.compare_digest(str(lease.get("token_digest", "")), expected_token_digest):
        raise ControlError(f"{scope} lease context token does not match its owner")
    derived_owner = owner_id_for(run_id, str(lease["cwd"]), context_token)
    if not hmac.compare_digest(owner, derived_owner):
        raise ControlError(f"{scope} lease context token does not derive its owner")
    if parse_time(lease["expires_at"]) <= now:
        raise ControlError(f"{scope} lease for {run_id}/{owner} expired")
    return lease


def lease_assert(
    root: Path,
    scope: str,
    run_id: str,
    *,
    owner_id: str,
    generation: int,
    context_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    _, lock_path = lease_paths(root, scope)
    with locked(lock_path):
        lease = _assert_lease_unlocked(
            root, scope, run_id, owner_id, generation, context_token, now
        )
        return {"state": "owned", "lease": lease}


def lease_heartbeat(
    root: Path,
    scope: str,
    run_id: str,
    ttl_seconds: int | None,
    *,
    owner_id: str,
    generation: int,
    context_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    lease_path, lock_path = lease_paths(root, scope)
    with locked(lock_path):
        lease = _assert_lease_unlocked(
            root, scope, run_id, owner_id, generation, context_token, now
        )
        ttl = ttl_seconds or int(lease["ttl_seconds"])
        if ttl < 60 or ttl > 24 * 60 * 60:
            raise ControlError("lease TTL must be between 60 seconds and 24 hours")
        lease["heartbeat_at"] = isoformat(now)
        lease["expires_at"] = isoformat(now + timedelta(seconds=ttl))
        lease["ttl_seconds"] = ttl
        atomic_write_json(lease_path, lease)
        return {"state": "renewed", "lease": lease}


def lease_release(
    root: Path,
    scope: str,
    run_id: str,
    *,
    owner_id: str,
    generation: int,
    context_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    lease_path, lock_path = lease_paths(root, scope)
    with locked(lock_path):
        lease = read_json(lease_path)
        if not lease:
            _ownership(owner_id, generation, context_token)
            return {"state": "already-released", "scope": scope, "run_id": run_id}
        lease = _assert_lease_unlocked(
            root, scope, run_id, owner_id, generation, context_token, now or utc_now()
        )
        lease_path.unlink()
        return {"state": "released", "scope": scope, "run_id": run_id}


def lease_recover(
    root: Path,
    scope: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    lease_path, lock_path = lease_paths(root, scope)
    with locked(lock_path):
        lease = read_json(lease_path)
        if not lease:
            return {"state": "no-active-lease", "scope": scope}
        _validate_lease_document(lease, scope)
        if parse_time(lease["expires_at"]) > now:
            raise ControlError(
                f"active {scope} lease has not expired; refusing recovery"
            )
        quarantine = secure_directory(root / "quarantine")
        recovered_at = isoformat(now).replace(":", "").replace("-", "")
        destination = quarantine / (
            f"{scope}-generation-{lease['generation']}-{recovered_at}.json"
        )
        if destination.exists():
            raise ControlError(f"lease quarantine target already exists: {destination}")
        os.replace(lease_path, destination)
        destination.chmod(stat.S_IRUSR)
        return {
            "state": "recovered-expired-lease",
            "scope": scope,
            "quarantine": str(destination),
            "lease": lease,
        }


@contextmanager
def lease_guard(
    root: Path,
    scope: str,
    run_id: str,
    *,
    owner_id: str,
    generation: int,
    context_token: str,
    hold_seconds: int = 600,
    now: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    if hold_seconds < 60 or hold_seconds > 2 * 60 * 60:
        raise ControlError("guard duration must be between 60 seconds and 2 hours")
    lease_path, lock_path = lease_paths(root, scope)
    with locked(lock_path):
        guard_now = now or utc_now()
        lease = _assert_lease_unlocked(
            root, scope, run_id, owner_id, generation, context_token, guard_now
        )
        lease["heartbeat_at"] = isoformat(guard_now)
        lease["expires_at"] = isoformat(guard_now + timedelta(seconds=hold_seconds))
        atomic_write_json(lease_path, lease)
        try:
            yield lease
        finally:
            finished = guard_now if now is not None else utc_now()
            lease["heartbeat_at"] = isoformat(finished)
            lease["expires_at"] = isoformat(
                finished + timedelta(seconds=int(lease["ttl_seconds"]))
            )
            atomic_write_json(lease_path, lease)


def run_path(root: Path, run_id: str) -> Path:
    validate_component(run_id, "run id")
    return secure_directory(root / "runs") / f"{run_id}.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    _reject_unsafe_file(path)
    records: list[dict[str, Any]] = []
    try:
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        with os.fdopen(os.open(path, flags), "r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.endswith("\n"):
                    raise ControlError(f"ledger {path} has an incomplete final record")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ControlError(f"record {line_number} in {path} is not an object")
                records.append(value)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ControlError(f"cannot read ledger {path}: {exc}") from exc
    return records


def verify_chain(records: list[dict[str, Any]], label: str) -> dict[str, Any]:
    previous_hash = ""
    for expected_sequence, record in enumerate(records, 1):
        if record.get("sequence") != expected_sequence:
            raise ControlError(f"{label} sequence mismatch at event {expected_sequence}")
        if record.get("previous_hash") != previous_hash:
            raise ControlError(f"{label} previous-hash mismatch at event {expected_sequence}")
        payload = {key: value for key, value in record.items() if key != "event_hash"}
        expected_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
        if record.get("event_hash") != expected_hash:
            raise ControlError(f"{label} event-hash mismatch at event {expected_sequence}")
        previous_hash = expected_hash
    return {
        "events": len(records),
        "final_hash": previous_hash or None,
        "finalized": bool(records and records[-1].get("event_type") == "run_finished"),
    }


def _append_chained_event(
    path: Path,
    lock_path: Path,
    identity: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    terminal_event: str | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    with locked(lock_path):
        return _append_chained_event_unlocked(
            path,
            identity,
            event_type,
            payload,
            now=now,
            terminal_event=terminal_event,
        )


def _append_chained_event_unlocked(
    path: Path,
    identity: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    *,
    now: datetime,
    terminal_event: str | None = None,
) -> dict[str, Any]:
    records = _read_jsonl(path)
    verify_chain(records, str(path))
    if terminal_event and records and records[-1].get("event_type") == terminal_event:
        raise ControlError(f"ledger is already finalized: {path}")
    event = _make_chained_event(records, identity, event_type, payload, now)
    secure_directory(path.parent)
    _reject_unsafe_file(path)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "a", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def _make_chained_event(
    records: list[dict[str, Any]],
    identity: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    previous_hash = records[-1]["event_hash"] if records else ""
    event = {
        "version": VERSION,
        "sequence": len(records) + 1,
        "timestamp": isoformat(now),
        **identity,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    event["event_hash"] = hashlib.sha256(canonical_json(event)).hexdigest()
    return event


def _atomic_write_jsonl(
    path: Path, records: list[dict[str, Any]], *, mode: int = stat.S_IRUSR
) -> None:
    secure_directory(path.parent)
    _reject_unsafe_file(path)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


RUN_IDENTITY_KEYS = (
    "run_id",
    "kind",
    "automation_id",
    "scope",
    "lease_run_id",
    "lease_owner_id",
    "lease_generation",
    "lease_token_digest",
    "lease_cwd",
    "lease_state",
)


def _owned_run_identity(
    run_id: str,
    kind: str,
    automation_id: str,
    scope: str,
    lease_run_id: str,
    owner_id: str,
    generation: int,
    context_token: str,
    cwd: Path | str,
) -> dict[str, Any]:
    validate_component(run_id, "run id")
    validate_component(kind, "run kind")
    validate_component(automation_id, "automation id")
    validate_scope(scope)
    validate_component(lease_run_id, "lease run id")
    validate_component(owner_id, "lease owner id")
    if generation < 1:
        raise ControlError("lease generation must be positive")
    return {
        "run_id": run_id,
        "kind": kind,
        "automation_id": automation_id,
        "scope": scope,
        "lease_run_id": lease_run_id,
        "lease_owner_id": owner_id,
        "lease_generation": generation,
        "lease_token_digest": run_token_digest(context_token),
        "lease_cwd": str(Path(cwd).expanduser().resolve()),
        "lease_state": "owned",
    }


def _validate_run_records(
    records: list[dict[str, Any]],
    label: str,
    *,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    verification = verify_chain(records, label)
    if not records:
        return verification
    first = records[0]
    if first.get("event_type") != "run_started":
        raise ControlError(f"{label} has an invalid first event")
    for key in RUN_IDENTITY_KEYS:
        if key not in first:
            raise ControlError(f"{label} is missing immutable fencing identity: {key}")
    for key in (
        "run_id",
        "kind",
        "automation_id",
        "scope",
        "lease_run_id",
        "lease_owner_id",
        "lease_token_digest",
        "lease_cwd",
        "lease_state",
    ):
        if not isinstance(first[key], str):
            raise ControlError(f"{label} has an invalid fencing identity field: {key}")
    validate_component(first["run_id"], "run id")
    if expected_run_id is not None and first["run_id"] != expected_run_id:
        raise ControlError(f"{label} run id does not match its ledger path")
    validate_component(first["kind"], "run kind")
    validate_component(first["automation_id"], "automation id")
    validate_scope(first["scope"])
    validate_component(first["lease_run_id"], "lease run id")
    validate_component(first["lease_owner_id"], "lease owner id")
    generation = first["lease_generation"]
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise ControlError(f"{label} has an invalid lease generation")
    if first["lease_state"] == "owned" and generation < 1:
        raise ControlError(f"{label} has an invalid owned lease generation")
    if first["lease_state"] == "overlap" and generation != 0:
        raise ControlError(f"{label} has an invalid overlap lease generation")
    if first["lease_state"] not in {"owned", "overlap"}:
        raise ControlError(f"{label} has an invalid lease state")
    if not re.fullmatch(r"[0-9a-f]{64}", str(first["lease_token_digest"])):
        raise ControlError(f"{label} has an invalid lease token digest")
    if first["lease_cwd"] != str(Path(str(first["lease_cwd"])).expanduser().resolve()):
        raise ControlError(f"{label} has an invalid lease cwd")
    expected = {key: first[key] for key in RUN_IDENTITY_KEYS}
    for sequence, record in enumerate(records, 1):
        actual = {key: record.get(key) for key in RUN_IDENTITY_KEYS}
        if actual != expected:
            raise ControlError(f"{label} fencing identity changed at event {sequence}")
    return verification


def _assert_run_fence(
    records: list[dict[str, Any]],
    label: str,
    run_id: str,
    lease_run_id: str,
    owner_id: str,
    generation: int,
    context_token: str,
) -> dict[str, Any]:
    _validate_run_records(records, label, expected_run_id=run_id)
    first = records[0]
    expected = (
        lease_run_id,
        owner_id,
        generation,
        run_token_digest(context_token),
        "owned",
    )
    actual = (
        first["lease_run_id"],
        first["lease_owner_id"],
        first["lease_generation"],
        first["lease_token_digest"],
        first["lease_state"],
    )
    if actual != expected:
        raise ControlError(f"{label} is fenced to a different lease identity")
    return first


def run_begin(
    root: Path,
    run_id: str,
    kind: str,
    automation_id: str,
    scope: str,
    metadata: dict[str, Any],
    *,
    lease_run_id: str,
    owner_id: str,
    generation: int,
    context_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_component(run_id, "run id")
    validate_component(kind, "run kind")
    validate_component(automation_id, "automation id")
    validate_scope(scope)
    with lease_guard(
        root,
        scope,
        lease_run_id,
        owner_id=owner_id,
        generation=generation,
        context_token=context_token,
        hold_seconds=60,
        now=now,
    ) as lease:
        identity = _owned_run_identity(
            run_id,
            kind,
            automation_id,
            scope,
            lease_run_id,
            owner_id,
            generation,
            context_token,
            str(lease["cwd"]),
        )
        path = run_path(root, run_id)
        with locked(path.with_suffix(".lock")):
            records = _read_jsonl(path)
            if records:
                _validate_run_records(records, f"run {run_id}", expected_run_id=run_id)
                existing = {key: records[0][key] for key in RUN_IDENTITY_KEYS}
                if existing != identity:
                    raise ControlError(f"run {run_id} already belongs to another lease identity")
                return {"state": "already-started", "run_id": run_id, "path": str(path)}
            event = _append_chained_event_unlocked(
                path,
                identity,
                "run_started",
                metadata,
                now=now or utc_now(),
                terminal_event="run_finished",
            )
            return {"state": "started", "path": str(path), "event": event}


def run_event(
    root: Path,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    lease_run_id: str,
    owner_id: str,
    generation: int,
    context_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_component(event_type, "event type")
    path = run_path(root, run_id)
    with locked(path.with_suffix(".lock")):
        initial_records = _read_jsonl(path)
        if not initial_records:
            raise ControlError(f"run {run_id} has not started")
        first = _assert_run_fence(
            initial_records,
            f"run {run_id}",
            run_id,
            lease_run_id,
            owner_id,
            generation,
            context_token,
        )
        scope = str(first["scope"])
    with lease_guard(
        root,
        scope,
        lease_run_id,
        owner_id=owner_id,
        generation=generation,
        context_token=context_token,
        hold_seconds=60,
        now=now,
    ):
        with locked(path.with_suffix(".lock")):
            records = _read_jsonl(path)
            if not records:
                raise ControlError(f"run {run_id} has not started")
            first = _assert_run_fence(
                records,
                f"run {run_id}",
                run_id,
                lease_run_id,
                owner_id,
                generation,
                context_token,
            )
            identity = {key: first[key] for key in RUN_IDENTITY_KEYS}
            event = _append_chained_event_unlocked(
                path,
                identity,
                event_type,
                payload,
                now=now or utc_now(),
                terminal_event="run_finished",
            )
            return {"state": "recorded", "path": str(path), "event": event}


def run_finish(
    root: Path,
    run_id: str,
    status_value: str,
    summary: dict[str, Any],
    *,
    lease_run_id: str,
    owner_id: str,
    generation: int,
    context_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_component(status_value, "run status")
    result = run_event(
        root,
        run_id,
        "run_finished",
        {"status": status_value, "summary": summary},
        lease_run_id=lease_run_id,
        owner_id=owner_id,
        generation=generation,
        context_token=context_token,
        now=now,
    )
    path = run_path(root, run_id)
    path.chmod(stat.S_IRUSR)
    result["state"] = "finished"
    return result


def run_overlap(
    root: Path,
    run_id: str,
    kind: str,
    automation_id: str,
    scope: str,
    metadata: dict[str, Any],
    *,
    lease_run_id: str,
    owner_id: str,
    cwd: Path | str,
    context_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically persist a finalized record for an invocation denied by a live lease."""
    now = now or utc_now()
    resolved_cwd = str(Path(cwd).expanduser().resolve())
    expected_owner = owner_id_for(lease_run_id, resolved_cwd, context_token)
    if not hmac.compare_digest(owner_id, expected_owner):
        raise ControlError("overlap context token does not derive the supplied owner id")
    identity = _owned_run_identity(
        run_id,
        kind,
        automation_id,
        scope,
        lease_run_id,
        owner_id,
        1,
        context_token,
        resolved_cwd,
    )
    identity["lease_generation"] = 0
    identity["lease_state"] = "overlap"
    lease_path, lease_lock = lease_paths(root, scope)
    with locked(lease_lock):
        active = read_json(lease_path)
        if not active:
            raise ControlError(f"no active {scope} lease blocks this invocation")
        _validate_lease_document(active, scope)
        if parse_time(active["expires_at"]) <= now:
            raise ControlError(f"expired {scope} lease does not establish an overlap")
        contender = (
            lease_run_id,
            owner_id,
            run_token_digest(context_token),
            resolved_cwd,
        )
        active_identity = (
            active["run_id"],
            active["owner_id"],
            active["token_digest"],
            active["cwd"],
        )
        if contender == active_identity:
            raise ControlError("the invocation already owns the active lease")
        path = run_path(root, run_id)
        with locked(path.with_suffix(".lock")):
            existing = _read_jsonl(path)
            if existing:
                _validate_run_records(
                    existing, f"run {run_id}", expected_run_id=run_id
                )
                current_identity = {key: existing[0][key] for key in RUN_IDENTITY_KEYS}
                if current_identity != identity or not verify_chain(
                    existing, f"run {run_id}"
                )["finalized"]:
                    raise ControlError(f"run {run_id} already has different records")
                return {"state": "already-recorded", "path": str(path)}
            busy = {
                "active_run_id": active["run_id"],
                "active_owner_id": active["owner_id"],
                "active_generation": active["generation"],
                "expires_at": active["expires_at"],
            }
            records: list[dict[str, Any]] = []
            records.append(_make_chained_event(records, identity, "run_started", metadata, now))
            records.append(_make_chained_event(records, identity, "lease_busy", busy, now))
            records.append(
                _make_chained_event(
                    records,
                    identity,
                    "run_finished",
                    {
                        "status": "skipped-overlap",
                        "summary": {"reason": "lease-busy", **busy},
                    },
                    now,
                )
            )
            _atomic_write_jsonl(path, records)
            return {"state": "finished", "path": str(path), "events": records}


def verify_run(root: Path, run_id: str) -> dict[str, Any]:
    path = run_path(root, run_id)
    records = _read_jsonl(path)
    if not records:
        raise ControlError(f"run {run_id} does not exist")
    return {
        "state": "valid",
        "path": str(path),
        **_validate_run_records(records, f"run {run_id}", expected_run_id=run_id),
    }


def recent_run_summaries(root: Path, kind: str, limit: int) -> list[dict[str, Any]]:
    if limit < 1 or limit > 100:
        raise ControlError("recent run limit must be between 1 and 100")
    validate_component(kind, "run kind")
    summaries: list[dict[str, Any]] = []
    for path in (root / "runs").glob("*.jsonl") if (root / "runs").exists() else []:
        try:
            records = _read_jsonl(path)
            _validate_run_records(records, str(path), expected_run_id=path.stem)
        except ControlError:
            continue
        if not records or records[0].get("kind") != kind:
            continue
        if records[-1].get("event_type") != "run_finished":
            continue
        summaries.append(
            {
                "run_id": records[0].get("run_id"),
                "finished_at": records[-1].get("timestamp"),
                **records[-1].get("payload", {}),
            }
        )
    summaries.sort(key=lambda item: str(item.get("finished_at", "")), reverse=True)
    return summaries[:limit]


def review_path(root: Path, repository: str, pull_number: int, head_sha: str) -> Path:
    if not REPO_RE.fullmatch(repository):
        raise ControlError(f"invalid repository: {repository!r}")
    if pull_number < 1:
        raise ControlError("pull request number must be positive")
    if not SHA_RE.fullmatch(head_sha):
        raise ControlError("review head must be a lowercase 40-character Git SHA")
    repo_component = repository.replace("/", "__")
    reviews = secure_directory(root / "reviews")
    repo_dir = secure_directory(reviews / repo_component)
    pr_dir = secure_directory(repo_dir / f"pr-{pull_number}")
    return pr_dir / f"{head_sha}.jsonl"


def review_record(
    root: Path,
    repository: str,
    pull_number: int,
    head_sha: str,
    status_value: str,
    details: dict[str, Any],
    retry_after_seconds: int | None,
    *,
    transition: str | None = None,
    run_id: str | None = None,
    cwd: Path | str | None = None,
    context_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_component(status_value, "review status")
    if status_value == "started":
        raise ControlError("use the atomic review claim operation to start an attempt")
    now = now or utc_now()
    if retry_after_seconds is not None and retry_after_seconds < 0:
        raise ControlError("review retry delay cannot be negative")
    path = review_path(root, repository, pull_number, head_sha)
    identity = {
        "repository": repository,
        "pull_number": pull_number,
        "head_sha": head_sha,
    }

    def append_checked(existing: list[dict[str, Any]]) -> dict[str, Any]:
        nonlocal retry_after_seconds, details
        details = dict(details)
        effective_status = status_value
        if status_value in RETRYABLE_REVIEW_STATES:
            failure_count = 1 + sum(
                1 for record in existing if record.get("event_type") in RETRYABLE_REVIEW_STATES
            )
            details = {**details, "failure_count": failure_count}
            if failure_count >= 3:
                effective_status = "needs-human"
                details = {**details, "original_status": status_value}
                retry_after_seconds = None
            elif retry_after_seconds is None:
                retry_after_seconds = (4 * 60 * 60, 12 * 60 * 60)[failure_count - 1]
        if retry_after_seconds is not None:
            details = {
                **details,
                "retry_after": isoformat(now + timedelta(seconds=retry_after_seconds)),
            }
        event = _append_chained_event_unlocked(
            path,
            identity,
            effective_status,
            details,
            now=now,
        )
        return {"state": "recorded", "path": str(path), "event": event}

    if status_value == "queued":
        if transition is not None or run_id is not None or context_token is not None:
            raise ControlError("queued must be an unclaimed first review event")
        with locked(path.with_suffix(".lock")):
            existing = _read_jsonl(path)
            verify_chain(existing, str(path))
            latest = existing[-1] if existing else None
            if latest and latest.get("event_type") == "queued":
                return {"state": "already-recorded", "path": str(path), "event": latest}
            if latest:
                raise ControlError("queued is valid only as the first exact-head review event")
            return append_checked(existing)

    if transition not in {"active-claim", "active-context"}:
        raise ControlError(
            f"review status {status_value} requires an active claim or context"
        )
    if not run_id or cwd is None or context_token is None:
        raise ControlError("active review transitions require run id, cwd, and context token")
    context = context_read(
        root,
        context_token,
        run_id=run_id,
        scope="review",
        cwd=cwd,
    )["context"]

    if transition == "active-context":
        allowed_source = status_value == "posted" and details.get("source") in {
            "github-state-migration",
            "github-marker-reconciliation",
        }
        if not allowed_source:
            raise ControlError(
                "active-context is limited to fenced GitHub marker reconciliation"
            )

    with lease_guard(
        root,
        "review",
        run_id,
        owner_id=str(context["owner_id"]),
        generation=int(context["generation"]),
        context_token=context_token,
        hold_seconds=60,
        now=now,
    ):
        with locked(path.with_suffix(".lock")):
            existing = _read_jsonl(path)
            verify_chain(existing, str(path))
            latest = existing[-1] if existing else None
            if transition == "active-context":
                if latest and latest.get("event_type") == "posted":
                    return {
                        "state": "already-recorded",
                        "path": str(path),
                        "event": latest,
                    }
                details = {
                    **details,
                    "run_id": run_id,
                    "owner_id": context["owner_id"],
                    "lease_generation": context["generation"],
                    "context_token_digest": context["token_digest"],
                    "cwd": context["cwd"],
                }
                return append_checked(existing)

            if not latest or latest.get("event_type") not in {"started", "posted"}:
                raise ControlError("the exact head has no active review claim")
            claim_id = latest.get("payload", {}).get("claim_id")
            if not isinstance(claim_id, str):
                raise ControlError("the exact head's active review claim id is missing")
            claim = next(
                (
                    record
                    for record in reversed(existing)
                    if record.get("event_type") == "started"
                    and record.get("payload", {}).get("claim_id") == claim_id
                ),
                None,
            )
            if claim is None:
                raise ControlError("the exact head's active review claim has no start event")
            claim_payload = claim.get("payload", {})
            expected_claim_fence = (
                run_id,
                context["owner_id"],
                context["generation"],
                context["token_digest"],
                context["cwd"],
            )
            actual_claim_fence = (
                claim_payload.get("run_id"),
                claim_payload.get("owner_id"),
                claim_payload.get("lease_generation"),
                claim_payload.get("context_token_digest"),
                claim_payload.get("cwd"),
            )
            if actual_claim_fence != expected_claim_fence:
                raise ControlError("the exact head's active claim belongs to another invocation")
            if latest.get("event_type") == "posted" and status_value != "remediated":
                raise ControlError(
                    "only remediation may follow a posted review in the same claim"
                )
            details = {**details, "claim_id": claim_id}
            return append_checked(existing)


def _review_eligibility_from_records(
    records: list[dict[str, Any]],
    path: Path,
    now: datetime,
    started_ttl_seconds: int,
    marker_present: bool,
    unresolved_feedback: bool,
    repair_needed: bool,
) -> tuple[bool, dict[str, Any]]:
    latest = records[-1] if records else None
    if latest and latest.get("event_type") == "started":
        started_at = parse_time(str(latest["timestamp"]))
        if started_at + timedelta(seconds=started_ttl_seconds) > now:
            return False, {
                "state": "not-eligible",
                "reason": "review-attempt-active",
                "latest": latest,
            }
    if (unresolved_feedback or repair_needed) and (
        not latest
        or latest.get("event_type") not in {"needs-human", "superseded", "remediated"}
    ):
        return True, {
            "state": "eligible",
            "reason": "unresolved-feedback" if unresolved_feedback else "repair-needed",
            "marker_present": marker_present,
            "latest": latest,
        }
    if marker_present:
        return False, {
            "state": "not-eligible",
            "reason": "github-marker-present",
            "latest": latest,
        }
    if not records:
        return True, {"state": "eligible", "reason": "unseen-head", "path": str(path)}
    assert latest is not None
    status_value = str(latest["event_type"])
    if status_value == "posted":
        return True, {
            "state": "eligible",
            "reason": "local-posted-state-without-github-marker",
            "latest": latest,
        }
    if status_value in TERMINAL_REVIEW_STATES:
        return False, {
            "state": "not-eligible",
            "reason": f"head-already-{status_value}",
            "latest": latest,
        }
    if status_value == "started":
        return True, {"state": "eligible", "reason": "stale-started-attempt", "latest": latest}
    if status_value in RETRYABLE_REVIEW_STATES:
        retry_after = latest.get("payload", {}).get("retry_after")
        if retry_after and parse_time(str(retry_after)) > now:
            return False, {
                "state": "not-eligible",
                "reason": "retry-backoff",
                "retry_after": retry_after,
                "latest": latest,
            }
    return True, {"state": "eligible", "reason": f"latest-{status_value}", "latest": latest}


def review_claim(
    root: Path,
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: str,
    cwd: Path | str,
    context_token: str,
    *,
    marker_present: bool = False,
    unresolved_feedback: bool = False,
    repair_needed: bool = False,
    started_ttl_seconds: int = 2 * 60 * 60,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any]]:
    now = now or utc_now()
    context = context_read(
        root,
        context_token,
        run_id=run_id,
        scope="review",
        cwd=cwd,
    )["context"]
    path = review_path(root, repository, pull_number, head_sha)
    identity = {
        "repository": repository,
        "pull_number": pull_number,
        "head_sha": head_sha,
    }
    with lease_guard(
        root,
        "review",
        run_id,
        owner_id=str(context["owner_id"]),
        generation=int(context["generation"]),
        context_token=context_token,
        hold_seconds=60,
        now=now,
    ) as lease:
        with locked(path.with_suffix(".lock")):
            records = _read_jsonl(path)
            verify_chain(records, str(path))
            eligible, result = _review_eligibility_from_records(
                records,
                path,
                now,
                started_ttl_seconds,
                marker_present,
                unresolved_feedback,
                repair_needed,
            )
            if not eligible:
                return False, result
            claim_id = uuid.uuid4().hex
            event = _append_chained_event_unlocked(
                path,
                identity,
                "started",
                {
                    "claim_id": claim_id,
                    "run_id": run_id,
                    "owner_id": context["owner_id"],
                    "lease_generation": lease["generation"],
                    "context_token_digest": context["token_digest"],
                    "cwd": context["cwd"],
                    "eligibility_reason": result["reason"],
                },
                now=now,
            )
            return True, {
                "state": "claimed",
                "claim_id": claim_id,
                "path": str(path),
                "event": event,
            }


def review_eligibility(
    root: Path,
    repository: str,
    pull_number: int,
    head_sha: str,
    *,
    now: datetime | None = None,
    started_ttl_seconds: int = 2 * 60 * 60,
    marker_present: bool = False,
    unresolved_feedback: bool = False,
    repair_needed: bool = False,
) -> tuple[bool, dict[str, Any]]:
    now = now or utc_now()
    path = review_path(root, repository, pull_number, head_sha)
    records = _read_jsonl(path)
    verify_chain(records, str(path))
    return _review_eligibility_from_records(
        records,
        path,
        now,
        started_ttl_seconds,
        marker_present,
        unresolved_feedback,
        repair_needed,
    )


def active_review_claim(
    root: Path,
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: str,
    cwd: Path | str,
    context_token: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    context = context_read(
        root,
        context_token,
        run_id=run_id,
        scope="review",
        cwd=cwd,
    )["context"]
    lease_assert(
        root,
        "review",
        run_id,
        owner_id=str(context["owner_id"]),
        generation=int(context["generation"]),
        context_token=context_token,
        now=now,
    )
    path = review_path(root, repository, pull_number, head_sha)
    with locked(path.with_suffix(".lock")):
        records = _read_jsonl(path)
        verify_chain(records, str(path))
        latest = records[-1] if records else None
        if not latest or latest.get("event_type") not in {"started", "posted"}:
            raise ControlError("the exact head has no active review claim")
        claim_id = latest.get("payload", {}).get("claim_id")
        if not isinstance(claim_id, str):
            raise ControlError("the exact head's active review claim id is missing")
        started = next(
            (
                record
                for record in reversed(records)
                if record.get("event_type") == "started"
                and record.get("payload", {}).get("claim_id") == claim_id
            ),
            None,
        )
        if not started:
            raise ControlError("the exact head's review claim has no started event")
        payload = started.get("payload", {})
        expected = (
            run_id,
            context["owner_id"],
            context["generation"],
            context["token_digest"],
            context["cwd"],
        )
        actual = (
            payload.get("run_id"),
            payload.get("owner_id"),
            payload.get("lease_generation"),
            payload.get("context_token_digest"),
            payload.get("cwd"),
        )
        if actual != expected:
            raise ControlError("the exact head's active claim belongs to another invocation")
        return {"state": "ok", "claim_id": claim_id, "context": context}


def _validate_signal(signal: Any) -> tuple[str, str, str, str]:
    if not isinstance(signal, dict):
        raise ControlError("architecture signal must be an object")
    kind = signal.get("kind")
    evidence = signal.get("evidence")
    source = signal.get("source")
    conclusion = signal.get("conclusion", "")
    if (
        not isinstance(kind, str)
        or not isinstance(evidence, str)
        or not evidence.strip()
        or not isinstance(source, str)
        or not source.strip()
        or not isinstance(conclusion, str)
    ):
        raise ControlError("architecture signal requires kind, source, and non-empty evidence")
    if not any(pattern.fullmatch(source) for pattern in SIGNAL_SOURCE_RES):
        raise ControlError(
            "architecture signal source must be a canonical anchor-free repo:, linear:, "
            "openspec:, decision:, or owner: artifact identifier; put line or section "
            "detail in optional location"
        )
    scheme, identifier = source.split(":", 1)
    if scheme in {"repo", "openspec"}:
        normalized_identifier = posixpath.normpath(identifier)
        if (
            identifier.startswith("/")
            or normalized_identifier in {".", ".."}
            or normalized_identifier.startswith("../")
            or identifier != normalized_identifier
        ):
            raise ControlError(
                "architecture signal source must use one normalized relative artifact id"
            )
    return kind, evidence, source, conclusion


def _signal_source_identity(source: str) -> str:
    """Return the already validated, canonical artifact identity."""
    return source


def architecture_gate(candidate: dict[str, Any]) -> tuple[bool, list[str]]:
    if "architecture_resurrection" not in candidate:
        raise ControlError("every candidate requires an architecture_resurrection assessment")
    resurrection = candidate["architecture_resurrection"]
    if not isinstance(resurrection, dict):
        raise ControlError("architecture_resurrection must be an object")
    suspected = resurrection.get("suspected")
    if not isinstance(suspected, bool):
        raise ControlError("architecture_resurrection.suspected must be a boolean")
    rationale = resurrection.get("classification_rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ControlError(
            "architecture_resurrection requires a non-empty classification_rationale"
        )
    if not suspected:
        return True, []

    reasons: list[str] = []
    absence = resurrection.get("absence_explained")
    if not isinstance(absence, str) or not absence.strip():
        reasons.append("the path's current absence or dormancy is not explained")
    if resurrection.get("absence_status") != "accidental-regression":
        reasons.append(
            "absence_status does not establish an accidental regression; retirement, "
            "migration, and ambiguity require a human decision"
        )
    canonical_fit = resurrection.get("canonical_fit")
    if not isinstance(canonical_fit, str) or not canonical_fit.strip():
        reasons.append("canonical ownership and competing-path fit are not established")
    if resurrection.get("canonical_fit_status") != "fits-current-canonical-owner":
        reasons.append(
            "canonical_fit_status does not establish fit with the current canonical owner"
        )
    signals = resurrection.get("signals", [])
    if not isinstance(signals, list):
        raise ControlError("architecture resurrection signals must be a list")
    validated_signals = [_validate_signal(signal) for signal in signals]
    runtime_sources = {
        _signal_source_identity(source)
        for kind, _, source, conclusion in validated_signals
        if kind in RUNTIME_SIGNAL_KINDS and conclusion == "supports-current-path"
    }
    authority_sources = {
        _signal_source_identity(source)
        for kind, _, source, conclusion in validated_signals
        if kind in AUTHORITY_SIGNAL_KINDS and conclusion == "supports-intended-owner"
    }
    if not runtime_sources:
        reasons.append("no current executable/runtime signal establishes a supported path")
    if not authority_sources:
        reasons.append("no current authority signal establishes intended ownership")
    if not any(runtime != authority for runtime in runtime_sources for authority in authority_sources):
        reasons.append("runtime and authority evidence do not come from independent sources")
    return not reasons, reasons


def score_candidates(root: Path, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    recent = recent_run_summaries(root, "delivery", 100)
    recent_families = [
        family
        for summary in recent
        if isinstance(summary.get("summary"), dict)
        for family in [summary["summary"].get("selected_family")]
        if isinstance(family, str) and family.strip()
    ][:3]
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ControlError("each selection candidate must be an object")
        candidate_id = candidate.get("id")
        title = candidate.get("title")
        family = candidate.get("family")
        if not all(isinstance(value, str) and value.strip() for value in (candidate_id, title, family)):
            raise ControlError("each candidate requires non-empty id, title, and family")
        assert isinstance(family, str)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", family):
            raise ControlError(
                f"candidate {candidate_id} family must be lowercase kebab-case"
            )
        evidence = candidate.get("evidence")
        oracle = candidate.get("success_oracle")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise ControlError(f"candidate {candidate_id} requires concrete evidence")
        if not isinstance(oracle, str) or not oracle.strip():
            raise ControlError(f"candidate {candidate_id} requires a success oracle")

        impact_class = candidate.get("impact_class")
        impact_rationale = candidate.get("impact_rationale")
        if not isinstance(impact_class, str) or impact_class not in IMPACT_CLASS_VALUES:
            raise ControlError(
                f"candidate {candidate_id} impact_class must be one of: "
                + ", ".join(IMPACT_CLASS_VALUES)
            )
        if not isinstance(impact_rationale, str) or not impact_rationale.strip():
            raise ControlError(f"candidate {candidate_id} requires an impact_rationale")
        if "value" in candidate:
            supplied_value = candidate["value"]
            if (
                not isinstance(supplied_value, int)
                or isinstance(supplied_value, bool)
                or supplied_value != IMPACT_CLASS_VALUES[impact_class]
            ):
                raise ControlError(
                    f"candidate {candidate_id} value conflicts with its mechanical impact_class"
                )

        dimensions: dict[str, int] = {"value": IMPACT_CLASS_VALUES[impact_class]}
        for name, maximum in (("leverage", 3), ("readiness", 3), ("urgency", 2)):
            raw = candidate.get(name)
            if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw <= maximum:
                raise ControlError(f"candidate {candidate_id} {name} must be an integer 0-{maximum}")
            dimensions[name] = raw

        adjustments: list[str] = []
        if impact_class in {"local-maintenance", "cosmetic"} and dimensions["leverage"] > 1:
            dimensions["leverage"] = 1
            adjustments.append("low-impact leverage capped at 1")

        linear_priority = candidate.get("linear_priority", 0)
        scope_size = candidate.get("scope_size", 40)
        created_at = candidate.get("created_at", "9999-12-31T23:59:59Z")
        if (
            not isinstance(linear_priority, int)
            or isinstance(linear_priority, bool)
            or not 0 <= linear_priority <= 4
        ):
            raise ControlError(f"candidate {candidate_id} linear_priority must be 0-4")
        if (
            not isinstance(scope_size, int)
            or isinstance(scope_size, bool)
            or not 1 <= scope_size <= 40
        ):
            raise ControlError(f"candidate {candidate_id} scope_size must be 1-40")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ControlError(f"candidate {candidate_id} created_at must be a non-empty ISO time")
        try:
            created_at = isoformat(parse_time(created_at))
        except (TypeError, ValueError) as exc:
            raise ControlError(
                f"candidate {candidate_id} created_at must be an ISO-8601 time"
            ) from exc

        gate_ok, gate_reasons = architecture_gate(candidate)
        ineligible_reasons = list(gate_reasons)
        if candidate.get("duplicate", False):
            ineligible_reasons.append("an open issue or pull request already owns this problem")
        if candidate.get("blocked", False):
            ineligible_reasons.append("required evidence or a human decision is unavailable")
        repeat_count = sum(1 for recent_family in recent_families if recent_family == family)
        repeat_penalty = min(repeat_count, 2)
        total = sum(dimensions.values()) - repeat_penalty
        scored.append(
            {
                "id": candidate_id,
                "title": title,
                "family": family,
                "impact_class": impact_class,
                "impact_rationale": impact_rationale,
                "eligible": not ineligible_reasons and gate_ok,
                "score": total,
                "dimensions": dimensions,
                "adjustments": adjustments,
                "repeat_penalty": repeat_penalty,
                "linear_priority": linear_priority,
                "created_at": created_at,
                "scope_size": scope_size,
                "ineligible_reasons": ineligible_reasons,
                "evidence": evidence,
                "success_oracle": oracle,
            }
        )

    scored.sort(
        key=lambda item: (
            not item["eligible"],
            -item["dimensions"]["value"],
            -item["score"],
            -item["dimensions"]["leverage"],
            -item["dimensions"]["readiness"],
            {1: 0, 2: 1, 3: 2, 4: 3, 0: 4}[item["linear_priority"]],
            item["created_at"],
            item["scope_size"],
            item["id"],
        )
    )
    selected = next((item for item in scored if item["eligible"]), None)
    return {
        "state": "selected" if selected else "no-eligible-candidate",
        "recent_families": recent_families,
        "selected": selected,
        "ranking": scored,
    }


def add_state_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_state_dir(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    owner = commands.add_parser("owner-id")
    owner.add_argument("--run-id", required=True)
    owner.add_argument("--cwd", required=True, type=Path)
    owner.add_argument("--nonce")

    context = commands.add_parser("context")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    context_create_parser = context_commands.add_parser("create")
    context_create_parser.add_argument("--token", required=True)
    context_create_parser.add_argument("--run-id", required=True)
    context_create_parser.add_argument("--owner-id", required=True)
    context_create_parser.add_argument("--generation", type=int, required=True)
    context_create_parser.add_argument("--ledger-run-id", required=True)
    context_create_parser.add_argument("--scope", required=True)
    context_create_parser.add_argument("--cwd", required=True, type=Path)
    context_get_parser = context_commands.add_parser("get")
    context_get_parser.add_argument("--token", required=True)
    context_get_parser.add_argument("--run-id")
    context_get_parser.add_argument("--scope")
    context_get_parser.add_argument("--cwd", type=Path)
    context_delete_parser = context_commands.add_parser("delete")
    context_delete_parser.add_argument("--token", required=True)
    lease = commands.add_parser("lease")
    lease_commands = lease.add_subparsers(dest="lease_command", required=True)
    acquire = lease_commands.add_parser("acquire")
    acquire.add_argument("--scope", required=True)
    acquire.add_argument("--run-id", required=True)
    acquire.add_argument("--ttl-seconds", type=int, default=DEFAULT_LEASE_TTL_SECONDS)
    acquire.add_argument("--metadata-json")
    acquire.add_argument("--owner-id", required=True)
    acquire.add_argument("--cwd", required=True, type=Path)
    acquire.add_argument("--context-token", required=True)
    assertion = lease_commands.add_parser("assert")
    assertion.add_argument("--scope", required=True)
    assertion.add_argument("--run-id", required=True)
    assertion.add_argument("--owner-id", required=True)
    assertion.add_argument("--generation", type=int, required=True)
    assertion.add_argument("--context-token", required=True)
    heartbeat = lease_commands.add_parser("heartbeat")
    heartbeat.add_argument("--scope", required=True)
    heartbeat.add_argument("--run-id", required=True)
    heartbeat.add_argument("--ttl-seconds", type=int)
    heartbeat.add_argument("--owner-id", required=True)
    heartbeat.add_argument("--generation", type=int, required=True)
    heartbeat.add_argument("--context-token", required=True)
    release = lease_commands.add_parser("release")
    release.add_argument("--scope", required=True)
    release.add_argument("--run-id", required=True)
    release.add_argument("--owner-id", required=True)
    release.add_argument("--generation", type=int, required=True)
    release.add_argument("--context-token", required=True)
    recover = lease_commands.add_parser("recover")
    recover.add_argument("--scope", required=True)

    run = commands.add_parser("run")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    begin = run_commands.add_parser("begin")
    begin.add_argument("--run-id", required=True)
    begin.add_argument("--kind", required=True)
    begin.add_argument("--automation-id", required=True)
    begin.add_argument("--scope", required=True)
    begin.add_argument("--metadata-json")
    begin.add_argument("--lease-run-id", required=True)
    begin.add_argument("--owner-id", required=True)
    begin.add_argument("--generation", type=int, required=True)
    begin.add_argument("--context-token", required=True)
    event = run_commands.add_parser("event")
    event.add_argument("--run-id", required=True)
    event.add_argument("--event-type", required=True)
    event.add_argument("--payload-json")
    event.add_argument("--lease-run-id", required=True)
    event.add_argument("--owner-id", required=True)
    event.add_argument("--generation", type=int, required=True)
    event.add_argument("--context-token", required=True)
    finish = run_commands.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", required=True)
    finish.add_argument("--summary-json")
    finish.add_argument("--lease-run-id", required=True)
    finish.add_argument("--owner-id", required=True)
    finish.add_argument("--generation", type=int, required=True)
    finish.add_argument("--context-token", required=True)
    overlap = run_commands.add_parser("overlap")
    overlap.add_argument("--run-id", required=True)
    overlap.add_argument("--kind", required=True)
    overlap.add_argument("--automation-id", required=True)
    overlap.add_argument("--scope", required=True)
    overlap.add_argument("--metadata-json")
    overlap.add_argument("--lease-run-id", required=True)
    overlap.add_argument("--owner-id", required=True)
    overlap.add_argument("--cwd", required=True, type=Path)
    overlap.add_argument("--context-token", required=True)
    verify = run_commands.add_parser("verify")
    verify.add_argument("--run-id", required=True)
    recent = run_commands.add_parser("recent")
    recent.add_argument("--kind", required=True)
    recent.add_argument("--limit", type=int, default=3)

    review = commands.add_parser("review")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    record = review_commands.add_parser("record")
    record.add_argument("--repo", required=True)
    record.add_argument("--pr", required=True, type=int)
    record.add_argument("--head", required=True)
    record.add_argument(
        "--status",
        required=True,
        choices=(
            "queued",
            "posted",
            "remediated",
            "timeout",
            "failed",
            "superseded",
            "needs-human",
        ),
    )
    record.add_argument("--details-json")
    record.add_argument("--retry-after-seconds", type=int)
    transition = record.add_mutually_exclusive_group()
    transition.add_argument("--active-claim", action="store_true")
    transition.add_argument("--active-context", action="store_true")
    record.add_argument("--run-id")
    record.add_argument("--cwd", type=Path, default=Path.cwd())
    record.add_argument("--context-token")
    claim = review_commands.add_parser("claim")
    claim.add_argument("--repo", required=True)
    claim.add_argument("--pr", required=True, type=int)
    claim.add_argument("--head", required=True)
    claim.add_argument("--run-id", required=True)
    claim.add_argument("--context-token", required=True)
    claim.add_argument("--cwd", type=Path, default=Path.cwd())
    claim.add_argument("--started-ttl-seconds", type=int, default=2 * 60 * 60)
    claim.add_argument("--marker-present", action="store_true")
    claim.add_argument("--unresolved-feedback", action="store_true")
    claim.add_argument("--repair-needed", action="store_true")
    eligible = review_commands.add_parser("eligible")
    eligible.add_argument("--repo", required=True)
    eligible.add_argument("--pr", required=True, type=int)
    eligible.add_argument("--head", required=True)
    eligible.add_argument("--started-ttl-seconds", type=int, default=2 * 60 * 60)
    eligible.add_argument("--marker-present", action="store_true")
    eligible.add_argument("--unresolved-feedback", action="store_true")
    eligible.add_argument("--repair-needed", action="store_true")

    score = commands.add_parser("score")
    score.add_argument("--input", required=True, type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        root = state_root(args.state_dir)
        result: dict[str, Any]
        if args.command == "owner-id":
            result = {
                "state": "ok",
                "owner_id": owner_id_for(args.run_id, args.cwd, args.nonce),
            }
        elif args.command == "context":
            if args.context_command == "create":
                result = context_create(
                    root,
                    args.token,
                    args.run_id,
                    args.owner_id,
                    args.generation,
                    args.ledger_run_id,
                    args.scope,
                    args.cwd,
                )
            elif args.context_command == "get":
                result = context_read(
                    root,
                    args.token,
                    run_id=args.run_id,
                    scope=args.scope,
                    cwd=args.cwd,
                )
            elif args.context_command == "delete":
                result = context_delete(root, args.token)
            else:
                raise ControlError(f"unknown context command: {args.context_command}")
        elif args.command == "lease":
            if args.lease_command == "acquire":
                owned, result = lease_acquire(
                    root,
                    args.scope,
                    args.run_id,
                    args.ttl_seconds,
                    parse_json_object(args.metadata_json, "metadata-json"),
                    owner_id=args.owner_id,
                    cwd=args.cwd,
                    context_token=args.context_token,
                )
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0 if owned else EXIT_BUSY
            if args.lease_command == "assert":
                result = lease_assert(
                    root,
                    args.scope,
                    args.run_id,
                    owner_id=args.owner_id,
                    generation=args.generation,
                    context_token=args.context_token,
                )
            elif args.lease_command == "heartbeat":
                result = lease_heartbeat(
                    root,
                    args.scope,
                    args.run_id,
                    args.ttl_seconds,
                    owner_id=args.owner_id,
                    generation=args.generation,
                    context_token=args.context_token,
                )
            elif args.lease_command == "release":
                result = lease_release(
                    root,
                    args.scope,
                    args.run_id,
                    owner_id=args.owner_id,
                    generation=args.generation,
                    context_token=args.context_token,
                )
            else:
                result = lease_recover(root, args.scope)
        elif args.command == "run":
            if args.run_command == "begin":
                result = run_begin(
                    root,
                    args.run_id,
                    args.kind,
                    args.automation_id,
                    args.scope,
                    parse_json_object(args.metadata_json, "metadata-json"),
                    lease_run_id=args.lease_run_id,
                    owner_id=args.owner_id,
                    generation=args.generation,
                    context_token=args.context_token,
                )
            elif args.run_command == "event":
                result = run_event(
                    root,
                    args.run_id,
                    args.event_type,
                    parse_json_object(args.payload_json, "payload-json"),
                    lease_run_id=args.lease_run_id,
                    owner_id=args.owner_id,
                    generation=args.generation,
                    context_token=args.context_token,
                )
            elif args.run_command == "finish":
                result = run_finish(
                    root,
                    args.run_id,
                    args.status,
                    parse_json_object(args.summary_json, "summary-json"),
                    lease_run_id=args.lease_run_id,
                    owner_id=args.owner_id,
                    generation=args.generation,
                    context_token=args.context_token,
                )
            elif args.run_command == "overlap":
                result = run_overlap(
                    root,
                    args.run_id,
                    args.kind,
                    args.automation_id,
                    args.scope,
                    parse_json_object(args.metadata_json, "metadata-json"),
                    lease_run_id=args.lease_run_id,
                    owner_id=args.owner_id,
                    cwd=args.cwd,
                    context_token=args.context_token,
                )
            elif args.run_command == "verify":
                result = verify_run(root, args.run_id)
            else:
                result = {"state": "ok", "runs": recent_run_summaries(root, args.kind, args.limit)}
        elif args.command == "review":
            if args.review_command == "record":
                details = parse_json_object(args.details_json, "details-json")
                transition_name = (
                    "active-claim"
                    if args.active_claim
                    else "active-context" if args.active_context else None
                )
                active_run_id: str | None = None
                if transition_name:
                    active_run_id = args.run_id or os.environ.get("CODEX_THREAD_ID", "")
                    if not active_run_id:
                        raise ControlError(
                            "active review transition requires run-id or CODEX_THREAD_ID"
                        )
                result = review_record(
                    root,
                    args.repo,
                    args.pr,
                    args.head,
                    args.status,
                    details,
                    args.retry_after_seconds,
                    transition=transition_name,
                    run_id=active_run_id,
                    cwd=args.cwd if transition_name else None,
                    context_token=args.context_token,
                )
            elif args.review_command == "claim":
                claimed, result = review_claim(
                    root,
                    args.repo,
                    args.pr,
                    args.head,
                    args.run_id,
                    args.cwd,
                    args.context_token,
                    marker_present=args.marker_present,
                    unresolved_feedback=args.unresolved_feedback,
                    repair_needed=args.repair_needed,
                    started_ttl_seconds=args.started_ttl_seconds,
                )
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0 if claimed else EXIT_NOT_ELIGIBLE
            else:
                eligible, result = review_eligibility(
                    root,
                    args.repo,
                    args.pr,
                    args.head,
                    started_ttl_seconds=args.started_ttl_seconds,
                    marker_present=args.marker_present,
                    unresolved_feedback=args.unresolved_feedback,
                    repair_needed=args.repair_needed,
                )
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0 if eligible else EXIT_NOT_ELIGIBLE
        else:
            try:
                candidates = json.loads(args.input.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ControlError(f"cannot read candidate input: {exc}") from exc
            if not isinstance(candidates, list):
                raise ControlError("candidate input must be a JSON array")
            result = score_candidates(root, candidates)

        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ControlError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"state": "error", "reason": str(exc)}, indent=2), file=sys.stderr)
        if args.command == "lease" and args.lease_command in {
            "assert",
            "heartbeat",
            "release",
        }:
            return EXIT_NOT_OWNER
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
