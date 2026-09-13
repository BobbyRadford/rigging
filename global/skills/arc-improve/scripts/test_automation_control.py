from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import stat
import threading
import tempfile
import unittest

import automation_control as control


UTC = timezone.utc
TOKEN_A = "a" * 32
TOKEN_REVIEW = "b" * 32
TOKEN_C = "c" * 32
TOKEN_D = "d" * 32


def candidate_policy(impact_class: str) -> dict[str, object]:
    return {
        "impact_class": impact_class,
        "impact_rationale": "The observed reachable consequence matches this impact class.",
        "architecture_resurrection": {
            "suspected": False,
            "classification_rationale": (
                "The change modifies a current canonical path and does not restore a dormant one."
            ),
        },
    }


class AutomationControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

    def owner(self, run_id: str, token: str) -> str:
        return control.owner_id_for(run_id, self.root, token)

    def acquire(
        self,
        scope: str,
        run_id: str,
        token: str,
        *,
        ttl: int = 600,
        now: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, object]]:
        owner = self.owner(run_id, token)
        owned, result = control.lease_acquire(
            self.root,
            scope,
            run_id,
            ttl,
            metadata or {},
            owner_id=owner,
            cwd=self.root,
            context_token=token,
            now=now or self.now,
        )
        self.assertTrue(owned)
        return owner, result

    def create_context(
        self,
        scope: str,
        run_id: str,
        token: str,
        owner: str,
        lease: dict[str, object],
        *,
        ledger_run_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        lease_document = lease["lease"]
        assert isinstance(lease_document, dict)
        control.context_create(
            self.root,
            token,
            run_id,
            owner,
            int(lease_document["generation"]),
            ledger_run_id or f"{run_id}-ledger",
            scope,
            self.root,
            now=now or self.now,
        )

    def run_fence(
        self,
        lease_run_id: str,
        token: str,
        owner: str,
        lease: dict[str, object],
    ) -> dict[str, object]:
        lease_document = lease["lease"]
        assert isinstance(lease_document, dict)
        return {
            "lease_run_id": lease_run_id,
            "owner_id": owner,
            "generation": int(lease_document["generation"]),
            "context_token": token,
        }

    def tearDown(self) -> None:
        for path in self.root.rglob("*"):
            try:
                path.chmod(0o700 if path.is_dir() else 0o600)
            except OSError:
                pass
        self.temp_dir.cleanup()

    def test_lease_is_exclusive_and_same_owner_acquire_is_idempotent(self) -> None:
        owner_a = self.owner("run-a", TOKEN_A)
        owned, first = control.lease_acquire(
            self.root,
            "delivery",
            "run-a",
            600,
            {"test": 1},
            owner_id=owner_a,
            cwd=self.root,
            context_token=TOKEN_A,
            now=self.now,
        )
        self.assertTrue(owned)
        busy, second = control.lease_acquire(
            self.root,
            "delivery",
            "run-b",
            600,
            {},
            owner_id=self.owner("run-b", TOKEN_C),
            cwd=self.root,
            context_token=TOKEN_C,
            now=self.now,
        )
        self.assertFalse(busy)
        self.assertEqual("run-a", second["lease"]["run_id"])

        owned_again, refreshed = control.lease_acquire(
            self.root,
            "delivery",
            "run-a",
            900,
            {"next": 2},
            owner_id=owner_a,
            cwd=self.root,
            context_token=TOKEN_A,
            now=self.now + timedelta(seconds=10),
        )
        self.assertTrue(owned_again)
        self.assertEqual("owned", refreshed["state"])
        self.assertEqual(2, refreshed["lease"]["metadata"]["next"])
        self.assertEqual("run-a", first["lease"]["run_id"])

    def test_expired_lease_is_reclaimed_and_old_owner_is_fenced(self) -> None:
        owner_a = self.owner("run-a", TOKEN_A)
        owner_b = self.owner("run-b", TOKEN_C)
        _, original = control.lease_acquire(
            self.root,
            "delivery",
            "run-a",
            60,
            {},
            owner_id=owner_a,
            cwd=self.root,
            context_token=TOKEN_A,
            now=self.now,
        )
        owned, reclaimed = control.lease_acquire(
            self.root,
            "delivery",
            "run-b",
            600,
            {},
            owner_id=owner_b,
            cwd=self.root,
            context_token=TOKEN_C,
            now=self.now + timedelta(seconds=61),
        )
        self.assertTrue(owned)
        self.assertEqual("run-a", reclaimed["lease"]["reclaimed"]["run_id"])
        self.assertGreater(reclaimed["lease"]["generation"], original["lease"]["generation"])
        with self.assertRaisesRegex(control.ControlError, "owned by run-b/"):
            control.lease_assert(
                self.root,
                "delivery",
                "run-a",
                owner_id=owner_a,
                generation=original["lease"]["generation"],
                context_token=TOKEN_A,
                now=self.now + timedelta(seconds=62),
            )
        with self.assertRaisesRegex(control.ControlError, "owned by run-b/"):
            control.lease_release(
                self.root,
                "delivery",
                "run-a",
                owner_id=owner_a,
                generation=original["lease"]["generation"],
                context_token=TOKEN_A,
                now=self.now + timedelta(seconds=62),
            )

    def test_expired_same_thread_invocation_gets_new_generation(self) -> None:
        owner_a = self.owner("thread-a", TOKEN_A)
        owner_b = self.owner("thread-a", TOKEN_C)
        _, first = control.lease_acquire(
            self.root,
            "delivery",
            "thread-a",
            60,
            {},
            owner_id=owner_a,
            cwd=self.root,
            context_token=TOKEN_A,
            now=self.now,
        )
        owned, second = control.lease_acquire(
            self.root,
            "delivery",
            "thread-a",
            600,
            {},
            owner_id=owner_b,
            cwd=self.root,
            context_token=TOKEN_C,
            now=self.now + timedelta(seconds=61),
        )
        self.assertTrue(owned)
        self.assertGreater(second["lease"]["generation"], first["lease"]["generation"])
        with self.assertRaisesRegex(control.ControlError, "owned by thread-a/"):
            control.lease_heartbeat(
                self.root,
                "delivery",
                "thread-a",
                None,
                owner_id=owner_a,
                generation=first["lease"]["generation"],
                context_token=TOKEN_A,
                now=self.now + timedelta(seconds=62),
            )

    def test_expired_lease_can_be_quarantined_but_active_cannot(self) -> None:
        control.lease_acquire(
            self.root,
            "delivery",
            "run-a",
            60,
            {},
            owner_id=self.owner("run-a", TOKEN_A),
            cwd=self.root,
            context_token=TOKEN_A,
            now=self.now,
        )
        with self.assertRaisesRegex(control.ControlError, "has not expired"):
            control.lease_recover(
                self.root, "delivery", now=self.now + timedelta(seconds=59)
            )
        recovered = control.lease_recover(
            self.root, "delivery", now=self.now + timedelta(seconds=61)
        )
        self.assertEqual("recovered-expired-lease", recovered["state"])
        quarantined = Path(recovered["quarantine"])
        self.assertTrue(quarantined.exists())
        self.assertFalse((self.root / "leases" / "delivery.json").exists())
        self.assertEqual(0, stat.S_IMODE(quarantined.stat().st_mode) & stat.S_IWUSR)

    def test_malformed_generation_document_fails_closed(self) -> None:
        generation = self.root / "leases" / "delivery.generation.json"
        generation.parent.mkdir(parents=True)
        generation.write_text(
            json.dumps({"version": 1, "scope": "delivery", "generation": True}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(control.ControlError, "generation document"):
            control.lease_acquire(
                self.root,
                "delivery",
                "run-a",
                600,
                {},
                owner_id=self.owner("run-a", TOKEN_A),
                cwd=self.root,
                context_token=TOKEN_A,
                now=self.now,
            )

    def test_lease_owner_generation_replay_cannot_replace_secret_capability(self) -> None:
        owner, lease = self.acquire("delivery", "victim-thread", TOKEN_A)
        generation = int(lease["lease"]["generation"])
        lease_text = (self.root / "leases" / "delivery.json").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN_A, lease_text)
        self.assertIn(control.run_token_digest(TOKEN_A), lease_text)

        for operation in (
            lambda: control.lease_assert(
                self.root,
                "delivery",
                "victim-thread",
                owner_id=owner,
                generation=generation,
                context_token=TOKEN_C,
                now=self.now,
            ),
            lambda: control.lease_heartbeat(
                self.root,
                "delivery",
                "victim-thread",
                None,
                owner_id=owner,
                generation=generation,
                context_token=TOKEN_C,
                now=self.now,
            ),
            lambda: control.lease_release(
                self.root,
                "delivery",
                "victim-thread",
                owner_id=owner,
                generation=generation,
                context_token=TOKEN_C,
                now=self.now,
            ),
        ):
            with self.assertRaisesRegex(control.ControlError, "context token"):
                operation()
        with self.assertRaisesRegex(control.ControlError, "context token"):
            with control.lease_guard(
                self.root,
                "delivery",
                "victim-thread",
                owner_id=owner,
                generation=generation,
                context_token=TOKEN_C,
                now=self.now,
            ):
                self.fail("a replayed lease identity entered the guarded section")

        renewed = control.lease_heartbeat(
            self.root,
            "delivery",
            "victim-thread",
            None,
            owner_id=owner,
            generation=generation,
            context_token=TOKEN_A,
            now=self.now,
        )
        self.assertEqual("renewed", renewed["state"])
        released = control.lease_release(
            self.root,
            "delivery",
            "victim-thread",
            owner_id=owner,
            generation=generation,
            context_token=TOKEN_A,
            now=self.now,
        )
        self.assertEqual("released", released["state"])

        with self.assertRaisesRegex(control.ControlError, "does not derive"):
            control.lease_acquire(
                self.root,
                "delivery",
                "forged-thread",
                600,
                {},
                owner_id=owner,
                cwd=self.root,
                context_token=TOKEN_D,
                now=self.now,
            )

    def test_context_creation_rejects_forged_token_and_duplicate_lease_identity(self) -> None:
        owner, lease = self.acquire("delivery", "victim-thread", TOKEN_A)
        generation = int(lease["lease"]["generation"])
        with self.assertRaisesRegex(control.ControlError, "does not derive"):
            control.context_create(
                self.root,
                TOKEN_C,
                "victim-thread",
                owner,
                generation,
                "forged-ledger",
                "delivery",
                self.root,
                now=self.now,
            )

        self.create_context("delivery", "victim-thread", TOKEN_A, owner, lease)
        with self.assertRaisesRegex(control.ControlError, "already has a persisted context"):
            self.create_context("delivery", "victim-thread", TOKEN_A, owner, lease)

        attacker_owner = self.owner("victim-thread", TOKEN_C)
        with self.assertRaisesRegex(control.ControlError, "owned by victim-thread/"):
            control.context_create(
                self.root,
                TOKEN_C,
                "victim-thread",
                attacker_owner,
                generation,
                "second-forged-ledger",
                "delivery",
                self.root,
                now=self.now,
            )

    def test_run_ledger_rejects_unfenced_and_stale_capability_writes(self) -> None:
        owner, lease = self.acquire("delivery", "victim-thread", TOKEN_A)
        fence = self.run_fence("victim-thread", TOKEN_A, owner, lease)
        control.run_begin(
            self.root,
            "victim-ledger",
            "delivery",
            "arc-daily-improvement",
            "delivery",
            {},
            **fence,
            now=self.now,
        )

        with self.assertRaises(TypeError):
            control.run_event(self.root, "victim-ledger", "forged-checkpoint", {})
        with self.assertRaises(TypeError):
            control.run_begin(
                self.root,
                "unfenced-ledger",
                "delivery",
                "arc-daily-improvement",
                "delivery",
                {},
            )
        parser = control.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "run",
                    "event",
                    "--run-id",
                    "victim-ledger",
                    "--event-type",
                    "forged-checkpoint",
                ]
            )
        with self.assertRaisesRegex(control.ControlError, "different lease identity"):
            control.run_event(
                self.root,
                "victim-ledger",
                "forged-checkpoint",
                {},
                lease_run_id="victim-thread",
                owner_id=owner,
                generation=int(lease["lease"]["generation"]),
                context_token=TOKEN_C,
                now=self.now,
            )
        with self.assertRaisesRegex(control.ControlError, "different lease identity"):
            control.run_finish(
                self.root,
                "victim-ledger",
                "forged",
                {},
                lease_run_id="victim-thread",
                owner_id=owner,
                generation=int(lease["lease"]["generation"]),
                context_token=TOKEN_C,
                now=self.now,
            )

        recorded = control.run_event(
            self.root,
            "victim-ledger",
            "legitimate-checkpoint",
            {},
            **fence,
            now=self.now,
        )
        self.assertEqual("recorded", recorded["state"])
        control.lease_release(
            self.root,
            "delivery",
            "victim-thread",
            owner_id=owner,
            generation=int(lease["lease"]["generation"]),
            context_token=TOKEN_A,
            now=self.now,
        )
        with self.assertRaisesRegex(control.ControlError, "no active delivery lease"):
            control.run_event(
                self.root,
                "victim-ledger",
                "after-release",
                {},
                **fence,
                now=self.now,
            )

    def test_run_records_are_hash_chained_and_finalized_read_only(self) -> None:
        owner, lease = self.acquire("delivery", "thread-a", TOKEN_A)
        fence = self.run_fence("thread-a", TOKEN_A, owner, lease)
        control.run_begin(
            self.root,
            "run-a",
            "delivery",
            "arc-daily-improvement",
            "delivery",
            {"base": "abc"},
            **fence,
            now=self.now,
        )
        control.run_event(
            self.root,
            "run-a",
            "selected",
            {"issue": "ROW-1"},
            **fence,
            now=self.now + timedelta(seconds=1),
        )
        control.run_finish(
            self.root,
            "run-a",
            "completed",
            {"selected_family": "runtime-reliability"},
            **fence,
            now=self.now + timedelta(seconds=2),
        )

        verified = control.verify_run(self.root, "run-a")
        self.assertEqual(3, verified["events"])
        self.assertTrue(verified["finalized"])
        mode = stat.S_IMODE(control.run_path(self.root, "run-a").stat().st_mode)
        self.assertEqual(0, mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        with self.assertRaisesRegex(control.ControlError, "already finalized"):
            control.run_event(self.root, "run-a", "late", {}, **fence, now=self.now)

        records = control._read_jsonl(control.run_path(self.root, "run-a"))
        immutable = {
            key: records[0][key] for key in control.RUN_IDENTITY_KEYS
        }
        self.assertTrue(
            all({key: event[key] for key in immutable} == immutable for event in records)
        )
        self.assertEqual(control.run_token_digest(TOKEN_A), immutable["lease_token_digest"])

    def test_run_chain_detects_committed_mutation(self) -> None:
        owner, lease = self.acquire("delivery", "thread-a", TOKEN_A)
        fence = self.run_fence("thread-a", TOKEN_A, owner, lease)
        control.run_begin(
            self.root,
            "run-a",
            "delivery",
            "arc-daily-improvement",
            "delivery",
            {},
            **fence,
            now=self.now,
        )
        path = control.run_path(self.root, "run-a")
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.replace("run_started", "run_changed"), encoding="utf-8")
        with self.assertRaisesRegex(control.ControlError, "event-hash mismatch"):
            control.verify_run(self.root, "run-a")

    def test_review_backoff_and_exact_head_isolation(self) -> None:
        head_a = "a" * 40
        head_b = "b" * 40
        owner, lease = self.acquire("review", "review-run-a", TOKEN_REVIEW)
        self.create_context("review", "review-run-a", TOKEN_REVIEW, owner, lease)
        claimed, _ = control.review_claim(
            self.root,
            "arc-edge/arc-uas",
            314,
            head_a,
            "review-run-a",
            self.root,
            TOKEN_REVIEW,
            now=self.now,
        )
        self.assertTrue(claimed)
        control.review_record(
            self.root,
            "arc-edge/arc-uas",
            314,
            head_a,
            "timeout",
            {"failure_class": "timeout"},
            240,
            transition="active-claim",
            run_id="review-run-a",
            cwd=self.root,
            context_token=TOKEN_REVIEW,
            now=self.now,
        )
        eligible, state = control.review_eligibility(
            self.root,
            "arc-edge/arc-uas",
            314,
            head_a,
            now=self.now + timedelta(seconds=120),
        )
        self.assertFalse(eligible)
        self.assertEqual("retry-backoff", state["reason"])
        eligible, _ = control.review_eligibility(
            self.root,
            "arc-edge/arc-uas",
            314,
            head_a,
            now=self.now + timedelta(seconds=241),
        )
        self.assertTrue(eligible)
        new_head_eligible, state = control.review_eligibility(
            self.root, "arc-edge/arc-uas", 314, head_b, now=self.now
        )
        self.assertTrue(new_head_eligible)
        self.assertEqual("unseen-head", state["reason"])

    def test_posted_review_is_terminal_for_exact_head(self) -> None:
        head = "c" * 40
        owner, lease = self.acquire("review", "review-marker", TOKEN_REVIEW)
        self.create_context("review", "review-marker", TOKEN_REVIEW, owner, lease)
        control.review_record(
            self.root,
            "arc-edge/arc-uas",
            1,
            head,
            "posted",
            {
                "url": "https://github.com/arc-edge/arc-uas/pull/1",
                "source": "github-marker-reconciliation",
            },
            None,
            transition="active-context",
            run_id="review-marker",
            cwd=self.root,
            context_token=TOKEN_REVIEW,
            now=self.now,
        )
        eligible, state = control.review_eligibility(
            self.root,
            "arc-edge/arc-uas",
            1,
            head,
            now=self.now,
            marker_present=True,
        )
        self.assertFalse(eligible)
        self.assertEqual("github-marker-present", state["reason"])

        eligible_without_marker, missing_marker = control.review_eligibility(
            self.root, "arc-edge/arc-uas", 1, head, now=self.now
        )
        self.assertTrue(eligible_without_marker)
        self.assertEqual("local-posted-state-without-github-marker", missing_marker["reason"])

    def test_unresolved_feedback_overrides_same_head_marker(self) -> None:
        head = "e" * 40
        eligible, state = control.review_eligibility(
            self.root,
            "arc-edge/arc-uas",
            3,
            head,
            now=self.now,
            marker_present=True,
            unresolved_feedback=True,
        )
        self.assertTrue(eligible)
        self.assertEqual("unresolved-feedback", state["reason"])

    def test_repository_repair_overrides_same_head_marker(self) -> None:
        head = "9" * 40
        eligible, state = control.review_eligibility(
            self.root,
            "arc-edge/arc-uas",
            4,
            head,
            now=self.now,
            marker_present=True,
            repair_needed=True,
        )
        self.assertTrue(eligible)
        self.assertEqual("repair-needed", state["reason"])

    def test_three_review_failures_stop_retrying_same_head(self) -> None:
        head = "d" * 40
        for index in range(3):
            attempt_now = self.now + timedelta(hours=index * 13)
            run_id = f"review-run-{index}"
            token = f"{index + 1:032x}"
            owner_id, lease = self.acquire(
                "review", run_id, token, ttl=60, now=attempt_now
            )
            self.create_context(
                "review", run_id, token, owner_id, lease, now=attempt_now
            )
            claimed, _ = control.review_claim(
                self.root,
                "arc-edge/arc-uas",
                2,
                head,
                run_id,
                self.root,
                token,
                now=attempt_now,
            )
            self.assertTrue(claimed)
            result = control.review_record(
                self.root,
                "arc-edge/arc-uas",
                2,
                head,
                "timeout",
                {"attempt": index + 1},
                None,
                transition="active-claim",
                run_id=run_id,
                cwd=self.root,
                context_token=token,
                now=attempt_now,
            )
        self.assertEqual("needs-human", result["event"]["event_type"])
        eligible, state = control.review_eligibility(
            self.root,
            "arc-edge/arc-uas",
            2,
            head,
            now=self.now + timedelta(days=10),
        )
        self.assertFalse(eligible)
        self.assertEqual("head-already-needs-human", state["reason"])

    def test_architecture_resurrection_requires_runtime_and_authority(self) -> None:
        with self.assertRaisesRegex(
            control.ControlError, "requires an architecture_resurrection assessment"
        ):
            control.architecture_gate({})

        stale_only = {
            "architecture_resurrection": {
                "suspected": True,
                "classification_rationale": "The proposal would restore an old container path.",
                "absence_explained": "The current native path replaced the old container.",
                "absence_status": "superseded-migration",
                "canonical_fit": "The native bridge is the current canonical path.",
                "canonical_fit_status": "competing-path",
                "signals": [
                    {
                        "kind": "historical-doc",
                        "source": "repo:docs/old-docker.md",
                        "evidence": "An old Docker README exists.",
                        "conclusion": "context-only",
                    }
                ],
            }
        }
        allowed, reasons = control.architecture_gate(stale_only)
        self.assertFalse(allowed)
        self.assertIn("no current executable/runtime signal establishes a supported path", reasons)
        self.assertIn("no current authority signal establishes intended ownership", reasons)

        supported = {
            "architecture_resurrection": {
                "suspected": True,
                "classification_rationale": "The proposal restores an omitted deployment target.",
                "absence_explained": "A packaging regression omitted the still-supported target.",
                "absence_status": "accidental-regression",
                "canonical_fit": "The existing service remains the sole runtime owner.",
                "canonical_fit_status": "fits-current-canonical-owner",
                "signals": [
                    {
                        "kind": "current-deployment",
                        "source": "repo:infra/compose.yml",
                        "evidence": "Current compose references it.",
                        "conclusion": "supports-current-path",
                    },
                    {
                        "kind": "approved-spec",
                        "source": "openspec:runtime",
                        "evidence": "The approved runtime spec requires it.",
                        "conclusion": "supports-intended-owner",
                    },
                ],
            }
        }
        self.assertEqual((True, []), control.architecture_gate(supported))

        same_source = json.loads(json.dumps(supported))
        same_source["architecture_resurrection"]["signals"][1]["source"] = (
            "repo:infra/compose.yml"
        )
        allowed, reasons = control.architecture_gate(same_source)
        self.assertFalse(allowed)
        self.assertIn(
            "runtime and authority evidence do not come from independent sources", reasons
        )

        for anchored_source in (
            "repo:infra/compose.yml#runtime",
            "repo:infra/compose.yml:20",
        ):
            anchored_same_source = json.loads(json.dumps(supported))
            anchored_same_source["architecture_resurrection"]["signals"][0][
                "source"
            ] = anchored_source
            with self.assertRaisesRegex(control.ControlError, "anchor-free"):
                control.architecture_gate(anchored_same_source)

        for alternate_spelling in (
            "repo:./infra/compose.yml",
            "repo:infra//compose.yml",
            "repo:infra/../infra/compose.yml",
        ):
            malformed_identity = json.loads(json.dumps(supported))
            malformed_identity["architecture_resurrection"]["signals"][0][
                "source"
            ] = alternate_spelling
            with self.assertRaisesRegex(control.ControlError, "normalized relative"):
                control.architecture_gate(malformed_identity)

        malformed_source = json.loads(json.dumps(supported))
        malformed_source["architecture_resurrection"]["signals"][0]["source"] = (
            "current compose, trust me"
        )
        with self.assertRaisesRegex(control.ControlError, "anchor-free"):
            control.architecture_gate(malformed_source)

        contradictory = json.loads(json.dumps(supported))
        contradictory["architecture_resurrection"].update(
            {
                "absence_explained": "The path was intentionally retired and has no callers.",
                "absence_status": "intentional-retirement",
                "canonical_fit": "This would create a competing non-canonical path.",
                "canonical_fit_status": "competing-path",
            }
        )
        allowed, reasons = control.architecture_gate(contradictory)
        self.assertFalse(allowed)
        self.assertTrue(any("accidental regression" in reason for reason in reasons))
        self.assertTrue(any("current canonical owner" in reason for reason in reasons))

        negated_signal = json.loads(json.dumps(supported))
        negated_signal["architecture_resurrection"]["signals"][0]["conclusion"] = (
            "does-not-support-current-path"
        )
        allowed, reasons = control.architecture_gate(negated_signal)
        self.assertFalse(allowed)
        self.assertIn(
            "no current executable/runtime signal establishes a supported path", reasons
        )

    def test_scoring_prefers_impact_and_penalizes_repeated_easy_family(self) -> None:
        owner, lease = self.acquire("delivery", "history-thread", TOKEN_A)
        fence = self.run_fence("history-thread", TOKEN_A, owner, lease)
        for index in range(3):
            run_id = f"prior-{index}"
            control.run_begin(
                self.root,
                run_id,
                "delivery",
                "arc-daily-improvement",
                "delivery",
                {},
                **fence,
                now=self.now + timedelta(seconds=index * 2),
            )
            control.run_finish(
                self.root,
                run_id,
                "completed",
                {"selected_family": "lint"},
                **fence,
                now=self.now + timedelta(seconds=index * 2 + 1),
            )

        candidates = [
            {
                "id": "ROW-LINT",
                "title": "Clean one warning",
                "family": "lint",
                **candidate_policy("local-maintenance"),
                "leverage": 1,
                "readiness": 3,
                "urgency": 0,
                "evidence": ["Strict lint reports one warning."],
                "success_oracle": "Strict lint passes.",
            },
            {
                "id": "ROW-RUNTIME",
                "title": "Preserve recorder evidence on worker exit",
                "family": "runtime-reliability",
                **candidate_policy("mission-runtime"),
                "leverage": 2,
                "readiness": 2,
                "urgency": 1,
                "evidence": ["A reachable exit returns before evidence is drained."],
                "success_oracle": "A regression test observes the final evidence before exit.",
            },
        ]

        result = control.score_candidates(self.root, candidates)
        self.assertEqual("ROW-RUNTIME", result["selected"]["id"])
        lint = next(item for item in result["ranking"] if item["id"] == "ROW-LINT")
        self.assertEqual(2, lint["repeat_penalty"])

    def test_scoring_filters_non_delivery_history_and_enforces_tiebreaks(self) -> None:
        active_owner, _ = self.acquire("delivery", "active-thread", TOKEN_A)
        contender_owner = self.owner("overlap-thread", TOKEN_C)
        overlap = control.run_overlap(
            self.root,
            "overlap",
            "delivery",
            "arc-daily-improvement",
            "delivery",
            {},
            lease_run_id="overlap-thread",
            owner_id=contender_owner,
            cwd=self.root,
            context_token=TOKEN_C,
            now=self.now,
        )
        self.assertEqual("finished", overlap["state"])
        self.assertNotEqual(active_owner, contender_owner)
        self.assertEqual(3, control.verify_run(self.root, "overlap")["events"])
        self.assertEqual(
            0,
            stat.S_IMODE(control.run_path(self.root, "overlap").stat().st_mode)
            & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH),
        )
        candidates = [
            {
                "id": "NEW",
                "title": "Newer normal priority concern",
                "family": "build-dx",
                **candidate_policy("systemic-engineering"),
                "leverage": 2,
                "readiness": 2,
                "urgency": 0,
                "linear_priority": 3,
                "created_at": "2026-08-12T12:00:00Z",
                "scope_size": 4,
                "evidence": ["evidence"],
                "success_oracle": "oracle",
            },
            {
                "id": "OLD",
                "title": "Older high priority concern",
                "family": "build-dx",
                **candidate_policy("systemic-engineering"),
                "leverage": 2,
                "readiness": 2,
                "urgency": 0,
                "linear_priority": 2,
                "created_at": "2026-08-01T12:00:00Z",
                "scope_size": 9,
                "evidence": ["evidence"],
                "success_oracle": "oracle",
            },
        ]
        result = control.score_candidates(self.root, candidates)
        self.assertEqual([], result["recent_families"])
        self.assertEqual("OLD", result["selected"]["id"])

    def test_lint_score_is_mechanically_capped_below_runtime_value(self) -> None:
        candidates = [
            {
                "id": "LINT",
                "title": "Inflated lint cleanup",
                "family": "developer-experience-alias",
                **candidate_policy("local-maintenance"),
                "leverage": 3,
                "readiness": 3,
                "urgency": 2,
                "evidence": ["warning"],
                "success_oracle": "lint clean",
            },
            {
                "id": "RUNTIME",
                "title": "Reachable mission recovery defect",
                "family": "runtime-reliability",
                **candidate_policy("systemic-engineering"),
                "leverage": 1,
                "readiness": 1,
                "urgency": 0,
                "evidence": ["reachable failure"],
                "success_oracle": "regression passes",
            },
        ]
        result = control.score_candidates(self.root, candidates)
        self.assertEqual("RUNTIME", result["selected"]["id"])
        lint = next(item for item in result["ranking"] if item["id"] == "LINT")
        self.assertEqual(1, lint["dimensions"]["value"])

    def test_state_root_rejects_symlink_component(self) -> None:
        real = self.root / "real"
        real.mkdir()
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(control.ControlError, "contains a symlink"):
            control.state_root(link / "state")

    def test_run_context_is_unique_scoped_and_removable(self) -> None:
        live_now = control.utc_now()
        owner, lease = self.acquire(
            "delivery", "thread-a", TOKEN_A, now=live_now
        )
        control.context_create(
            self.root,
            TOKEN_A,
            "thread-a",
            owner,
            lease["lease"]["generation"],
            "thread-a-owner",
            "delivery",
            self.root,
            now=live_now,
        )
        found = control.context_read(
            self.root,
            TOKEN_A,
            run_id="thread-a",
            scope="delivery",
            cwd=self.root,
        )
        self.assertEqual(owner, found["context"]["owner_id"])
        context_files = list((self.root / "contexts").glob("*.json"))
        self.assertEqual(1, len(context_files))
        self.assertNotIn(TOKEN_A, context_files[0].name)
        self.assertNotIn(TOKEN_A, context_files[0].read_text(encoding="utf-8"))
        self.assertTrue(
            control.has_active_context(
                self.root, "thread-a", "delivery", self.root
            )
        )
        context_parser = control.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            context_parser.parse_args(
                [
                    "context",
                    "active",
                    "--run-id",
                    "thread-a",
                    "--scope",
                    "delivery",
                    "--cwd",
                    str(self.root),
                ]
            )
        with self.assertRaisesRegex(control.ControlError, "different lease scope"):
            control.context_read(
                self.root,
                TOKEN_A,
                run_id="thread-a",
                scope="review",
                cwd=self.root,
            )
        self.assertEqual("deleted", control.context_delete(self.root, TOKEN_A)["state"])
        self.assertFalse(
            control.has_active_context(
                self.root, "thread-a", "delivery", self.root
            )
        )

    def test_review_claim_is_atomic(self) -> None:
        head = "f" * 40
        owner, lease = self.acquire("review", "run-review", TOKEN_REVIEW)
        self.create_context("review", "run-review", TOKEN_REVIEW, owner, lease)
        results: list[bool] = []

        def claim() -> None:
            claimed, _ = control.review_claim(
                self.root,
                "arc-edge/arc-uas",
                9,
                head,
                "run-review",
                self.root,
                TOKEN_REVIEW,
                now=self.now,
            )
            results.append(claimed)

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([False, True], sorted(results))

    def test_review_claim_id_is_not_authority_and_marker_reconciliation_is_fenced(self) -> None:
        head = "7" * 40
        owner, lease = self.acquire("review", "review-thread", TOKEN_REVIEW)
        self.create_context("review", "review-thread", TOKEN_REVIEW, owner, lease)
        queued = control.review_record(
            self.root,
            "arc-edge/arc-uas",
            17,
            head,
            "queued",
            {"source": "publisher"},
            None,
            now=self.now,
        )
        self.assertEqual("recorded", queued["state"])
        claimed, claim = control.review_claim(
            self.root,
            "arc-edge/arc-uas",
            17,
            head,
            "review-thread",
            self.root,
            TOKEN_REVIEW,
            now=self.now,
        )
        self.assertTrue(claimed)

        parser = control.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "review",
                    "record",
                    "--repo",
                    "arc-edge/arc-uas",
                    "--pr",
                    "17",
                    "--head",
                    head,
                    "--status",
                    "timeout",
                    "--claim-id",
                    str(claim["claim_id"]),
                ]
            )
        with self.assertRaises(TypeError):
            control.review_record(
                self.root,
                "arc-edge/arc-uas",
                17,
                head,
                "timeout",
                {},
                None,
                claim_id=claim["claim_id"],
                now=self.now,
            )
        with self.assertRaisesRegex(control.ControlError, "active claim or context"):
            control.review_record(
                self.root,
                "arc-edge/arc-uas",
                17,
                head,
                "timeout",
                {},
                None,
                now=self.now,
            )
        with self.assertRaisesRegex(control.ControlError, "run token does not exist"):
            control.review_record(
                self.root,
                "arc-edge/arc-uas",
                17,
                head,
                "timeout",
                {},
                None,
                transition="active-claim",
                run_id="review-thread",
                cwd=self.root,
                context_token=TOKEN_C,
                now=self.now,
            )
        terminal = control.review_record(
            self.root,
            "arc-edge/arc-uas",
            17,
            head,
            "timeout",
            {},
            None,
            transition="active-claim",
            run_id="review-thread",
            cwd=self.root,
            context_token=TOKEN_REVIEW,
            now=self.now,
        )
        self.assertEqual("timeout", terminal["event"]["event_type"])

        marker_head = "6" * 40
        marker_details = {"source": "github-marker-reconciliation"}
        with self.assertRaisesRegex(control.ControlError, "active claim or context"):
            control.review_record(
                self.root,
                "arc-edge/arc-uas",
                18,
                marker_head,
                "posted",
                marker_details,
                None,
                now=self.now,
            )
        with self.assertRaisesRegex(control.ControlError, "run token does not exist"):
            control.review_record(
                self.root,
                "arc-edge/arc-uas",
                18,
                marker_head,
                "posted",
                marker_details,
                None,
                transition="active-context",
                run_id="review-thread",
                cwd=self.root,
                context_token=TOKEN_C,
                now=self.now,
            )
        reconciled = control.review_record(
            self.root,
            "arc-edge/arc-uas",
            18,
            marker_head,
            "posted",
            marker_details,
            None,
            transition="active-context",
            run_id="review-thread",
            cwd=self.root,
            context_token=TOKEN_REVIEW,
            now=self.now,
        )
        self.assertEqual("posted", reconciled["event"]["event_type"])
        self.assertEqual(
            control.run_token_digest(TOKEN_REVIEW),
            reconciled["event"]["payload"]["context_token_digest"],
        )

    def test_active_review_claim_is_bound_to_current_fenced_context(self) -> None:
        head = "8" * 40
        live_now = control.utc_now()
        owner, lease = self.acquire(
            "review", "thread-review", TOKEN_REVIEW, now=live_now
        )
        control.context_create(
            self.root,
            TOKEN_REVIEW,
            "thread-review",
            owner,
            lease["lease"]["generation"],
            "thread-review-ledger",
            "review",
            self.root,
            now=live_now,
        )
        claimed, claim = control.review_claim(
            self.root,
            "arc-edge/arc-uas",
            12,
            head,
            "thread-review",
            self.root,
            TOKEN_REVIEW,
            now=live_now,
        )
        self.assertTrue(claimed)
        active = control.active_review_claim(
            self.root,
            "arc-edge/arc-uas",
            12,
            head,
            "thread-review",
            self.root,
            TOKEN_REVIEW,
            now=live_now,
        )
        self.assertEqual(claim["claim_id"], active["claim_id"])

        with self.assertRaisesRegex(control.ControlError, "run token does not exist"):
            control.active_review_claim(
                self.root,
                "arc-edge/arc-uas",
                12,
                head,
                "thread-review",
                self.root,
                "c" * 32,
            )


if __name__ == "__main__":
    unittest.main()
