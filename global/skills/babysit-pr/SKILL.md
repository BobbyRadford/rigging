---
name: babysit-pr
description: Use when the user says "babysit", "babysit this PR", "watch the PR", "file and babysit", or "get this PR to green".
---

# Babysit PR

Your job is to carry an open pull request the rest of the way so the user doesn't have to watch it. Respond to CI and reviewers as soon as they post, and keep going until you reach the end state.

The user's prompt sets the end state. They might say "babysit until green", "merge once it's green", or "just handle the first round". If they don't say, get the PR to done (defined below), report back, and leave the merge to them.

## Getting started

Use the PR number or URL the user gave you, or the PR for the current branch if they didn't give one. If there's no PR, ask. If it's already merged or closed, say so and stop. Babysit a draft like any other PR, but leave it in draft.

Work out which review bots to expect by looking at who has reviewed this PR so far and who reviewed the repo's most recent merged PR.

If the repo's `AGENTS.md` or `CLAUDE.md` has rules for answering review comments, follow them where they differ from this skill.

## The loop

1. Run `scripts/watch-pr.sh <pr-url>` from this skill's directory and wait for it to exit. Run it in the background if your harness supports that. The script waits until something new happens on the PR, prints what happened, and exits. If nothing happens for 10 minutes, it prints `quiet` instead.
2. Gather everything that's still open on the PR. The script's output tells you when to look, not what to look at, because older work can still be open. Open work means failed checks, unresolved review threads where the last comment isn't from the user's account, PR comments with no reply from the user's account after them, and any review that requests changes. Fetch review threads over GraphQL so you can see which ones are resolved, and read every page.
3. Check each finding against the code before you act on it. Review bots are wrong often enough that a finding is only a claim until you've confirmed it. A true finding also has to be worth the code its fix adds, because every fix grows the PR and gives the bots new code to comment on. Then handle it one of three ways.
   - Fix it when it's a real bug, meaning the code does the wrong thing on inputs or sequences the system actually produces. Fix an edge case that can really happen when the fix stays small and adds no new state, lock, retry, timeout, cache, flag, fallback, or validation layer. Prefer a fix that deletes or narrows code over one that adds a guard. Reply with the commit SHA, react 👍, and resolve the thread.
   - Decline it when it's wrong, misreads the code, is a nitpick outside the PR's goal, or needs inputs the system never produces, such as malformed data from the project's own components, values no caller sends, or two people running a single-user tool at once. Reply with a one-sentence reason, react 👎, and resolve the thread. Bots learn from the reactions.
   - Leave it for the user when it needs a product or architecture decision, when the fix would reach beyond what the PR set out to do, or when a real edge case would need new machinery to handle. Reply that the user will decide and leave the thread open.

   Failed checks always get fixed. If the failure is a test that's known to be flaky, rerun it once with `gh run rerun <id> --failed` before digging in. Fix the code rather than loosening the check.
4. Commit each fix separately, staging files by name, and push once the whole batch is done. If the PR has fallen behind its base branch or has conflicts, rebase and push with `--force-with-lease`. That's the only force push allowed.
5. Go back to step 1 unless the PR is done or one of the reasons to stop early applies.

You don't need to wait for CI to finish before handling review comments.

## Staying in scope

Only change what a finding actually needs. If you spot other improvements, put them in your report instead of the PR. Every comment you post should carry a fix SHA or a reason, and should end with `<!-- babysit -->` so the watch script ignores your own replies.

When a bot flags code you added to answer an earlier finding, go back to the simplest version of that code instead of adding to it. A chain of fixes on fixes usually means the first fix was bigger than the finding deserved.

A person's decision outlasts a bot's reply. If someone reviewing the PR asked you to simplify it or cut scope, a later bot finding doesn't undo that. If you left a thread for the user, a bot's follow-up in that thread doesn't settle it.

Keep a running count of the lines your review fixes have added. Once they reach about a fifth of the PR's size, say so in your report and name the simpler design if you see one, then keep going.

## When the PR is done

The PR is done once all of these are true for the latest commit.

- Every check passed.
- Every expected bot has reviewed the latest commit. If a bot stays silent until the script prints `quiet`, count it as having skipped the commit, which usually means it ran out of credits.
- Nothing is open except threads you left for the user.
- No review is requesting changes.

## When to stop early

Stop and report if a push or rebase fails, or if a rebase hits conflicts you can't resolve with confidence. Stop if a human reviewer asks for changes that need the user's judgment, or if another PR makes this one unnecessary, and ask before closing it. Stop after six pushes that haven't gotten the PR to done, because something is probably going back and forth.

## Reporting back

Write two or three sentences, then a table listing each finding with its source, what you did, and the commit or reason. Say how many lines the review fixes added in total. Mention any expected bot that never reviewed the latest commit. Finish with the PR link and where it stands, whether that's merged, ready to merge, waiting on the user, or blocked.
