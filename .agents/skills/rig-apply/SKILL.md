---
name: rig-apply
description: Apply rigging changes to a machine. Use when Bobby says "rig apply", "sync my agent config", or "apply rigging to <machine>", or simply "rig"
---

# rig-apply

`bin/rig` is deterministic; you run it and interpret the output. It reads the repo it lives in and writes into the invoking user's `$HOME`, so the repo must be cloned on the machine being configured and rig must run there as the target user. Never copy files into harness homes by hand.

## This machine

1. `bin/rig status`. Exit 2 means conflicts.
2. If conflicts: `bin/rig diff`, show Bobby the hand edits, and ask whether to fold them into the repo or discard with `bin/rig apply --force`.
3. `bin/rig apply`. Summarize wrote/pruned counts.

## A remote machine

The Mac is where the repo gets edited; each machine pulls and applies itself. Push first, then drive the same three steps over ssh. The clone lives at `~/rigging` on every machine.

1. Commit and push the change.
2. `ssh <host> 'cd ~/rigging && git pull --ff-only && bin/rig status'`. If `~/rigging` is missing, clone `https://github.com/BobbyRadford/rigging.git` there first.
3. On conflicts, run `bin/rig diff` over ssh and ask Bobby before using `--force`, same as locally.
4. `ssh <host> 'cd ~/rigging && bin/rig apply'`.

rig resolves the machine from `hostname -s`, so the remote needs a `machines/<name>/machine.toml` whose `hostname` matches. If status dies with "matched 0 machines", the machine is not registered yet; see AGENTS.md.

## Summary output

After every apply, end with a terse log in a fenced code block: one fixed-width line per machine, then a totals footer. Nothing else goes in the block.

```
✅ bobby-mbp   328299f  wrote=1 pruned=0 ok=21
❌ pasture1    328299f  conflict: ~/.claude/settings.json (hand-edited)
── 1/2 machines applied, 1 conflict
```

Rules:

- Icon: `✅` applied cleanly, `❌` conflict, ssh failure, or non-zero exit. Put the reason after the commit instead of the counts.
- Commit: short sha the machine applied. If a remote could not pull, show the sha it is still on.
- Counts come straight from `bin/rig apply` / `bin/rig status`: `wrote`, `pruned`, `ok`. Always show all three, even when zero.
- Footer: `── <applied>/<total> machines applied, <n> conflicts`. Say `0 conflicts` when clean.
- Pad machine names so the sha column lines up. Machines in the order they were applied.
- One or two sentences of prose before the block if something needs explaining (what changed, why a machine was skipped). No prose after it.
