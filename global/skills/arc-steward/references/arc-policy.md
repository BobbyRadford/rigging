# ARC private decision policy

Use this policy only inside Bobby's ARC Steward and ARC Improve workflows. It guides private product and engineering judgment; it is not repository policy and does not replace team review.

## Required reading order

1. The complete repository `AGENTS.md` instruction chain.
2. [Product Soul](product-soul.md).
3. [Engineering Constitution](engineering-constitution.md).
4. A relevant approved main specification or active OpenSpec change when the bounded capability is specified there.

Directory presence, task checkboxes, age, implementation claims, and this private doctrine do not establish team approval or verified behavior.

## Precedence

1. Legal constraints and flight-safety invariants win.
2. Repository `AGENTS.md` files govern how work may be performed.
3. Product Soul guides Bobby's product-alignment decisions.
4. Engineering Constitution guides Bobby's engineering-quality decisions.
5. Approved specifications govern the behavior of their bounded capabilities.

Do not promote code, tests, docs, an OpenSpec artifact, or this private doctrine to verified implementation truth merely because it exists. Record contradictions as drift.

## Evidence vocabulary

- **Verified** — supported by reproducible evidence appropriate to the claim's risk. Verification is scoped; a unit test does not verify integrated flight behavior.
- **Observed** — directly present in executable code or configuration without sufficient behavioral proof.
- **Claimed** — asserted by documentation, tests, task ledgers, or recorded results whose evidence was not reproduced.
- **Aspirational** — desired future behavior without a demonstrated, reachable implementation.
- **Unknown** — evidence is absent, incomplete, inaccessible, or conflicting.

Time-sensitive findings identify the repository revision and review date. Linear owns observations, priority, ownership, and backlog state. OpenSpec owns bounded change contracts when the repository requires one; it is not the backlog. Pull requests and proportionate evidence demonstrate implemented work.

Assess product alignment and engineering integrity independently. Strongly aligned but weak code is a candidate for characterization, stabilization, simplification, consolidation, or reimplementation behind a safe seam—not automatic acceptance or deletion. Large files, duplicate-looking code, TODOs, and stale documents are leads rather than findings until qualified.

## Flight boundary

Treat `services/flight-control/` and anything that can arm, disarm, move, change mode, execute a mission/pattern, or trigger a failsafe as flight critical. `flight-control` is the sole command originator and MAVLink/PX4 owner.

Do not propose a second command path. Do not infer that a small diff inside a flight-critical capability is mechanically safe. Flight behavior requires an accountable human owner and risk-appropriate SITL evidence before implementation can be accepted.

## Runtime boundaries

Preserve the repository's edge-versus-ground deployment boundary, Zenoh topology, fixed MAVLink UDP local-port rule, platform descriptors, and read-only shared `arc-config` contract. Crossing or changing any of these is at least a boundary finding and cannot be treated as routine cleanup.

## Evidence boundary

Use source and configuration reads, local graph analysis, and explicitly safe local checks. Never treat a mocked unit test as integrated flight evidence. Never claim that SITL proves behavior outside the simulator profile and conditions actually exercised.
