# rigging

Source of truth for the config that every one of Bobby's machines carries: agent-harness directives and skills for Claude Code and Codex, fanned out by `bin/rig`.

## Layout

- `global/AGENTS.md`: shared directives, becomes the body of `~/.codex/AGENTS.md` and `~/.claude/CLAUDE.md`.
- `global/CLAUDE.md`: optional Claude-only fragment appended to `~/.claude/CLAUDE.md`.
- `global/skills/<name>/`: skills in Agent Skills format plus an optional `harnesses: [claude, codex]` frontmatter line. Omit it to target every harness. The line is stripped on output.
- `global/harness/claude/settings.json`, `global/harness/codex/config.toml`: the keys and tables rig owns. They are merged key by key into the live file on apply; everything else in the live file (project trust, permission allowlists the app appends, marketplaces, app paths) is left alone. Machine overlays live at `machines/<name>/harness/<h>/` and merge on top of global. Merged files are never pruned and never conflict.
- `machines/<name>/machine.toml`: which harnesses live where on that machine. `hostname` must equal `hostname -s`.
- `machines/<name>/{AGENTS.md,CLAUDE.md,skills/}`: machine-only fragments and skills. A machine skill with the same name replaces the global one.
- `bin/rig`: `status`, `diff`, `apply [--force]`, `machine`. Python 3.11+, stdlib only.
- `.agents/skills/`: skills for working in this repo (Codex path; `.claude/skills` symlinks to it). `rig-apply` covers running rig locally or over ssh.

## Common edits

Every edit ends with `bin/rig apply` on each affected machine (see the `rig-apply` skill for the remote flow).

- **Add a skill**: create `global/skills/<name>/SKILL.md` with `name`, `description`, and optionally `harnesses: [claude, codex]` as an inline list. Put it under `machines/<m>/skills/` if it is machine-specific. `bin/rig status` lists it as `create` per target harness.
- **Change a harness preference**: edit `global/harness/claude/settings.json` or `global/harness/codex/config.toml`, or the machine overlay under `machines/<m>/harness/<h>/`. Status shows the live file as `merge`. Never hand-edit the live file for keys rig owns; the next apply puts them back.
- **Add a machine**: run `hostname -s` there, create `machines/<name>/machine.toml` with `name`, `hostname`, `os`, and a `[harnesses.<h>]` table with `home` for each harness installed there (copy `machines/bobby-mbp`). Clone the repo to `~/projects/BobbyRadford/rigging` on that machine and apply.

## Rules

- Edit here, then `bin/rig apply`. Never edit generated files in `~/.claude` or `~/.codex`; rig detects hand edits and refuses to overwrite without `--force`.
- No secrets, no absolute `/Users/...` paths. This repo is public.
- Only preferences go in `global/harness/*`. Do not add project trust lists, app-internal paths, or anything the app rewrites on its own. MCP servers with machine-specific paths belong in a machine overlay or nowhere.
- Skill `name` must equal its directory name.
