#!/usr/bin/env bash
# Verify Jenkins controller (k3d + UI + jobs) is healthy.
set -euo pipefail
# shellcheck source=./_common.sh
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

require_mac_k3d

test -f "$CONTROLLER_CONFIG" || die "missing controller config: $CONTROLLER_CONFIG (run: mac-k3d setup -c $CONTROLLER_CONFIG)"

STATUS_OUT="$("$MAC_K3D_BIN" status -c "$CONTROLLER_CONFIG" 2>&1)" || true
echo "$STATUS_OUT"

echo "$STATUS_OUT" | grep -qi 'Docker Engine:.*running' || die "Docker Engine not running per status"
echo "$STATUS_OUT" | grep -qi 'k3d cluster:.*running' || die "k3d cluster not running"
echo "$STATUS_OUT" | grep -qi 'Jenkins:.*Running' || die "Jenkins pod not Running"
pass "controller status healthy"

CODE="$(jenkins_login_code)"
[ "$CODE" = "200" ] || die "Jenkins UI ${JENKINS_URL}/login returned HTTP $CODE (want 200)"
pass "Jenkins UI ${JENKINS_URL}/login HTTP 200"

job_exists lolbench_one_task || die "job lolbench_one_task not found at ${JENKINS_URL}"
pass "job lolbench_one_task present"
job_exists deepswe_one_task || die "job deepswe_one_task not found at ${JENKINS_URL}"
pass "job deepswe_one_task present"
job_exists swebenchpro_one_task || die "job swebenchpro_one_task not found at ${JENKINS_URL}"
pass "job swebenchpro_one_task present"

pass "02_check_controller complete"
