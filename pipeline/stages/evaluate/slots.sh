#!/usr/bin/env bash
# evaluate/slots: how many trials Harbor may run at once on the cores this build
# holds (CPU_LOCK_QTY). Harbor applies each task.toml's cpus/memory_mb; this
# only divides the lock by them, and refuses a worker that cannot host one.
# Writes eval_resources.json, which every later step reads.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

TASKS_DIR="$(benchmark_tasks_dir)"
[ -d "$TASKS_DIR" ] || die "run the tasks phase first (missing $TASKS_DIR)"
ensure_selected_tasks

# This build's Harbor job and memory samples start empty.
mkdir -p "$HARNESS_DIR"
rm -rf "$HARNESS_DIR/harbor_runs/jenkins-${BUILD_NUMBER:-local}"
rm -f "$HARNESS_DIR/harbor_jobs_dir.txt" \
  "$HARNESS_DIR/container_mem_peak_gb" \
  "$HARNESS_DIR/container_mem.jsonl" \
  "$HARNESS_DIR/container_mem_current.json" \
  "$HARNESS_DIR/active_question.txt" \
  "$HARNESS_DIR/skipped_questions.txt" \
  "$HARNESS_DIR/oom_trials.txt"
echo "slots: cleared harbor_runs/jenkins-${BUILD_NUMBER:-local} for this run"

RESOURCE_PLAN="$WORKDIR/eval_resources.json"
jenkins_flag=()
if [ -n "${BUILD_NUMBER:-}" ]; then
  jenkins_flag=(--jenkins)
fi
plan_out="$(
  python3 "$PIPELINE_LIB/task_resources.py" plan \
    --tasks-dir "$TASKS_DIR" \
    --selected "$WORKDIR/selected_tasks.txt" \
    --cpu "${CPU_LOCK_QTY:-1}" \
    --n-rollouts "${N_ROLLOUTS:-1}" \
    --workdir "$WORKDIR" \
    --measured-peak-gb "$(cat "$HARNESS_DIR/container_mem_peak_gb" 2>/dev/null || echo 0)" \
    --out "$RESOURCE_PLAN" \
    ${jenkins_flag[@]+"${jenkins_flag[@]}"}
)" || die "this worker cannot run the selected tasks as declared (see the error above)"
eval "$plan_out"
python3 "$PIPELINE_LIB/provenance.py" record-resources \
  --inputs "$WORKDIR/eval_protocol_inputs.json" --plan "$RESOURCE_PLAN" || true
echo "OK slots: EVAL_SLOTS=$EVAL_SLOTS (CPU_LOCK_QTY=${CPU_LOCK_QTY:-1}, declared cpus=$DECLARED_CPUS memory_mb=$DECLARED_MEMORY_MB)"
