#!/usr/bin/env bash
# anticheat/verdict: clean, flagged or rejected per rollout, from patch
# similarity to the gold patch and a scan of the agent transcript
# (pipeline/config/anticheat-v1.json). The report scores rejected rollouts as
# unresolved. A rollout Harbor never ran (no patch, transcript or reward) is
# not_run.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

rm -rf "$HARNESS_DIR/anticheat"
python3 "$PIPELINE_LIB/anticheat_verdict.py" \
  --harness-dir "$HARNESS_DIR" \
  --tasks-dir "$(benchmark_tasks_dir)" \
  --task-file "$WORKDIR/selected_tasks.txt" \
  --benchmark "${BENCHMARK:-deepswe}" \
  || echo "WARNING: anti-cheat verdicts failed; artifact.json records anticheat not_run"
