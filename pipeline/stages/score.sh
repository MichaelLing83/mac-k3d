#!/usr/bin/env bash
# score phase: F2P/P2P per rollout into results/score-temp.json.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(score/score)

progress 88 "score: f2p/p2p"
run_steps "${STEPS[@]}"
progress 90 "score complete"
