#!/usr/bin/env bash
set -euo pipefail

remote="${CODEX_WORKTREE_BASE_REMOTE:-origin}"
branch="${CODEX_WORKTREE_BASE_BRANCH:-main}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$root" ]; then
  fail "ARC Improve worktree refresh must run inside a Git repository."
fi

codex_home="${CODEX_HOME:-$HOME/.codex}"
case "$root" in
  "$codex_home"/worktrees/*) ;;
  *) exit 0 ;;
esac

if ! git show-ref --verify --quiet "refs/heads/$branch"; then
  fail "Cannot identify the local $branch branch used to create this managed worktree."
fi

current_ref="$(git symbolic-ref -q HEAD || true)"
if [ -n "$current_ref" ] && [ "$current_ref" != "refs/heads/$branch" ]; then
  exit 0
fi

head_sha="$(git rev-parse HEAD)"
local_branch_sha="$(git rev-parse "refs/heads/$branch")"
if [ "$head_sha" != "$local_branch_sha" ]; then
  exit 0
fi

if ! worktree_status="$(git -c submodule.recurse=false status --porcelain --ignore-submodules=all)"; then
  fail "Cannot establish a fresh $remote/$branch base because worktree status could not be read."
fi
if [ -n "$worktree_status" ]; then
  fail "Cannot establish a fresh $remote/$branch base because the managed worktree has local changes, including untracked files."
fi

if ! git -c submodule.recurse=false fetch --no-write-fetch-head --no-recurse-submodules --prune \
  "$remote" "+refs/heads/$branch:refs/remotes/$remote/$branch"; then
  fail "Cannot establish a fresh $remote/$branch base because fetch failed."
fi

if ! remote_branch_sha="$(git rev-parse --verify --quiet "refs/remotes/$remote/$branch")"; then
  fail "Cannot establish a fresh $remote/$branch base because the remote branch could not be resolved."
fi

if [ "$head_sha" != "$remote_branch_sha" ] || [ -n "$current_ref" ]; then
  if ! git -c submodule.recurse=false switch --no-recurse-submodules --detach "$remote_branch_sha"; then
    fail "Fetched $remote/$branch but could not detach the managed worktree at it."
  fi
fi

refreshed_sha="$(git rev-parse HEAD)"
if [ "$refreshed_sha" != "$remote_branch_sha" ]; then
  fail "Managed worktree HEAD does not match $remote/$branch after refresh."
fi

echo "Verified ARC Improve worktree at $remote/$branch (${remote_branch_sha:0:12}) without updating submodules."
