#!/usr/bin/env bash
# anticheat phase: judge what Harbor left behind. Capture receipts first, then
# a verdict per rollout that the score and report phases honour.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(anticheat/receipts anticheat/verdict)

progress 75 "anticheat: receipts and verdicts"
run_steps "${STEPS[@]}"
progress 85 "anticheat complete"
