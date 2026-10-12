#!/usr/bin/env bash
# env/harbor: Harbor at HARBOR_VERSION (pipeline/config/toolchain.env), the same
# pin `mac-k3d setup` installs. Reinstalls only when the version differs.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

ensure_uv() {
  have uv && return 0
  echo "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  have uv || die "uv still not on PATH after install"
}

harbor_version() {
  have harbor || return 0
  harbor --version 2>/dev/null | head -n 1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/.*[[:space:]]//'
}

export PATH="${HOME}/.local/bin:${PATH}"
node_lock harbor
got="$(harbor_version)"
if [ "$got" != "$HARBOR_VERSION" ]; then
  ensure_uv
  echo "Installing harbor ${HARBOR_VERSION} (found ${got:-none})…"
  uv tool install --force "harbor==${HARBOR_VERSION}"
  got="$(harbor_version)"
fi
node_unlock
have harbor || die "harbor still not on PATH after install. Try: uv tool install --force \"harbor==${HARBOR_VERSION}\""
[ "$got" = "$HARBOR_VERSION" ] || die "harbor --version is '${got}', wanted ${HARBOR_VERSION}"
printf '%s\n' "$got" >"$WORKDIR/harbor_version.txt"
echo "OK harbor=$(command -v harbor) version=$got"
