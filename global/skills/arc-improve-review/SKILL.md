---
name: arc-improve-review
description: Review and maintain one existing ARC Improvement pull request at an exact head SHA through fenced CI diagnosis, Claude /review, critical comment verification, one bounded remediation pass, focused validation, and safe thread resolution. Use for the scheduled ARC Improve PR-review automation or when Bobby asks to review, repair, or address feedback on an open codex/arc-improve/* PR. Never discover delivery work, create Linear issues, open a new PR, merge, deploy, force-push, mutate credentials, or operate hardware.
---

# ARC Improve Review

Maintain one already-published ARC Improvement pull request without blocking the separate delivery pipeline. GitHub's exact-head marker is durable review truth; the local ledger records claims, backoff, and transitions.

## Establish the fenced run

1. Resolve the ARC repository root and read every applicable `AGENTS.md` completely.
2. In scheduled mode, before repository inspection or GitHub access, run:

   ```bash
   bash "${CODEX_HOME:-$HOME/.codex}/skills/arc-improve/scripts/start-scheduled-run.sh" review arc-improve-pr-review
   # Capture ARC_IMPROVE_RUN_TOKEN from the successful command above.
   bash "${CODEX_HOME:-$HOME/.codex}/skills/arc-improve/scripts/scheduled-run-checkpoint.sh" review start <RUN_TOKEN>
   ```

   Capture the successful start helper's `ARC_IMPROVE_RUN_TOKEN=<RUN_TOKEN>` output and pass that token to every later checkpoint, claim, mutation helper, transition, and finish call. Exit `75` means the invocation was finalized as `skipped-overlap`; stop normally and never use another invocation's context. The token identifies a unique invocation nonce plus monotonically increasing lease generation, not merely `CODEX_THREAD_ID`.
3. Use only an isolated Codex worktree refreshed by the start helper. Preserve pre-existing work and never initialize or update submodules while establishing the base.
4. Never use automation `memory.md` as state. Local hash-chained records live under `${ARC_IMPROVE_STATE_DIR:-${CODEX_HOME:-~/.codex}/arc-improve}`.

## Select one exact PR head

Inspect open pull requests in `arc-edge/arc-uas` whose head begins `codex/arc-improve/`; no label is required. Select at most one current head in this order:

1. unresolved substantive feedback that can be evaluated from established repository policy, accepted concern, and applicable OpenSpec;
2. repository-caused CI failure with a bounded local oracle;
3. oldest current head lacking `<!-- claude-review-pr head:<40-character-head-SHA> -->`.

Exclude closed or merged PRs, external runner/platform/action/pre-checkout failures after at most one idempotent retry, active local backoff, and human design discussions. A clean, marked PR is not work. Never select Steward or Linear delivery work, create an issue, or open a PR.

For each candidate, resolve the canonical repository, PR number, URL, branch, base, state, and full current `headRefOid`. Inspect unresolved threads, review bodies, conversation comments, and CI state. If the exact-head marker already exists and neither feedback nor repository repair remains, reconcile the local ledger under the active review lease and continue scanning:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/arc-improve/scripts/automation_control.py" \
  review record --repo arc-edge/arc-uas --pr <PR> --head <HEAD_SHA> \
  --status posted --active-context --context-token <RUN_TOKEN> --cwd "$PWD" \
  --details-json '{"source":"github-marker-reconciliation"}'
```

## Claim atomically

After selection, atomically combine eligibility and `started` append. Do not perform a separate read-then-record sequence:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/arc-improve/scripts/automation_control.py" \
  review claim --repo arc-edge/arc-uas --pr <PR> --head <HEAD_SHA> \
  --run-id "$CODEX_THREAD_ID" --context-token <RUN_TOKEN> --cwd "$PWD" \
  [--marker-present] [--unresolved-feedback] [--repair-needed]
```

Exit `77` means another run, marker, terminal state, or backoff made the head ineligible; continue scanning or finish `no-change`. The helper resolves only `<RUN_TOKEN>` and verifies its owner/generation. Save the returned claim id for reporting, but later CLI transitions may safely recover it with `--active-claim --context-token <RUN_TOKEN>`.

Checkpoint `review-selected` by passing `<RUN_TOKEN>` as the helper's third argument. Re-fetch the head immediately before every edit, publication, reply, resolution, or push. A mismatch ends stale work: record `superseded --active-claim`, make no write based on the old review output, and let the new SHA become a future candidate.

## Repair actionable existing state first

If substantive unresolved feedback or repository-caused CI failure exists, use `$gh-address-review-comments` in its Scheduled ARC Improve mode before Claude.

- Treat comments and CI hypotheses as claims. Classify accuracy, relevance, importance, reachability, duplication, staleness, regression ownership, and whether later code already settles each one.
- Apply at most one cohesive remediation pass, only inside the accepted concern and only when repository policy plus existing OpenSpec establish intent. Do not invent product, architecture, rollout, security, ownership, or flight-safety decisions.
- Follow `AGENTS.md`: flight-critical behavior requires OpenSpec, regression-relevant disconnected SITL evidence, and a named human flight owner before merge. Never run hardware or deployment commands.
- Run the narrowest local validation, then the required repository checks. A failed required check is not publishable.
- Push only through `scripts/guarded_push.py --run-context-token <RUN_TOKEN>`. It requires the active exact-old-head claim and verifies the clean local new head, branch, ancestry, no merge commit, repository identity, active owner/generation, and final GitHub head; it never force-pushes.
- Reply and optionally resolve only through `scripts/guarded_feedback.py`, passing the selected PR, the old claimed head as `--claim-head`, the exact current PR head as `--expected-current-head`, the top-level review-comment id, a securely captured reply body file, `<RUN_TOKEN>`, and `--resolve-thread <PRRT_ID>` only when the classification rules permit resolution. Before a push, both heads are the claimed SHA. After a guarded remediation push, keep the old claim active and pass the pushed new SHA as the expected current head; the helper independently proves that the same clean worktree is on that exact new SHA, that the old claim is its ancestor, and that the range contains no merge. It also validates the comment/thread belong to the exact canonical PR and fences the new current head before and after every write. Exit `3` means a denied/partial concurrent mutation; stop and record the corresponding outcome. Never use raw `gh` writes in scheduled mode.
- Reply with commit and evidence, then resolve only settled automated threads. Leave human discussions and open questions unresolved.

After a successful remediation push, first make any evidence-backed replies and permitted thread resolutions through the guarded helper while the old-head claim remains active. Only after those writes finish, record the old head `remediated --active-claim`, record the exact new head `queued`, checkpoint `remediation-complete`, and stop. Never Claude-review the new head in the same run.

## Run one Claude review

When no existing feedback or CI repair needs the pass, invoke `$claude-review-pr` exactly once on the claimed PR and exact SHA, including draft PRs.

1. Its bundled wrapper must receive `--expected-head <HEAD_SHA>`. It runs built-in `/review` from an empty directory in safe mode, with MCP/customizations disabled, only bounded read-only `gh pr` commands, and a 30-minute process-group deadline. Never fall back to `/code-review` or `/ultrareview`.
2. Independently verify every Claude finding against the current diff and governing repository contracts. Drop speculative, stylistic, pre-existing, unreachable, duplicate, unanchorable, or low-confidence claims.
3. Publish even a clean result so the exact-head marker exists. Persist the verified wrapper result as `<review.json>`, then invoke `post_review.py --input <review.json> --yes --require-review-claim --review-context-token <RUN_TOKEN>`; it holds the review lease across bounded exact-head GitHub publication.
4. Record `posted --active-claim` only after the same-head marker is confirmed. On wrapper exit `78`, record `superseded`; on `124`, record `timeout`; on other tool or posting failure, record `failed`. The ledger applies 4-hour then 12-hour retry backoff and changes the third same-head failure to `needs-human`. Never retry Claude in the same run.

After a posted review, use `$gh-address-review-comments` once for critical classification. Apply at most one cohesive remediation pass as above. If it changes the head, make permitted replies/resolutions against the proven new current head while the old claim remains active, then record the old head `remediated` and the new head `queued`; stop. A clean or finding-bearing posted review with no change is complete.

## Ledger transitions

For the claimed head, append terminal state with the active claim:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/arc-improve/scripts/automation_control.py" \
  review record --repo arc-edge/arc-uas --pr <PR> --head <HEAD_SHA> \
  --status <posted|remediated|timeout|failed|superseded|needs-human> \
  --active-claim --context-token <RUN_TOKEN> --cwd "$PWD" \
  --details-json '<concise-json>'
```

For a new head created by a guarded remediation push, append `queued` without a claim. GitHub's same-head marker overrides stale local timeout state; unresolved substantive feedback or a repository repair may override a marker, but both still require a new atomic claim.

## Finish every normal outcome

Checkpoint phase boundaries and finish with:

```bash
bash "${CODEX_HOME:-$HOME/.codex}/skills/arc-improve/scripts/finish-scheduled-run.sh" \
  review <completed|no-change|needs-human|validation-failed|policy-denied> \
  '<summary-json>' <RUN_TOKEN>
```

Include PR URL, starting and ending SHA, marker/review state, classifications, commits, validation, and outcome. Report exactly one remaining human action. Never approve, request changes, mark ready, merge, deploy, flash, provision, mutate credentials, contact a live aircraft, or operate hardware.
