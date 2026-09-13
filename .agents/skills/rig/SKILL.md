---
name: rig
description: Apply, inspect, or extend the rigging repo - fan out global/machine directives and skills to ~/.claude and ~/.codex with bin/rig, add a new skill in the harnesses-aware format, or register a new machine. Use when Bobby says "rig apply", "sync my agent config", "add a skill to rigging", or "add this machine".
---

# rig

`bin/rig` is deterministic; you run it and interpret the output. Do not copy files into harness homes by hand.

## Sync this machine

1. `bin/rig status`. Exit 2 means conflicts.
2. If conflicts: `bin/rig diff`, show Bobby the hand edits, and ask whether to fold them into the repo or discard with `bin/rig apply --force`.
3. `bin/rig apply`. Summarize wrote/pruned counts.

## Add a skill

1. Create `global/skills/<name>/SKILL.md` with `name`, `description`, and `harnesses: [claude, codex]` (inline list only; omit for all). Put it under `machines/<m>/skills/` instead if it is machine-specific.
2. `bin/rig status` should list it as `create` for each target harness, then `bin/rig apply`.

## Add a machine

1. `hostname -s` on that machine.
2. Create `machines/<name>/machine.toml` with `name`, `hostname`, `os`, and a `[harnesses.<h>]` table with `home` for each harness installed there.
3. Clone the repo there and run `bin/rig apply`.
