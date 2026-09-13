from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("guarded_push.py")
SPEC = importlib.util.spec_from_file_location("guarded_push", SCRIPT)
assert SPEC and SPEC.loader
guarded_push = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guarded_push
SPEC.loader.exec_module(guarded_push)


OLD = "1" * 40
NEW = "2" * 40
OTHER = "3" * 40
BRANCH = "codex/arc-improve/arc-123-review-fix"
REPOSITORY = "arc-edge/arc-uas"
TOKEN = "a" * 32


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int, bool]] = []
        self.dirty = ""
        self.branch = BRANCH
        self.local_head = NEW
        self.merges = ""
        self.ancestor_returncode = 0
        self.push_returncode = 0
        self.push_stdout = ""
        self.push_stderr = ""
        self.push_timeout = False
        self.origin = "git@github.com:arc-edge/arc-uas.git\n"
        self.push_origin = "git@github.com:arc-edge/arc-uas.git\n"

    def __call__(self, command, *, timeout, check):
        command = list(command)
        self.calls.append((command, timeout, check))
        if "push" in command:
            if self.push_timeout:
                raise guarded_push.CommandTimeout(command, "", "")
            return guarded_push.CommandResult(
                self.push_returncode, self.push_stdout, self.push_stderr
            )
        if command[-3:] == ["remote", "get-url", "origin"]:
            return guarded_push.CommandResult(0, self.origin, "")
        if command[-4:] == [
            "remote",
            "get-url",
            "--push",
            "origin",
        ]:
            return guarded_push.CommandResult(0, self.push_origin, "")
        if "status" in command:
            return guarded_push.CommandResult(0, self.dirty, "")
        if command[-2:] == ["branch", "--show-current"]:
            return guarded_push.CommandResult(0, self.branch + "\n", "")
        if command[-2:] == ["rev-parse", "HEAD"]:
            return guarded_push.CommandResult(0, self.local_head + "\n", "")
        if "cat-file" in command:
            return guarded_push.CommandResult(0, "", "")
        if "merge-base" in command:
            return guarded_push.CommandResult(self.ancestor_returncode, "", "")
        if "rev-list" in command:
            return guarded_push.CommandResult(0, self.merges, "")
        raise AssertionError(f"unexpected command: {command}")

    @property
    def push_calls(self) -> list[list[str]]:
        return [command for command, _, _ in self.calls if "push" in command]


def pr_state(head: str, *, branch: str = BRANCH, state: str = "open"):
    return guarded_push.PullRequestState(
        state=state,
        head_branch=branch,
        head_sha=head,
        head_repository=REPOSITORY,
        base_repository=REPOSITORY,
        base_branch="main",
    )


class FetchSequence:
    def __init__(self, *states) -> None:
        self.states = list(states)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, repository: str, pr: int):
        self.calls.append((repository, pr))
        if not self.states:
            raise AssertionError("unexpected PR fetch")
        return self.states.pop(0)


class LeaseRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.entered = False

    @contextmanager
    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.entered = True
        try:
            yield {"generation": kwargs["generation"]}
        finally:
            self.entered = False


class GuardedPushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path("/tmp/codex/worktrees/review-worktree")
        self.root = Path("/tmp/state")
        self.runner = FakeRunner()
        self.lease = LeaseRecorder()
        self.fetch = FetchSequence(pr_state(OLD), pr_state(NEW))
        self.claim_calls: list[tuple] = []

    @staticmethod
    def context_reader(root, token, *, run_id, scope, cwd):
        if token != TOKEN:
            raise guarded_push.control.ControlError("run token does not exist")
        return {
            "state": "ok",
            "context": {
                "owner_id": "owner-token",
                "generation": 17,
                "run_id": run_id,
                "scope": scope,
                "cwd": str(cwd),
            },
        }

    def invoke(self):
        return guarded_push.guarded_push(
            repo=self.repo,
            repository=REPOSITORY,
            pr=123,
            expected_remote_head=OLD,
            expected_new_head=NEW,
            run_context_token=TOKEN,
            run_id="thread-123",
            root=self.root,
            runner=self.runner,
            pr_fetcher=self.fetch,
            context_reader=self.context_reader,
            claim_reader=self.claim_reader,
            lease_factory=self.lease,
        )

    def claim_reader(
        self, root, repository, pr, head, run_id, cwd, context_token
    ):
        self.claim_calls.append((root, repository, pr, head, run_id, cwd, context_token))
        return {
            "state": "ok",
            "claim_id": "claim-123",
            "context": self.context_reader(
                root,
                context_token,
                run_id=run_id,
                scope="review",
                cwd=cwd,
            )["context"],
        }

    def test_success_is_exactly_one_non_force_push_and_uses_lease_generation(self):
        result = self.invoke()

        self.assertEqual(result["state"], "pushed")
        self.assertEqual(result["lease_generation"], 17)
        self.assertEqual(self.claim_calls[0][1:4], (REPOSITORY, 123, OLD))
        self.assertEqual(self.fetch.calls, [(REPOSITORY, 123), (REPOSITORY, 123)])
        self.assertEqual(len(self.runner.push_calls), 1)
        push = self.runner.push_calls[0]
        self.assertEqual(
            push[-4:],
            [
                "push",
                "--no-verify",
                "origin",
                f"HEAD:refs/heads/{BRANCH}",
            ],
        )
        self.assertFalse(
            any(argument == "-f" or argument.startswith("--force") for argument in push)
        )
        self.assertIn("push.followTags=false", push)
        self.assertIn("core.hooksPath=/dev/null", push)
        _, lease_kwargs = self.lease.calls[0]
        self.assertEqual(lease_kwargs["owner_id"], "owner-token")
        self.assertEqual(lease_kwargs["generation"], 17)
        self.assertEqual(lease_kwargs["context_token"], TOKEN)

    def test_stale_remote_head_refuses_before_push(self):
        self.fetch = FetchSequence(pr_state(OTHER))

        with self.assertRaisesRegex(guarded_push.GuardedPushError, "head mismatch"):
            self.invoke()

        self.assertEqual(self.runner.push_calls, [])

    def test_active_claim_for_exact_old_head_is_mandatory(self):
        def missing_claim(*_args, **_kwargs):
            raise guarded_push.control.ControlError(
                "the exact head has no active review claim"
            )

        with self.assertRaisesRegex(
            guarded_push.control.ControlError, "no active review claim"
        ):
            guarded_push.guarded_push(
                repo=self.repo,
                repository=REPOSITORY,
                pr=123,
                expected_remote_head=OLD,
                expected_new_head=NEW,
                run_context_token=TOKEN,
                run_id="thread-123",
                root=self.root,
                runner=self.runner,
                pr_fetcher=self.fetch,
                context_reader=self.context_reader,
                claim_reader=missing_claim,
                lease_factory=self.lease,
            )
        self.assertEqual(self.runner.calls, [])

    def test_dirty_worktree_refuses_before_remote_lookup_or_push(self):
        self.runner.dirty = " M changed.py\n"

        with self.assertRaisesRegex(guarded_push.GuardedPushError, "clean"):
            self.invoke()

        self.assertEqual(self.runner.push_calls, [])
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.lease.calls[0][1]["generation"], 17)

    def test_non_arc_improve_branch_refuses(self):
        self.runner.branch = "feature/not-automation"

        with self.assertRaisesRegex(guarded_push.GuardedPushError, "current branch"):
            self.invoke()

        self.assertEqual(self.runner.push_calls, [])

    def test_separate_push_url_must_be_exact_allowed_origin(self):
        self.runner.push_origin = "git@github.com:attacker/fork.git\n"

        with self.assertRaisesRegex(guarded_push.GuardedPushError, "push URL"):
            self.invoke()

        self.assertEqual(self.runner.push_calls, [])

    def test_pr_branch_must_equal_local_branch(self):
        self.fetch = FetchSequence(pr_state(OLD, branch="codex/arc-improve/other"))

        with self.assertRaisesRegex(guarded_push.GuardedPushError, "branch mismatch"):
            self.invoke()

        self.assertEqual(self.runner.push_calls, [])

    def test_timeout_reconciles_only_when_exact_pr_head_advanced(self):
        self.runner.push_timeout = True

        result = self.invoke()

        self.assertEqual(result["state"], "pushed-reconciled")
        self.assertEqual(result["head"], NEW)
        self.assertEqual(len(self.runner.push_calls), 1)

    def test_timeout_with_old_remote_head_is_an_error_and_never_retries(self):
        self.runner.push_timeout = True
        self.fetch = FetchSequence(pr_state(OLD), pr_state(OLD))

        with self.assertRaisesRegex(guarded_push.GuardedPushError, "did not reconcile"):
            self.invoke()

        self.assertEqual(len(self.runner.push_calls), 1)

    def test_failed_push_can_reconcile_lost_success_output(self):
        self.runner.push_returncode = 1
        self.runner.push_stderr = "connection reset"

        result = self.invoke()

        self.assertEqual(result["state"], "pushed")

    def test_failed_push_without_head_advance_is_an_error(self):
        self.runner.push_returncode = 1
        self.fetch = FetchSequence(pr_state(OLD), pr_state(OLD))

        with self.assertRaisesRegex(guarded_push.GuardedPushError, "non-force push failed"):
            self.invoke()

    def test_non_ancestor_and_merge_commit_ranges_refuse(self):
        self.runner.ancestor_returncode = 1
        with self.assertRaisesRegex(guarded_push.GuardedPushError, "not an ancestor"):
            self.invoke()

        self.runner = FakeRunner()
        self.runner.merges = OTHER + "\n"
        with self.assertRaisesRegex(guarded_push.GuardedPushError, "merge commit"):
            self.invoke()

    def test_local_head_must_equal_expected_new_head(self):
        self.runner.local_head = OTHER
        with self.assertRaisesRegex(guarded_push.GuardedPushError, "local HEAD mismatch"):
            self.invoke()

    def test_new_head_must_advance(self):
        with self.assertRaisesRegex(guarded_push.GuardedPushError, "must advance"):
            guarded_push.guarded_push(
                repo=self.repo,
                repository=REPOSITORY,
                pr=123,
                expected_remote_head=OLD,
                expected_new_head=OLD,
                run_context_token=TOKEN,
                run_id="thread-123",
                root=self.root,
                runner=self.runner,
                pr_fetcher=self.fetch,
                context_reader=self.context_reader,
                claim_reader=self.claim_reader,
                lease_factory=self.lease,
            )
        self.assertEqual(self.runner.push_calls, [])

    def test_wrong_context_token_refuses_before_lease_or_git(self):
        with self.assertRaisesRegex(guarded_push.control.ControlError, "token"):
            guarded_push.guarded_push(
                repo=self.repo,
                repository=REPOSITORY,
                pr=123,
                expected_remote_head=OLD,
                expected_new_head=NEW,
                run_context_token="b" * 32,
                run_id="thread-123",
                root=self.root,
                runner=self.runner,
                pr_fetcher=self.fetch,
                context_reader=self.context_reader,
                claim_reader=self.claim_reader,
                lease_factory=self.lease,
            )
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.lease.calls, [])

    def test_context_mismatch_from_control_layer_refuses(self):
        def mismatched_context(*args, **kwargs):
            raise guarded_push.control.ControlError(
                "run context belongs to a different worktree"
            )

        with self.assertRaisesRegex(guarded_push.control.ControlError, "different worktree"):
            guarded_push.guarded_push(
                repo=self.repo,
                repository=REPOSITORY,
                pr=123,
                expected_remote_head=OLD,
                expected_new_head=NEW,
                run_context_token=TOKEN,
                run_id="thread-123",
                root=self.root,
                runner=self.runner,
                pr_fetcher=self.fetch,
                context_reader=mismatched_context,
                claim_reader=self.claim_reader,
                lease_factory=self.lease,
            )
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.lease.calls, [])

    def test_stale_lease_generation_refuses_before_git(self):
        @contextmanager
        def rejecting_lease(*args, **kwargs):
            self.assertEqual(kwargs["generation"], 17)
            raise guarded_push.control.ControlError("lease generation mismatch")
            yield  # pragma: no cover

        with self.assertRaisesRegex(guarded_push.control.ControlError, "generation"):
            guarded_push.guarded_push(
                repo=self.repo,
                repository=REPOSITORY,
                pr=123,
                expected_remote_head=OLD,
                expected_new_head=NEW,
                run_context_token=TOKEN,
                run_id="thread-123",
                root=self.root,
                runner=self.runner,
                pr_fetcher=self.fetch,
                context_reader=self.context_reader,
                claim_reader=self.claim_reader,
                lease_factory=rejecting_lease,
            )
        self.assertEqual(self.runner.calls, [])


class ValidationTests(unittest.TestCase):
    def test_pull_request_fetch_pins_github_hostname(self):
        payload = {
            "state": "open",
            "head": {
                "ref": BRANCH,
                "sha": OLD,
                "repo": {"full_name": REPOSITORY},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": REPOSITORY},
            },
        }
        with mock.patch.object(
            guarded_push,
            "run_command",
            return_value=guarded_push.CommandResult(0, json.dumps(payload), ""),
        ) as runner:
            result = guarded_push.fetch_pull_request(
                REPOSITORY, 123, runner=runner
            )
        self.assertEqual(OLD, result.head_sha)
        self.assertEqual(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                f"repos/{REPOSITORY}/pulls/123",
            ],
            runner.call_args.args[0],
        )

    def test_repo_must_be_inside_codex_worktrees(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            codex_home = root / ".codex"
            (codex_home / "worktrees").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            with self.assertRaisesRegex(guarded_push.GuardedPushError, "inside"):
                guarded_push.resolve_repo(outside, codex_home)

    def test_cli_rejects_uppercase_or_short_sha_and_other_repository(self):
        parser = guarded_push.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--repo",
                    "/tmp/repo",
                    "--pr",
                    "1",
                    "--expected-remote-head",
                    "A" * 40,
                    "--expected-new-head",
                    NEW,
                ]
            )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--repo",
                    "/tmp/repo",
                    "--pr",
                    "1",
                    "--expected-remote-head",
                    OLD,
                    "--expected-new-head",
                    NEW,
                    "--run-context-token",
                    "A" * 32,
                ]
            )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--repo",
                    "/tmp/repo",
                    "--repository",
                    "someone/fork",
                    "--pr",
                    "1",
                    "--expected-remote-head",
                    OLD,
                    "--expected-new-head",
                    NEW,
                ]
            )

    @mock.patch.object(guarded_push.os, "killpg")
    @mock.patch.object(guarded_push.subprocess, "Popen")
    def test_timeout_terminates_and_reaps_process_group(self, popen, killpg):
        process = mock.Mock()
        process.pid = 4242
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["tool"], 1),
            ("partial", "timed out"),
        ]
        popen.return_value = process

        with self.assertRaises(guarded_push.CommandTimeout) as raised:
            guarded_push.run_command(["tool"], timeout=1)

        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(4242, guarded_push.signal.SIGTERM)
        self.assertEqual(raised.exception.stdout, "partial")

    @mock.patch.object(guarded_push.os, "killpg")
    @mock.patch.object(guarded_push.subprocess, "Popen")
    def test_interruption_terminates_and_reaps_process_group(self, popen, killpg):
        process = mock.Mock()
        process.pid = 4343
        process.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        popen.return_value = process

        with self.assertRaises(KeyboardInterrupt):
            guarded_push.run_command(["tool"], timeout=1)

        killpg.assert_called_once_with(4343, guarded_push.signal.SIGTERM)
        self.assertEqual(process.communicate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
