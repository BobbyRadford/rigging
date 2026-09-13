# ARC Engineering Constitution

Status: Bobby's private working direction
Established: 2026-08-05

Document type: private engineering-quality input for Bobby's Codex workflows. Repository `AGENTS.md` files remain the mandatory execution policy and win wherever this private standard would conflict with them.

## Engineering mission

ARC engineering makes the safest correct behavior the easiest behavior to understand, test, operate, and extend. A capability is not complete until it is reproducible, observable, documented, owned, and supported by evidence proportional to its risk.

Product alignment and engineering integrity are independent requirements. A feature can be central to the Product Soul and still be unfit to depend on.

## Non-negotiable priorities

When guidance conflicts:

1. Flight-safety invariants and legal constraints hold.
2. The verified build and test baseline remains green.
3. Canonical architecture and local conventions are followed.
4. Delivery speed is optimized only after the first three.

`AGENTS.md` contains the executable repository rules behind these priorities. This constitution does not weaken them.

## Architecture standard

- Every important responsibility has one authoritative owner.
- Dependencies have an intentional direction. Cycles, reach-through access, and side doors are architectural defects.
- An operation has one canonical production path. A competing path is allowed only during an explicit, owned, time-bounded migration.
- Domain decisions are separated from transport, persistence, rendering, orchestration, and hardware I/O.
- Contracts across process, network, configuration, storage, and language boundaries are typed, versioned where necessary, validated at ingress, and tested at producer and consumer.
- Edge and ground responsibilities remain explicit. Ground-only convenience cannot leak onto constrained aircraft by accident.
- Hardware and accelerator variation comes from platform descriptors and adapters rather than scattered conditionals.
- Runtime state has an explicit owner and cannot be smuggled into desired configuration or another service's storage.
- Failure, cancellation, timeout, restart, and partial-connectivity behavior are part of the design, not follow-up polish.
- Experimental paths are isolated and labeled. They include an owner, hypothesis, time horizon, and promotion or removal criteria.

Large files and functions are audit triggers, not automatic violations. The real tests are cohesion, coupling, independent testability, explicit dependencies, and change blast radius. Splitting one incoherent file into many mutually entangled files is not an improvement.

Structural recovery follows this order:

1. Trace the reachable behavior.
2. State the responsibility and invariants.
3. Add characterization evidence.
4. Establish a stable seam.
5. Move one cohesive responsibility.
6. Compare behavior and failure modes.
7. Remove the superseded path.

## Trust and security standard

Every path that can command an aircraft, mutate desired configuration, provision connectivity, install operational data, retrieve sensitive evidence, or expose a sensor stream has an explicit trust boundary.

- Command and configuration requests carry authenticated principal, role, target device, request identity, freshness, and replay protection appropriate to their transport.
- Authorization is fail-closed and least-privilege. Possession of network reachability or one transport key does not imply authority over every topic or operation.
- Network services bind to the narrowest useful interface and are private by default. Development convenience requires an explicit opt-in before becoming remotely reachable.
- Emergency land, RTL, kill, and safe recovery remain available through deliberately authenticated safety channels; security hardening cannot accidentally strand the aircraft.
- Untrusted messages, streams, uploads, queues, filenames, destinations, and reassembly state are schema-validated, size-bounded, time-bounded, rate-limited where appropriate, and contained to their authorized resources.
- Secrets do not live in source, command arguments, ordinary logs, archives, images, or broadly readable files. Exposure triggers revocation or rotation, not just deletion from the current tree.
- Provisioning and supply-chain inputs are pinned and verified through trusted hashes, signatures, or package repositories. Remote shell installers, mutable branches, and unsigned operational data are not production inputs.
- Privileged services and sockets are minimized. Read-only intent is enforced by the interface or proxy, not inferred from current caller behavior.
- Threat model, deployed reachability, credential lifecycle, migration, and rollback are part of the contract for every control boundary.

Security changes that affect the command path, safe handoffs, networking topology, or on-aircraft trust material receive the same careful integration evidence as other safety-relevant boundary changes.

## Flight-control assurance standard

The target is assurance-grade flight control: deterministic, legible, bounded, fail-safe, and supported by compelling evidence. “Perfect” is an aspiration; confidence must come from explainable architecture and verification rather than the word itself.

`services/flight-control` is the sole authority that originates aircraft motion, arming, disarming, mode changes, mission or pattern execution, and failsafe commands. Other services express intent and supply evidence; they never command PX4 directly or create a parallel safety policy.

Every aircraft action has one auditable lifecycle:

`intent → validation → safety authorization → execution → observation → reconciliation`

For flight-critical behavior:

- Preconditions and invariants are explicit, centralized at the appropriate boundary, and fail closed.
- State transitions are modeled and reviewable rather than emerging from scattered flags and callbacks.
- Units, coordinate frames, reference origins, timestamps, freshness, validity, and uncertainty are represented explicitly and, where practical, enforced by types.
- Missing, stale, late, duplicated, conflicting, and invalid data have deliberate behavior.
- Commands define acceptance, rejection, idempotency, cancellation, timeout, completion, and recovery semantics.
- Retries, queues, memory, allocation, execution time, and failure escalation are bounded on flight-sensitive paths.
- Blocking I/O, unbounded work, and hidden allocation do not enter the flight loop.
- Probabilistic AI output crosses a deterministic policy and safety boundary before it can influence motion.
- Defaults and fallbacks are visible; a degraded path cannot silently claim nominal behavior.
- Safety decisions and command outcomes emit enough causality for replay and incident analysis.
- Changes are reviewed by an accountable human owner and verified at the evidence tier required by `AGENTS.md`.

Flight-control refactoring is never a big-bang rewrite or aesthetic cleanup. Before moving behavior, establish the command trace, hazards, invariants, characterization tests, simulation scenarios, and comparison method. Preserve conservative behavior unless an approved specification intentionally changes it.

## Evidence and testing

Tests form an assurance ladder:

1. Unit tests for deterministic decisions, parsers, state transitions, and math.
2. Contract tests for messages, configuration, APIs, persistence, and service boundaries.
3. Component and integration tests using real cooperating boundaries where practical.
4. Deterministic simulation for vehicle and mission behavior.
5. Fault injection for loss, delay, duplication, restart, stale state, degraded positioning, sensor faults, and partial fleet failure.
6. Replay against recorded missions and incidents.
7. Hardware-in-the-loop or bench validation for behavior simulation cannot represent.
8. Controlled flight evidence for genuinely flight-dependent behavior.

Evidence is scoped to the claim. Test count, coverage, task completion, a mocked happy path, or one successful run is not general acceptance. A useful test must fail when the protected behavior breaks, and safety rules require negative evidence showing unsafe actions are rejected.

Every meaningful failure should leave behind the strongest reasonable durable ratchet: a regression test, contract assertion, static architecture check, simulation scenario, replay fixture, operational guard, or explicit invariant.

## Developer experience and operations

Developer experience is reliability work. The repository should converge on:

- one discoverable command surface for build, test, lint, format, simulation, packaging, deployment, recording, and replay;
- a clean-checkout path to a meaningful local simulation without undocumented machine state;
- pinned, reproducible toolchains and external artifacts;
- fast local feedback separated from comprehensive CI and release gates;
- simulation and replay paths that exercise production contracts rather than bespoke imitations;
- explicit edge, ground, simulator, and hardware targets;
- unmistakable hardware-mutating commands that never run as a side effect of ordinary validation;
- build identity, configuration revision, mission identity, platform identity, and command causality in operational evidence;
- one authoritative troubleshooting path for recurring failures;
- deployment and rollback procedures that are reproducible and target-aware.

If two documented workflows perform the same job differently, one must be named canonical or the divergence must be an explicit supported platform distinction.

## Documentation and specifications

- Documentation states whether it is normative doctrine, a current-state reference, an operational runbook, an experiment, a proposal, or historical material.
- Current-state claims name their scope and last verification point.
- Aspirations are labeled and are not written as implemented facts.
- OpenSpec describes bounded change contracts; it is not an immortal backlog or proof of completion.
- Changes are moved through proposed, implemented, verified, superseded, abandoned, and archived states deliberately.
- Task checkboxes record task assertions. They do not replace executable evidence.
- Generated facts should be generated from their source where practical; hand-maintained duplication needs an owner and drift check.
- A code change updates its affected contracts, runbooks, and operator behavior in the same reviewable unit.

## Code quality

- Names use the domain language consistently.
- Types encode meaningful distinctions instead of relying on strings, booleans, or comments.
- Public interfaces are small and intentional.
- Functions make decisions or perform effects; complex code does not mix both without a seam.
- Errors retain cause and operational context and are neither swallowed nor converted into false success.
- Concurrency ownership, cancellation, ordering, and retry safety are explicit.
- Copy-and-modify implementations are consolidated unless the difference represents an intentional boundary.
- Dead code is removed only after reachability, feature, platform, deployment, and rollback checks.
- Comments explain invariants, trade-offs, and why—not a stale paraphrase of what the code says.
- Warnings, ignored tests, blanket lints, dangerous casts, and TODOs require a reason and an owner or removal condition.

## Capability maturity

When a significant capability needs an explicit maturity claim, use this shared vocabulary:

- **Exploratory** — a bounded experiment; not a supported production promise.
- **Candidate** — reachable integration exists, but required evidence is incomplete.
- **Supported** — canonical path, ownership, documentation, and risk-appropriate evidence exist.
- **Assurance-critical** — supported capability whose failure can affect aircraft safety; stricter evidence and human ownership apply.
- **Deprecated** — supported only during a named migration or removal window.
- **Retired** — no supported runtime path remains.

Maturity is not inferred from age, code volume, task count, or naming. Record it in the existing Linear issue or OpenSpec change when it helps a real decision; do not create a separate capability-record system. Include only the ownership, runtime path, evidence, gaps, and review condition needed for that decision.

## AI-assisted development

Codex is an engineering control system, not an unconstrained feature generator.

It may autonomously:

- inventory and trace the repository;
- detect drift, duplication, boundary violations, stale artifacts, and unverified claims;
- reproduce checks and assemble evidence;
- prepare bounded documentation, test, tooling, and low-risk cleanup changes;
- propose architecture and implementation plans;
- turn accepted failure lessons into ratchets.

It may not autonomously:

- deploy, flash, provision, or mutate live aircraft;
- weaken a safety invariant;
- merge a flight-critical behavior change without human approval;
- infer that apparently unused hardware, feature-gated, reflective, or platform-specific code is safe to delete;
- promote a claim to verified without reproducible evidence;
- create a new canonical path merely because an existing path is difficult to understand.

Automation authority is risk-tiered. Read-only analysis may run unattended. Low-risk proposals still pass normal review and validation. Flight-critical and architecture-boundary changes require explicit human decisions.

## Definition of done

A change is done only when, in proportion to its risk:

- its Product Soul relationship is clear;
- its authoritative owner and boundaries are clear;
- it follows or deliberately migrates the canonical path;
- behavior and failure modes are specified;
- effective tests or simulation evidence exist;
- operational state and errors are observable;
- documentation and OpenSpec are coherent with reality;
- deployment, rollback, migration, or removal behavior is known;
- no temporary parallel path is left without an owner and deadline;
- flight-critical human review and evidence requirements are satisfied.
