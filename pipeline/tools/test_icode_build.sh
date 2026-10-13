#!/usr/bin/env bash
# Fixture tests for tasks/icode.sh: binary-mode official *-full-* names (no network).
set -euo pipefail

STAGES="$(cd "$(dirname "$0")/../stages" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# These fixtures cover release shapes, not emulation: task containers native to
# this host, so no Docker is needed for the Rosetta / QEMU check.
export BENCHMARK=deepswe
case "$(uname -m)" in
  aarch64 | arm64) export DEEPSWE_DOCKER_PLATFORM=linux/arm64 ;;
  *) export DEEPSWE_DOCKER_PLATFORM=linux/amd64 ;;
esac

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

ok() {
  echo "OK $*"
}

make_stub() {
  cat >"$1" <<'EOF'
#!/bin/sh
echo "usage: icode [--help]"
exit 0
EOF
  chmod +x "$1"
}

run_icode() {
  # Ignore the worker's saved release in ~/.config/mac-k3d/icode-paths.yaml.
  env \
    MAC_K3D_EVAL_WORKDIR="$1" \
    MAC_K3D_SHARE="$2" \
    MAC_K3D_ICODE_PATHS="$TMP/no-saved-icode-paths.yaml" \
    ICODE_MODE=binary \
    ICODE_RELEASE="${3-}" \
    bash "$STAGES/tasks/icode.sh"
}

assert_icode_ok() {
  local work="$1"
  run_icode "$work" "$2" "${3-}" >/dev/null
  [ -s "$work/icode_bin_path.txt" ] || fail "missing $work/icode_bin_path.txt"
  local bin
  bin="$(cat "$work/icode_bin_path.txt")"
  [ -f "$bin" ] || fail "ICODE_BIN not a file: $bin"
  "$bin" --help >/dev/null || fail "icode --help failed: $bin"
}

pack_full_tarball() {
  local dest="$1"
  local pack="$TMP/pack-$$"
  mkdir -p "$pack"
  make_stub "$pack/icode"
  tar -C "$pack" -czf "$dest" icode
  rm -rf "$pack"
}

SHARE="$TMP/share"
mkdir -p "$SHARE"

# 1. Official .tar.gz via ICODE_RELEASE
TARBALL="$TMP/icode-linux-x86_64-full-v0.1.41.tar.gz"
pack_full_tarball "$TARBALL"
W1="$TMP/w1"
assert_icode_ok "$W1" "$SHARE" "$TARBALL"
ok "ICODE_RELEASE tarball icode-linux-x86_64-full-v0.1.41.tar.gz"

# 2. Same archive, extensionless official name
BARE="$TMP/icode-linux-x86_64-full-v0.1.41"
cp "$TARBALL" "$BARE"
W2="$TMP/w2"
assert_icode_ok "$W2" "$SHARE" "$BARE"
ok "ICODE_RELEASE extensionless icode-linux-x86_64-full-v0.1.41"

# 3. Unpacked official directory (keep sibling files)
DIR="$TMP/icode-linux-x86_64-full-v0.1.41-dir"
mkdir -p "$DIR"
make_stub "$DIR/icode"
echo sibling >"$DIR/libicode.so"
W3="$TMP/w3"
assert_icode_ok "$W3" "$SHARE" "$DIR"
BIN3="$(cat "$W3/icode_bin_path.txt")"
[ -f "$(dirname "$BIN3")/libicode.so" ] || fail "sibling lib not copied next to icode"
ok "ICODE_RELEASE unpacked *-full-* directory"

# 4. Discover official tarball in MAC_K3D_SHARE (ICODE_RELEASE empty)
cp "$TARBALL" "$SHARE/icode-linux-x86_64-full-v0.1.41.tar.gz"
W4="$TMP/w4"
assert_icode_ok "$W4" "$SHARE" ""
ok "discover MAC_K3D_SHARE/icode-linux-x86_64-full-v0.1.41.tar.gz"

# 5. Random file is not a release
RAND="$TMP/notes.txt"
echo not-icode >"$RAND"
W5="$TMP/w5"
if run_icode "$W5" "$TMP/empty-share" "$RAND" >/dev/null 2>&1; then
  fail "expected tasks/icode to reject $RAND"
fi
ok "reject random file that is not icode or *-full-*"

# 6. Leftover named icode + official tarball → discover prefers *-full-*
SHARE6="$TMP/share6"
mkdir -p "$SHARE6"
cat >"$SHARE6/icode" <<'EOF'
#!/bin/sh
echo WRAPPER
exit 0
EOF
chmod +x "$SHARE6/icode"
cp "$TARBALL" "$SHARE6/icode-linux-x86_64-full-v0.1.41.tar.gz"
W6="$TMP/w6"
assert_icode_ok "$W6" "$SHARE6" ""
BIN6="$(cat "$W6/icode_bin_path.txt")"
help6="$("$BIN6" --help)"
echo "$help6" | grep -q "usage: icode" || fail "expected tarball stub, got: $help6"
echo "$help6" | grep -q WRAPPER && fail "leftover wrapper won over *-full-*"
ok "discover prefers *-full-* over leftover icode"

# 7. git mode with MAC_K3D_ICODE_FETCH_DIR (no network): wrapper + host root
FETCH="$TMP/fake-icode-src"
mkdir -p "$FETCH/.venv/bin"
make_stub "$FETCH/.venv/bin/icode"
W7="$TMP/w7"
mkdir -p "$W7"
env \
  MAC_K3D_EVAL_WORKDIR="$W7" \
  MAC_K3D_SHARE="$SHARE" \
  ICODE_MODE=git \
  MAC_K3D_ICODE_FETCH_DIR="$FETCH" \
  ICODE_RELEASE="" \
  bash "$STAGES/tasks/icode.sh" >/dev/null
BIN7="$(cat "$W7/icode_bin_path.txt")"
ROOT7="$(cat "$W7/icode_host_root.txt")"
[ "$ROOT7" = "$FETCH" ] || fail "icode_host_root.txt expected $FETCH got $ROOT7"
[ -x "$BIN7" ] || fail "missing git wrapper $BIN7"
grep -q 'sandbox-cpython' "$BIN7" || fail "wrapper does not exec sandbox-cpython"
"$FETCH/.venv/bin/icode" --help >/dev/null || fail "fixture venv icode --help"
ok "ICODE_MODE=git MAC_K3D_ICODE_FETCH_DIR wrapper"

# 8. Release mode must not keep a previous git-mode icode_git.json
W8="$TMP/w8"
mkdir -p "$W8"
printf '%s\n' '{"url":"stale","kind":"commit","ref":"dead","sha":"deadbeef","subject":"no"}' >"$W8/icode_git.json"
assert_icode_ok "$W8" "$SHARE" "$TARBALL"
[ ! -f "$W8/icode_git.json" ] || fail "release mode left stale icode_git.json"
ok "release mode drops leftover icode_git.json"

# 8b. Git mode must not keep a previous release-mode icode_release.json
W8B="$TMP/w8b"
mkdir -p "$W8B"
printf '%s\n' '{"filename":"ICODE_RELEASE_FILE","sha256":"abc"}' >"$W8B/icode_release.json"
env \
  MAC_K3D_EVAL_WORKDIR="$W8B" \
  MAC_K3D_SHARE="$SHARE" \
  ICODE_MODE=git \
  MAC_K3D_ICODE_FETCH_DIR="$FETCH" \
  ICODE_RELEASE="" \
  bash "$STAGES/tasks/icode.sh" >/dev/null
[ ! -f "$W8B/icode_release.json" ] || fail "git mode left stale icode_release.json"
ok "git mode drops leftover icode_release.json"

# 9. source mode is rejected (worker is not a developer checkout)
W9="$TMP/w9"
mkdir -p "$W9"
if env \
  MAC_K3D_EVAL_WORKDIR="$W9" \
  MAC_K3D_SHARE="$SHARE" \
  ICODE_MODE=source \
  ICODE_RELEASE="" \
  bash "$STAGES/tasks/icode.sh" >/dev/null 2>&1; then
  fail "expected tasks/icode to reject ICODE_MODE=source"
fi
ok "tasks/icode rejects ICODE_MODE=source"

# 10. release from persist parent folder containing *-full-* child
PARENT="$TMP/iCode-binary"
mkdir -p "$PARENT"
cp "$TARBALL" "$PARENT/icode-linux-x86_64-full-v0.1.41.tar.gz"
PATHS="$TMP/icode-paths.yaml"
printf 'release: %s\n' "$PARENT" >"$PATHS"
W10="$TMP/w10"
env \
  MAC_K3D_EVAL_WORKDIR="$W10" \
  MAC_K3D_SHARE="$TMP/empty-share-10" \
  MAC_K3D_ICODE_PATHS="$PATHS" \
  ICODE_MODE=release \
  ICODE_RELEASE="" \
  bash "$STAGES/tasks/icode.sh" >/dev/null
[ -s "$W10/icode_bin_path.txt" ] || fail "missing release bin from parent folder persist"
ok "release mode from persist parent folder with *-full-* child"

# 11. Jenkins File Parameter: gzip magic, name is not *-full-*
UP_GZ="$TMP/ws-upload/ICODE_RELEASE_FILE"
mkdir -p "$TMP/ws-upload"
cp "$TARBALL" "$UP_GZ"
W11="$TMP/w11"
mkdir -p "$W11"
env \
  MAC_K3D_EVAL_WORKDIR="$W11" \
  MAC_K3D_SHARE="$TMP/empty-share-11" \
  ICODE_MODE=release \
  ICODE_RELEASE="$UP_GZ" \
  ICODE_RELEASE_UPLOADED=1 \
  bash "$STAGES/tasks/icode.sh" >/dev/null
[ -s "$W11/icode_bin_path.txt" ] || fail "missing bin from unnamed gzip upload"
ok "Jenkins upload unnamed gzip (ICODE_RELEASE_FILE) unpacks"

# 12. Jenkins upload standalone binary (not named icode or *-full-*)
UP_BIN="$TMP/ws-stub/ICODE_RELEASE_FILE"
mkdir -p "$TMP/ws-stub"
make_stub "$UP_BIN"
W12="$TMP/w12"
mkdir -p "$W12"
env \
  MAC_K3D_EVAL_WORKDIR="$W12" \
  MAC_K3D_SHARE="$TMP/empty-share-12" \
  ICODE_MODE=release \
  ICODE_RELEASE="$UP_BIN" \
  ICODE_RELEASE_UPLOADED=1 \
  bash "$STAGES/tasks/icode.sh" >/dev/null
[ -s "$W12/icode_bin_path.txt" ] || fail "missing bin from unnamed stub upload"
ok "Jenkins upload unnamed stub copies as icode"

# 13. Empty Jenkins upload must not fall back to persist
PATHS13="$TMP/icode-paths-13.yaml"
printf 'release: %s\n' "$TARBALL" >"$PATHS13"
W13="$TMP/w13"
mkdir -p "$W13"
if env \
  MAC_K3D_EVAL_WORKDIR="$W13" \
  MAC_K3D_SHARE="$TMP/empty-share-13" \
  MAC_K3D_ICODE_PATHS="$PATHS13" \
  ICODE_MODE=release \
  ICODE_RELEASE="" \
  ICODE_RELEASE_UPLOADED=1 \
  bash "$STAGES/tasks/icode.sh" >/dev/null 2>&1; then
  fail "expected tasks/icode to reject empty Jenkins upload"
fi
ok "empty Jenkins upload does not fall back to persist"

echo "OK test_icode_build.sh"
