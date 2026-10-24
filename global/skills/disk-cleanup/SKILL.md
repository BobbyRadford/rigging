---
name: disk-cleanup
description: Free disk space by removing idle build output, merged worktrees, container images and stale scratch files without losing work. Use when the user asks to clean up a machine or free space, or when commands fail with ENOSPC or "no space left on device".
---

# Disk cleanup

Agent work fills a disk in predictable ways. Every worktree gets its own build output, container images pile up, and scratch folders outlive the work they were for. Your job is to delete as much of that as you can. For each thing you delete, you must first show that it holds no unique work and that nothing is using it.

Stay inside the current user's home directory. Other users and the system share the same volume, so the free space you measure includes their files too. Those files are the user's call, so mention them in the report if they look large.

The lists you build get long, so write them to files in a new scratch directory and delete that directory when you finish. If the shell is zsh, it doesn't split `$VAR` into words, so put a command you reuse in a shell function instead of a variable.

## 1. Measure

Find the volume that holds the home directory and record its free space. On macOS that's the data volume (`/System/Volumes/Data`), not `/`. Measure every top-level entry in the home directory, dotfiles included. Then drill into anything over a few GB until you know what kind of thing is using the space. This step is done when you can name what fills at least 90% of the space the home directory uses.

These places usually hold most of it:

- Build output inside repos and worktrees, such as Rust `target/`, `node_modules/`, Python `.venv/`, `.next/`, `dist/` and `build/`.
- Worktrees that agent harnesses create, either inside a repo (like `.claude/worktrees/`) or in a harness's own worktree folder. Run `git worktree list` in each repo to find them all.
- A container VM's disk image, from tools like colima, Lima, OrbStack or Docker Desktop. The file can be much bigger than `docker system df` reports.
- `~/Downloads`, scratch and evidence folders, and pulled data like logs, recordings and datasets.
- Caches under `~/.cache` and `~/Library/Caches`, plus the npm, pip, Homebrew and cargo caches.

## 2. Find what's live

Other agent sessions and builds are probably running on this machine right now. Anything they're using is live, and you leave it alone even when it looks stale. Build the live list before you delete anything.

- Record every process's current directory and reduce each one to its worktree or repo root. On macOS run `lsof -d cwd`, and on Linux read `/proc/*/cwd`.
- Collect the paths named in running command lines from `ps -eo args`. This catches builds and background loops that `cd` somewhere else. When you `grep` that output, bracket one character of the pattern (`[c]argo`) so the grep doesn't match itself.
- Treat a tree as live if its git index or build output changed in the last day. For a worktree, the index is at `$(git rev-parse --absolute-git-dir)/index`.
- Look for lock or marker files that a project uses to keep jobs from running at the same time.

Sessions keep starting while you work, so check recency again right before each deletion.

## 3. Delete idle build output

Build output in a tree that isn't live can be rebuilt. Once the user has asked for a cleanup, you can delete it without asking again. Find build folders by name inside every repo and worktree. Skip any inside a live tree, and delete the rest. Prune the search so it doesn't descend into `node_modules` or into the build folders themselves. The cost is a cold build the next time someone works in that tree. This step usually frees the most space.

## 4. Remove worktrees whose work has landed

A worktree is safe to remove only when it holds no unique work. Check all of the following, and keep the worktree if any check fails or you can't run it.

- It's idle, with no live process and no change to its git index in the last two days.
- `git status --porcelain` prints nothing. Ignored files like build output don't count.
- Its commits have landed. Either `HEAD` is an ancestor of the remote's default branch, or `HEAD` is exactly the head commit of a merged pull request for its branch. A squash merge leaves the branch's own commits out of the default branch, so you need the second check to catch it. On GitHub, `gh pr list --state merged --json headRefName,headRefOid` lists merged PRs with their head commits. A branch with commits made after its PR merged holds unique work, so keep it.
- Every submodule is clean, and each submodule's `HEAD` is on a remote branch. Removing a worktree deletes its submodules' git directories, and any commit that existed only there goes with them.
- No other worktree keeps its git metadata inside this one. A worktree created from a submodule stores that metadata under the parent worktree's git directory, at `.git/worktrees/<parent>/modules/<path>/worktrees/<name>`. Removing the parent orphans it. Read the `gitdir:` line in every other worktree's `.git` file and look for this worktree's path. Deal with any nested worktree first, using these same checks.

Get the list of worktrees from `git worktree list`, not from globbing folders. A worktree can sit directly in a folder that holds other worktrees, and a glob like `folder/*/*` then treats its subdirectories as worktrees.

Remove each one with `git -C <main repo> worktree remove <path>`. A worktree with submodules needs `--force`, which is fine once your own checks have passed. Keep the local branches. They take almost no space and keep the commits reachable. Afterwards, run `git worktree prune` in each repo. Then check every remaining worktree's `.git` file for a `gitdir:` that no longer exists, which means you orphaned it.

You can still check an orphaned worktree for lost work. Point `GIT_INDEX_FILE` at a temporary file, read the commit it should match into that index with `git read-tree`, and run `git diff --stat` and `git ls-files --others --exclude-standard` with `--git-dir` set to the main repo and `--work-tree` set to the orphaned folder. If both print nothing, the folder matches the commit and you can delete it.

## 5. Propose the rest and ask

What's left can hold data the user wants. List each item with its size, what deleting it costs and what you'd keep, then ask before deleting.

- Container images and build cache can be rebuilt. Named volumes can hold real data, so look inside each one before you propose it. `docker system prune -a --volumes` skips named volumes, so remove those with `docker volume rm`. Pruning frees space inside the container VM, but the VM's disk file on the host only shrinks after a trim, such as `colima ssh -- sudo fstrim -a`. Measure the file before and after the trim.
- For `~/Downloads` and scratch folders, check memory files and project notes for anything that points into them, and keep those paths.
- For pulled data like logs, recordings and datasets, say whether another copy exists.
- Package manager caches and leftover browser profiles from automation can go. Keep installed test browsers if a test might be running, because deleting them breaks that test.

## 6. Report

Tell the user the free space before and after. Say what you deleted, with sizes, and what you kept and why. Then say what the deletions will cost later, such as cold builds or images that have to be pulled again. If something went wrong, such as an orphaned worktree, say what happened and what you checked to show that nothing was lost. Then delete your scratch directory.
