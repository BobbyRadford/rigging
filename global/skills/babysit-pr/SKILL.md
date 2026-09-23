---
name: babysit-pr
description: Use when Bobby says "babysit", "babysit this PR", "watch the PR", "file and babysit", or "get this PR to green".
---

# Babysit PR

Carry an open PR to its end state without Bobby watching. React to CI and reviewers the moment they post, and stop only at the end state.

**End state** comes from Bobby's prompt: "until green", "merge once green", "first round only", and so on. Default: reach **done**, report, and leave the merge to Bobby.

## Setup

Resolve the PR from the number or URL Bobby gave, else the current branch (`gh pr view --json url,state,isDraft`). No PR: ask. Merged or closed: say so and stop. Draft: babysit it, leave it draft.

Find the **expected bots**: the review bots that commented on this PR or the repo's most recent merged PR.

## Loop

1. **Watch.** Run `scripts/watch-pr.sh <pr-url>` from this skill's directory, in the background where the harness supports it (Claude Code: Bash `run_in_background`), and wait for it to exit. It blocks until something new happens, prints those events, and exits; after 10 minutes with nothing new it prints `quiet`. The first call prints the current state.
2. **Collect** all open work on the head commit, whatever the events said. Events only wake you; state decides what is open:
   - failed checks (`gh run view <id> --log-failed`)
   - unresolved review threads (GraphQL `reviewThreads.isResolved`, every page) whose last comment is not from Bobby's account
   - PR-level comments with no later reply from Bobby's account
   - a changes-requested review
3. **Triage** each finding. A bot finding is a claim: verify it against the source first.

   | Verdict | When | Action |
   |---|---|---|
   | Fix | Real, in scope, clear fix | Fix it, reply with the commit SHA, resolve |
   | Dismiss | Wrong, misreads the code, or a nit outside the PR's goal | Reply with a one-line reason, resolve |
   | Escalate | Needs a product or architecture call, or a fix over ~50 lines | Reply that Bobby will decide, leave open |

   A failing check is always Fix. Rerun a known-flaky test once (`gh run rerun <id> --failed`) before diagnosing. Fix the code, never the check.
4. **Push.** Commit each fix on its own, staging files by name, then push the batch once. If `mergeStateStatus` is `DIRTY` or `BEHIND`, rebase onto the base and push with `--force-with-lease`, the only force-push allowed.
5. Back to 1, unless the PR is **done** or a stop applies.

Handle review comments as they land, even while CI is still running.

## Scope

Change only what a finding needs; list new ideas in the report instead of the PR. Every comment you post is a fix SHA or a dismiss/escalate reason, and ends with `<!-- babysit -->` so the watcher skips your own replies.

## Done

The PR is **done** when all of these hold on the head commit:

- every check passed
- every expected bot has reviewed the head commit, or the watcher went quiet while waiting on it (that bot skipped, usually out of credits)
- no open work remains except escalated threads
- no review requests changes

## Stop and report early

- A push or rebase fails, or a rebase conflict has no confident resolution.
- A human reviewer asks for changes that need Bobby's judgement.
- Another PR makes this one obsolete. Ask before closing it.
- Six pushes without reaching done. Something is oscillating.

## Report

Two or three sentences, then a table: source, finding, verdict, commit or reason. Name any expected bot that skipped the head commit. End with the PR URL and its state: merged, mergeable, waiting on Bobby, or blocked.
