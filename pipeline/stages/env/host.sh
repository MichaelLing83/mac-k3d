#!/usr/bin/env bash
# env/host: bash, python3, git, RAM, disk, Docker access, VPN MTU and the
# mac-k3d binary on this worker. The minimums are the toolchain.env pins
# `mac-k3d setup` checks, so a worker setup accepted does not fail here.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

case "${BENCHMARK:-deepswe}" in
  deepswe | lolbench | swebenchpro | "") ;;
  *) die "unknown BENCHMARK=${BENCHMARK} (use deepswe, lolbench, or swebenchpro)" ;;
esac
check_canary_settings
echo "OK bash ${BASH_VERSION} (min ${BASH_MIN})"

# Every step runs python3; capture_receipt.py and canary_verdict.py import tomllib.
python_fix="Linux: apt-get install -y python3 (Ubuntu 24.04 ships 3.12); macOS: brew install python"
have python3 || die "python3 not on PATH; builds need python3 ${PYTHON_MIN}+. ${python_fix}"
python_version="$(python3 -c 'import platform; print(platform.python_version())')"
python3 - "$PYTHON_MIN" <<'PY' || die "python3 ${python_version} at $(command -v python3) is older than ${PYTHON_MIN} (PYTHON_MIN in pipeline/config/toolchain.env). ${python_fix}"
import sys
want = tuple(int(p) for p in sys.argv[1].split("."))
sys.exit(0 if sys.version_info[: len(want)] >= want else 1)
PY
echo "OK python3 ${python_version} (min ${PYTHON_MIN})"

have git || die "git not on PATH; the tasks phase clones every benchmark with it. Linux: apt-get install -y git; macOS: brew install git"
echo "OK $(git --version)"

ensure_eval_preflight

have docker || die "docker not on PATH"
docker info >/dev/null 2>&1 || die "docker info failed (no Server / permission). Log out/in on Linux or open Docker Desktop on macOS."
warn_docker_mtu

# DeepSWE's and SWE-bench Pro's images are linux/amd64 only.
if task_platform_emulated; then
  ensure_platform_runs "$(task_container_platform)"
fi

MAC_K3D_BIN="${MAC_K3D_BIN:-}"
if [ -z "$MAC_K3D_BIN" ]; then
  if have mac-k3d; then
    MAC_K3D_BIN="$(command -v mac-k3d)"
  elif [ -x "$MAC_K3D_ROOT/target/release/mac-k3d" ]; then
    MAC_K3D_BIN="$MAC_K3D_ROOT/target/release/mac-k3d"
  else
    die "mac-k3d not found; build with cargo build --release or put Release asset on PATH"
  fi
fi
"$MAC_K3D_BIN" --help | grep -q eval || die "mac-k3d --help does not list eval (rebuild from this branch)"

echo "OK docker Server section present"
echo "OK mac-k3d=$MAC_K3D_BIN lists eval"

if [ "$(uname -s)" = "Darwin" ]; then
  if launchctl print "gui/$(id -u)/com.mac-k3d.jenkins-agent" >/dev/null 2>&1 \
    || launchctl list 2>/dev/null | grep -q 'com.mac-k3d.jenkins-agent'; then
    echo "OK Jenkins LaunchAgent com.mac-k3d.jenkins-agent loaded"
  else
    echo "NOTE: Jenkins LaunchAgent not loaded (OK for --local stage tests)"
  fi
elif have systemctl; then
  if systemctl --user is-active mac-k3d-jenkins-agent.service >/dev/null 2>&1; then
    echo "OK Jenkins agent unit active"
  else
    echo "NOTE: Jenkins agent unit not active (OK for --local stage tests)"
  fi
fi
