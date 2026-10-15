#!/usr/bin/env bash
# Grade each LLM-only patch with Harbor's verifier. Parallelism matches CPU_LOCK_QTY.
set -euo pipefail
source "$(cd "$(dirname "$0")/../stages" && pwd)/_common.sh"

eval_parallel_degree || die "N_ROLLOUTS and CPU_LOCK_QTY must be integers >= 1"
have harbor || die "harbor not on PATH (run the env phase)"
[ -f "$PIPELINE_LIB/patch_harbor_agent.py" ] || die "missing patch_harbor_agent.py"
ensure_selected_tasks
TASKS_DIR="$(benchmark_tasks_dir)"
echo "baseline grade: n_rollouts=$N_ROLLOUTS slots=$EVAL_SLOTS cpus_each=$EVAL_CPUS_EACH"

grade_one() {
  local tid="$1" dest="$2"
  local task_path="$TASKS_DIR/$tid"
  local run_dir="$WORKDIR"
  if [ "${BENCHMARK:-deepswe}" = "lolbench" ]; then
    task_path="harbor_tasks/${tid}"
    run_dir="$LOLBENCH_DIR"
  fi
  local jobs="$dest/harbor"
  mkdir -p "$jobs"
  local mounts
  mounts="$(python3 - "$dest" <<'PY'
import json, sys
print(json.dumps([{"type": "bind", "source": sys.argv[1], "target": "/opt/baseline-patch"}]))
PY
)"
  local log="$dest/harbor.log"
  local declared repo_decl="" base_decl=""
  local -a capture_ae=()
  declared="$(python3 "$PIPELINE_LIB/capture_receipt.py" declared-repo \
    --task-dir "$TASKS_DIR/$tid" --benchmark "${BENCHMARK:-deepswe}")" || declared=""
  { read -r repo_decl; read -r base_decl; } <<<"$declared" || true
  if [ -n "$repo_decl" ]; then capture_ae+=(--ae "MAC_K3D_REPO=$repo_decl"); fi
  if [ -n "$base_decl" ]; then capture_ae+=(--ae "MAC_K3D_BASE_COMMIT=$base_decl"); fi
  echo "baseline grade: $tid $(basename "$dest") cpus=$EVAL_CPUS_EACH"
  set +e
  (
    cd "$run_dir"
    export PYTHONPATH="$PIPELINE_LIB${PYTHONPATH:+:$PYTHONPATH}"
    harbor run \
      -p "$task_path" \
      -a "patch_harbor_agent:PatchAgent" \
      -m "${DEEPSEEK_MODEL:-deepseek-flash}" \
      --job-name "$(basename "$dest")" \
      --jobs-dir "$jobs" \
      --no-delete \
      -n 1 \
      -k 1 \
      --override-cpus "$EVAL_CPUS_EACH" \
      -y \
      --mounts "$mounts" \
      --ae "MAC_K3D_BENCHMARK=${BENCHMARK:-deepswe}" \
      --ae "PYTHONDONTWRITEBYTECODE=1" \
      ${capture_ae[@]+"${capture_ae[@]}"}
  ) >"$log" 2>&1
  local rc=$?
  set -e
  local reward
  reward="$(find "$jobs" -name reward.json -type f 2>/dev/null | head -n 1 || true)"
  if [ -n "$reward" ]; then
    mkdir -p "$dest/verifier"
    cp -f "$reward" "$dest/verifier/reward.json"
    echo "OK baseline reward $dest/verifier/reward.json"
  else
    echo "missing reward.json" >>"$dest/notes.txt"
    echo "WARNING: no reward.json for $tid $(basename "$dest") (see $log)"
  fi
  return 0
}

slots=0
while read -r tid; do
  [ -n "$tid" ] || continue
  task_dir="$BASELINE_DIR/$tid"
  [ -d "$task_dir" ] || continue
  for dest in "$task_dir"/attempt-*; do
    [ -d "$dest" ] || continue
    [ -f "$dest/agent.patch" ] || continue
    grade_one "$tid" "$dest" &
    slots=$((slots + 1))
    if [ "$slots" -ge "$EVAL_SLOTS" ]; then
      wait -n || true
      slots=$((slots - 1))
    fi
  done
done <"$WORKDIR/selected_tasks.txt"
wait || true
echo "baseline grade complete"
