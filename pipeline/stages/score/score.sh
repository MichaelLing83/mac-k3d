#!/usr/bin/env bash
# score/score: F2P/P2P per rollout from Harbor's reward.json files.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

python3 "$PIPELINE_LIB/score_results.py" \
  --harness-dir "$HARNESS_DIR" \
  --baseline-dir "$BASELINE_DIR" \
  --tasks-dir "$(benchmark_tasks_dir)" \
  --n-tasks "$N_TASKS" \
  --task-file "$WORKDIR/selected_tasks.txt" \
  --out "$RESULTS_DIR/score-temp.json"

[ -f "$RESULTS_DIR/score-temp.json" ] || die "score-temp.json not written"
echo "OK wrote $RESULTS_DIR/score-temp.json"
