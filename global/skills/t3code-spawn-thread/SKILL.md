---
name: t3code-spawn-thread
description: Spawn a T3 Code thread on this machine's T3 server, send it a prompt, and return the agent's reply. Use when the user says "spawn a t3code thread", "start a thread in t3code", "open a thread for <project> with <model>", or wants a second agent kicked off in a T3 Code project so it shows up in their app.
---

# Spawn a T3 Code thread

T3 Code runs one server per machine (`t3 serve` under `~/.t3`), and every desktop app the user has paired with that server shows its threads. There is no CLI for creating threads, so this skill speaks the server's WebSocket RPC the same way the desktop app does. The script in `scripts/spawn-thread.mjs` does all of it. Run it from this skill's directory and relay the reply.

The script runs on the machine that hosts the server. If the user names a machine (for example "on rownd-mac-build-1") and you are not on it, run the script there over ssh, or tell the user you can only reach the local server.

## Steps

1. Work out the project, model, effort, and prompt from the user's request. The project can be its title in T3 Code, its workspace path, or its id. The model is a provider model id such as `claude-opus-5-5` or `claude-fable-5-1`. Effort is one of `low`, `medium`, `high`, `xhigh`, `max`. If the user gave no model, leave the flag off and the script uses `claude-opus-5-5` at medium effort.
2. Run the script and wait for it to exit. It prints one status line to stderr, then the reply to stdout.

   ```bash
   node scripts/spawn-thread.mjs --project arc-uas --model claude-opus-5-5 --effort high --prompt "hello"
   ```

   Add `--json` when you need the thread id, branch, or worktree path as well as the reply. Add `--no-worktree` to run the thread in the project's main checkout instead of a fresh worktree. `--branch`, `--base-branch`, `--title`, `--context 200k|1m`, and `--timeout-min` are there when the user asks for them.
3. Report the thread id, where it is running (branch or worktree), and the agent's reply verbatim. If the script exits non-zero, report the error it printed instead of retrying.

## How the script works

Read this when the script fails or when you need to send a different command to the server.

- The server's port comes from `~/.t3/userdata/server-runtime.json`. The `t3` binary lives under `~/.t3/runtime/versions/<active>/node_modules/@t3code/t3-*/t3`, with the active version in `~/.t3/runtime/service-state.json`. Set `T3_BIN` to override.
- Auth is a short-lived bearer session from `t3 auth session issue --json`, which the script revokes on exit. The bearer buys a ticket at `POST /api/auth/websocket-ticket`, and the ticket goes on the upgrade as `/ws?wsTicket=…`.
- The socket carries Effect RPC as JSON. A call is `{"_tag":"Request","id":"1","tag":"orchestration.dispatchCommand","payload":{…},"headers":[]}` and comes back as `{"_tag":"Exit","requestId":"1","exit":{"_tag":"Success","value":…}}`. Streams reply with `Chunk` messages and stall until the client sends `{"_tag":"Ack","requestId":…}` for each one, which is the usual cause of a subscription that only ever delivers its first item.
- Creating a thread and sending its first message is one `thread.turn.start` command with a `bootstrap` block that carries `createThread` and, for a worktree thread, `prepareWorktree`. Passing a `branch` in `prepareWorktree` matters. Without it the server checks out a detached worktree named after the base commit. The server renames the branch again once it generates the thread title.
- The reply is collected by subscribing to `orchestration.subscribeThread` and reading `thread.message-sent` events with the assistant role, until `thread.session-set` reports the session back at `ready` with no active turn.
- The model selection is `{instanceId:"claudeAgent", model, options:[{id:"effort",value},{id:"fastMode",value:false},{id:"contextWindow",value:"1m"}]}`. The provider's own option ids are listed in `~/.t3/userdata/model-manifest.json`.
- Every accepted command lands in `~/.t3/userdata/state.sqlite`. Query `projection_threads`, `projection_thread_messages`, and `orchestration_events` there when you need to check what the server actually recorded.
