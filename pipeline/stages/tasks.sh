#!/usr/bin/env bash
# tasks phase: everything the selected tasks need before Harbor starts, and
# every check that can refuse them. No CPU lock.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(
  tasks/benchmark
  tasks/select
  tasks/icode
  tasks/icode_sandbox
  tasks/agent
  tasks/images
  tasks/isolation
  tasks/leakscan
  tasks/resources
)

progress 15 "tasks: preparing benchmark=$BENCHMARK"
run_steps "${STEPS[@]}"
progress 40 "tasks complete ($(tr '\n' ' ' <"$WORKDIR/selected_tasks.txt"))"
