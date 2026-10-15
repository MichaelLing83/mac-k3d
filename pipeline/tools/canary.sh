#!/usr/bin/env bash
# Canary only: run the isolation canary on every selected task, no rollouts.
# Uses an existing eval workspace where the tasks phase already ran. Needs
# DEEPSEEK_API_KEY in the environment or .local.env, because the canary gets
# iCode's exact environment; it never calls the model.
#
#   MAC_K3D_EVAL_WORKDIR=~/jenkins-agent/workspace/lolbench_one_task/eval-runs \
#     BENCHMARK=lolbench TASKS=cpython_5,fastapi_1 bash pipeline/tools/canary.sh
#
# CANARY_ALLOW_HOST=github.com opens one host for the canary alone; it must then fail.
set -euo pipefail
export CANARY=only
export MAC_K3D_PHASE=evaluate
exec bash "$(cd "$(dirname "$0")/../stages" && pwd)/run_all.sh" "$@"
