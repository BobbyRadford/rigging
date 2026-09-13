# Observation lenses

Choose one lens per run unless Bobby specifies a narrower target. Rotate based on the last three immutable delivery records and previously qualified findings rather than scanning every lens each time. Repeating a lens is allowed when a current incident or clearly higher-value qualified concern justifies it.

## Product and capability truth

- Compare reachable behavior with the Product Soul and flagship mission slices.
- Identify experimental or aspirational paths presented as supported.
- Distinguish important-but-weak capabilities from well-built but misaligned surfaces.

## Architecture and canonical paths

- Trace competing ingress, command, configuration, mission, world-state, and UI representations.
- Find boundary violations, cycles, oversized authority hubs, hidden cross-service contracts, and copy-modify divergence.
- Treat line count and graph centrality as investigation triggers, not architectural verdicts.
- Apply the architecture-resurrection gate before recommending that a dormant, removed, legacy, deprecated, unsupported, or feature-gated path return.

## Flight assurance

- Map a command or failsafe lifecycle to its invariant, validation gates, transitions, acknowledgements, timeouts, recovery, and evidence.
- Look for missing negative scenarios, fault-manifestation proof, stale intent handling, restart/link-loss behavior, and simulation credibility gaps.
- Never run hardware or flight operations during a Steward observation.

## Tests, simulation, and evidence

- Identify important decisions without effective tests, mocks that bypass the real boundary, ignored/flaky checks, stale fixtures, or tests that could not fail meaningfully.
- Compare unit, contract, replay, SITL, bench, and flight evidence without treating them as interchangeable.
- Verify that failure artifacts are reproducible and revision-scoped.

## Developer experience and build

- Inspect clean-checkout setup, toolchain pinning, `just`/CI consistency, platform assumptions, local simulation entry points, warnings, and slow or fragile feedback loops.
- Prefer a reproducible canonical workflow over adding another convenience path.

## Documentation and OpenSpec

- Find broken references, outdated commands, contradictory procedures, unlabeled aspiration, abandoned active changes, completion/archive drift, overlapping specifications, and behavior changes whose docs/specs did not move.
- Preserve useful historical material with status and migration context when deletion would erase provenance.

## Deployment and operational recovery

- Compare build lists, platform descriptors, compose/Ansible deployment declarations, service ownership, startup dependencies, health signals, rollback, and failure recovery.
- Treat stale Dockerfiles, disabled jobs, old deployment docs, historical tests, and dangling OpenSpec changes as leads rather than proof that an old topology should be restored.
- Observation remains source-only unless Bobby separately authorizes access to a named live target.

## Security and trust boundaries

- Inspect command/config/provisioning identity, authorization, target scope, freshness/replay, rate/resource limits, secret handling, and default reachability.
- Do not use or reveal credentials and do not probe deployed networks in an unattended or ordinary observation run.
