#!/usr/bin/env bash
# File-only export/import check. Never --force onto ~/.config/mac-k3d/*.
set -euo pipefail
# shellcheck source=./_common.sh
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

require_mac_k3d

"$MAC_K3D_BIN" export --help >/dev/null 2>&1 \
  || die "this mac-k3d has no export (need GitHub v0.5.3 or later)"
pass "CLI lists export"

src=""
if [ -f "$WORKER_CONFIG" ]; then
  src="$WORKER_CONFIG"
elif [ -f "$CONTROLLER_CONFIG" ]; then
  src="$CONTROLLER_CONFIG"
else
  die "no live YAML at $WORKER_CONFIG or $CONTROLLER_CONFIG"
fi
pass "source $src"

# A worker YAML keeps an empty api_token slot; any value in it is a leak.
# Prints nothing, so a matched token never reaches the terminal.
token_filled() {
  awk '/^[[:space:]]*api_token:/ {
    v = $0; sub(/^[[:space:]]*api_token:[[:space:]]*/, "", v); sub(/[[:space:]]+$/, "", v)
    if (v != "" && v != "\047\047" && v != "\"\"") found = 1
  } END { exit !found }' "$1"
}

# BSD mktemp (macOS) only fills trailing Xs, so no suffix after them.
scratch="$(mktemp -d "${TMPDIR:-/tmp}/mac-k3d-export.XXXXXX")"
out="$scratch/export.yaml"
dest="$scratch/import.yaml"

"$MAC_K3D_BIN" export -c "$src" -o "$out"
token_filled "$out" && die "exported YAML still has an api_token value: $out"
grep -qi 'sk-[A-Za-z0-9]\{16,\}' "$out" && die "exported YAML looks like it contains an API key"
pass "export wrote $out (api_token empty or absent)"

"$MAC_K3D_BIN" import "$out" -c "$dest"
[ -f "$dest" ] || die "import did not write $dest"
grep -q '^role:' "$dest" || die "imported YAML missing role: $dest"
token_filled "$dest" && die "imported YAML has an api_token value: $dest"
pass "import wrote $dest (role kept, api_token empty or absent)"

pass "05_check_export_import complete"
echo "NOTE scratch files: $out $dest (not live ~/.config/mac-k3d)"
