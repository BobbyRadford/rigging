# rigging

The gear every one of my machines carries: shared directives and skills for AI coding harnesses (Claude Code, Codex), fanned out per machine by `bin/rig`.

```
bin/rig status          # what would change on this machine
bin/rig diff            # unified diff of pending changes
bin/rig apply [--force] # write to ~/.claude, ~/.codex, ...; --force overwrites hand edits
```

New machine: add `machines/<name>/machine.toml` (see `machines/bobby-mbp`), clone to `~/projects/BobbyRadford/rigging` there, run `bin/rig apply`.

See `AGENTS.md` for layout, skill format, and rules.
