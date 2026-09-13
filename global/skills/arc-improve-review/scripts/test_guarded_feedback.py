from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import guarded_feedback as subject


HEAD = "a" * 40
NEW_HEAD = "b" * 40
TOKEN = "c" * 32
THREAD = "PRRT_test123"
BRANCH = "codex/arc-improve/example"


def pr(head: str = HEAD, *, branch: str = "codex/arc-improve/example") -> subject.PullRequest:
    return subject.PullRequest(
        repository=subject.ALLOWED_REPOSITORY,
        number=17,
        state="open",
        head_repository=subject.ALLOWED_REPOSITORY,
        head_branch=branch,
        head_sha=head,
        base_repository=subject.ALLOWED_REPOSITORY,
        base_branch="main",
    )


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(
        self, command: list[str], *, timeout: float, input_text: str | None = None
    ) -> subject.CommandResult:
        del timeout
        self.calls.append((command, input_text))
        joined = " ".join(command)
        if "/pulls/comments/73" in joined:
            return result(
                {
                    "id": 73,
                    "pull_request_url": (
                        "https://api.github.com/repos/arc-edge/arc-uas/pulls/17"
                    ),
                    "in_reply_to_id": None,
                }
            )
        if "query($id:" in joined:
            return result(
                {
                    "data": {
                        "node": {
                            "id": THREAD,
                            "isResolved": False,
                            "pullRequest": {
                                "number": 17,
                                "repository": {"nameWithOwner": "arc-edge/arc-uas"},
                            },
                            "comments": {"nodes": [{"databaseId": 73}]},
                        }
                    }
                }
            )
        if "/replies" in joined:
            return result({"id": 99, "in_reply_to_id": 73})
        if "mutation($threadId:" in joined:
            return result(
                {
                    "data": {
                        "resolveReviewThread": {
                            "thread": {"id": THREAD, "isResolved": True}
                        }
                    }
                }
            )
        raise AssertionError(f"unexpected command: {command}")


def result(payload: dict[str, Any]) -> subject.CommandResult:
    return subject.CommandResult(0, json.dumps(payload), "")


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", "-c", "submodule.recurse=false", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def make_linear_transition_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "worktree"
    repo.mkdir()
    git(repo, "init", "-b", BRANCH)
    git(repo, "config", "user.name", "ARC Improve Test")
    git(repo, "config", "user.email", "arc-improve@example.invalid")
    git(repo, "remote", "add", "origin", "https://github.com/arc-edge/arc-uas.git")
    git(repo, "commit", "--allow-empty", "-m", "test: claimed head")
    old_head = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "fix: remediation")
    new_head = git(repo, "rev-parse", "HEAD")
    return repo, old_head, new_head


def commit_tree(
    repo: Path,
    tree: str,
    *parents: str,
) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "ARC Improve Test",
        "GIT_AUTHOR_EMAIL": "arc-improve@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_NAME": "ARC Improve Test",
        "GIT_COMMITTER_EMAIL": "arc-improve@example.invalid",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    }
    args: list[str] = ["commit-tree", tree]
    for parent in parents:
        args.extend(["-p", parent])
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        input="test commit\n",
        env=environment,
    )
    return completed.stdout.strip()


def dependencies(tmp_path: Path, heads: list[str]):
    events: list[tuple[Any, ...]] = []
    head_values = iter(heads)

    def context_reader(root, token, *, run_id, scope, cwd):
        events.append(("context", root, token, run_id, scope, cwd))
        if run_id != "run-1":
            raise subject.FeedbackError("wrong thread")
        return {
            "state": "ok",
            "context": {"run_id": "run-1", "owner_id": "owner-1", "generation": 4},
        }

    def claim_reader(root, repository, number, head, **kwargs):
        events.append(("claim", root, repository, number, head, kwargs))
        return {
            "state": "ok",
            "claim_id": "claim-1",
            "context": {"run_id": "run-1", "owner_id": "owner-1", "generation": 4},
        }

    @contextmanager
    def lease_factory(
        root,
        scope,
        run_id,
        *,
        owner_id,
        generation,
        context_token,
        hold_seconds,
    ):
        events.append(
            (
                "lease-enter",
                root,
                scope,
                run_id,
                owner_id,
                generation,
                context_token,
                hold_seconds,
            )
        )
        try:
            yield {"state": "ok"}
        finally:
            events.append(("lease-exit",))

    def fetcher(repository, number):
        value = next(head_values)
        events.append(("head", repository, number, value))
        return pr(value)

    return {
        "repo": tmp_path,
        "context_reader": context_reader,
        "claim_reader": claim_reader,
        "lease_factory": lease_factory,
        "state_root_factory": lambda: tmp_path / "state",
        "pr_fetcher": fetcher,
    }, events


def invoke(tmp_path: Path, runner: FakeRunner, heads: list[str], **overrides):
    deps, events = dependencies(tmp_path, heads)
    arguments = {
        "repository": subject.ALLOWED_REPOSITORY,
        "pr_number": 17,
        "claim_head": HEAD,
        "expected_current_head": HEAD,
        "comment_id": 73,
        "body": "Addressed in the current head.",
        "run_context_token": TOKEN,
        "resolve_thread_id": None,
        "runner": runner,
        **deps,
        **overrides,
    }
    return subject.guarded_feedback(**arguments), events


def test_reply_is_claimed_leased_and_exact_head_fenced(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    runner = FakeRunner()

    outcome, events = invoke(tmp_path, runner, [HEAD, HEAD])

    assert outcome["state"] == "ok"
    assert outcome["reply_id"] == 99
    assert outcome["thread_resolution"] == "not-requested"
    assert [event[0] for event in events] == [
        "context",
        "claim",
        "lease-enter",
        "head",
        "head",
        "lease-exit",
    ]
    assert events[1][1:5] == (
        tmp_path / "state",
        subject.ALLOWED_REPOSITORY,
        17,
        HEAD,
    )
    assert events[1][5] == {
        "run_id": "run-1",
        "cwd": tmp_path,
        "context_token": TOKEN,
    }
    assert events[2] == (
        "lease-enter",
        tmp_path / "state",
        "review",
        "run-1",
        "owner-1",
        4,
        TOKEN,
        subject.LEASE_HOLD_SECONDS,
    )
    write = next(call for call in runner.calls if "/replies" in " ".join(call[0]))
    assert write[0][:6] == ["gh", "api", "--hostname", "github.com", "-X", "POST"]
    assert json.loads(write[1] or "") == {"body": "Addressed in the current head."}
    assert all(
        command[:4] == ["gh", "api", "--hostname", "github.com"]
        for command, _ in runner.calls
    )


def test_optional_resolution_happens_after_reply_with_own_head_fence(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    runner = FakeRunner()

    outcome, events = invoke(
        tmp_path, runner, [HEAD, HEAD, HEAD, HEAD], resolve_thread_id=THREAD
    )

    assert outcome["thread_resolution"] == "resolved"
    assert [event[0] for event in events].count("head") == 4
    writes = [
        "reply" if "/replies" in " ".join(command) else "resolve"
        for command, _ in runner.calls
        if "/replies" in " ".join(command) or "mutation($threadId:" in " ".join(command)
    ]
    assert writes == ["reply", "resolve"]


def test_head_change_after_reply_reports_partial_and_skips_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    runner = FakeRunner()
    deps, _events = dependencies(tmp_path, [HEAD, NEW_HEAD])

    with pytest.raises(subject.HeadChanged) as caught:
        subject.guarded_feedback(
            repository=subject.ALLOWED_REPOSITORY,
            pr_number=17,
            claim_head=HEAD,
            expected_current_head=HEAD,
            comment_id=73,
            body="reply",
            run_context_token=TOKEN,
            resolve_thread_id=THREAD,
            runner=runner,
            **deps,
        )

    assert caught.value.result == {
        "state": "partial",
        "reason": "pull request head changed after reply",
        "operation": "reply",
        "phase": "after",
        "expected_head": HEAD,
        "observed_head": NEW_HEAD,
        "reply_applied": True,
        "resolution_applied": False,
        "mutation_dispatched": True,
        "detail": f"pull request head mismatch: expected {HEAD}, found {NEW_HEAD}",
    }
    assert not any("mutation($threadId:" in " ".join(call[0]) for call in runner.calls)


def test_head_change_before_resolution_reports_partial_without_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    runner = FakeRunner()
    deps, _events = dependencies(tmp_path, [HEAD, HEAD, NEW_HEAD])

    with pytest.raises(subject.HeadChanged) as caught:
        subject.guarded_feedback(
            repository=subject.ALLOWED_REPOSITORY,
            pr_number=17,
            claim_head=HEAD,
            expected_current_head=HEAD,
            comment_id=73,
            body="reply",
            run_context_token=TOKEN,
            resolve_thread_id=THREAD,
            runner=runner,
            **deps,
        )

    assert caught.value.result["state"] == "partial"
    assert caught.value.result["operation"] == "resolve"
    assert caught.value.result["phase"] == "before"
    assert caught.value.result["reply_applied"] is True
    assert caught.value.result["mutation_dispatched"] is False
    assert not any("mutation($threadId:" in " ".join(call[0]) for call in runner.calls)


def test_nested_review_comment_is_denied_before_any_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")

    class NestedRunner(FakeRunner):
        def __call__(self, command, *, timeout, input_text=None):
            if "/pulls/comments/73" in " ".join(command):
                self.calls.append((command, input_text))
                return result(
                    {
                        "id": 73,
                        "pull_request_url": (
                            "https://api.github.com/repos/arc-edge/arc-uas/pulls/17"
                        ),
                        "in_reply_to_id": 5,
                    }
                )
            return super().__call__(command, timeout=timeout, input_text=input_text)

    runner = NestedRunner()
    deps, _events = dependencies(tmp_path, [])
    with pytest.raises(subject.FeedbackError, match="top-level"):
        subject.guarded_feedback(
            repository=subject.ALLOWED_REPOSITORY,
            pr_number=17,
            claim_head=HEAD,
            expected_current_head=HEAD,
            comment_id=73,
            body="reply",
            run_context_token=TOKEN,
            runner=runner,
            **deps,
        )
    assert not any("/replies" in " ".join(call[0]) for call in runner.calls)


def test_requires_codex_thread_id(tmp_path, monkeypatch):
    runner = FakeRunner()
    deps, _events = dependencies(tmp_path, [])
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    with pytest.raises(subject.FeedbackError, match="CODEX_THREAD_ID"):
        subject.guarded_feedback(
            repository=subject.ALLOWED_REPOSITORY,
            pr_number=17,
            claim_head=HEAD,
            expected_current_head=HEAD,
            comment_id=73,
            body="reply",
            run_context_token=TOKEN,
            runner=runner,
            **deps,
        )


def test_mismatched_claim_context_is_denied_before_lease_or_write(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    runner = FakeRunner()
    deps, events = dependencies(tmp_path, [])

    def wrong_claim(*_args, **_kwargs):
        return {
            "state": "ok",
            "claim_id": "claim-other",
            "context": {"run_id": "run-1", "owner_id": "owner-other", "generation": 4},
        }

    deps["claim_reader"] = wrong_claim
    with pytest.raises(subject.FeedbackError, match="not owned"):
        subject.guarded_feedback(
            repository=subject.ALLOWED_REPOSITORY,
            pr_number=17,
            claim_head=HEAD,
            expected_current_head=HEAD,
            comment_id=73,
            body="reply",
            run_context_token=TOKEN,
            runner=runner,
            **deps,
        )
    assert not any(event[0] == "lease-enter" for event in events)
    assert not runner.calls


def run_transition_feedback(
    repo: Path,
    runner: FakeRunner,
    claim_head: str,
    expected_current_head: str,
    heads: list[str],
):
    deps, events = dependencies(repo, heads)
    outcome = subject.guarded_feedback(
        repository=subject.ALLOWED_REPOSITORY,
        pr_number=17,
        claim_head=claim_head,
        expected_current_head=expected_current_head,
        comment_id=73,
        body="Addressed in the pushed remediation commit.",
        run_context_token=TOKEN,
        runner=runner,
        **deps,
    )
    return outcome, events


def test_valid_linear_post_push_transition_can_reply_on_new_head(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    repo, old_head, new_head = make_linear_transition_repo(tmp_path)
    runner = FakeRunner()

    outcome, events = run_transition_feedback(
        repo, runner, old_head, new_head, [new_head, new_head]
    )

    assert outcome["state"] == "ok"
    assert outcome["claim_head"] == old_head
    assert outcome["head"] == new_head
    assert [event[0] for event in events].count("head") == 2
    assert any("/replies" in " ".join(command) for command, _ in runner.calls)


def test_arbitrary_unproved_transition_is_denied_before_github_reads_or_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    runner = FakeRunner()

    with pytest.raises(subject.FeedbackError, match="local remediation proof failed"):
        run_transition_feedback(tmp_path, runner, HEAD, NEW_HEAD, [])

    assert runner.calls == []


def test_non_ancestor_transition_is_denied_before_github_reads_or_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    repo, old_head, _new_head = make_linear_transition_repo(tmp_path)
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    unrelated_head = commit_tree(repo, tree)
    git(repo, "update-ref", f"refs/heads/{BRANCH}", unrelated_head)
    runner = FakeRunner()

    with pytest.raises(subject.FeedbackError, match="not an ancestor"):
        run_transition_feedback(repo, runner, old_head, unrelated_head, [])

    assert runner.calls == []


def test_dirty_transition_is_denied_before_github_reads_or_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    repo, old_head, new_head = make_linear_transition_repo(tmp_path)
    (repo / "untracked.txt").write_text("not part of the remediation\n", encoding="utf-8")
    runner = FakeRunner()

    with pytest.raises(subject.FeedbackError, match="worktree must be clean"):
        run_transition_feedback(repo, runner, old_head, new_head, [])

    assert runner.calls == []


def test_wrong_local_head_transition_is_denied_before_github_reads_or_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    repo, old_head, new_head = make_linear_transition_repo(tmp_path)
    # Both test commits are empty, so moving the symbolic branch back leaves a
    # clean worktree and exercises the independent exact-HEAD check.
    git(repo, "update-ref", f"refs/heads/{BRANCH}", old_head)
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=all") == ""
    runner = FakeRunner()

    with pytest.raises(subject.FeedbackError, match="local HEAD mismatch"):
        run_transition_feedback(repo, runner, old_head, new_head, [])

    assert runner.calls == []


def test_merge_commit_transition_is_denied_before_github_reads_or_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_THREAD_ID", "run-1")
    repo, old_head, new_head = make_linear_transition_repo(tmp_path)
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    merge_head = commit_tree(repo, tree, new_head, old_head)
    git(repo, "update-ref", f"refs/heads/{BRANCH}", merge_head)
    runner = FakeRunner()

    with pytest.raises(subject.FeedbackError, match="merge commit"):
        run_transition_feedback(repo, runner, old_head, merge_head, [])

    assert runner.calls == []


def test_body_file_rejects_symlink_and_captures_regular_file(tmp_path):
    body = tmp_path / "body.txt"
    body.write_text("review reply\n", encoding="utf-8")
    link = tmp_path / "body-link.txt"
    link.symlink_to(body)

    assert subject.read_body_file(body) == "review reply\n"
    with pytest.raises(subject.FeedbackError, match="non-symlink"):
        subject.read_body_file(link)


def test_body_file_rejects_unsafe_mode_and_secret_like_text(tmp_path):
    writable = tmp_path / "writable.txt"
    writable.write_text("ordinary review reply", encoding="utf-8")
    writable.chmod(0o666)
    with pytest.raises(subject.FeedbackError, match="group/world writable"):
        subject.read_body_file(writable)

    secret = tmp_path / "secret.txt"
    secret.write_text("access_token='ghp_abcdefghijklmnopqrstuvwxyz'", encoding="utf-8")
    with pytest.raises(subject.FeedbackError, match="secret-like"):
        subject.read_body_file(secret)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_kills_descendant_after_group_leader_exits(tmp_path):
    pid_file = tmp_path / "child.pid"
    leader = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"open({str(pid_file)!r},'w').write(str(p.pid)); "
        "sys.exit(0)"
    )

    with pytest.raises(subject.CommandTimeout):
        subject.run_command(
            [sys.executable, "-c", leader],
            timeout=0.2,
        )

    deadline = time.monotonic() + 2
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while _process_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _process_exists(child_pid)


def test_canonical_pr_identity_rejects_fork_and_wrong_branch():
    fork = pr()
    fork = subject.PullRequest(**{**fork.__dict__, "head_repository": "someone/fork"})
    with pytest.raises(subject.FeedbackError, match="canonical"):
        subject.require_current_head(fork, HEAD)
    with pytest.raises(subject.FeedbackError, match="prefix"):
        subject.require_current_head(pr(branch="feature/untrusted"), HEAD)
