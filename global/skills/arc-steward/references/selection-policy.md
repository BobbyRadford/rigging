# ARC improvement selection

Use this policy only to compare already-qualified candidates. It is an internal decision aid, not a Linear taxonomy, label scheme, or substitute for evidence.

## Eligibility first

Exclude a candidate before scoring when it:

- duplicates an open issue or pull request that already owns the problem;
- lacks a falsifiable problem, concrete evidence, or repeatable success oracle;
- cannot fit one cohesive reviewable change;
- requires unavailable hardware, credentials, generated artifacts, or a human decision;
- fails the architecture-resurrection gate in [architecture-resurrection.md](architecture-resurrection.md).

An explicit issue selected by Bobby remains first. Otherwise compare the bounded eligible Todo issues. Use Steward research only when no Todo issue is eligible.

## Impact class plus three scored dimensions

Score each eligible candidate with `arc-improve/scripts/automation_control.py score --input <json>`:

- **Impact class (mechanical value):** `flight-safety-trust` = 4; `mission-runtime` = 3; `systemic-engineering` = 2; `local-maintenance` = 1; `cosmetic` = 0. Supply a concrete `impact_rationale`. The helper derives value from this allowlisted class; the free-form finding family cannot inflate it.
- **Leverage (0–3):** 3 strengthens a shared contract or prevents a class of failures; 2 improves one important capability or recurring workflow; 1 is localized; 0 has no durable leverage.
- **Readiness (0–3):** 3 has reproduced evidence, established intent, bounded scope, and a strong local oracle; 2 has a clear problem and oracle with limited unknowns; 1 needs material qualification; 0 is not autonomously implementable.
- **Urgency (0–2):** 2 blocks current work, CI, or a near-term demonstration path; 1 has explicit Linear/user priority; 0 has no time signal.

Each candidate object also supplies `id`, `title`, lowercase kebab-case `family`, `impact_class`, `impact_rationale`, one or more concrete `evidence` strings, a falsifiable `success_oracle`, Linear `linear_priority` (`0` when unset), issue `created_at` as an ISO-8601 timestamp, and a relative `scope_size` from 1–40. Use `duplicate: true` or `blocked: true` to preserve an inspected but ineligible candidate in the compact ranking.

Every candidate must provide an `architecture_resurrection` assessment with explicit boolean `suspected` and a concrete `classification_rationale`. A suspected resurrection also supplies `absence_explained`, structured `absence_status`, `canonical_fit`, structured `canonical_fit_status`, and evidence `signals`. Autonomous implementation is allowed only when `absence_status` is exactly `accidental-regression` and `canonical_fit_status` is exactly `fits-current-canonical-owner`; intentional retirement, migration, a competing path, or ambiguity is `needs-human`. Every signal contains `kind`, concrete `evidence`, a canonical anchor-free artifact identifier prefixed with `repo:`, `linear:`, `openspec:`, `decision:`, or `owner:`, and an explicit conclusion. Put line, section, or comment anchors in optional `location`, never in `source`. Current executable kinds count only with `conclusion: supports-current-path`; current authority kinds count only with `conclusion: supports-intended-owner`. The helper requires one of each from different exact artifact identifiers; relabeling one file with different anchors or supplying negated prose does not count as corroboration.

The helper subtracts up to two points when the same finding family dominated the last three completed delivery records that actually selected work. Skipped overlap, setup failure, no-change, and other records without a selected family do not displace real delivery history. This is a diversity correction, not a quota. A high-value repeated family may still win.

Use a plain family name such as `mission`, `world-state`, `flight-assurance`, `runtime-reliability`, `architecture`, `simulation`, `security`, `build-dx`, `docs-openspec`, or `lint`. Keep it only in the private run record; do not add it as a Linear or GitHub label.

## Selection rule

Impact is categorical before convenience: sort eligible work by the mechanically derived impact value first, then total score, leverage, readiness, existing Linear priority, age, and finally the smaller coherent scope. `linear_priority` follows Linear's numeric convention (`1` urgent through `4` low; `0` unset), `created_at` is the issue's ISO timestamp, and `scope_size` is a bounded 1–40 relative estimate used only after impact and age. Do not inflate impact classes to manufacture work. Record the compact ranking and selected family in the immutable run record.

`local-maintenance` and `cosmetic` candidates are mechanically capped at leverage 1 regardless of the family alias. They cannot outrank a qualified impact-2-or-higher mission, runtime, safety, architecture, simulation, or systemic developer-workflow defect merely because they are faster to implement.
