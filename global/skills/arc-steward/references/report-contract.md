# ARC Steward report contract

Return concise Markdown in this order.

## Run header

- **Mode:** `observe`, `qualify`, or `prepare`
- **Revision:** full or short Git SHA
- **Working tree:** `clean` or `dirty`; summarize relevant pre-existing changes
- **Capability slice:** one bounded runtime capability or development workflow
- **Question:** the smallest question investigated
- **Evidence inspected:** graph queries, paths, specs, tests, and safe commands actually used
- **Checks skipped:** relevant checks not run and why
- **Outcome:** `finding`, `no-change`, `needs-human`, `inconclusive`, or `blocked`

## Qualified findings

Return zero to three findings. For each include:

- **ID:** stable readable ID formed from rule family, capability, and semantic target
- **Summary:** one falsifiable statement
- **Product relationship:** how it affects a Product Soul outcome or enabling capability
- **Engineering dimension:** architecture, flight assurance, security, tests, simulation, build/DX, docs/OpenSpec, deployment/recovery, or ownership
- **Evidence status:** `verified`, `observed`, `claimed`, `aspirational`, or `unknown`
- **Evidence:** precise file/line, graph edge/flow, command result, or specification references
- **Existing record:** matching Linear issue, OpenSpec change, pull request, or `none`
- **Competing explanation checked:** the strongest plausible reason this may be intentional or harmless
- **Scope risk / authority:** `F`, `B`, `C`, or `M`, plus the human gate required
- **Canonical owner/path:** current authority or `unknown`
- **Minimum next move:** characterization, consolidation, simplification, documentation correction, OpenSpec, test/scenario, or bounded repair
- **Success oracle:** what repeatable evidence would show resolution
- **Unknowns:** only uncertainty that can change the decision
- **Selection factors:** private impact class and rationale, leverage, readiness, urgency, finding family, and explicit architecture-resurrection assessment when this finding is compared for scheduled delivery; never expose these as backlog labels
- **Architecture-resurrection status:** `not-applicable`, `passed`, or `needs-human`; a pass requires `absence_status: accidental-regression`, `canonical_fit_status: fits-current-canonical-owner`, and positive current executable/authority signal conclusions from different canonical anchor-free artifact sources

Do not call an item a finding when the evidence cannot support these fields. Keep it under `Leads not promoted` instead.

## Known findings re-observed

List an existing Linear, OpenSpec, or pull-request record only when the run added material evidence, changed its priority, or showed that its status is stale. Do not create a second finding ID for unchanged known debt.

## Recommendation

Recommend exactly one next action. Explain why it outranks the other findings or why no action is justified.

When the next action is implementation, append a compact work contract:

- base revision;
- allowed and forbidden scope;
- behavior that must not change;
- expected failing or characterization evidence;
- required validation ladder;
- owner and approval gate;
- docs/OpenSpec obligations;
- rollback or removal strategy;
- durable ratchet to add if feasible.

## Leads not promoted

Optionally list up to three observations that lacked enough evidence. State the missing proof rather than suggesting cleanup.

## Steward lesson

Include only when the workflow itself should improve. State one concrete adjustment for a later, separately reviewed skill revision.
