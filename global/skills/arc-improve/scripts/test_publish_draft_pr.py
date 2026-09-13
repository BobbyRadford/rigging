from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import publish_draft_pr as publisher


EVIDENCE_BODY = """## Why

Bounded improvement.

## Evidence

The required risk-specific checks passed and no live operation occurred.

## Not verified

Production execution was intentionally not performed.

## Review focus

Review the highest-risk changed boundary.
"""


def completed(*args: str, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def init_repo(repo: Path) -> str:
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ARC Improve Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "chore: base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    return base


def args_for(body_file: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "repo": body_file.parent,
        "branch": "codex/arc-improve/test-change",
        "commit_message": "fix(tooling): harden publication",
        "title": "Harden scheduled pull request publication",
        "body_file": body_file,
        "base": "main",
        "max_files": 24,
        "max_changed_lines": 1600,
        "evidence_profile": [],
        "preflight_path": [],
        "validate_only": False,
        "require_lease": "delivery",
        "run_context_token": "0123456789abcdef0123456789abcdef",
        "lease_owner_id": "owner-uuid-1",
        "lease_generation": 1,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class PublisherRiskTests(unittest.TestCase):
    def test_row_408_paths_are_evidence_gated_not_denied(self) -> None:
        paths = [
            ".github/workflows/ci.yml",
            ".github/scripts/ci_change_plan.py",
            "justfile",
        ]

        profiles = publisher.risk_profiles_for_paths(paths)

        self.assertEqual({"build-system", "workflow"}, profiles)
        self.assertFalse(any(publisher.is_secret_material_path(path) for path in paths))
        publisher.validate_risk_evidence(
            profiles,
            ["workflow", "build-system"],
            EVIDENCE_BODY,
        )

    def test_repository_areas_classify_risk_without_path_denial(self) -> None:
        paths = [
            "services/flight-control/src/failsafe.rs",
            "infra/ansible/roles/deploy/tasks/main.yml",
            "infra/platforms/jetson-orin-nx-ark-pab.yaml",
            "AGENTS.md",
            "crates/auth/src/credential_store.rs",
            ".gitmodules",
        ]

        self.assertEqual(
            {
                "deployment",
                "flight-critical",
                "hardware",
                "policy",
                "secret-handling",
                "submodule",
            },
            publisher.risk_profiles_for_paths(paths),
        )

    def test_missing_risk_evidence_is_denied(self) -> None:
        with self.assertRaisesRegex(publisher.PublishError, "missing validated risk evidence"):
            publisher.validate_risk_evidence({"workflow"}, [], EVIDENCE_BODY)

    def test_flight_change_requires_sitl_and_owner_in_manifest(self) -> None:
        with self.assertRaisesRegex(publisher.PublishError, "FLIGHT-CRITICAL CHANGE"):
            publisher.validate_risk_evidence(
                {"flight-critical"},
                ["flight-critical"],
                EVIDENCE_BODY,
            )

        flight_body = (
            "FLIGHT-CRITICAL CHANGE\n\n"
            + EVIDENCE_BODY
            + "\nSITL: PASS; scenario=loss-of-link rejection; "
            "result=RTL scenario completed safely.\nFlight owner: Matt\n"
        )
        publisher.validate_risk_evidence(
            {"flight-critical"},
            ["flight-critical"],
            flight_body,
        )

    def test_flight_change_rejects_unrun_sitl_and_placeholder_owner(self) -> None:
        body = (
            "FLIGHT-CRITICAL CHANGE\n\n"
            + EVIDENCE_BODY
            + "\nSITL: not run\nFlight owner: TBD\n"
        )
        with self.assertRaisesRegex(publisher.PublishError, "SITL: PASS"):
            publisher.validate_risk_evidence(
                {"flight-critical"}, ["flight-critical"], body
            )

        placeholder_owner = (
            "FLIGHT-CRITICAL CHANGE\n\n"
            + EVIDENCE_BODY
            + "\nSITL: PASS; scenario=loss-of-link rejection; "
            "result=RTL scenario completed safely.\n"
            "Flight owner: required before merge\n"
        )
        with self.assertRaisesRegex(publisher.PublishError, "named human"):
            publisher.validate_risk_evidence(
                {"flight-critical"}, ["flight-critical"], placeholder_owner
            )

        contradictory_result = (
            "FLIGHT-CRITICAL CHANGE\n\n"
            + EVIDENCE_BODY
            + "\nSITL: PASS; scenario=loss-of-link rejection; "
            "result=scenario failed before RTL.\nFlight owner: Matt\n"
        )
        with self.assertRaisesRegex(publisher.PublishError, "placeholder"):
            publisher.validate_risk_evidence(
                {"flight-critical"}, ["flight-critical"], contradictory_result
            )

    def test_secret_material_is_denied_but_templates_and_code_are_allowed(self) -> None:
        for path in (".env", "config/.env.production", "keys/device.key", "identity.p12"):
            with self.subTest(path=path):
                self.assertTrue(publisher.is_secret_material_path(path))

        for path in (
            ".env.example",
            "config/.env.production.template",
            "crates/auth/src/secrets.rs",
            "services/comms/src/credential_store.rs",
        ):
            with self.subTest(path=path):
                self.assertFalse(publisher.is_secret_material_path(path))

    def test_modes_add_reviewable_link_and_submodule_profiles(self) -> None:
        profiles = publisher.risk_profiles_for_paths(
            ["docs/latest", "services/arc-ui"],
            {"docs/latest": "120000", "services/arc-ui": "160000"},
        )
        self.assertEqual({"filesystem-link", "submodule"}, profiles)

    def test_preflight_paths_fail_closed_without_overclassifying_prose_names(self) -> None:
        for path in ("", ".", "/docs/runbook.md", "docs/../AGENTS.md", "docs//runbook.md"):
            with self.subTest(path=path):
                self.assertFalse(publisher._is_well_formed_repo_path(path))

        self.assertEqual(
            set(),
            publisher.risk_profiles_for_paths(
                ["openspec/changes/document-deployment-history/proposal.md"]
            ),
        )

    def test_scheduled_publication_requires_current_delivery_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_state = os.environ.get("ARC_IMPROVE_STATE_DIR")
            previous_thread = os.environ.get("CODEX_THREAD_ID")
            os.environ["ARC_IMPROVE_STATE_DIR"] = temp_dir
            os.environ["CODEX_THREAD_ID"] = "run-owner"
            try:
                context_token = "0123456789abcdef0123456789abcdef"
                owner_id = publisher.automation_control.owner_id_for(
                    "run-owner", Path(temp_dir), context_token
                )
                acquired, lease_result = publisher.automation_control.lease_acquire(
                    Path(temp_dir),
                    "delivery",
                    "run-owner",
                    600,
                    {},
                    owner_id=owner_id,
                    cwd=Path(temp_dir),
                    context_token=context_token,
                )
                self.assertTrue(acquired)
                generation = lease_result["lease"]["generation"]
                publisher.automation_control.context_create(
                    Path(temp_dir),
                    context_token,
                    "run-owner",
                    owner_id,
                    generation,
                    "ledger-run-owner",
                    "delivery",
                    Path(temp_dir),
                )
                lease_args = args_for(
                    Path(temp_dir) / "body.md",
                    lease_owner_id=owner_id,
                    lease_generation=generation,
                )
                publisher.validate_lease(lease_args, Path(temp_dir))
                lease_args.lease_owner_id = None
                lease_args.lease_generation = None
                publisher.validate_lease(lease_args, Path(temp_dir))
                lease_args.run_context_token = "fedcba9876543210fedcba9876543210"
                with self.assertRaisesRegex(
                    publisher.PublishError, "cannot resolve scheduled-run context token"
                ):
                    publisher.validate_lease(lease_args, Path(temp_dir))
                lease_args.run_context_token = "0123456789abcdef0123456789abcdef"
                lease_args.lease_owner_id = "different-owner"
                lease_args.lease_generation = 1
                with self.assertRaisesRegex(publisher.PublishError, "does not match active"):
                    publisher.validate_lease(lease_args, Path(temp_dir))
            finally:
                if previous_state is None:
                    os.environ.pop("ARC_IMPROVE_STATE_DIR", None)
                else:
                    os.environ["ARC_IMPROVE_STATE_DIR"] = previous_state
                if previous_thread is None:
                    os.environ.pop("CODEX_THREAD_ID", None)
                else:
                    os.environ["CODEX_THREAD_ID"] = previous_thread


class PublisherIdentityTests(unittest.TestCase):
    def test_scheduled_base_requires_head_exactly_origin_main(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base = init_repo(repo)
            self.assertEqual(base, publisher.ensure_fresh_base(repo, "main", exact=True))

            (repo / "README.md").write_text("later\n", encoding="utf-8")
            git(repo, "commit", "-qam", "docs: later")
            with self.assertRaisesRegex(publisher.PublishError, "HEAD exactly"):
                publisher.ensure_fresh_base(repo, "main", exact=True)
            self.assertEqual(base, publisher.ensure_fresh_base(repo, "main", exact=False))

    def test_commit_must_have_exact_base_parent_and_validated_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base = init_repo(repo)
            (repo / "change.txt").write_text("validated\n", encoding="utf-8")
            git(repo, "add", "change.txt")
            staged_tree = git(repo, "write-tree")
            git(repo, "commit", "-qm", "fix: bounded change")
            commit = git(repo, "rev-parse", "HEAD")

            publisher.validate_created_commit(repo, commit, base, staged_tree)
            with self.assertRaisesRegex(publisher.PublishError, "tree differs"):
                publisher.validate_created_commit(repo, commit, base, "0" * 40)
            with self.assertRaisesRegex(publisher.PublishError, "sole parent"):
                publisher.validate_created_commit(repo, commit, "1" * 40, staged_tree)

    def test_existing_origin_branch_is_never_adopted_or_overwritten(self) -> None:
        commit = "a" * 40
        with mock.patch.object(publisher, "origin_branch_sha", return_value="b" * 40), mock.patch.object(
            publisher, "run"
        ) as run_mock:
            with self.assertRaisesRegex(publisher.PublishError, "refusing to adopt or overwrite"):
                publisher.push_with_reconciliation(Path("/tmp"), "codex/arc-improve/x", commit)
        run_mock.assert_not_called()

    def test_existing_origin_branch_is_rejected_before_local_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base = init_repo(repo)
            git(repo, "switch", "--detach", "-q", base)
            (repo / "change.txt").write_text("bounded\n", encoding="utf-8")
            body = repo / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            git(repo, "add", "change.txt", "body.md")
            tree = git(repo, "write-tree")
            with mock.patch.object(publisher, "origin_branch_sha", return_value="b" * 40):
                with self.assertRaisesRegex(publisher.PublishError, "already exists"):
                    publisher.publish(
                        repo,
                        args_for(body),
                        set(),
                        publisher.capture_pr_body(body),
                        base_oid=base,
                        staged_tree=tree,
                    )
            self.assertEqual(base, git(repo, "rev-parse", "HEAD"))

    def test_symlinks_must_stay_inside_repo_and_avoid_sensitive_targets(self) -> None:
        publisher.validate_symlink_target("docs/latest", "guide.md")
        publisher.validate_symlink_target("docs/latest", "../README.md")
        denied = {
            "/etc/passwd": "absolute",
            "../../outside": "escapes",
            "../.git/config": "Git metadata",
            "../.env": "secret material",
            "../keys/device.key": "secret material",
            "../.ssh/config": "secret material",
        }
        for target, reason in denied.items():
            with self.subTest(target=target), self.assertRaisesRegex(
                publisher.PublishError, reason
            ):
                publisher.validate_symlink_target("docs/latest", target)

    def test_staged_symlink_blob_is_read_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            init_repo(repo)
            (repo / "docs").mkdir()
            os.symlink("/etc/passwd", repo / "docs" / "latest")
            body = repo / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            with self.assertRaisesRegex(publisher.PublishError, "absolute target"):
                publisher.validate_staged_change(
                    repo,
                    args_for(body, evidence_profile=["filesystem-link"]),
                    publisher.capture_pr_body(body),
                )

    def test_publication_requires_explicit_unique_fencing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            body = Path(temp_dir) / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            with self.assertRaisesRegex(publisher.PublishError, "supplied together"):
                publisher.validate_inputs(args_for(body, lease_owner_id=None))
            with self.assertRaisesRegex(publisher.PublishError, "supplied together"):
                publisher.validate_inputs(args_for(body, lease_generation=None))
            publisher.validate_inputs(
                args_for(body, lease_owner_id=None, lease_generation=None)
            )
            for token in (None, "", "ABCDEF0123456789ABCDEF0123456789", "a" * 31, "g" * 32):
                with self.subTest(token=token), self.assertRaisesRegex(
                    publisher.PublishError, "32 lowercase hex"
                ):
                    publisher.validate_inputs(args_for(body, run_context_token=token))
            with self.assertRaisesRegex(publisher.PublishError, "actual publication requires"):
                publisher.validate_inputs(
                    args_for(
                        body,
                        require_lease=None,
                        run_context_token=None,
                        lease_owner_id=None,
                        lease_generation=None,
                    )
                )

    def test_publication_guard_uses_explicit_run_owner_and_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            body = Path(temp_dir) / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            args = args_for(body, lease_owner_id="invocation-uuid", lease_generation=9)
            prior_thread = os.environ.get("CODEX_THREAD_ID")
            os.environ["CODEX_THREAD_ID"] = "thread-123"
            sentinel = mock.MagicMock()
            try:
                with mock.patch.object(
                    publisher.automation_control,
                    "state_root",
                    return_value=Path(temp_dir) / "state",
                ), mock.patch.object(
                    publisher.automation_control,
                    "context_read",
                    return_value={
                        "context": {
                            "owner_id": "invocation-uuid",
                            "generation": 9,
                        }
                    },
                ) as context_read, mock.patch.object(
                    publisher.automation_control,
                    "lease_guard",
                    return_value=sentinel,
                ) as lease_guard:
                    self.assertIs(sentinel, publisher._publication_guard(Path(temp_dir), args))
            finally:
                if prior_thread is None:
                    os.environ.pop("CODEX_THREAD_ID", None)
                else:
                    os.environ["CODEX_THREAD_ID"] = prior_thread

        lease_guard.assert_called_once_with(
            Path(temp_dir) / "state",
            "delivery",
            "thread-123",
            owner_id="invocation-uuid",
            generation=9,
            context_token="0123456789abcdef0123456789abcdef",
            hold_seconds=publisher.LEASE_GUARD_SECONDS,
        )
        context_read.assert_called_once_with(
            Path(temp_dir) / "state",
            "0123456789abcdef0123456789abcdef",
            run_id="thread-123",
            scope="delivery",
            cwd=Path(temp_dir),
        )

    def test_lease_identity_reads_exact_token_context_when_flags_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            body = Path(temp_dir) / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            args = args_for(body, lease_owner_id=None, lease_generation=None)
            prior_thread = os.environ.get("CODEX_THREAD_ID")
            os.environ["CODEX_THREAD_ID"] = "thread-123"
            try:
                with mock.patch.object(
                    publisher.automation_control, "state_root", return_value=Path(temp_dir)
                ), mock.patch.object(
                    publisher.automation_control,
                    "context_read",
                    return_value={
                        "context": {"owner_id": "unique-owner", "generation": 17}
                    },
                ) as context_read:
                    identity = publisher.lease_identity(args, Path(temp_dir))
            finally:
                if prior_thread is None:
                    os.environ.pop("CODEX_THREAD_ID", None)
                else:
                    os.environ["CODEX_THREAD_ID"] = prior_thread
        self.assertEqual(
            (
                Path(temp_dir),
                "thread-123",
                "unique-owner",
                17,
                "0123456789abcdef0123456789abcdef",
            ),
            identity,
        )
        context_read.assert_called_once_with(
            Path(temp_dir),
            "0123456789abcdef0123456789abcdef",
            run_id="thread-123",
            scope="delivery",
            cwd=Path(temp_dir),
        )

    def test_lease_identity_rejects_unknown_or_mismatched_context_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            body = Path(temp_dir) / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            prior_thread = os.environ.get("CODEX_THREAD_ID")
            os.environ["CODEX_THREAD_ID"] = "thread-123"
            try:
                with mock.patch.object(
                    publisher.automation_control, "state_root", return_value=Path(temp_dir)
                ), mock.patch.object(
                    publisher.automation_control,
                    "context_read",
                    side_effect=publisher.automation_control.ControlError(
                        "run context belongs to a different thread"
                    ),
                ):
                    with self.assertRaisesRegex(
                        publisher.PublishError, "cannot resolve scheduled-run context token"
                    ):
                        publisher.lease_identity(args_for(body), Path(temp_dir))
            finally:
                if prior_thread is None:
                    os.environ.pop("CODEX_THREAD_ID", None)
                else:
                    os.environ["CODEX_THREAD_ID"] = prior_thread


class PublisherRemoteReconciliationTests(unittest.TestCase):
    def test_push_accepts_lost_command_output_only_when_remote_sha_matches(self) -> None:
        commit = "a" * 40
        failed_push = completed("git", returncode=1, stderr="connection lost")
        with mock.patch.object(
            publisher, "origin_branch_sha", side_effect=[None, commit]
        ), mock.patch.object(publisher, "run", return_value=failed_push) as run_mock:
            publisher.push_with_reconciliation(
                Path("/tmp"), "codex/arc-improve/reconcile", commit
            )
        push_args = run_mock.call_args.args
        self.assertIn("repos/arc-edge/arc-uas/git/refs", push_args)
        self.assertIn("--hostname", push_args)
        self.assertIn("github.com", push_args)
        self.assertIn("ref=refs/heads/codex/arc-improve/reconcile", push_args)
        self.assertIn(f"sha={commit}", push_args)
        self.assertNotIn("--force", " ".join(str(arg) for arg in push_args))

        with mock.patch.object(
            publisher, "origin_branch_sha", side_effect=[None, "b" * 40]
        ), mock.patch.object(publisher, "run", return_value=failed_push):
            with self.assertRaisesRegex(publisher.PublishError, "not reconciled"):
                publisher.push_with_reconciliation(
                    Path("/tmp"), "codex/arc-improve/reconcile", commit
                )

        with mock.patch.object(
            publisher, "origin_branch_sha", side_effect=[None, commit]
        ), mock.patch.object(
            publisher, "run", side_effect=publisher.PublishError("branch creation timed out")
        ):
            publisher.push_with_reconciliation(
                Path("/tmp"), "codex/arc-improve/reconcile", commit
            )

    def test_pr_create_accepts_lost_output_then_queries_canonical_head(self) -> None:
        commit = "a" * 40
        pr = {
            "number": 42,
            "url": "https://github.com/arc-edge/arc-uas/pull/42",
            "baseRefName": "main",
            "headRefName": "codex/arc-improve/test-change",
            "headRefOid": commit,
            "headRepositoryOwner": {"login": "arc-edge"},
            "state": "OPEN",
            "isDraft": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            body = Path(temp_dir) / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            args = args_for(body)
            with mock.patch.object(
                publisher, "open_prs_for_branch", side_effect=[[], [pr]]
            ), mock.patch.object(
                publisher,
                "run",
                return_value=completed("gh", returncode=1, stderr="response lost"),
            ) as run_mock, mock.patch.object(
                publisher, "canonical_pr", return_value=pr
            ) as canonical:
                number, url, authoritative = publisher.create_pr_with_reconciliation(
                    Path(temp_dir), args, commit, publisher.capture_pr_body(body)
                )
                self.assertEqual("-", run_mock.call_args.args[-1])
                repo_index = run_mock.call_args.args.index("--repo")
                self.assertEqual(
                    "github.com/arc-edge/arc-uas",
                    run_mock.call_args.args[repo_index + 1],
                )
                self.assertEqual(EVIDENCE_BODY, run_mock.call_args.kwargs["input_text"])
        self.assertEqual(42, number)
        self.assertEqual(pr["url"], url)
        self.assertEqual(commit, authoritative["headRefOid"])
        canonical.assert_called_once_with(Path(temp_dir), 42)

    def test_pr_with_mismatched_head_is_never_adopted(self) -> None:
        commit = "a" * 40
        wrong = {
            "number": 42,
            "url": "https://github.com/arc-edge/arc-uas/pull/42",
            "baseRefName": "main",
            "headRefName": "codex/arc-improve/test-change",
            "headRefOid": "b" * 40,
            "headRepositoryOwner": {"login": "arc-edge"},
            "state": "OPEN",
            "isDraft": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            body = Path(temp_dir) / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            with mock.patch.object(publisher, "open_prs_for_branch", return_value=[wrong]):
                with self.assertRaisesRegex(publisher.PublishError, "points to"):
                    publisher.create_pr_with_reconciliation(
                        Path(temp_dir), args_for(body), commit, publisher.capture_pr_body(body)
                    )

        wrong["headRefOid"] = commit
        wrong["headRepositoryOwner"] = {"login": "untrusted-fork"}
        with tempfile.TemporaryDirectory() as temp_dir:
            body = Path(temp_dir) / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            with mock.patch.object(publisher, "open_prs_for_branch", return_value=[wrong]):
                with self.assertRaisesRegex(publisher.PublishError, "repository owner"):
                    publisher.create_pr_with_reconciliation(
                        Path(temp_dir), args_for(body), commit, publisher.capture_pr_body(body)
                    )

    def test_remote_mutations_happen_only_while_publication_guard_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base = init_repo(repo)
            git(repo, "switch", "--detach", "-q", base)
            (repo / "change.txt").write_text("bounded\n", encoding="utf-8")
            git(repo, "add", "change.txt")
            tree = git(repo, "write-tree")
            body = repo / "body.md"
            body.write_text(EVIDENCE_BODY, encoding="utf-8")
            # Body is publication metadata, not part of this deliberately staged fixture.
            git(repo, "add", "body.md")
            tree = git(repo, "write-tree")
            state = {"active": False}

            class Guard:
                def __enter__(self):
                    state["active"] = True
                    return {}

                def __exit__(self, *_args):
                    state["active"] = False

            commit_holder: dict[str, str] = {}

            def pushed(_repo: Path, _branch: str, commit: str) -> None:
                self.assertTrue(state["active"])
                commit_holder["commit"] = commit

            def created(
                _repo: Path,
                _args: argparse.Namespace,
                commit: str,
                body_snapshot: publisher.BodySnapshot,
            ):
                self.assertTrue(state["active"])
                self.assertEqual(commit_holder["commit"], commit)
                self.assertEqual(EVIDENCE_BODY, body_snapshot.text)
                pr = {
                    "number": 7,
                    "url": "https://github.com/arc-edge/arc-uas/pull/7",
                    "baseRefName": "main",
                    "headRefName": "codex/arc-improve/test-change",
                    "headRefOid": commit,
                    "headRepositoryOwner": {"login": "arc-edge"},
                    "state": "OPEN",
                    "isDraft": True,
                }
                return 7, pr["url"], pr

            def queued_review(*_args, **_kwargs):
                self.assertTrue(state["active"])
                return {"state": "queued"}

            with mock.patch.object(
                publisher, "origin_branch_sha", return_value=None
            ), mock.patch.object(
                publisher, "_publication_guard", return_value=Guard()
            ), mock.patch.object(
                publisher, "require_expected_remote"
            ), mock.patch.object(
                publisher, "require_remote_base_unchanged"
            ), mock.patch.object(
                publisher, "push_with_reconciliation", side_effect=pushed
            ), mock.patch.object(
                publisher, "create_pr_with_reconciliation", side_effect=created
            ), mock.patch.object(
                publisher.automation_control,
                "review_record",
                side_effect=queued_review,
            ) as queued:
                result = publisher.publish(
                    repo,
                    args_for(body),
                    set(),
                    publisher.capture_pr_body(body),
                    base_oid=base,
                    staged_tree=tree,
                )

            self.assertFalse(state["active"])
            self.assertEqual("published", result["state"])
            self.assertEqual(commit_holder["commit"], result["pull_request_head"])
            queued.assert_called_once()


class PublisherProcessTests(unittest.TestCase):
    def test_pr_body_is_captured_once_without_links_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target.md"
            target.write_text(EVIDENCE_BODY, encoding="utf-8")
            linked = root / "linked.md"
            os.symlink(target, linked)
            with self.assertRaisesRegex(publisher.PublishError, "securely open"):
                publisher.capture_pr_body(linked)

            target.write_text(EVIDENCE_BODY + "\nghp_abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
            with self.assertRaisesRegex(publisher.PublishError, "secret-like"):
                publisher.capture_pr_body(target)

            target.write_text(EVIDENCE_BODY, encoding="utf-8")
            snapshot = publisher.capture_pr_body(target)
            target.write_text("replacement that must never be published\n" * 5, encoding="utf-8")
            self.assertEqual(EVIDENCE_BODY, snapshot.text)
            self.assertEqual(len(EVIDENCE_BODY.encode("utf-8")), snapshot.size)
            self.assertRegex(snapshot.sha256, r"^[0-9a-f]{64}$")

    def test_subprocess_timeout_is_bounded_and_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            started = time.monotonic()
            with self.assertRaisesRegex(publisher.PublishError, "timed out"):
                publisher.run(
                    Path(temp_dir),
                    sys.executable,
                    "-c",
                    "import time; time.sleep(30)",
                    timeout_seconds=1,
                )
            self.assertLess(time.monotonic() - started, 4)


if __name__ == "__main__":
    unittest.main()
