#!/usr/bin/env bash
# Prepare a worker's task images ahead of any eval, with no rollout and no
# model call. `mac-k3d eval prepare --benchmark deepswe` runs this.
#
#   BENCHMARK=deepswe bash pipeline/tools/prewarm_task_images.sh        # every question
#   BENCHMARK=deepswe TASKS=abs-module-cache-flags,cliffy-config-file-parsing \
#     bash pipeline/tools/prewarm_task_images.sh
#
# DeepSWE on an arm64 host (Apple silicon): clones the benchmark at DEEPSWE_REF,
# then per question builds the task Dockerfile natively on DEEPSWE_BASE_IMAGE,
# tags it with the official image name, and proves it once with Harbor's oracle
# and nop agents; the result is stored under $MAC_K3D_SHARE/env-check/deepswe.
# A question that cannot build natively, or whose build fails its check, runs
# the published image under emulation in evals; the reason is printed here.
# DEEPSWE_REBUILD=1 builds again even when a build is current. A rerun redoes
# only what changed.
# DeepSWE on an x86-64 host: nothing to prepare; Harbor pulls the published images.
# LoLBench: the task image for this worker's CPU type.
#
# Each question holds the per-node `images` lock while it is prepared. Run it
# when no DeepSWE build is running on this machine. Files go to
# MAC_K3D_EVAL_WORKDIR (default eval-runs/ in this tree), not to a job's
# workspace. DeepSWE exits 1 when a rerun could still make a question native (a
# failed build, or a check that did not finish); LoLBench when an image failed.
set -euo pipefail
STAGES="$(cd "$(dirname "$0")/../stages" && pwd)"
# shellcheck source=../stages/_common.sh
source "$STAGES/_common.sh"

case "${BENCHMARK:-deepswe}" in
  deepswe | lolbench) ;;
  *) die "prepare covers DeepSWE and LoLBench; Harbor pulls the ${BENCHMARK} images at the trial" ;;
esac
HOST_PLATFORM="$(docker_host_platform)"
if [ "${BENCHMARK:-deepswe}" = deepswe ] && [ "$HOST_PLATFORM" = linux/amd64 ]; then
  echo "prepare deepswe ($HOST_PLATFORM): nothing to prepare: Linux workers use the published images, which Harbor pulls"
  exit 0
fi

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

# One question's outcome in env_check.json: "native", or "emulated<TAB>reason<TAB>hint".
outcome() {
  python3 - "$WORKDIR/env_check.json" "$1" <<'PY'
import json, sys
try:
    entry = json.load(open(sys.argv[1], encoding="utf-8"))["tasks"][sys.argv[2]]
except (OSError, ValueError, KeyError, TypeError):
    print("failed\tno image decision\t")
    sys.exit(0)
if entry.get("mode") == "native" and entry.get("pass") is True:
    print("native")
else:
    print("\t".join(["emulated", str(entry.get("reason") or "-"), str(entry.get("hint") or "")]))
PY
}

ready=0
retry=0
out=()
i=0
while IFS= read -r tid || [ -n "$tid" ]; do
  [ -n "$tid" ] || continue
  i=$((i + 1))
  echo "== prepare $tid ($i of $total)"
  if ! env -u TASKS TASK="$tid" N_TASKS=1 TASK_OFFSET=0 bash "$STAGES/tasks/select.sh" \
    || ! env -u TASKS TASK="$tid" N_TASKS=1 TASK_OFFSET=0 MAC_K3D_DEEPSWE_PREPARE=1 \
      bash "$STAGES/tasks/images.sh"; then
    out+=("$tid: see the output above")
    retry=$((retry + 1))
    continue
  fi
  if [ "${BENCHMARK:-deepswe}" = lolbench ]; then
    ready=$((ready + 1))
    continue
  fi
  IFS=$'\t' read -r mode why hint <<<"$(outcome "$tid")"
  if [ "$mode" = native ]; then
    ready=$((ready + 1))
  else
    out+=("$tid: $why${hint:+; a rerun may make it native}")
    [ -z "$hint" ] || retry=$((retry + 1))
  fi
done <"$LIST"

if [ "${BENCHMARK:-deepswe}" = lolbench ]; then
  echo "prepare lolbench: $ready of $total questions ready"
  if [ "${#out[@]}" -gt 0 ]; then
    printf '  %s\n' "${out[@]}"
    exit 1
  fi
  exit 0
fi
echo "prepare deepswe ($HOST_PLATFORM): $total questions, $ready native, $((total - ready)) emulated"
if [ "${#out[@]}" -gt 0 ]; then
  echo "emulated (the published linux/amd64 image in evals on this worker):"
  printf '  %s\n' "${out[@]}"
fi
[ "$retry" = 0 ] || exit 1
