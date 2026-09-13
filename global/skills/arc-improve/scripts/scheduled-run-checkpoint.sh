#!/usr/bin/env bash
set -euo pipefail

scope="${1:-}"
phase="${2:-}"
run_id="${CODEX_THREAD_ID:-}"
run_token="${3:-${ARC_IMPROVE_RUN_TOKEN:-}}"
skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
control="$skill_dir/scripts/automation_control.py"

if [[ ! "$scope" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || \
   [[ ! "$phase" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
   [[ ! "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
   [[ ! "$run_token" =~ ^[a-f0-9]{32}$ ]]; then
  echo "ERROR: invalid scheduled-run checkpoint arguments." >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
context_json="$(python3 "$control" context get --token "$run_token" \
  --run-id "$run_id" --scope "$scope" --cwd "$repo_root")"
read -r owner_id generation ledger_run_id < <(
  python3 -c 'import json,sys; c=json.load(sys.stdin)["context"]; print(c["owner_id"], c["generation"], c["ledger_run_id"])' <<<"$context_json"
)

python3 "$control" lease assert --scope "$scope" --run-id "$run_id" \
  --owner-id "$owner_id" --generation "$generation" --context-token "$run_token" >/dev/null
python3 "$control" lease heartbeat --scope "$scope" --run-id "$run_id" \
  --owner-id "$owner_id" --generation "$generation" --context-token "$run_token" >/dev/null
python3 "$control" run event \
  --run-id "$ledger_run_id" \
  --event-type checkpoint \
  --payload-json "{\"phase\":\"$phase\"}" \
  --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
  --context-token "$run_token" >/dev/null

echo "ARC Improve $scope checkpoint recorded: $phase"
