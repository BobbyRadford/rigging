#!/usr/bin/env bash
# Waits until something new happens on a PR, prints it, and exits.
#
# Usage: watch-pr.sh <pr-number-or-url> [quiet-seconds]
#
# New comments, reviews, failed checks, finished CI runs, new pushes, and
# conflicts each print as one line. Once something shows up, the script waits
# another 20 seconds so a bot's burst of comments arrives together. If nothing
# happens for quiet-seconds (600 by default), it prints "quiet" instead. The
# first call on a PR prints its current state.
#
# Comments containing <!-- babysit --> were posted by the agent and are
# ignored. Events already seen are remembered in $TMPDIR, so each call only
# reports what's new.
set -uo pipefail

pr=${1:?usage: watch-pr.sh <pr> [quiet-seconds]}
quiet=${2:-600}

url=$(gh pr view "$pr" --json url --jq .url) || exit 1
repo=$(sed -E 's#https://[^/]+/([^/]+/[^/]+)/pull/.*#\1#' <<<"$url")
num=${url##*/}
seen="${TMPDIR:-/tmp}/babysit-${repo//\//-}-$num.seen"
touch "$seen"

snapshot() {
  gh pr view "$num" -R "$repo" --json state,headRefOid,mergeStateStatus,statusCheckRollup --jq '
    .headRefOid[0:7] as $h
    | (if .state != "OPEN" then "state \(.state)" else empty end),
      "head \($h)",
      (if .mergeStateStatus == "DIRTY" or .mergeStateStatus == "BEHIND"
        then "merge-state \(.mergeStateStatus) \($h)" else empty end),
      (.statusCheckRollup[]
        | select((.conclusion // .state) as $c
            | ["FAILURE","ERROR","TIMED_OUT","CANCELLED","ACTION_REQUIRED","STARTUP_FAILURE"] | index($c))
        | "check-failed \(.name // .context) \(.detailsUrl // .targetUrl)"),
      (.statusCheckRollup as $c
        | if ($c | length) > 0
             and all($c[]; (.status // "COMPLETED") == "COMPLETED" and (.state // "") != "PENDING" and (.state // "") != "EXPECTED")
          then "checks-done \($h) " + (if any($c[]; (.conclusion // .state) as $x
                  | ["FAILURE","ERROR","TIMED_OUT","CANCELLED","ACTION_REQUIRED","STARTUP_FAILURE"] | index($x)) then "fail" else "pass" end)
          else empty end)' &&
  gh api "repos/$repo/issues/$num/comments" --paginate --jq '.[]
    | select(.body | contains("<!-- babysit -->") | not)
    | "comment \(.user.login) \(.html_url) \(.updated_at)"' &&
  gh api "repos/$repo/pulls/$num/comments" --paginate --jq '.[]
    | select(.body | contains("<!-- babysit -->") | not)
    | "inline \(.user.login) \(.html_url) \(.updated_at)"' &&
  gh api "repos/$repo/pulls/$num/reviews" --paginate --jq '.[]
    | select(.state != "PENDING" and (.state != "COMMENTED" or (.body // "") != ""))
    | select(.body // "" | contains("<!-- babysit -->") | not)
    | "review \(.user.login) \(.state) \(.commit_id[0:7]) \(.html_url)"'
}

news() { grep -Fxv -f "$seen" <<<"$1" | grep -v '^$'; }

start=$SECONDS
while (( SECONDS - start < quiet )); do
  if snap=$(snapshot 2>/dev/null) && [[ -n $(news "$snap") ]]; then
    sleep 20
    snap=$(snapshot 2>/dev/null) || continue
    news "$snap"
    printf '%s\n' "$snap" >>"$seen"
    exit 0
  fi
  sleep 30
done
echo "quiet: nothing new for ${quiet}s"
