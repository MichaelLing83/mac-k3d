#!/usr/bin/env bash
# evaluate phase: the only phase that holds this worker's CPU lock. Plans the
# trial slots, runs the isolation canary, then one `harbor run`.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(evaluate/slots evaluate/canary evaluate/harbor_run)

progress 45 "evaluate: canary + harbor run (holding ${CPU_LOCK_QTY:-1} cores on ${NODE_NAME:-this host})"
run_steps "${STEPS[@]}"
progress 70 "evaluate complete"
