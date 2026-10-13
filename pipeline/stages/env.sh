#!/usr/bin/env bash
# env phase: is this worker fit to run any evaluation? Nothing here depends on
# the selected tasks. No CPU lock.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(env/host env/compose env/harbor env/egress env/network env/model_api)

# Written only by report/render and archive/backup; Jenkins archives whatever they name.
rm -f "$WORKDIR/last_output.txt" "$WORKDIR/last_backup.txt" "$WORKDIR/unscored_rollouts.txt"
# A model key an older pipeline left behind; evaluate now removes its own copy.
rm -f "$WORKDIR/.harbor-env"
# Per-build state a shard archives for its dispatcher. A build that stops before
# the tasks phase would otherwise ship the previous build's copies.
rm -f "$WORKDIR/eval_protocol_inputs.json" "$WORKDIR/eval_resources.json" "$WORKDIR/egress_probe.json" \
  "$WORKDIR/trial_network.json" \
  "$WORKDIR/selected_tasks.txt" "$WORKDIR/selected_tasks_offset.txt" "$WORKDIR/suite_tasks.txt" \
  "$WORKDIR/skipped_tasks.txt" "$HARNESS_DIR/container_mem.jsonl" \
  "$WORKDIR/icode_git.json" "$WORKDIR/icode_release.json" \
  "$WORKDIR/icode_bin_path.txt" "$WORKDIR/icode_host_root.txt" "$WORKDIR/container_platform.json" \
  "$HARNESS_DIR/anticheat/summary.json" "$HARNESS_DIR/anticheat/report.md" "$HARNESS_DIR/anticheat/anticheat.jsonl" \
  "$HARNESS_DIR/oom_trials.txt"

progress 0 "env: checking this worker (benchmark=$BENCHMARK n=$N_TASKS harness=$HARNESS llm=$LLM)"
run_steps "${STEPS[@]}"
progress 10 "env complete"
