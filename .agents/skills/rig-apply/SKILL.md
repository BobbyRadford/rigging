---
name: rig-apply
description: Apply the rigging repo to a machine - run bin/rig on this box, or over ssh on a remote one, so its ~/.claude and ~/.codex match the repo. Use when Bobby says "rig apply", "sync my agent config", or "apply rigging to <machine>".
---

# rig-apply

`bin/rig` is deterministic; you run it and interpret the output. It reads the repo it lives in and writes into the invoking user's `$HOME`, so the repo must be cloned on the machine being configured and rig must run there as the target user. Never copy files into harness homes by hand.

## This machine

1. `bin/rig status`. Exit 2 means conflicts.
2. If conflicts: `bin/rig diff`, show Bobby the hand edits, and ask whether to fold them into the repo or discard with `bin/rig apply --force`.
3. `bin/rig apply`. Summarize wrote/pruned counts.

## A remote machine

The Mac is where the repo gets edited; each machine pulls and applies itself. Push first, then drive the same three steps over ssh. The clone lives at `~/projects/BobbyRadford/rigging` on every machine.

1. Commit and push the change.
2. `ssh <host> 'cd ~/projects/BobbyRadford/rigging && git pull --ff-only && bin/rig status'`. If `~/projects/BobbyRadford/rigging` is missing, clone `https://github.com/BobbyRadford/rigging.git` there first.
3. On conflicts, run `bin/rig diff` over ssh and ask Bobby before using `--force`, same as locally.
4. `ssh <host> 'cd ~/projects/BobbyRadford/rigging && bin/rig apply'`.

rig resolves the machine from `hostname -s`, so the remote needs a `machines/<name>/machine.toml` whose `hostname` matches. If status dies with "matched 0 machines", the machine is not registered yet; see AGENTS.md.
