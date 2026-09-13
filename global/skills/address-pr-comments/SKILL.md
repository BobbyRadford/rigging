---
name: "address-pr-comments"
description: "Address unresolved GitHub PR review feedback and red PR CI checks — locate the PR, fetch inline review threads, PR-level comments, review bodies, and failing check runs, then fix each actionable thread or failing build with grounded evidence, focused verification, one commit per thread or failing-check root cause, push, reply/resolve review threads when applicable, and watch CI back to green. Be especially good at mutation-gate failures: inspect logs/artifacts for surviving mutants, add/repair behavioral tests or fix escaped production bugs, and never weaken the gate to pass. Trigger phrases — \"address PR comments\", \"address review comments\", \"fix review feedback\", \"respond to PR review\", \"/address-pr-comments\", \"handle the review\", \"fix failing CI on this PR\", \"make PR checks green\", \"address mutation-gate\", \"fix mutation gate\". Skip for — opening a new PR (use open-pr), editing a PR description (use `gh pr edit`), local failures unrelated to a PR, or branches with no open PR."
---

> **User-question protocol:** Whenever this skill needs the user to pick between options, confirm an action, or answer a multiple-choice prompt, you MUST call the `AskUserQuestion` tool to render a proper interactive picker. Do NOT print numbered options as plain text and wait for the user to type a number — that produces a degraded UX. Free-form questions (open-ended typing) may be asked in prose, but any time you would write "1) … 2) … 3) …", use `AskUserQuestion` instead.


# Address PR Comments

Resolve every reviewer thread and red CI check on a PR with a clear audit trail. Review threads get a per-thread commit, reply, and resolution. CI failures get a per-root-cause commit, focused verification, and a watched rerun back to green; reply only when there is an actual reviewer/bot comment thread to answer.

---

## Phase 1 — Locate the PR

Determine the target PR from the user's argument or current branch:

```bash
# explicit number/URL provided
gh pr view <number-or-url> --json number,headRefName,baseRefName,url,state,isDraft

# current branch
gh pr view --json number,headRefName,baseRefName,url,state,isDraft

# issue number provided — find linked PR
gh issue view <number> --json closedByPullRequestsReferences
```

**Exit conditions:**
- PR found and `state=OPEN` → continue.
- No PR for current branch → ask the user for a PR number/URL; do not guess.
- PR is `MERGED` or `CLOSED` → stop and ask the user to confirm.

Checkout the PR branch if not already on it: `gh pr checkout <number>`.

---

## Phase 2 — Fetch review feedback and CI state

Use the comments APIs and the checks APIs. Missing either means missing PR work.

```bash
PR=<number>
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)  # e.g. owner/repo
OWNER=${REPO%/*}
NAME=${REPO#*/}

gh pr view "$PR" --json number,title,url,headRefName,baseRefName,headRefOid,statusCheckRollup,reviews,comments
gh pr checks "$PR" --json bucket,completedAt,description,event,link,name,startedAt,state,workflow

# Inline (review) comments — anchored to a file/line
gh api "repos/$REPO/pulls/$PR/comments" --paginate \
  --jq '.[] | {id, user: .user.login, path, line, body, in_reply_to_id, commit_id, created_at, html_url}'

# PR-level issue comments (top-level conversation, not anchored; includes bots)
gh api "repos/$REPO/issues/$PR/comments" --paginate \
  --jq '.[] | {id, user: .user.login, body, created_at, html_url}'

# Review threads with resolved state (GraphQL — needed to skip already-resolved)
gh api graphql -f query='
  query($owner:String!,$repo:String!,$pr:Int!){
    repository(owner:$owner,name:$repo){
      pullRequest(number:$pr){
        reviewThreads(first:100){
          nodes{ id isResolved isOutdated comments(first:50){ nodes{ id databaseId body path line author{login} } } }
        }
      }
    }
  }' -f owner=$OWNER -f repo=$NAME -F pr=$PR

# Check-run details for failures not fully represented by gh pr checks
HEAD_SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
gh api "repos/$REPO/commits/$HEAD_SHA/check-runs" --paginate \
  --jq '.check_runs[] | select(.conclusion == "failure" or .conclusion == "timed_out" or .status != "completed") | {id, name, status, conclusion, details_url, html_url, check_suite_id, started_at, completed_at, output}'
```

**Build two worklists:**
- **Review threads:** threads where `isResolved=false`. Skip resolved threads. Note `isOutdated=true` threads — the anchor line may have moved; verify before acting.
- **CI failures:** checks where `bucket=fail`, `conclusion=failure`, or `conclusion=timed_out` for the current PR head SHA. Deduplicate PR-level CI bot comments against the underlying failing check. Ignore canceled/skipped runs only when a newer run exists for the same head SHA.

**Pagination caps**: `reviewThreads(first:100)` truncates at 100 threads, and `comments(first:50)` truncates at 50 per thread. For larger PRs, also request `pageInfo{hasNextPage endCursor}` and re-query with `after:$cursor` until `hasNextPage=false`. If the first page already returns `hasNextPage=true`, you have not seen all threads — do NOT proceed to Phase 3 with a partial worklist.

---

## Phase 3 — Address worklist items

### 3A. Review thread loop (fan out to subagents)

For each unresolved review thread, in order from oldest to newest, **dispatch a general-purpose subagent** — one invocation per thread, sequentially. Each subagent isolates the read-edit-commit-reply-resolve work in a fresh context, so the parent's window stays clean as you work through 10+ threads.

**Pass to the subagent** (per invocation):

```
PR_NUMBER: <number>
REPO: <owner/repo>
BASE_REF: <base branch name>

THREAD:
  threadNodeId: <GraphQL node id from Phase 2>
  isOutdated: <bool>
  rootCommentId: <REST id of the first comment — for in_reply_to>
  rootCommentDatabaseId: <database id>
  path: <file path or null>
  line: <line number or null>
  author: <github login>
  body: <comment markdown>
  isInline: <true for file-anchored, false for PR-level>
```

**The subagent's contract**:
- Read the file at `path:line` (with the branch-local diff context).
- Classify the comment: `change_request` | `question` | `nit` | `stale` | (sub-decision) `declined`.
- If applicable: edit code, commit (one commit per thread), reply via the right gh API surface, resolve the thread (unless declined).
- Return a structured JSON result.

**Why fan out?** Each thread is a self-contained unit (one comment, one diff slice, one fix, one reply). Reading file context for each one accumulates in the parent's window if done inline; isolating per-thread keeps the parent at a constant context size regardless of PR size.

**Why sequential, not parallel?** All threads commit to the same branch. Parallel commits race on `git index.lock` and blow up the one-commit-per-comment audit trail.

After each subagent returns, record its result in a worklist for Phase 4. If the subagent returns `status: "error"`, surface immediately to the user and stop — do not auto-retry.

**Manual fallback** — if for any reason the subagent isn't available or the thread is unusually complex (multi-comment back-and-forth requiring context the agent can't be briefed with), fall back to running the steps inline:

### 3a. Ground the fix
- Read the file at the comment's `path` and `line`.
- Run `git log <base>..HEAD -- <path>` to see what this branch already changed there.
- If the anchor is stale (line shifted, code rewritten), re-read surrounding context and confirm the comment still applies. If it doesn't, note this for the reply (don't silently skip).

### 3b. Before fixing, ask yourself:
- **Is this a change request, a question, or a nit?** Reviewers signal differently — a question may not want code touched at all; a nit may want acknowledgement, not action.
- **What outcome would satisfy the reviewer?** The fix you want to write and the fix they're asking for can diverge — pick theirs, or push back explicitly.
- **Does the question imply a fix?** If a reviewer asks "why not X here?" and X is obviously better, the question IS the change request. If X is a tradeoff, answer the question and let them decide.
- **Is the nit worth a commit?** Trivial → fix. Stylistic preference you disagree with → reply, decline, leave unresolved for them to resolve.

This classification drives whether you fix-and-reply, reply-only, or decline-and-leave-unresolved in 3c–3e.

### 3c. Apply the fix (when needed)
Edit the code. Stay in scope — fix only what this comment is about. Do not opportunistically refactor neighbors.

### 3d. Commit (one per addressed comment)
```bash
git add <files>
git commit -m "fix: address <short review concern>"
```
One thread = one commit. If a single reviewer comment legitimately requires changes in multiple files, that's still one commit. If you replied without code changes, no commit. Use the appropriate Conventional Commits type (`fix:`, `test:`, `docs:`, `refactor:`) for the actual change.

### 3e. Reply and resolve
Reply to the thread (use `in_reply_to` for inline comments so it threads properly):

```bash
# Inline reply — REST
gh api "repos/$REPO/pulls/$PR/comments" -f body="<reply text>" -F in_reply_to=<comment-id> -X POST

# Resolve the thread — GraphQL (use the thread node id from Phase 2)
gh api graphql -f query='mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread{ id isResolved } } }' -f id=<thread-node-id>
```

Reply text:
- **Fixed**: one short sentence on what changed + the commit SHA. Example: `Fixed in abc1234 — extracted to formatUserName().`
- **Question only**: answer it directly.
- **Declined**: explain why briefly; do NOT auto-resolve a declined thread — leave for reviewer.

### 3B. CI failure loop

For each failing CI work item, group only failures proven to have the same root cause. A single compiler error fanning out to three jobs is one work item; unrelated red jobs are separate work items and separate commits.

**Fetch the real failure evidence:**

```bash
HEAD_REF=$(gh pr view "$PR" --json headRefName -q .headRefName)
HEAD_SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)

gh run list --branch "$HEAD_REF" --commit "$HEAD_SHA" \
  --json databaseId,headSha,workflowName,name,status,conclusion,url

gh run view <run-id> --log-failed
gh run view <run-id> --log                 # if --log-failed is truncated
gh api "repos/$REPO/check-runs/<check-run-id>/annotations" --paginate
```

If the failed job uploaded artifacts, download the artifacts from that exact run:

```bash
mkdir -p work/ci-artifacts/<run-id>
gh run download <run-id> -D work/ci-artifacts/<run-id>
rg -n "fail|error|assert|surviv|mutant|mutation|diff|patch|coverage" work/ci-artifacts/<run-id>
```

**Classify before editing:**
- `real-bug`: CI exposed production behavior that is wrong.
- `stale-test`: tests encode old intended behavior and the PR intentionally changed it.
- `test-gap`: production code is acceptable, but the PR lacks tests for the behavior CI expects.
- `flake`: same command alternates pass/fail without code changes; identify nondeterminism before patching.
- `infra`: missing secret/runner/tool/cache/service, or a broken reusable workflow/action. Do not edit product code to hide infra.

**Mutation-gate specialization:** when a check named `mutation-gate`, `mutation gate`, or similar fails, treat the log/artifacts as the source of truth. Extract every surviving mutant's file, line/range, mutator, diff/patch, and test command if present. For each survivor:
- Prefer adding or repairing a behavioral test that fails against the mutant and passes against the real code.
- If the mutant reveals an escaped production bug, fix production code and keep/add the test that kills it.
- If the mutant is genuinely equivalent, write down the source-level reason and ask before adding an ignore or exclusion.
- Never pass by lowering thresholds, broadening ignore globs, deleting the changed line from analysis, adding retries, or marking mutants equivalent without proof.

Reproduce with the exact command/env printed by the job whenever possible. If the job is CI-only, run the closest focused local command and state the local verification limit in the final report and any PR reply.

**Fix and commit:**

```bash
git status --short
# edit the minimum files needed
# run the focused command from the failed job, then any adjacent check needed by the touched code
git diff -- <paths>
git add <paths>
git commit -m "fix: <short failing-check root cause>"
```

Use `test:` when the change only adds/updates tests for a mutation/test gap. Use `fix:` when production behavior changes. One CI work item = one commit.

---

## Phase 4 — Push and verify

```bash
git push
```

**If the push is rejected** (non-fast-forward — common after the reviewer rebased main into the PR branch): STOP. Run `git fetch` and report the divergence to the user. Do NOT auto-`--force-with-lease` or auto-rebase — ask the user how they want to reconcile. Force-pushing during an active review can drop reviewer commits or invalidate review state.

Then verify zero unresolved, non-declined threads remain and CI is no longer red:

Re-run the Phase 2 `reviewThreads` GraphQL query and assert that every thread node has `isResolved=true` OR is on the declined-list reported above. Any other unresolved thread means a comment was missed — return to Phase 3 for it.

Watch or poll checks for the pushed head:

```bash
gh pr checks "$PR" --watch --fail-fast
gh pr checks "$PR" --json bucket,name,state,workflow,link
```

If CI is still red, return to Phase 2 with the new head SHA and fresh logs. Do not assume the second failure is the same failure.

Report to the user: number of threads addressed, number of CI failures fixed, number of commits made, number of replies sent, verification commands, current PR check status, and any threads/checks left intentionally unresolved with rationale.

---

## NEVER

- **NEVER bundle multiple comment fixes into one commit**
  **Instead:** One commit per addressed comment, even if commits touch the same file. Use the comment's gist in the subject.
  **Why:** The user picked one-commit-per-comment so reviewers can map each commit back to the thread that drove it. A combined commit destroys that audit trail.

- **NEVER reply without resolving (when you fixed it) or resolve without replying**
  **Instead:** Fix → commit → reply with the commit SHA → resolve. All four, in that order.
  **Why:** A resolved-but-silent thread reads as dismissive; a replied-but-unresolved thread leaves the reviewer wondering if you're still working on it.

- **NEVER act on a thread without checking it's still unresolved**
  **Instead:** Use the GraphQL `reviewThreads.isResolved` field from Phase 2; skip resolved threads entirely.
  **Why:** Re-fixing already-resolved comments creates noise commits and confuses the reviewer about what changed since their last pass.

- **NEVER skip the inline-comments API and rely on `gh pr view`**
  **Instead:** Hit both `pulls/:n/comments` (inline) AND `issues/:n/comments` (top-level). `gh pr view --comments` shows only the latter.
  **Why:** Inline review comments are the bulk of review feedback and live on a different endpoint. Missing them means addressing a fraction of the review.

- **NEVER auto-resolve a thread you declined**
  **Instead:** Reply with the rationale and leave `isResolved=false` for the reviewer to resolve.
  **Why:** Some teams treat thread resolution as the reviewer's signal of acceptance. Self-resolving a decline overrides that signal and erodes trust.

- **NEVER opportunistically edit unrelated code while in the file**
  **Instead:** Stay in the scope of the comment. If you spot something else, mention it in the report at the end.
  **Why:** Drive-by edits inflate the per-comment commit, hide unrelated changes from review, and break the one-commit-per-comment promise.

- **NEVER act on stale/outdated thread anchors without verifying the comment still applies**
  **Instead:** Re-read the current code at that path. If the concern is no longer relevant, reply explaining and resolve. If it's moved, address it at the new location.
  **Why:** After rebases or earlier fixes, an anchor can point to code that no longer matches what the reviewer was critiquing — fixing the new code is a non-sequitur.

- **NEVER "fix" CI by weakening the gate**
  **Instead:** Fix the code, fix the test, or surface an infra/config blocker. For mutation-gate failures, kill the surviving mutant with behavior coverage or prove equivalence before asking about an exclusion.
  **Why:** Lowering thresholds, adding broad excludes, or adding `continue-on-error` makes the PR green by deleting signal every future PR depends on.

- **NEVER edit both production code and tests before classifying a CI failure**
  **Instead:** Decide whether the failure is `real-bug`, `stale-test`, `test-gap`, `flake`, or `infra` first, then touch the correct side.
  **Why:** Changing both sides at once hides whether CI caught a regression or the test needed to move with intentional behavior.

---

## Post-step: /simplify

After all comment/CI commits land, run the `simplify` skill against the cumulative diff to catch duplication or smell drift introduced across the per-item fixes.
