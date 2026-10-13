#!/usr/bin/env bash
# tasks/icode: resolve the iCode under test (release drop, or git clone + build).
# Reads the checkout; never changes iCode's source.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"
# shellcheck source=../../lib/icode_input.sh
source "$PIPELINE_LIB/icode_input.sh"

ICODE_MODE="$(icode_normalize_mode)"
export ICODE_MODE
echo "icode: resolving iCode ($ICODE_MODE)"

# A reused workspace must not carry the previous run's iCode identity.
rm -f "$WORKDIR/icode_git.json" "$WORKDIR/icode_release.json"

write_container_platform
if task_platform_emulated; then
  ensure_platform_runs "$(task_container_platform)"
fi

ICODE_BIN=""
case "$ICODE_MODE" in
  release)
    ICODE_BIN="$(get_release_icode)"
    ;;
  git)
    ICODE_BIN="$(get_bin_icode)"
    ;;
  *)
    die "unknown ICODE_MODE=$ICODE_MODE (use git clone or release binary drop)"
    ;;
esac

[ -n "$ICODE_BIN" ] || die "tasks/icode did not resolve ICODE_BIN"
export ICODE_BIN
echo "$ICODE_BIN" >"$WORKDIR/icode_bin_path.txt"

HOST_ROOT=""
if [ -s "$WORKDIR/icode_host_root.txt" ]; then
  HOST_ROOT="$(cat "$WORKDIR/icode_host_root.txt")"
fi

# Git wrapper execs /opt/icode-host/.venv/bin/icode (sandbox path). Probe the venv on the host,
# or in the Linux build image when that is where uv built it.
if [ -n "$HOST_ROOT" ] && [ -x "$HOST_ROOT/.venv/bin/icode" ]; then
  if icode_tree_built_in_container "$HOST_ROOT"; then
    icode_linux_run "$HOST_ROOT" "$HOST_ROOT/.venv/bin/icode" --help >/dev/null
  else
    "$HOST_ROOT/.venv/bin/icode" --help >/dev/null
  fi
elif [[ "$ICODE_BIN" == uv\ run* ]]; then
  # shellcheck disable=SC2086
  eval "$ICODE_BIN --help" >/dev/null
else
  icode_release_help "$ICODE_BIN" >/dev/null
fi
echo "OK icode helps ($ICODE_BIN)"
if [ -s "$WORKDIR/icode_git.json" ]; then
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); print("OK iCode git kind={kind} ref={ref} sha={sha}".format(**d)); print("iCode under test: git {kind} {ref} -> {sha} ({subject})".format(**d))' "$WORKDIR/icode_git.json" || true
  cp "$WORKDIR/icode_git.json" "$OUTPUT_DIR/icode_git.json"
elif [ -s "$WORKDIR/icode_release.json" ]; then
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); print("iCode under test: release {filename} sha256 {sha256}".format(**d))' "$WORKDIR/icode_release.json" || true
fi
if [ "$ICODE_MODE" = "git" ] && [ -n "${HOST_ROOT:-}" ]; then
  icode_assert_clean_checkout "$HOST_ROOT"
fi
icode_assert_recorded_sha
