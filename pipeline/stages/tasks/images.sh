#!/usr/bin/env bash
# tasks/images: each selected LoLBench task has an image for this worker's
# architecture. DeepSWE and SWE-bench Pro images are Harbor's to pull.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

TASKS_DIR="$(benchmark_tasks_dir)"
ensure_selected_tasks
TASK_IDS=()
while IFS= read -r tid || [ -n "$tid" ]; do
  case "$tid" in *[![:space:]]*) TASK_IDS+=("$tid") ;; esac
done <"$WORKDIR/selected_tasks.txt"
node_lock images
for tid in ${TASK_IDS[@]+"${TASK_IDS[@]}"}; do
  [ -d "$TASKS_DIR/$tid" ] || die "no task under $TASKS_DIR (wanted $tid)"
  [ "${BENCHMARK:-deepswe}" = "lolbench" ] || continue
  image="$(
    python3 - "$TASKS_DIR/$tid/task.toml" "$tid" <<'PY'
import re, sys
path, tid = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read() if path else ""
m = re.search(r'^docker_image\s*=\s*"([^"]+)"', text, re.M)
print(m.group(1) if m else f"smartdub26/lolbench:{tid}-1.0.0")
PY
  )"
  ensure_task_image "$tid" "$image" "$TASKS_DIR/$tid/environment"
done
node_unlock
echo "OK task images ready for $BENCHMARK"
