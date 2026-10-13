#!/usr/bin/env bash
# Shared helpers for environment bootstrap checks.
set -euo pipefail

ENV_SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MAC_K3D_ROOT="$(cd "$ENV_SETUP_DIR/../.." && pwd)"

export MAC_K3D_RELEASE_TAG="${MAC_K3D_RELEASE_TAG:-v0.5.3}"
export MAC_K3D_REPO="${MAC_K3D_REPO:-Toby-Yu/mac-k3d}"
export JENKINS_URL="${JENKINS_URL:-http://localhost:17070}"
export CONTROLLER_CONFIG="${CONTROLLER_CONFIG:-$HOME/.config/mac-k3d/config.yaml}"
export WORKER_CONFIG="${WORKER_CONFIG:-$HOME/.config/mac-k3d/worker.yaml}"
export INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
export MAC_K3D_BIN="${MAC_K3D_BIN:-}"

pass() { echo "OK $*"; }
fail() { echo "FAIL $*" >&2; return 1; }
die() { echo "ERROR: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
note() { echo "NOTE $*"; }

resolve_mac_k3d() {
  if [ -n "${MAC_K3D_BIN}" ] && [ -x "${MAC_K3D_BIN}" ]; then
    return 0
  fi
  if have mac-k3d; then
    MAC_K3D_BIN="$(command -v mac-k3d)"
    export MAC_K3D_BIN
    return 0
  fi
  if [ -x "$INSTALL_DIR/mac-k3d" ]; then
    MAC_K3D_BIN="$INSTALL_DIR/mac-k3d"
    export MAC_K3D_BIN
    return 0
  fi
  if [ -x "$MAC_K3D_ROOT/target/release/mac-k3d" ]; then
    MAC_K3D_BIN="$MAC_K3D_ROOT/target/release/mac-k3d"
    export MAC_K3D_BIN
    return 0
  fi
  return 1
}

require_mac_k3d() {
  resolve_mac_k3d || die "mac-k3d not found. Run 01_download_binary.sh or set MAC_K3D_BIN."
  pass "mac-k3d=$MAC_K3D_BIN"
}

detect_asset_name() {
  local os arch uname_s uname_m
  uname_s="$(uname -s)"
  uname_m="$(uname -m)"
  case "$uname_s" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) die "unsupported OS: $uname_s (need Linux or Darwin)" ;;
  esac
  case "$uname_m" in
    x86_64|amd64) arch=x86_64 ;;
    aarch64|arm64) arch=aarch64 ;;
    *) die "unsupported arch: $uname_m" ;;
  esac
  echo "mac-k3d-${os}-${arch}"
}

jenkins_login_code() {
  curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${JENKINS_URL%/}/login" || echo "000"
}

job_exists() {
  local job="$1"
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${JENKINS_URL%/}/job/${job}/" || echo "000")"
  # 200 = open, 403 = exists but needs auth — both OK for presence
  case "$code" in
    200|403) return 0 ;;
    *) return 1 ;;
  esac
}
