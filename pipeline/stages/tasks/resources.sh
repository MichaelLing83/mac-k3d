#!/usr/bin/env bash
# tasks/resources: the whole CPUs this build's tasks declare. The Evaluate
# stage locks that many cores of this worker. RAM is not part of the count.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

TASKS_DIR="$(benchmark_tasks_dir)"
[ -d "$TASKS_DIR" ] || die "run tasks/benchmark and tasks/select first (missing $TASKS_DIR)"
ensure_selected_tasks
python3 "$PIPELINE_LIB/task_resources.py" declare \
  --tasks-dir "$TASKS_DIR" \
  --selected "$WORKDIR/selected_tasks.txt" \
  --out "$WORKDIR/cpus_each.txt"
echo "OK resources: lock $(tr -d '[:space:]' <"$WORKDIR/cpus_each.txt") CPUs (from task.toml; RAM is not a limit)"
