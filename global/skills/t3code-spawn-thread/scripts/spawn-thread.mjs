#!/usr/bin/env node
// Spawn a T3 Code thread on the server running on this machine, send it one
// prompt, and print the assistant's reply. Speaks the same WebSocket RPC the
// desktop app uses, so the thread shows up in every app connected to this server.
//
//   spawn-thread.mjs --project <title|path|id> --prompt "hello" \
//     [--model claude-opus-5-5] [--effort high] [--context 1m] \
//     [--branch t3code/abc12345] [--no-worktree] [--timeout-min 15] [--json]
//
// Needs Node 22+ (global WebSocket) and a running `t3 serve` under ~/.t3.
import { execFileSync } from "node:child_process";
import { randomUUID, randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const args = parseArgs(process.argv.slice(2));
if (!args.project || !args.prompt) fail("usage: spawn-thread.mjs --project <title|path|id> --prompt <text> [--model] [--effort] [--context] [--branch] [--no-worktree] [--timeout-min] [--json]");

const t3Home = process.env.T3CODE_HOME ?? path.join(os.homedir(), ".t3");
const userdata = path.join(t3Home, "userdata");
const runtime = readJson(path.join(userdata, "server-runtime.json"));
const origin = runtime.origin ?? `http://127.0.0.1:${runtime.port}`;
const t3Bin = resolveT3Binary(t3Home);

// Short-lived bearer session, revoked on exit.
const session = JSON.parse(execFileSync(t3Bin, ["auth", "session", "issue", "--ttl", "20m", "--label", "spawn-thread", "--json"], { encoding: "utf8" }));
process.on("exit", () => { try { execFileSync(t3Bin, ["auth", "session", "revoke", session.sessionId], { stdio: "ignore" }); } catch {} });
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => process.exit(130));

const ticketRes = await fetch(`${origin}/api/auth/websocket-ticket`, {
  method: "POST", headers: { authorization: `Bearer ${session.token}`, "content-type": "application/json" }, body: "{}",
});
if (!ticketRes.ok) fail(`websocket ticket failed: HTTP ${ticketRes.status}`);
const { ticket } = await ticketRes.json();

const wsUrl = new URL("/ws", origin); wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:"; wsUrl.searchParams.set("wsTicket", ticket);
const ws = new WebSocket(wsUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("websocket failed to open")); });

// Effect RPC over JSON: {_tag:"Request", id, tag, payload, headers}. Replies are
// {_tag:"Exit", requestId, exit} for calls and {_tag:"Chunk", requestId, values} for
// streams. A stream only sends its next chunk after the client acks the last one.
let nextId = 1;
const pending = new Map(); // id -> { onChunk, onExit }
ws.onmessage = (ev) => {
  const raw = JSON.parse(ev.data);
  for (const m of Array.isArray(raw) ? raw : [raw]) {
    if (m._tag === "Ping") { ws.send(JSON.stringify({ _tag: "Pong" })); continue; }
    const p = pending.get(m.requestId);
    if (!p) continue;
    if (m._tag === "Chunk") { for (const v of m.values) p.onChunk(v); ws.send(JSON.stringify({ _tag: "Ack", requestId: m.requestId })); }
    if (m._tag === "Exit") p.onExit(m.exit);
  }
};
const call = (tag, payload) => new Promise((res, rej) => {
  const id = String(nextId++);
  pending.set(id, { onChunk: () => {}, onExit: (exit) => { pending.delete(id); exit._tag === "Success" ? res(exit.value) : rej(new Error(JSON.stringify(exit))); } });
  ws.send(JSON.stringify({ _tag: "Request", id, tag, payload, headers: [] }));
});
const subscribe = (tag, payload, onChunk) => new Promise((res, rej) => {
  const id = String(nextId++);
  pending.set(id, { onChunk: (v) => onChunk(v, () => { pending.delete(id); ws.send(JSON.stringify({ _tag: "Interrupt", requestId: id })); res(); }), onExit: (exit) => { pending.delete(id); exit._tag === "Success" ? res() : rej(new Error(JSON.stringify(exit))); } });
  ws.send(JSON.stringify({ _tag: "Request", id, tag, payload, headers: [] }));
});

// Resolve the project from the shell snapshot by id, title, or workspace path.
let project;
await subscribe("orchestration.subscribeShell", { requestCompletionMarker: true }, (item, done) => {
  if (item.kind === "snapshot") {
    const want = args.project;
    project = item.snapshot.projects.find((p) => p.id === want || p.title === want || p.workspaceRoot === path.resolve(want));
    done();
  }
});
if (!project) fail(`no project matches "${args.project}" on ${origin}`);

const model = args.model ?? "claude-opus-5-5";
const modelSelection = {
  instanceId: args.instance ?? "claudeAgent",
  model,
  options: [
    { id: "effort", value: args.effort ?? "medium" },
    { id: "fastMode", value: false },
    { id: "contextWindow", value: args.context ?? "1m" },
  ],
};
const now = new Date().toISOString();
const threadId = randomUUID();
const title = args.title ?? args.prompt.slice(0, 50);
const useWorktree = !args["no-worktree"];
const branch = args.branch ?? `t3code/${randomBytes(4).toString("hex")}`;
const command = {
  type: "thread.turn.start",
  commandId: randomUUID(),
  threadId,
  message: { messageId: randomUUID(), role: "user", text: args.prompt, attachments: [] },
  modelSelection,
  titleSeed: title,
  runtimeMode: args["runtime-mode"] ?? "full-access",
  interactionMode: args["interaction-mode"] ?? "default",
  bootstrap: {
    createThread: {
      projectId: project.id, title, modelSelection,
      runtimeMode: args["runtime-mode"] ?? "full-access", interactionMode: args["interaction-mode"] ?? "default",
      branch: args["base-branch"] ?? "main", worktreePath: null, createdAt: now,
    },
    ...(useWorktree ? { prepareWorktree: { projectCwd: project.workspaceRoot, baseBranch: args["base-branch"] ?? "main", branch, startFromOrigin: true } } : {}),
    runSetupScript: false,
  },
  createdAt: now,
};

await call("orchestration.dispatchCommand", command);
log(`thread ${threadId} created in ${project.title}${useWorktree ? ` on ${branch}` : ""}`);

// Follow the thread until the provider session goes back to ready after running.
let sawRunning = false, reply = "", lastError = null, worktreePath = null;
const timer = setTimeout(() => { log("timed out waiting for the reply"); finish(2); }, (Number(args["timeout-min"]) || 15) * 60 * 1000);
await subscribe("orchestration.subscribeThread", { threadId }, (item, done) => {
  if (item.kind === "snapshot") {
    // The turn may already be over by the time the subscription starts.
    const t = item.snapshot.thread;
    if (t.worktreePath) worktreePath = t.worktreePath;
    if (t.latestTurn?.state === "completed") { sawRunning = true; }
    return;
  }
  if (item.kind !== "event") return;
  const { type, payload } = item.event;
  if (type === "thread.meta-updated" && payload.worktreePath) worktreePath = payload.worktreePath;
  if (type === "thread.message-sent" && payload.role === "assistant" && payload.text) reply = payload.text;
  if (type === "thread.session-set") {
    const s = payload.session;
    if (s.status === "running") sawRunning = true;
    if (s.status === "error") { lastError = s.lastError; done(); }
    if (s.status === "ready" && sawRunning && !s.activeTurnId) done();
  }
});
clearTimeout(timer);

if (args.json) console.log(JSON.stringify({ threadId, projectId: project.id, branch: useWorktree ? branch : null, worktreePath, reply, error: lastError }, null, 2));
else if (lastError) { log(`session error: ${JSON.stringify(lastError)}`); finish(1); }
else console.log(reply);
finish(lastError ? 1 : 0);

function finish(code) { try { ws.close(); } catch {} process.exit(code); }
function log(msg) { process.stderr.write(`${msg}\n`); }
function fail(msg) { log(msg); process.exit(1); }
function readJson(p) { return JSON.parse(fs.readFileSync(p, "utf8")); }
function resolveT3Binary(home) {
  const fromPath = process.env.T3_BIN; if (fromPath) return fromPath;
  const state = readJson(path.join(home, "runtime", "service-state.json"));
  const dir = path.join(home, "runtime", "versions", state.activeVersion, "node_modules", "@t3code");
  const pkg = fs.readdirSync(dir).find((n) => n.startsWith("t3-"));
  if (!pkg) fail(`no t3 binary under ${dir}`);
  return path.join(dir, pkg, "t3");
}
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[key] = true; else { out[key] = next; i++; }
  }
  return out;
}
