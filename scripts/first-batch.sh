#!/usr/bin/env bash
# Run locally. Commit and upload the checkpoint even if individual posts failed.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p .cache
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.cache/uv}"
batch_status=0
uv run --frozen tao2typ batch --output archive --max-posts 0 --max-seconds 18000 \
  > .cache/first-batch.log 2>&1 || batch_status=$?
git add -- archive
if ! git diff --cached --quiet; then
  git commit --quiet -m "Seed PDFs locally $(date -u +%Y-%m-%dT%H:%M:%SZ)" || exit 1
fi
git push origin main || exit 1
exit "$batch_status"
