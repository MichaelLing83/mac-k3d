#!/usr/bin/env bash
# tools/baseline: the LLM-only baseline arm (DeepSeek API, no iCode), graded by
# tools/grade_baseline.sh. Manual; not a Jenkins phase. Run after the tasks phase.
set -euo pipefail
source "$(cd "$(dirname "$0")/../stages" && pwd)/_common.sh"

progress 75 "baseline: DeepSeek baseline arm (benchmark=$BENCHMARK n=$N_TASKS)"

TASKS_DIR="$(benchmark_tasks_dir)"
[ -d "$TASKS_DIR" ] || die "run the tasks phase first (missing $TASKS_DIR)"
[ -n "${DEEPSEEK_API_KEY:-}" ] || die "$(missing_deepseek_key_hint)"

ensure_selected_tasks
mkdir -p "$BASELINE_DIR"
python3 "$PIPELINE_LIB/baseline_deepseek.py" \
  --tasks-dir "$TASKS_DIR" \
  --n-tasks "$N_TASKS" \
  --task-file "$WORKDIR/selected_tasks.txt" \
  --out-dir "$BASELINE_DIR" \
  --model "${DEEPSEEK_MODEL:-deepseek-flash}" \
  | tee "$BASELINE_DIR/baseline.log"

bash "$(cd "$(dirname "$0")" && pwd)/grade_baseline.sh"

progress 85 "baseline complete"
