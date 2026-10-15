#!/usr/bin/env bash
# tasks/benchmark: check out the selected benchmark at its pinned commit and
# prepare its task folders. Harbor later reads these folders through
# `-p <dir> -i <task>`; it never downloads a benchmark itself.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

have git || die "git required"
case "${BENCHMARK:-deepswe}" in
  lolbench)
    pin_benchmark_sha "$LOLBENCH_DIR" "$LOLBENCH_GIT_URL" "$LOLBENCH_REF"
    [ -d "$LOLBENCH_DIR/harbor_tasks" ] || die "missing $LOLBENCH_DIR/harbor_tasks after pin $LOLBENCH_REF"
    # Ensure reward.json always carries integer F2P/P2P counts (DeepSWE-shaped).
    python3 "$PIPELINE_LIB/lolbench_fix_rewards.py" "$LOLBENCH_DIR/harbor_tasks"
    python3 "$PIPELINE_LIB/provenance.py" assert-count "$LOLBENCH_DIR/harbor_tasks" "$LOLBENCH_TASK_COUNT"
    ;;
  swebenchpro)
    [ -f "$PIPELINE_LIB/swebenchpro_tasks.py" ] \
      || die "pipeline at $MAC_K3D_ROOT is too old for swebenchpro (missing pipeline/lib/swebenchpro_tasks.py). Redeploy this worker's mac-k3d binary (bash scripts/redeploy.sh) and rebuild."
    if [ ! -f "$SWEBENCHPRO_DIR/src/swe_bench_pro_eval.py" ] \
      && [ ! -f "$SWEBENCHPRO_DIR/swe_bench_pro_eval.py" ]; then
      mkdir -p "$SWEBENCHPRO_DIR"
      rm -rf "$SWEBENCHPRO_DIR/src"
      git clone --depth 1 "$SWEBENCHPRO_GIT_URL" "$SWEBENCHPRO_DIR/src"
    fi
    python3 "$PIPELINE_LIB/swebenchpro_tasks.py" \
      --src-dir "$SWEBENCHPRO_DIR" \
      --out-dir "$SWEBENCHPRO_DIR/tasks" \
      --task "${TASK:-}" \
      --tasks "${TASKS:-}" \
      --n-tasks "${N_TASKS:-1}"
    [ -d "$SWEBENCHPRO_DIR/tasks" ] || die "missing $SWEBENCHPRO_DIR/tasks after materialize"
    ;;
  deepswe | "")
    pin_benchmark_sha "$DEEPSWE_DIR" "$DEEPSWE_GIT_URL" "$DEEPSWE_REF"
    [ -d "$DEEPSWE_DIR/tasks" ] || die "missing $DEEPSWE_DIR/tasks after pin $DEEPSWE_REF"
    python3 "$PIPELINE_LIB/provenance.py" assert-count "$DEEPSWE_DIR/tasks" "$DEEPSWE_TASK_COUNT"
    ;;
  *)
    die "unknown BENCHMARK=${BENCHMARK} (use deepswe, lolbench, or swebenchpro)"
    ;;
esac

TASKS_DIR="$(benchmark_tasks_dir)"
TASK_COUNT="$(find "$TASKS_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
echo "OK $BENCHMARK tasks dir $TASKS_DIR ($TASK_COUNT task dirs)"
