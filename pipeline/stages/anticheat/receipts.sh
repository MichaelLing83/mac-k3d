#!/usr/bin/env bash
# anticheat/receipts: for every trial, the patch the grader read must match the
# capture receipt the agent wrote, at the task's declared base commit.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

# A trial no selected task claims would drop out of anti-cheat, score and
# report, so this one fails the build instead of warning.
python3 "$PIPELINE_LIB/capture_receipt.py" check-trials \
  --harness-dir "$HARNESS_DIR" \
  --task-file "$WORKDIR/selected_tasks.txt"

python3 "$PIPELINE_LIB/capture_receipt.py" annotate \
  --harness-dir "$HARNESS_DIR" \
  --tasks-dir "$(benchmark_tasks_dir)" \
  --task-file "$WORKDIR/selected_tasks.txt" \
  --benchmark "${BENCHMARK:-deepswe}" \
  || echo "WARNING: capture receipt check failed; scoring continues"
