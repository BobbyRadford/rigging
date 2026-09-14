---
name: babysit-pr
description: Use when Bobby says "babysit", "babysit this PR", "watch the PR", "file and babysit", or "get this PR to green".
---

# Babysit PR

Keep a PR moving without Bobby watching it. After the branch is pushed, the only thing left is reacting to CI and reviewers, and that reaction loop is mechanical enough to hand off. This skill owns the loop, including fixing, committing, replying, and resolving each item.

Modes, picked from how Bobby phrased it:

- **Full** (default): loop until done.
- **First round**: "babysit the first round", "stop after the first set of comments". Do one pass, then stop and report. Bobby takes it from there.

## 1. Find the PR

`gh pr view --json number,url,state,isDraft,headRefName,baseRefName,headRefOid,mergeStateStatus`. Use the number or URL Bobby gave, otherwise the current branch. No PR on the branch: ask, do not guess. Merged or closed: stop and say so. Draft: babysit anyway, but do not mark it ready.

Record the head SHA and the current time. That is the watermark: only feedback created after it counts as new.

## 2. Wait for CI and reviewers

Review bots post after CI finishes, so do not collect comments early.

```bash
gh pr checks <n> --watch --fail-fast
```

If checks are still in progress after `--watch` returns, or no checks exist yet, poll `gh pr checks` every 60 seconds. Then wait up to 5 more minutes for bot reviews to land, polling every 60 seconds, and stop waiting as soon as one arrives. Skip the reviewer wait if the repo has no review bots configured.

## 3. Collect what is new

Everything after the watermark, from all three sources:

- Failing or timed-out checks on the current head SHA.
- Unresolved review threads and PR-level comments created after the watermark.
- Formal reviews (approve, request changes) submitted after the watermark.

Skip anything Bobby's own account wrote, anything already resolved, and pure approvals or "LGTM". Use `gh pr view <n> --json reviews,comments` for formal reviews and PR-level comments, and `gh api repos/<owner>/<repo>/pulls/<n>/comments --paginate` for inline comments. Fetch review threads through GraphQL to check `isResolved`, paginating until all threads and their comments have been read. Inspect failed checks with `gh run view <run-id> --log-failed`.

## 4. Triage

Verify every finding against the source before touching code. Bots are confidently wrong often enough that a finding is a claim, not a fact.

| Verdict | When | Do |
|---|---|---|
| Fix | Real, in scope, clear fix | Fix it |
| Dismiss | Wrong, misreads the code, or nitpick outside the PR's goal | Reply with a one-line reason, resolve |
| Escalate | Needs a product or architecture call, touches unrelated code, or the fix would exceed roughly 50 lines | Reply that it is deferred to Bobby, leave open |

CI failures are always Fix unless the failure is a known-flaky test, in which case rerun once with `gh run rerun <id> --failed` before diagnosing. Never weaken a check to pass it.

## 5. Fix, push, loop

Make one commit per thread or CI root cause after focused verification. For fixed review threads, reply with the commit SHA and resolve the thread, then push. Never force-push, never `git add -A`, never touch files outside the PR's scope unless a fix requires it.

If the base branch moved and the PR shows `mergeStateStatus` of `DIRTY` or `BEHIND`, rebase onto the base branch and push with `--force-with-lease`. This is the one exception to the no-force-push rule. Stop and report if the rebase hits conflicts you cannot resolve with confidence.

After every push, move the watermark to the new head SHA and go back to step 2.

Stop when any of these is true:

- CI is green, no unresolved threads remain except escalated ones, and no reviewer has changes requested.
- First-round mode and one pass is done.
- Six passes have run. Something is oscillating; report and stop.
- A push or rebase fails.
- A human reviewer requested changes that need Bobby's judgement. Report rather than guessing.

## 6. Report

Two or three sentences, then a table of what happened this session: source, finding, verdict, commit or reason. End with the PR URL and its state: mergeable, waiting on Bobby, or blocked. Do not merge unless Bobby asked for that up front.
