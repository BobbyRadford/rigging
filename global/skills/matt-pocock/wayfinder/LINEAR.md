# Wayfinding on Linear

All maps and tickets live in Linear. Use whatever Linear access the session has: the Linear MCP server's tools first, a Linear CLI second. If neither is available, stop and ask Bobby to connect Linear rather than falling back to another tracker or local files.

Pick the team from the repo or the conversation. If it is not obvious, ask once and record it in the map's **Notes** so later sessions do not ask again.

## Operations

- **Map**: one Linear issue labelled `wayfinder:map`. Its description is the map body (Destination / Notes / Decisions so far / Not yet specified / Out of scope). Create labels on first use if the team lacks them.
- **Child ticket**: an issue whose **parent** is the map. Label it `wayfinder:<type>` (`research` / `prototype` / `grilling` / `task`). Body is the `## Question` block. Title is the ticket's name; refer to it by title everywhere.
- **Blocking**: Linear's native **blocked by** relation on the child. Create tickets first, then wire relations in a second pass once every ticket has an id. Linear renders these in the issue's Relations panel, so the human sees the frontier without opening the map.
- **Frontier query**: the map's children that are open (state type not `completed` or `canceled`), have no assignee, and have no open issue in their `blocked by` relations. First in map order wins.
- **Claim**: assign the ticket to Bobby, as the session's first write.
- **Resolve**: post the answer as a comment on the ticket, move it to the team's done state, then append one line to the map's Decisions so far: `- [<title>](<issue url>): <gist>`.
- **Rule out of scope**: cancel the ticket and add one line to the map's Out of scope section.
- **Links**: Linear issue URLs look like `https://linear.app/<workspace>/issue/<KEY>-<n>`. Always wrap the ticket title around the URL; never show a bare key.
