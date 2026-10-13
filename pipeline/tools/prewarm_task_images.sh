#!/usr/bin/env bash
# Prewarm a worker: prepare and prove each selected question's task image, with
# no rollout and no model call, so a real build of that question skips the
# first-time work. Runs env/harbor (the check is keyed on the pinned Harbor) and
# tasks/benchmark, then tasks/select and tasks/images once per question, as a
# Jenkins one_task build would. Works the same on Linux and macOS.
#
#   BENCHMARK=deepswe bash pipeline/tools/prewarm_task_images.sh        # every question
#   BENCHMARK=deepswe TASKS=abs-module-cache-flags,cliffy-config-file-parsing \
#     bash pipeline/tools/prewarm_task_images.sh
#
# DeepSWE: an x86-64 host pulls the published image; an arm64 host builds the
# task Dockerfile on DEEPSWE_BASE_IMAGE, falling back to the published image
# under emulation. Either way Harbor's oracle and nop agents check that image
# once, and the result is stored under $MAC_K3D_SHARE/env-check/deepswe.
# LoLBench: the task image for this worker's CPU type.
#
# Each question holds the per-node `images` lock while it is prepared, so Jenkins
# builds can run beside this; on an idle worker it only competes with itself.
# Files go to MAC_K3D_EVAL_WORKDIR (default eval-runs/ in this tree), not to a
# job's workspace. Exit 1 when any question has no proven image.
set -euo pipefail
STAGES="$(cd "$(dirname "$0")/../stages" && pwd)"
# shellcheck source=../stages/_common.sh
source "$STAGES/_common.sh"

case "${BENCHMARK:-deepswe}" in
  deepswe | lolbench) ;;
  *) die "prewarm covers DeepSWE and LoLBench; Harbor pulls the ${BENCHMARK} images at the trial" ;;
esac

# Where env/harbor installs Harbor (uv tool).
export PATH="${HOME}/.local/bin:${PATH}"
run_steps env/harbor tasks/benchmark
if [ -z "${TASK:-}${TASKS:-}${N_TASKS:-}" ]; then
  N_TASKS="$(find "$(benchmark_tasks_dir)" -mindepth 1 -maxdepth 1 -type d | grep -c . || true)"
  export N_TASKS
  export TASK_OFFSET=0
fi
echo "== tasks/select"
bash "$STAGES/tasks/select.sh"
LIST="$WORKDIR/prewarm_tasks.txt"
cp "$WORKDIR/selected_tasks.txt" "$LIST"
total="$(grep -c . "$LIST" || true)"

ready=0
out=()
i=0
while IFS= read -r tid || [ -n "$tid" ]; do
  [ -n "$tid" ] || continue
  i=$((i + 1))
  echo "== prewarm $tid ($i of $total)"
  rm -f "$WORKDIR/skipped_tasks.txt"
  if env -u TASKS TASK="$tid" N_TASKS=1 TASK_OFFSET=0 bash "$STAGES/tasks/select.sh" \
    && env -u TASKS TASK="$tid" N_TASKS=1 TASK_OFFSET=0 bash "$STAGES/tasks/images.sh"; then
    ready=$((ready + 1))
  else
    why="$(cut -f2- "$WORKDIR/skipped_tasks.txt" 2>/dev/null | head -n 1 || true)"
    out+=("$tid: ${why:-see the output above}")
  fi
done <"$LIST"

echo "prewarm ${BENCHMARK:-deepswe}: $ready of $total questions ready"
if [ "${#out[@]}" -gt 0 ]; then
  echo "kept out (no proven image on this worker):"
  printf '  %s\n' "${out[@]}"
  exit 1
fi
