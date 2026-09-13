#!/usr/bin/env bash
set -uo pipefail

scope="${1:-}"
status_value="${2:-}"
summary_json="${3-}"
run_id="${CODEX_THREAD_ID:-}"
run_token="${4:-${ARC_IMPROVE_RUN_TOKEN:-}}"
skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
control="$skill_dir/scripts/automation_control.py"

if [[ ! "$scope" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || \
   [[ ! "$status_value" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
   [[ ! "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
   [[ ! "$run_token" =~ ^[a-f0-9]{32}$ ]]; then
  echo "ERROR: invalid scheduled-run finish arguments." >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
context_json="$(python3 "$control" context get --token "$run_token" \
  --run-id "$run_id" --scope "$scope" --cwd "$repo_root")" || exit 76
read -r owner_id generation ledger_run_id < <(
  python3 -c 'import json,sys; c=json.load(sys.stdin)["context"]; print(c["owner_id"], c["generation"], c["ledger_run_id"])' <<<"$context_json"
)
if [[ -z "$summary_json" ]]; then
  summary_json='{}'
fi

finish_status=0
python3 "$control" lease assert --scope "$scope" --run-id "$run_id" \
  --owner-id "$owner_id" --generation "$generation" --context-token "$run_token" >/dev/null || {
    echo "ERROR: refusing to finalize a run whose lease ownership was lost." >&2
    exit 76
  }
python3 "$control" run finish \
  --run-id "$ledger_run_id" \
  --status "$status_value" \
  --summary-json "$summary_json" \
  --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
  --context-token "$run_token" >/dev/null || finish_status=$?

if [[ "$finish_status" -ne 0 ]]; then
  finish_status=0
  python3 "$control" run finish \
    --run-id "$ledger_run_id" \
    --status record-failed \
    --summary-json '{"reason":"caller supplied an invalid final summary"}' \
    --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
    --context-token "$run_token" >/dev/null || finish_status=$?
fi

release_status=0
python3 "$control" lease release --scope "$scope" --run-id "$run_id" \
  --owner-id "$owner_id" --generation "$generation" --context-token "$run_token" >/dev/null || release_status=$?

if [[ "$finish_status" -ne 0 ]]; then
  echo "ERROR: immutable run record could not be finalized." >&2
  exit "$finish_status"
fi
if [[ "$release_status" -ne 0 ]]; then
  echo "ERROR: run record finalized but lease release failed." >&2
  exit "$release_status"
fi

python3 "$control" context delete --token "$run_token" >/dev/null || {
  echo "ERROR: run completed but its local ownership context could not be removed." >&2
  exit 2
}
echo "ARC Improve $scope run finalized as $status_value."
