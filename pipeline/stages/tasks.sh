#!/usr/bin/env bash
# tasks phase: everything the selected tasks need before Harbor starts, and
# every check that can refuse them. No CPU lock. tasks/images comes before
# tasks/harness: it settles the task containers' platform, which picks the
# harness bundle (and, for an iCode release, the drop).
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(
  tasks/benchmark
  tasks/select
  tasks/images
  tasks/harness
  tasks/agent
  tasks/isolation
  tasks/leakscan
  tasks/resources
)

progress 15 "tasks: preparing benchmark=$BENCHMARK"
run_steps "${STEPS[@]}"
progress 40 "tasks complete ($(tr '\n' ' ' <"$WORKDIR/selected_tasks.txt"))"
