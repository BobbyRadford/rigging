# ARC Improve evidence

Use this compact structure in the draft pull request and final response.

## Why

- State the observed problem and why it matters to ARC.
- Link the Linear issue and applicable OpenSpec change.
- Identify any material assumption or human decision already supplied.

## What changed

- Explain the smallest cohesive solution.
- Name the canonical path strengthened and any superseded path removed.
- State explicit non-goals and rollback or removal behavior when relevant.

## Evidence

- Record the pre-change failure, characterization, drift proof, or reachability proof actually observed.
- List exact checks and scenarios that passed.
- State how the new test or ratchet would detect regression.
- Summarize independent changed-files review and resolved findings.
- Cover every publisher preflight risk profile: workflow triggers/permissions/secrets, build contracts, deployment rendering or dry-run, disconnected hardware validation, policy impact, submodule reachability, or secret-handling review as applicable.
- State explicitly that no live deployment, hardware operation, credential mutation, or aircraft contact occurred when those boundaries are relevant.

## Not verified

- List required or useful checks that were skipped, unavailable, flaky, or outside the environment.
- Never describe mocked, merely present, or unexecuted evidence as passing.

## Review focus

- Name the highest-risk assumption or boundary for the human reviewer.
- Lead with `FLIGHT-CRITICAL CHANGE` when production flight behavior changed and name the required flight owner and regression-relevant SITL evidence. Draft publication may precede owner approval; merge may not.

For a non-success outcome, return the problem, evidence gathered, current patch location when any, and exactly one smallest next action. Do not publish.
