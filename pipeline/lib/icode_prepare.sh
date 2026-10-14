#!/usr/bin/env bash
# iCode's prepare (sourced by harness_lib.sh): resolve the iCode under test into
# WORKDIR (a release drop, or a git clone + build), make it runnable inside a
# task container at /opt/icode-host, and probe it. Reads the checkout; never
# changes iCode's source.
#
# Leaves icode_bin_path.txt, icode_host_root.txt and icode_git.json or
# icode_release.json in WORKDIR, and prints the tree to store: icode-src for
# git, icode-bin (every drop of the release) for a release.

# shellcheck source=./icode_input.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/icode_input.sh"

icode_prepare_workspace() {
  local bin host_root=""
  ICODE_MODE="$(icode_normalize_mode)"
  export ICODE_MODE
  echo "icode: resolving iCode ($ICODE_MODE)" >&2
  case "$ICODE_MODE" in
    release) bin="$(get_release_icode)" ;;
    git) bin="$(get_bin_icode)" ;;
    *) die "unknown ICODE_MODE=$ICODE_MODE (use git clone or release binary drop)" ;;
  esac
  [ -n "$bin" ] || die "iCode prepare did not resolve ICODE_BIN"
  echo "$bin" >"$WORKDIR/icode_bin_path.txt"
  if [ -s "$WORKDIR/icode_host_root.txt" ]; then
    host_root="$(cat "$WORKDIR/icode_host_root.txt")"
  fi

  # Git wrapper execs /opt/icode-host/.venv/bin/icode (sandbox path). Probe the venv on the host,
  # or in the Linux build image when that is where uv built it.
  if [ -n "$host_root" ] && [ -x "$host_root/.venv/bin/icode" ]; then
    if icode_tree_built_in_container "$host_root"; then
      icode_linux_run "$host_root" "$host_root/.venv/bin/icode" --help >/dev/null
    else
      "$host_root/.venv/bin/icode" --help >/dev/null
    fi
  elif [[ "$bin" == uv\ run* ]]; then
    # shellcheck disable=SC2086
    eval "$bin --help" >/dev/null
  else
    icode_release_help "$bin" >/dev/null
  fi
  echo "OK icode helps ($bin)" >&2

  [ -n "$host_root" ] && [ -d "$host_root" ] || die "icode host tree missing: ${host_root:-<none>}"
  # Git clone has .venv/bin/icode. A release drop must keep its real binary:
  # writing the git wrapper over it makes Harbor exit 127.
  if [ -x "$host_root/.venv/bin/icode" ]; then
    echo "icode: git wrapper for $host_root" >&2
    icode_embed_sandbox_cpython "$host_root"
    icode_write_sandbox_pth "$host_root"
    icode_sanitize_host_tree "$host_root"
    icode_write_wrapper "$host_root/icode"
    icode_probe_sandbox "$host_root"
  else
    echo "icode: keeping release binary at $host_root/$(basename "$bin") (no git wrapper)" >&2
  fi
  if [ "$ICODE_MODE" = git ]; then
    icode_assert_clean_checkout "$host_root"
  fi
  HOST_ROOT="$host_root" icode_assert_recorded_sha
  if [ "$ICODE_MODE" = git ]; then
    printf '%s\n' "$host_root"
  else
    printf '%s\n' "$WORKDIR/icode-bin"
  fi
}
