#!/usr/bin/env bash
# Keep individual post failures in the queue while surfacing job-wide failures.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f archive/state.json ]]; then
  echo "::notice::Run bash scripts/first-batch.sh locally to seed the archive first."
  exit 0
fi
args=(batch --output archive --max-posts 20 --daily-limit 20 --max-seconds 600 --allow-post-failures)
if [[ "${FORCE:-false}" == "true" ]]; then args+=(--force); fi
report_path="${RUNNER_TEMP:-.cache}/tao2typ-daily-report.json"
mkdir -p "$(dirname "$report_path")"
batch_status=0
uv run --frozen --no-sync tao2typ "${args[@]}" > "$report_path" || batch_status=$?
uv run --frozen --no-sync python scripts/action-summary.py "$report_path"
exit "$batch_status"
