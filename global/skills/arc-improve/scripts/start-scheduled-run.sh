#!/usr/bin/env bash
set -uo pipefail

scope="${1:-}"
automation_id="${2:-}"
run_id="${CODEX_THREAD_ID:-}"
skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
control="$skill_dir/scripts/automation_control.py"

if [[ ! "$scope" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
  echo "ERROR: start-scheduled-run requires a valid lease scope." >&2
  exit 2
fi
if [[ ! "$automation_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "ERROR: start-scheduled-run requires a valid automation id." >&2
  exit 2
fi
if [[ ! "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "ERROR: CODEX_THREAD_ID is unavailable or invalid; refusing an unowned scheduled run." >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: scheduled run must start inside a Git worktree." >&2
  exit 2
}
codex_home="${CODEX_HOME:-$HOME/.codex}"
case "$repo_root" in
  "$codex_home"/worktrees/*) ;;
  *)
    echo "ERROR: scheduled runs require an isolated Codex worktree under $codex_home/worktrees." >&2
    exit 2
    ;;
esac
nonce="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')" || exit 2
owner_json="$(python3 "$control" owner-id --run-id "$run_id" --cwd "$repo_root" --nonce "$nonce")" || exit 2
owner_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["owner_id"])' <<<"$owner_json")" || exit 2
ledger_run_id="${run_id}-${owner_id:0:12}"
run_token="$nonce"

acquire_json="$(python3 "$control" lease acquire \
  --scope "$scope" \
  --run-id "$run_id" \
  --owner-id "$owner_id" \
  --cwd "$repo_root" \
  --context-token "$run_token" \
  --metadata-json "{\"automation_id\":\"$automation_id\",\"cwd\":\"$repo_root\"}")"
acquire_status=$?

if [[ "$acquire_status" -eq 75 ]]; then
  python3 "$control" run overlap \
    --run-id "$ledger_run_id" \
    --kind "$scope" \
    --automation-id "$automation_id" \
    --scope "$scope" \
    --metadata-json "{\"trigger\":\"codex-automation\",\"thread_id\":\"$run_id\",\"owner_id\":\"$owner_id\"}" \
    --lease-run-id "$run_id" --owner-id "$owner_id" --cwd "$repo_root" \
    --context-token "$run_token" >/dev/null || exit 2
  echo "ARC Improve $scope run skipped: another scheduled run still owns the lease."
  exit 75
fi
if [[ "$acquire_status" -ne 0 ]]; then
  printf '%s\n' "$acquire_json" >&2
  exit "$acquire_status"
fi

generation="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["lease"]["generation"])' <<<"$acquire_json")" || exit 2

cleanup_setup_failure() {
  local reason="$1"
  python3 "$control" run event \
    --run-id "$ledger_run_id" \
    --event-type setup_failed \
    --payload-json "{\"reason\":\"$reason\"}" \
    --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
    --context-token "$run_token" >/dev/null 2>&1 || true
  python3 "$control" run finish \
    --run-id "$ledger_run_id" \
    --status setup-failed \
    --summary-json "{\"reason\":\"$reason\"}" \
    --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
    --context-token "$run_token" >/dev/null 2>&1 || true
  python3 "$control" lease release \
    --scope "$scope" --run-id "$run_id" --owner-id "$owner_id" \
    --generation "$generation" --context-token "$run_token" >/dev/null 2>&1 || true
  python3 "$control" context delete --token "$run_token" >/dev/null 2>&1 || true
}

python3 "$control" run begin \
  --run-id "$ledger_run_id" \
  --kind "$scope" \
  --automation-id "$automation_id" \
  --scope "$scope" \
  --metadata-json "{\"trigger\":\"codex-automation\",\"thread_id\":\"$run_id\",\"owner_id\":\"$owner_id\",\"lease_generation\":$generation}" \
  --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
  --context-token "$run_token" >/dev/null || {
    cleanup_setup_failure "immutable run record initialization failed"
    exit 2
  }

python3 "$control" context create \
  --token "$run_token" --run-id "$run_id" --owner-id "$owner_id" \
  --generation "$generation" --ledger-run-id "$ledger_run_id" \
  --scope "$scope" --cwd "$repo_root" >/dev/null || {
    cleanup_setup_failure "owned run context could not be persisted"
    exit 2
  }

python3 "$control" run event \
  --run-id "$ledger_run_id" \
  --event-type lease_acquired \
  --payload-json "$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["lease"], separators=(",",":")))' <<<"$acquire_json")" \
  --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
  --context-token "$run_token" >/dev/null || {
    cleanup_setup_failure "lease acquisition could not be recorded"
    exit 2
  }

if ! bash "$skill_dir/scripts/refresh-worktree-origin-main.sh"; then
  cleanup_setup_failure "fresh origin/main verification failed"
  exit 1
fi

head_sha="$(git rev-parse HEAD)"
origin_main_sha="$(git rev-parse --verify refs/remotes/origin/main 2>/dev/null)" || {
  cleanup_setup_failure "origin/main could not be resolved after refresh"
  exit 1
}
if [[ "$head_sha" != "$origin_main_sha" ]] || git symbolic-ref -q HEAD >/dev/null; then
  cleanup_setup_failure "scheduled worktree is not detached at exact origin/main"
  exit 1
fi
if ! python3 "$control" run event \
  --run-id "$ledger_run_id" \
  --event-type base_verified \
  --payload-json "{\"head_sha\":\"$head_sha\"}" \
  --lease-run-id "$run_id" --owner-id "$owner_id" --generation "$generation" \
  --context-token "$run_token" >/dev/null; then
  cleanup_setup_failure "base verification could not be recorded"
  exit 2
fi

echo "ARC Improve $scope lease acquired for run $run_id."
echo "ARC_IMPROVE_RUN_TOKEN=$run_token"
