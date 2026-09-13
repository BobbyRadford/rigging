# Architecture-resurrection gate

Apply this gate whenever a proposed change would restore, re-enable, re-add to build/CI/deployment/documentation, or promote a path that is dormant, deleted, feature-gated, labeled legacy/deprecated/unsupported, apparently unused, or contradicted by the current canonical path.

File existence, old documentation, historical tests, commented configuration, git history, and an aspirational or dangling OpenSpec change are leads. They do not establish that the path should return.

## Evidence required before implementation

Establish all of the following:

1. **Current executable support:** a present runtime caller, deployment declaration, build target, platform descriptor, or other reachable current path establishes that the capability is meant to operate now.
2. **Current authority:** a current owner confirmation, approved specification, accepted Linear decision, or recent accepted architecture decision establishes intended ownership and support.
3. **Independent corroboration:** at least two independent current evidence sources agree; one must be executable and one authoritative. Each mechanical signal records its source, and the same file, decision, issue, or assertion relabeled under two kinds remains one source.
4. **Absence explained:** determine why the path is dormant or absent and produce evidence that the absence is accidental or incomplete—not an intentional retirement, migration, experiment boundary, or unsupported platform choice. Record `absence_status: accidental-regression`; any other status fails closed.
5. **Canonical fit:** identify the authoritative owner and show that restoration will not create a second production path or violate edge/ground, flight-command, configuration, platform, deployment, or trust boundaries. Record `canonical_fit_status: fits-current-canonical-owner`; any competing or ambiguous fit fails closed.

Every scored candidate must state whether resurrection is suspected and why. Use `automation_control.py score` to mechanically reject a missing assessment or a restoration candidate lacking the two positive statuses, an executable signal, authority signal from a different source, an explanation for the absence, or canonical ownership/competing-path fit. Executable signals require `conclusion: supports-current-path`; authority signals require `conclusion: supports-intended-owner`. Signal `source` values are exact anchor-free artifact identifiers such as `repo:infra/...`, `linear:ARC-123`, `openspec:capability-name`, `decision:<stable-id>`, or `owner:<handle>`; use optional `location` for a line, section, or comment anchor.

## Ambiguous outcome

When any required fact is ambiguous, do not restore the path. Create or update one ordinary Linear issue containing:

- the conflicting current evidence;
- the candidate canonical owner/path;
- the smallest architecture or product decision needed;
- the evidence that would make a later implementation safe.

Do not add custom labels or statuses. Do not reinterpret ambiguity as permission to delete the path either; removal still requires reachability, platform, deployment, history, and rollback proof.
