# ARC improvement work contract

Turn the issue or finding into a short working agreement before editing. Keep it in prose; do not require a custom Linear template or expose internal classifications.

Establish:

- the falsifiable problem and evidence supporting it;
- its relationship to ARC's Product Soul or engineering reliability;
- the canonical capability, owner, and runtime or developer path;
- the smallest coherent solution and explicit non-goals;
- behavior and safety invariants that must not change;
- the pre-change oracle and validation required to show improvement;
- whether an existing OpenSpec applies or a new proposal is required;
- whether a human product, architecture, security, or flight decision is required before implementation, or only accountable review before merge;
- whether the change triggers the architecture-resurrection gate by restoring or promoting a dormant, removed, legacy, deprecated, unsupported, feature-gated, or apparently unused path; if so, the current executable and authority signals with independent sources and positive structured conclusions, `absence_status: accidental-regression`, and `canonical_fit_status: fits-current-canonical-owner`; any retirement, migration, competing path, or ambiguity requires a human decision;
- the publisher preflight risk profiles and concrete evidence required for each;
- the rollback, removal proof, or durable ratchet appropriate to the change.

Derive facts that are evident from the repository and mark material assumptions in the pull request. Stop only when the missing decision can change product behavior, architecture, flight safety, trust boundaries, or the meaning of success.

For scheduled selection, require enough evidence to distinguish a real problem from a large file, TODO, duplicate-looking block, stale document, or unused-symbol report. If evidence cannot support one bounded change, leave a concise Linear issue in Backlog rather than manufacturing a patch.

An issue is not eligible for autonomous implementation merely because it exists. It must be reproducible, reviewable as one concern, locally verifiable, and inside the execution policy. Prefer `no-change` over speculative cleanup.
