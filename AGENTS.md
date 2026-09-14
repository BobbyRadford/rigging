# rigging

Source of truth for the config that every one of Bobby's machines carries: agent-harness directives and skills for Claude Code and Codex, fanned out by `bin/rig`.

## Layout

- `global/AGENTS.md`: shared directives, becomes the body of `~/.codex/AGENTS.md` and `~/.claude/CLAUDE.md`.
- `global/CLAUDE.md`: optional Claude-only fragment appended to `~/.claude/CLAUDE.md`.
- `global/skills/<name>/`: skills in Agent Skills format plus an optional `harnesses: [claude, codex]` frontmatter line. Omit it to target every harness. The line is stripped on output.
- `global/harness/claude/settings.json`, `global/harness/codex/config.toml`: the keys and tables rig owns. They are merged key by key into the live file on apply; everything else in the live file (project trust, permission allowlists the app appends, marketplaces, app paths) is left alone. Machine overlays live at `machines/<name>/harness/<h>/` and merge on top of global. Merged files are never pruned and never conflict.
- `machines/<name>/machine.toml`: which harnesses live where on that machine. `hostname` must equal `hostname -s`.
- `machines/<name>/{AGENTS.md,CLAUDE.md,skills/}`: machine-only fragments and skills. A machine skill with the same name replaces the global one.
- `global/secrets.toml`: names (not values) of secrets every machine carries, e.g. `FILE_HOST_TOKEN`. Values live in `~/.rig/secrets.json` on each machine via `bin/rig secret set NAME` (value on stdin); apply injects them into `env` in Claude's settings.json and `[shell_environment_policy.set]` in Codex's config.toml so agent shells see them. `bin/rig status` reports missing ones.
- `bin/rig`: `status`, `diff`, `apply [--force]`, `machine`, `secret list|set`. Python 3.11+, stdlib only.
- `.agents/skills/`: skills for working in this repo (Codex path; `.claude/skills` symlinks to it). `rig-apply` covers running rig locally or over ssh.

## Common edits

- **Add a skill**: create `global/skills/<name>/SKILL.md` with `name`, `description`, and optionally `harnesses: [claude, codex]` as an inline list. Put it under `machines/<m>/skills/` if it is machine-specific. `bin/rig status` lists it as `create` per target harness.
- **Change a harness preference**: edit `global/harness/claude/settings.json` or `global/harness/codex/config.toml`, or the machine overlay under `machines/<m>/harness/<h>/`. Status shows the live file as `merge`. Never hand-edit the live file for keys rig owns; the next apply puts them back.
- **Add a secret**: declare the name in `global/secrets.toml`, then on every machine run `printf %s "$VALUE" | bin/rig secret set NAME` and `bin/rig apply`. The rig-apply skill covers doing that over ssh.
- **Add a machine**: run `hostname -s` there, create `machines/<name>/machine.toml` with `name`, `hostname`, `os`, and a `[harnesses.<h>]` table with `home` for each harness installed there (copy `machines/bobby-mbp`). Clone the repo to `~/rigging` on that machine and apply.

## Rules

- Prefer making changes in new commits directly on main rather than feature branches or worktrees
- Once you finish making changes, offer to apply to all the affected machines if not already requested to do so.
- Never edit generated files in `~/.claude` or `~/.codex`; rig detects hand edits and refuses to overwrite without `--force`.
- No secrets, no absolute `/Users/...` paths. This repo is public.
- Only preferences go in `global/harness/*`. Do not add project trust lists, app-internal paths, or anything the app rewrites on its own. MCP servers with machine-specific paths belong in a machine overlay or nowhere.
- Skill `name` must equal its directory name.

## Computer use

Use these rules when doing _Computer use_ or _Computer automation_

- Use /Applications/Arc.app as your default browser
- Don't try to use iTerm or Terminal with computer use. Instead, use normal shell commands as you would for other cirucumstances

## Working on tickets

We use Linear as a team for ticket tracking and planning work. Often times, my coworkers have a bad habit of proposing exact architecture and implementation details in the ticket itself. When you start working on a ticket, you should take those suggestions with a grain of salt. Instead of doing what the ticket says ver-batum, you should first distill the ticket into the problem it is trying to solve and the "why" behind it. From there you can use the suggested architecture as reference, treating it as one of many potential approaches to take. Pick out the useful pieces. Drop the rest.

## SSH

- Never attempt to ssh to our machines through cloudflare *.rownd.ai tunnels. Always use tailscale or direct IP or *.local addresses