---
name: arc-improve
description: Take one ARC repository improvement from a Linear issue or fresh ARC Steward finding through evidence gathering, OpenSpec-aware design, implementation, validation, and at most one draft pull request. Use when Bobby asks to improve ARC, work an ARC issue, turn a Steward finding into a solution, or run the scheduled ARC Improvement delivery pipeline. Existing pull-request review and remediation belong to $arc-improve-review, never this skill. Never merge, deploy, flash, provision, mutate credentials, or operate a live aircraft.
---

# ARC Improve

Deliver one worthwhile ARC improvement as a small, reviewable draft pull request. Prefer a working solution over a report while preserving ARC's flight-safety and evidence requirements.

## Choose a delivery mode

- **Interactive delivery:** honor Bobby's explicit issue or finding. Do not autonomously select or publish unless his request grants that authority.
- **Scheduled delivery:** own the `delivery` lease, reconcile Linear, select and implement one concern, publish at most one new draft pull request, enqueue its exact head for review, and finish. Never invoke Claude or modify a previously published PR in this mode.

If the request is to review, repair, or address feedback on an already-published `codex/arc-improve/` pull request, stop this workflow and use `$arc-improve-review`. A clean review queue never blocks delivery, and a delivery run never waits for Claude.

In scheduled delivery mode, the automation prompt's first action is the start helper. Capture its token before any further command. Use that token on every checkpoint and finalization call described below.

## Establish authority and context

1. Resolve the repository root and read the complete governing `AGENTS.md` files.
2. Read ARC Steward's private [Product Soul](../arc-steward/references/product-soul.md), [Engineering Constitution](../arc-steward/references/engineering-constitution.md), and [decision policy](../arc-steward/references/arc-policy.md). Treat missing local doctrine as a blocker to autonomous selection, not permission to invent it. These files guide Bobby's private workflow and do not replace repository policy or human approval.
3. Record the base revision, branch, submodules when relevant, and working-tree state. Preserve every pre-existing change. Use an isolated worktree for scheduled or publishing work.
4. For scheduled work, run this skill's `scripts/refresh-worktree-origin-main.sh` before repository investigation or any Linear/GitHub activity. It refreshes only the superproject: never initialize, fetch, or switch submodules while establishing the base. Independently verify that detached `HEAD` equals fetched `refs/remotes/origin/main`. Never move the developer's local `main`; if freshness cannot be proven, stop with a setup error.
5. Read [references/execution-policy.md](references/execution-policy.md). Apply its safety boundary without exposing internal process codes or bespoke labels to Linear users.
6. For scheduled work, require `CODEX_THREAD_ID` and use `scripts/automation_control.py` through the bundled start/checkpoint/finish scripts. The start helper creates a unique owner nonce plus a monotonically increasing lease generation and prints `ARC_IMPROVE_RUN_TOKEN=<RUN_TOKEN>`. Capture that capability token and pass it explicitly as the checkpoint helper's third argument, the publisher's `--run-context-token`, and the finish helper's fourth argument; a duplicate invocation with the same task id cannot discover or join the owner. Exit `75` from start means this invocation already finalized `skipped-overlap`; stop normally and do not inspect the repository or external systems. Before any repository inspection or Linear/GitHub access, assert that this run owns the mode's lease. Record phase boundaries and the final outcome in the hash-chained run ledger under `${ARC_IMPROVE_STATE_DIR:-${CODEX_HOME:-~/.codex}/arc-improve}`. Never use an automation's shared `memory.md` as operational state.

## Reconcile pull requests with Linear

In scheduled delivery mode, reconcile delivery state before selecting new work:

1. List ordinary issues in the configured ARC project that are currently `In Review` and reference one or more `codex/arc-improve/` pull requests. Read the state of every linked ARC Improve pull request.
2. If any linked pull request remains open, leave the issue `In Review`.
3. If at least one linked pull request merged and none remain open, add a concise comment with the merged pull request and merge commit when available, then move the issue to the project's normal `Done` state.
4. If every linked pull request closed without merging, add a concise comment and return the issue to `Todo` so it can be reconsidered.
5. Make reconciliation idempotent: inspect current status and existing comments before writing; do not repeat a comment or transition already applied. Never overwrite an issue that a human has already moved out of `In Review`.
6. Leave ambiguous cases unchanged and report the smallest human decision needed. Reconciliation does not consume the run's delivery or learning budget.

## Select one concern

Honor an explicit issue or finding first. Normalize it with [references/work-contract.md](references/work-contract.md), deriving repository facts rather than asking Bobby to complete a form.

In scheduled delivery mode:

1. Inspect open `codex/arc-improve/` pull requests only for reconciliation, duplicate ownership, and current queue context. Do not invoke Claude, address review comments, repair CI, push to their branches, or let clean open PRs block new delivery; those belong to scheduled review mode.
2. Gather bounded ordinary Linear `Todo` issues that have no open pull request. Exclude duplicates, unreviewable scope, unavailable evidence, and decision-dependent work before comparing candidates.
3. Read ARC Steward's [selection policy](../arc-steward/references/selection-policy.md). Score qualified candidates with `scripts/automation_control.py score --input <candidate-json>` and record the compact ranking. Every candidate must state a mechanically allowlisted impact class with rationale and an explicit architecture-resurrection assessment. The helper derives value from impact class, then compares leverage, readiness, urgency, and the last three delivery families; do not expose these internal fields as Linear or GitHub labels.
4. Choose the highest eligible result. An issue Bobby explicitly selected still wins. Existing Linear priority and age break close ties. Easy lint must not outrank a qualified mission, runtime, safety, architecture, simulation, or systemic DX defect merely because it is easier.
5. If no eligible `Todo` exists, invoke `$arc-steward` on one bounded capability slice and qualify one fresh candidate. Search Linear and open pull requests for the same problem before creating anything. Create at most one normal issue using ordinary status, priority, and ownership fields; do not create workflow-specific labels.
6. Work a newly discovered issue immediately only when its problem, scope, intended behavior, architecture-resurrection status, and local success evidence are clear. Leave ambiguous or decision-dependent work in the backlog with the smallest human decision needed.

Select exactly one concern. Do not bundle nearby cleanup or create an issue merely to make a scheduled run look productive.

## Keep delivery and learning separate

- **Delivery budget:** advance at most one selected concern and open at most one draft pull request per run.
- **Housekeeping budget:** reconciliation, clean-PR inspection, and one retry of a confirmed external CI failure do not consume the delivery budget and must not prevent Todo selection or fresh Steward research.
- **Learning budget:** when Linear writes are authorized, create or update at most two ordinary `Todo` follow-up issues for consequential problems discovered while delivering the selected concern.
- Deduplicate every follow-up against existing Linear issues and open pull requests. Require concrete evidence, material impact, a bounded minimum intervention, and a success oracle. Do not file speculative smells, generic cleanup wishes, or an issue solely because a check was slow.
- Keep follow-ups independent from the active diff. Do not implement them, move them to `In Review`, or widen the current pull request. If a discovery blocks the selected concern's correctness, resolve it within the same concern when cohesive or stop with the blocker; do not disguise it as optional follow-up work.
- Preserve evidence while it is fresh. Include created or updated follow-up issues in the final result so later scheduled runs can select them before searching for new work.

## Explore before editing

1. Reproduce or re-trace the problem at the current revision. Return `no-change` when it is stale, unsupported, already resolved, or intentional.
2. Use the code-review graph before filesystem search for code and runtime relationships. Fall back promptly for prose, YAML, Jinja, shell, generated manifests, or unindexed surfaces.
3. Invoke `$openspec-explore` as a distinct read-only design phase. Prefer a clean-context read-only subagent when available so Explore's non-implementation boundary remains unambiguous. Exit Explore before any implementation.
4. Trace the canonical runtime or developer workflow, test the strongest competing explanation, identify behavior that must not change, and define the pre-change success oracle.
5. Before editing, run `scripts/publish_draft_pr.py --repo <worktree> --preflight-path <likely-path>` for every likely changed path. Path classification creates evidence obligations rather than an eligibility denylist. Re-run preflight if the scope changes.
6. Read ARC Steward's [architecture-resurrection gate](../arc-steward/references/architecture-resurrection.md). Apply it before any change that restores or promotes a dormant, deleted, legacy, deprecated, unsupported, feature-gated, or apparently unused path. Old files, docs, tests, history, and OpenSpec alone are insufficient. If current executable support, current authority, independent corroboration, the reason for absence, and canonical fit are not established, do not implement; create or update one ordinary issue with the smallest decision needed.

## Use OpenSpec deliberately

1. Search active and archived OpenSpec material for the affected capability before creating a change. Update or continue the applicable change rather than creating a duplicate.
2. Use `$openspec-propose` for significant behavior changes, architectural or cross-service changes, infrastructure changes, and all flight-critical work, as required by `AGENTS.md`.
3. Use `$openspec-apply-change` only after the required artifacts are coherent and any mandatory human design gate is satisfied. If implementation invalidates the design, update the artifacts before continuing.
4. Skip formal proposal artifacts for small bug fixes, documentation corrections, tests, and genuinely mechanical cleanup when `AGENTS.md` permits it. OpenSpec-aware does not mean OpenSpec-for-everything.
5. When this run creates and fully implements an OpenSpec change, validate it, sync its delta specs, and archive it in the same branch when the archive workflow can complete without an unresolved choice. Do not create new completed-but-dangling changes.

When OpenSpec lifecycle debt is systemic, create one inventory issue rather than one issue per active change. Classify the set as complete and archive-ready, complete but drifted, incomplete and relevant, abandoned or superseded, or owner-required/flight-critical. A single delivery concern may clean up a small homogeneous batch of no more than five independently verified, non-flight-critical, archive-ready changes when the resulting diff remains easy to review. Handle drifted, ambiguous, behavior-changing, and flight-critical changes individually through the normal design and ownership gates.

## Implement the minimum cohesive solution

1. Route implementation through the narrowest applicable skill such as `fix-bug` or the repository's `openspec-apply-change`.
2. Keep one implementation writer. Use clean-context agents only for separable read-only tracing, contract review, test assurance, or changed-files review.
3. Add the strongest proportionate durable ratchet: a regression test, contract test, architecture check, deterministic scenario, replay fixture, generated drift check, or corrected canonical runbook.
4. Update affected specifications, documentation, and operator behavior in the same reviewable unit. Remove a superseded path only after reachability, platform, feature, deployment, and rollback checks.

## Exercise the local environment safely

- Read the relevant `justfile` recipe before running it. Start with the smallest meaningful check and then follow the repository's validation ladder.
- Run local builds, tests, disconnected development stacks, replay, and disconnected SITL when they materially improve evidence. Bound runtime, capture useful failure output outside the repository, and clean up processes started by the run.
- Never contact or operate a live aircraft from this skill. Never run `ssh`, `scp`, `rsync`, `ansible`, deploy, flash, provision, credential, identity, model-install, fleet mutation, or hardware-target commands.
- Never run `just push`; it is a hardware/deployment operation. A Git branch push is permitted only by the publishing gate below.

## Validate independently

1. Read the relevant `justfile` recipes and `.github/workflows/ci.yml`; do not guess validation commands.
2. Prove the new ratchet detects the old defect when the contract requires failing-then-passing evidence.
3. Run risk-proportionate focused checks, formatting, and repository readiness checks. Satisfy every risk profile returned by publisher preflight. Flight behavior requires the `AGENTS.md`-mandated SITL evidence and a named accountable human owner before merge. Record it mechanically as `SITL: PASS; scenario=<regression-relevant scenario>; result=<observed result>` and `Flight owner: <named human or @handle>`; placeholders such as `not run`, `TBD`, or `required before merge` are publication failures.
4. Run an independent changed-files review matched to the change: contracts, authorization, security, concurrency, data integrity, accessibility, failure handling, or performance as relevant.
5. Inspect the final diff for scope creep, generated residue, secrets, policy weakening, stale parallel paths, and unrelated user changes.

Failed, skipped, unavailable, or flaky required validation means no pull request. Preserve the isolated patch for diagnosis and report the exact missing evidence.

## Publish one draft pull request

Publish only when the user or scheduled-task prompt explicitly authorizes Linear and GitHub writes.

1. Confirm the final diff contains one concern and satisfies [references/evidence-manifest.md](references/evidence-manifest.md).
2. For scheduled delivery runs, use `scripts/publish_draft_pr.py --require-lease delivery --run-context-token <RUN_TOKEN>`; the publisher resolves only that exact invocation context, then fences atomic remote branch creation, draft-PR creation, exact-head verification, and review queueing with its unique owner and lease generation. Optional explicit `--lease-owner-id` and `--lease-generation` values are accepted only when both match that context. The publisher also enforces the isolated-worktree, repository, risk-evidence, secret-material, size, branch, commit, and draft-PR boundary. Pass one `--evidence-profile <profile>` for every profile returned by preflight only after its required evidence is obtained and recorded. Run with `--validate-only` first, then without that flag only after validation and independent review pass.
3. In scheduled mode, leave the final validated diff staged on detached, freshly verified `origin/main`. Pass the target `codex/arc-improve/<short-name>` branch and one Conventional Commit message to the publisher; it alone creates the local branch and single commit before atomically creating the remote branch and at most one draft pull request. Never pre-create the branch or commit in scheduled mode. Never merge or mark a flight-critical change ready for merge.
4. Link the Linear issue and applicable OpenSpec change. Move the issue to the project's normal `In Review` state and add the pull-request link without replacing human-authored content.
5. Do not publish partial work after an exception, timeout, failed check, stuck loop, or exhausted budget.

After publishing, inspect the publisher result. `review_queue` must be `recorded` or `already-recorded`; otherwise record the queue warning and published PR/head as a partial local-ledger failure in the immutable run result. Do not retry publication—the GitHub PR is authoritative and the review poller independently discovers unmarked open heads. Do not invoke Claude or wait for review in scheduled delivery mode. Do not mark the draft ready for review or merge it.

Return `completed`, `no-change`, `needs-human`, `validation-failed`, or `policy-denied`. For scheduled work, checkpoint between selection, design, implementation, validation, and publication with `scheduled-run-checkpoint.sh delivery <phase> <RUN_TOKEN>`, then finalize and release on every normal outcome with `finish-scheduled-run.sh delivery <status> '<summary-json>' <RUN_TOKEN>`. Lead with the result, reconciliation performed, selected concern, evidence actually obtained, durable follow-ups, and exactly one remaining human action.
