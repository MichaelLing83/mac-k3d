#!/usr/bin/env bash
# Verify this machine is ready to launch an evaluation task (bootstrap + Docker + eval CLI).
set -euo pipefail
# shellcheck source=./_common.sh
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

require_mac_k3d

have docker || die "docker not on PATH"
docker info >/dev/null 2>&1 || die "docker info failed (no Server / permission). Linux: log out/in. macOS: open Docker Desktop."
pass "docker Server section present"
bash "$(cd "$(dirname "$0")/../../pipeline/stages" && pwd)/env/compose.sh"
pass "docker compose and buildx present"

"$MAC_K3D_BIN" --help | grep -q eval || die "mac-k3d --help does not list eval"
pass "mac-k3d lists eval"

CODE="$(jenkins_login_code)"
[ "$CODE" = "200" ] || die "Jenkins UI ${JENKINS_URL}/login HTTP $CODE — controller must be up before Jenkins eval"
pass "Jenkins UI reachable for eval"

# Prefer an online agent when worker config exists
if [ -f "$WORKER_CONFIG" ]; then
  if have systemctl && systemctl --user is-active mac-k3d-jenkins-agent.service >/dev/null 2>&1; then
    pass "worker agent unit active (Jenkins jobs can run on this host)"
  elif [ "$(uname -s)" = "Darwin" ] && launchctl list 2>/dev/null | grep -q 'com.mac-k3d.jenkins-agent'; then
    pass "macOS agent LaunchAgent present"
  else
    note "worker.yaml present but agent not active — Jenkins eval needs an online node; --local may still work"
  fi
else
  note "no worker.yaml — Jenkins-queued eval needs a worker somewhere; --local uses this host Docker only"
fi

if [ "${RUN_EVAL_SMOKE:-0}" = "1" ]; then
  echo "Running mac-k3d eval --stage env (RUN_EVAL_SMOKE=1)…"
  "$MAC_K3D_BIN" eval --stage env
  pass "eval --stage env"
else
  note "skipping eval --stage env (set RUN_EVAL_SMOKE=1 to run)"
fi

pass "eval-ready — safe to start an evaluation task (ensure deepseek-api-key in Jenkins for LLM stages)"
pass "04_check_eval_ready complete"
