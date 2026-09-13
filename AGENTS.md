# rigging

Source of truth for the config that every one of Bobby's machines carries: agent-harness directives and skills for Claude Code and Codex, fanned out by `bin/rig`.

## Layout

- `global/AGENTS.md`: shared directives, becomes the body of `~/.codex/AGENTS.md` and `~/.claude/CLAUDE.md`.
- `global/CLAUDE.md`: optional Claude-only fragment appended to `~/.claude/CLAUDE.md`.
- `global/skills/<name>/`: skills in Agent Skills format plus an optional `harnesses: [claude, codex]` frontmatter line. Omit it to target every harness. The line is stripped on output.
- `machines/<name>/machine.toml`: which harnesses live where on that machine. `hostname` must equal `hostname -s`.
- `machines/<name>/{AGENTS.md,CLAUDE.md,skills/}`: machine-only fragments and skills. A machine skill with the same name replaces the global one.
- `bin/rig`: `status`, `diff`, `apply [--force]`, `machine`. Python 3.11+, stdlib only.
- `.agents/skills/`: skills for working in this repo (Codex path; `.claude/skills` symlinks to it).

## Rules

- Edit here, then `bin/rig apply`. Never edit generated files in `~/.claude` or `~/.codex`; rig detects hand edits and refuses to overwrite without `--force`.
- No secrets, no absolute `/Users/...` paths. This repo is public.
- Provider-native config (settings.json, config.toml, MCP servers, hooks, automations) is out of scope and stays in each harness home.
- Skill `name` must equal its directory name.
