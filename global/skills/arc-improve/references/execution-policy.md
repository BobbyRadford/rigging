# ARC Improve execution policy

## Autonomous implementation and draft publication

Proceed without a pre-implementation human decision when the concern is bounded, intended behavior is clear, and meaningful local evidence can validate it. No repository area is categorically excluded from investigation, implementation, or a draft pull request. Paths classify risk and raise evidence requirements; they do not decide whether ARC may improve that area.

Use OpenSpec before implementing significant behavior, architecture, cross-service contracts, persistence, infrastructure, deployment design, or flight-critical changes. Proceed autonomously only when existing doctrine, specifications, and evidence resolve the intended behavior; otherwise request the smallest decision.

Flight-control, deployment, hardware, CI, policy, submodule, and credential-handling changes may be implemented and published as drafts. Apply the risk-specific evidence returned by the publisher preflight. Credential-handling code is allowed; actual secret values and credential or identity mutation are not.

## Human decisions and merge gates

Stop before implementation only when requirements or ownership are ambiguous at a flight-safety, security, configuration-authority, edge/ground, Zenoh-topology, platform, deployment, or operational trust boundary. A second AI review is not a substitute for a missing product or safety decision.

Production flight changes require an accountable human flight owner before merge, not before Codex can prepare a draft. They also require the repository-mandated OpenSpec and regression-relevant disconnected SITL evidence. Never mark a flight-critical draft ready for merge.

## Prohibited operations

Never deploy, flash, provision, install operational models or data, mutate credentials or identity, contact a live fleet, operate an aircraft, approve or merge a pull request, or weaken a required check. Never edit the policy governing a delivery run merely to bypass its result; policy changes must be the explicit concern authorized by Bobby in a separate run.

Treat local development and disconnected simulation as different from live operations. Inspect every `just` recipe before execution. Global allow rules and previous interactive approvals do not grant background authority.

## Working tree and publishing

- Use an isolated worktree for scheduled work and publication.
- Preserve existing work; never reset, clean, stash, amend, rewrite history, or switch another contributor's branch for convenience.
- Keep generated and temporary outputs outside the repository as required by `AGENTS.md`.
- Advance no more than one selected issue and create no more than one draft pull request per scheduled run. Within the separate learning budget, create or update at most two ordinary follow-up issues without implementing them.
- Do not impose a global limit on open ARC Improve pull requests. Existing drafts never block later scheduled delivery runs. The separate review worker prioritizes actionable failures and feedback within its own queue.
- Keep scheduled delivery and review/remediation in separately leased skills and runs. `$arc-improve` never waits for Claude or mutates an already-published PR; `$arc-improve-review` never selects new delivery work or opens a PR.
- Publish only after every required local check passes and the final diff is independently reviewed.
- Run publisher preflight against the likely paths before implementation. Treat its risk profiles as evidence obligations, update preflight when scope changes, and supply every validated profile at final publication.
- Deny actual secret material and unsafe operations, not broad source-code paths. A risk-bearing path with complete evidence may be published as a draft.
- Never convert a timeout, exception, stuck loop, partial patch, or missing check into a pull request.
