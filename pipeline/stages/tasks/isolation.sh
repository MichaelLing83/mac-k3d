#!/usr/bin/env bash
# tasks/isolation: what the agent may see and reach, checked before Harbor runs.
#   - one read-only mount, the harness tree at its target (iCode at
#     /opt/icode-host, JiuwenSwarm at /opt/jiuwenswarm-host), never overlapping
#     a benchmark (written to agent_mounts.json for every later `harbor run`);
#   - the model API host is on pipeline/config/network-allowlist-v1.json;
#   - both are recorded in eval_protocol_inputs.json for the report.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

TASKS_DIR="$(benchmark_tasks_dir)"
[ -d "$TASKS_DIR" ] || die "run tasks/benchmark first (missing $TASKS_DIR)"
ensure_selected_tasks
HOST_ICODE="$(harness_host_root)"
[ -d "$HOST_ICODE" ] || die "$(harness_display) host tree missing: $HOST_ICODE"
MOUNT_TARGET="$(harness_mount_target)"

case "${BENCHMARK:-deepswe}" in
  lolbench)
    PROV_REPO="$LOLBENCH_DIR"
    PROV_APPLIED=1
    ;;
  swebenchpro)
    PROV_REPO="$SWEBENCHPRO_DIR/src"
    PROV_APPLIED=0
    ;;
  *)
    PROV_REPO="$DEEPSWE_DIR"
    PROV_APPLIED=0
    ;;
esac

MOUNTS_FILE="$WORKDIR/agent_mounts.json"
rm -f "$MOUNTS_FILE"
MOUNTS_JSON="$(python3 "$PIPELINE_LIB/agent_mounts.py" build --root "$HOST_ICODE" --target "$MOUNT_TARGET")"
python3 "$PIPELINE_LIB/agent_mounts.py" check \
  --mounts "$MOUNTS_JSON" \
  --root "$HOST_ICODE" \
  --target "$MOUNT_TARGET" \
  --forbid "$DEEPSWE_DIR" \
  --forbid "$LOLBENCH_DIR" \
  --forbid "$SWEBENCHPRO_DIR" \
  --forbid "$TASKS_DIR" \
  || die "refusing to mount $HOST_ICODE for the agent (see errors above)"
printf '%s\n' "$MOUNTS_JSON" >"$MOUNTS_FILE"

python3 "$PIPELINE_LIB/network_allowlist.py" check --api-base "$ICODE_API_BASE" \
  || die "the agent could not reach its model API (see the error above)"

PROTOCOL_INPUTS="$WORKDIR/eval_protocol_inputs.json"
python3 "$PIPELINE_LIB/provenance.py" write-inputs \
  --out "$PROTOCOL_INPUTS" \
  --repo-dir "$PROV_REPO" \
  --tasks-dir "$TASKS_DIR" \
  --selected "$WORKDIR/selected_tasks.txt" \
  --overlay "$PIPELINE_LIB/lolbench_fix_rewards.py" \
  --applied "$PROV_APPLIED" \
  --workdir "$WORKDIR" \
  --pipeline-root "$MAC_K3D_ROOT" \
  --icode-root "$HOST_ICODE" \
  --mounts "$MOUNTS_JSON"
python3 "$PIPELINE_LIB/network_allowlist.py" record --inputs "$PROTOCOL_INPUTS"
echo "OK isolation: mount $MOUNTS_FILE, agent hosts $(python3 "$PIPELINE_LIB/network_allowlist.py" hosts | tr '\n' ' ')"
