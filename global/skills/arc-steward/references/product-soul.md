# ARC Product Soul

Status: Bobby's private working direction
Established: 2026-08-05

Document type: private normative product input for Bobby's Codex workflows. It defines what ARC is being built to become; it is not repository policy and does not claim that the current implementation already delivers these capabilities. Current implementation claims require revision-scoped evidence.

## Essence

ARC enables one operator to direct one aircraft or a coordinated fleet at the level of mission outcomes rather than vehicle mechanics. The platform plans and executes safe missions, builds a shared geolocated understanding of the world, and presents truthful operational awareness even when communications or positioning are degraded.

ARC is a drone companion and fleet operating platform. It is not primarily a joystick replacement, a collection of disconnected autonomy demos, or a dashboard for raw telemetry.

## Product promises

### Outcome-level autonomous missions

ARC accepts objectives, constraints, areas, routes, targets of interest, approval rules, and asset-preservation policies. One aircraft or a fleet can plan and adapt locally, cooperate over intermittent links, and continue within preauthorized bounds when contact with the operator or other aircraft is unavailable.

Mission creation has many first-class interfaces over one canonical structured model:

- map operations such as drawing zones, routes, landing areas, and exclusions;
- templates, forms, and parameter editing;
- textual or conversational intent;
- voice interaction;
- programmatic mission construction;
- controlled mid-mission revision.

Conversational input is an authoring interface, not a direct aircraft-control bypass. The twelve-month demonstration may use prepared missions, but the platform destination supports arbitrary, composable mission construction and replanning.

### Evidence-backed world understanding

ARC turns sensor observations into persistent, geolocated world objects rather than treating detections as disposable bounding boxes. A world object carries provenance, class belief, confidence, uncertainty, time, imagery or crops, and a stable identity suitable for operator reasoning and approved follow-on action.

Multiple observations may disagree. ARC retains source evidence and uncertainty while reconciling observations toward a shared track. Identity should survive temporary occlusion, disconnection, aircraft replacement, and—where operationally appropriate—mission boundaries. Reset, merge, split, and retirement are explicit operations rather than accidental side effects.

World objects are actionable within policy: an operator may designate an object for continued observation, search, tracking, loitering, safe approach or landing nearby, or another approved mission action. Probabilistic perception never grants itself authority to perform a consequential action.

### World-class fleet operations

Arc UI gives an operator a map-centric, exception-first view of mission state, fleet health, world objects, evidence, uncertainty, and required decisions. It supports fleet-level direction without preventing deliberate inspection or control of an individual aircraft.

After a communications blackout, the operator should quickly understand:

- whether the fleet is safe;
- whether the mission remains viable;
- where every aircraft is, and how recent or estimated that knowledge is;
- what occurred while disconnected;
- what decisions now require attention.

The interface distinguishes live, delayed, estimated, reconciled, and unknown state. It favors mission consequences and action queues over undifferentiated telemetry.

## Autonomy and authority

ARC uses a mission policy agreed before execution. Policy can express communications posture, loss-of-link behavior, asset-preservation priority, acceptable landing outcomes, mission-continuation rules, approval thresholds, and prohibited actions.

Authority is layered:

1. **Hard constraints** — law, geofence, physical limits, and engineered safety invariants cannot be waived by a mission prompt or casual operator instruction.
2. **Automatic safety behavior** — immediate protective responses execute without waiting for a communications round trip when delay would increase risk.
3. **Preauthorized discretion** — the fleet may make reversible, bounded, non-harmful decisions inside the approved mission envelope.
4. **Live human approval** — consequential actions with meaningful life, limb, property, or mission-policy implications require an accountable operator when not already covered by an approved safety policy.
5. **Prohibited actions** — behavior outside the legal or engineered envelope is rejected even if requested.

An operator's approval is necessary for some actions, but it is not itself a mechanism for bypassing hard safety constraints.

## Flagship twelve-month demonstration

A fleet of roughly six to eight aircraft launches from a boat approximately one mile offshore. From Arc UI, an operator directs the fleet to reach land and establish a perimeter.

The fleet:

- coordinates departure, transit, separation, and arrival;
- evaluates terrain, vegetation, landing suitability, communications, and mission geometry;
- assigns roles such as landed sentry, airborne observer, or relay;
- establishes a useful perimeter without requiring individual waypoint micromanagement;
- observes the area, creates evidence-backed world objects, and communicates findings through the mesh when practical;
- continues according to policy through degraded or intentionally quiet communications;
- requests approval for consequential follow-on actions;
- returns to the boat, lands elsewhere, or remains in place according to the configured recovery and asset-preservation policy;
- avoids collisions and maintains safe flight throughout.

A second representative mission uses one aircraft and a phone-class Arc UI. The operator identifies a prepared area and asks the aircraft to inspect it for a class of object or hazard, such as counting vehicles. The aircraft performs the mission without normal joystick piloting and returns mapped, reviewable evidence.

The demonstration may rely on prepared templates and known operating areas. It must nevertheless exercise a real vertical slice: structured mission intent, autonomous execution, degraded-link behavior, world understanding, operator awareness, and safe recovery. A scripted visual imitation is not sufficient.

## Enduring principles

- **Mission outcomes over vehicle mechanics.** Operators express intent and policy; ARC handles execution detail.
- **One mission model, many authoring interfaces.** Map, forms, templates, text, voice, and APIs converge before execution.
- **One safe aircraft-command path.** Autonomy, perception, and UI express intent; they do not create side doors around flight control.
- **Local competence.** Aircraft remain predictably useful inside policy when links or positioning degrade.
- **Truth before confidence theater.** Uncertainty, staleness, disagreement, and unknown state are product features, not details to hide.
- **Evidence before labels.** An object or mission claim retains provenance and can be audited.
- **Fleet behavior over coordinated animation.** Cooperation includes role assignment, shared understanding, conflict handling, and independent safe decisions.
- **Faithful simulation and replay.** Development environments must exercise the same contracts and decision paths as the fielded system wherever feasible.
- **Explicit platform variation.** Hardware differences are modeled, not scattered through assumptions.
- **Complexity must earn its place.** Aspirational infrastructure is not free; unsupported paths are isolated or removed.
- **Developer experience is reliability work.** Reproducible build, simulation, test, deploy, recording, and replay reduce operational risk.
- **Failures improve the system.** Important incidents and recurring defects produce durable evidence and regression ratchets.

## Product non-goals

- Making joystick flight the primary operating model.
- Allowing language models or vision confidence to command aircraft directly.
- Presenting stale or estimated state as live truth.
- Claiming swarm behavior when aircraft merely receive parallel individual commands.
- Treating task completion, code presence, or a successful demo run as general capability acceptance.
- Supporting every imagined feature at the expense of a coherent mission, world-state, safety, and operator model.
- Hiding platform constraints or communications loss behind optimistic UI.

## Success test

ARC succeeds when a trained operator can safely direct meaningful single-aircraft and fleet missions with dramatically less cognitive load than conventional vehicle-by-vehicle operation, while retaining clear authority, evidence, and understanding of what the system knows, does not know, decided, and requires next.

Before work is accepted, ask:

1. Does it advance autonomous mission execution, world understanding, or fleet operations?
2. Does it strengthen a coherent end-to-end mission rather than an isolated demo?
3. Does it preserve one structured mission model and one safe command path?
4. Does it behave deliberately under degraded communications, positioning, or sensors?
5. Does it expose truth, evidence, uncertainty, and causality to the operator?
6. Can it be reproduced and evaluated without relying on tribal knowledge?
7. Is its complexity justified and owned?
8. Would we be comfortable depending on it during the flagship demonstration?
