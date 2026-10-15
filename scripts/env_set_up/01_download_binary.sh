#!/usr/bin/env bash
# Download the mac-k3d Release asset for this OS/arch and install to ~/.local/bin.
set -euo pipefail
# shellcheck source=./_common.sh
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

if [ "${SKIP_DOWNLOAD:-0}" = "1" ]; then
  require_mac_k3d
  "$MAC_K3D_BIN" --help | grep -q setup || die "mac-k3d --help missing setup"
  "$MAC_K3D_BIN" --help | grep -q eval || die "mac-k3d --help missing eval"
  pass "SKIP_DOWNLOAD=1 — using existing binary"
  "$MAC_K3D_BIN" --version
  exit 0
fi

ASSET="$(detect_asset_name)"
TMPDIR_DL="${TMPDIR:-/tmp}/mac-k3d-env-setup-$$"
mkdir -p "$TMPDIR_DL" "$INSTALL_DIR"
cleanup() { rm -rf "$TMPDIR_DL"; }
trap cleanup EXIT

echo "Downloading ${MAC_K3D_REPO}@${MAC_K3D_RELEASE_TAG} asset ${ASSET} …"

if have gh; then
  gh release download "$MAC_K3D_RELEASE_TAG" \
    --repo "$MAC_K3D_REPO" \
    --pattern "$ASSET" \
    --dir "$TMPDIR_DL" \
    --clobber
else
  URL="https://github.com/${MAC_K3D_REPO}/releases/download/${MAC_K3D_RELEASE_TAG}/${ASSET}"
  curl -fsSL -o "$TMPDIR_DL/$ASSET" "$URL"
fi

test -f "$TMPDIR_DL/$ASSET" || die "download missing $ASSET"
chmod +x "$TMPDIR_DL/$ASSET"

# macOS: clear quarantine when present
if [ "$(uname -s)" = "Darwin" ] && have xattr; then
  xattr -d com.apple.quarantine "$TMPDIR_DL/$ASSET" 2>/dev/null || true
fi

cp "$TMPDIR_DL/$ASSET" "$INSTALL_DIR/mac-k3d"
chmod +x "$INSTALL_DIR/mac-k3d"
export MAC_K3D_BIN="$INSTALL_DIR/mac-k3d"

# Prefer install dir on PATH for this process
export PATH="$INSTALL_DIR:$PATH"

pass "downloaded $ASSET → $MAC_K3D_BIN"
"$MAC_K3D_BIN" --version
"$MAC_K3D_BIN" --help | grep -q setup || die "help missing setup"
"$MAC_K3D_BIN" --help | grep -q eval || die "help missing eval"
pass "help lists setup and eval"
pass "01_download_binary complete"
