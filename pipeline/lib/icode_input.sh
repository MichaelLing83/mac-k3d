#!/usr/bin/env bash
# Host-side iCode inputs (sourced by icode_prepare.sh, tasks/harness.sh and
# evaluate/harbor_run.sh). The git and build-container helpers are in
# harness_git.sh; the icode_* names below are iCode's view of them.
# get_release_icode: official *-full-* drop / URL (not a git clone of Release assets).
# get_bin_icode: allow-listed https git URL + tag/commit/branch, uv sync, Docker wrapper.
# Does not print tokens.

if ! declare -F die >/dev/null 2>&1; then
  die() {
    echo "ERROR: $*" >&2
    exit 1
  }
fi
if ! declare -F have >/dev/null 2>&1; then
  have() { command -v "$1" >/dev/null 2>&1; }
fi
# shellcheck source=./harness_git.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harness_git.sh"

icode_normalize_mode() {
  case "${ICODE_MODE:-binary}" in
    binary | release) printf '%s\n' release ;;
    git) printf '%s\n' git ;;
    source)
      die "ICODE_MODE=source is not supported. Push to GitHub/GitCode and use git (clone) or download a *-full-* binary and use release."
      ;;
    *) die "unknown ICODE_MODE=${ICODE_MODE} (use release or git; binary is an alias of release)" ;;
  esac
}

# Git inputs: harness_git.sh, with iCode's names in the messages.
icode_git_host_allowed() { hgit_host_allowed "$@"; }
icode_validate_git_url() { HGIT_LABEL=ICODE_GIT hgit_validate_url "$@"; }
icode_validate_git_ref() { HGIT_LABEL=ICODE_GIT hgit_validate_ref "$@"; }
icode_looks_like_commit() { hgit_looks_like_commit "$@"; }
icode_looks_like_pr_number() { hgit_looks_like_pr_number "$@"; }
icode_normalize_ref_kind() { HGIT_LABEL=ICODE_GIT hgit_normalize_ref_kind "$@"; }
icode_validate_ref_for_kind() { HGIT_LABEL=ICODE_GIT hgit_validate_ref_for_kind "$@"; }
icode_git_pr_refs() { hgit_pr_refs "$@"; }

icode_record_git_meta() {
  local dest="$1" url="$2" kind="$3" ref="$4" sha subject out
  sha="$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)"
  subject="$(git -C "$dest" log -1 --format=%s 2>/dev/null || true)"
  [ -n "${WORKDIR:-}" ] || die "WORKDIR is unset; cannot write icode_git.json"
  out="$WORKDIR/icode_git.json"
  python3 - "$out" "$url" "$kind" "$ref" "$sha" "$subject" <<'PY'
import json, sys
path, url, kind, ref, sha, subject = sys.argv[1:7]
doc = {
    "url": url,
    "kind": kind,
    "ref": ref,
    "sha": sha,
    "subject": subject,
}
open(path, "w", encoding="utf-8").write(json.dumps(doc, indent=2) + "\n")
PY
}

icode_assert_expect_sha() {
  local dest="$1" expect head
  expect="$(printf '%s' "${ICODE_EXPECT_SHA:-}" | tr '[:upper:]' '[:lower:]')"
  [ -n "$expect" ] || return 0
  head="$(git -C "$dest" rev-parse HEAD 2>/dev/null | tr '[:upper:]' '[:lower:]')"
  [ -n "$head" ] || die "ICODE_EXPECT_SHA is set but $dest has no HEAD"
  [ "$head" = "$expect" ] || die "ICODE_EXPECT_SHA=$expect but checkout HEAD is $head"
}

icode_assert_recorded_sha() {
  local expect head
  expect="$(printf '%s' "${ICODE_EXPECT_SHA:-}" | tr '[:upper:]' '[:lower:]')"
  [ -n "$expect" ] || return 0
  if [ -s "${WORKDIR:-}/icode_git.json" ]; then
    head="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("sha",""))' "$WORKDIR/icode_git.json" | tr '[:upper:]' '[:lower:]')"
  elif [ -n "${HOST_ROOT:-}" ] && [ -d "${HOST_ROOT}/.git" ]; then
    head="$(git -C "$HOST_ROOT" rev-parse HEAD 2>/dev/null | tr '[:upper:]' '[:lower:]')"
  else
    die "ICODE_EXPECT_SHA is set but tasks/harness did not record an iCode SHA"
  fi
  [ "$head" = "$expect" ] || die "ICODE_EXPECT_SHA=$expect but recorded SHA is $head"
}

# The generated launcher is the only file tasks/harness may add. .venv is gitignored upstream.
icode_assert_clean_checkout() {
  local dest="$1" line
  [ -n "$dest" ] && [ -d "$dest/.git" ] || return 0
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [ -n "$line" ] || continue
    case "$line" in
      "?? icode" | "?? ./icode") ;;
      *) die "iCode checkout $dest is modified (${line}). Only the generated icode launcher may be untracked." ;;
    esac
  done < <(git -C "$dest" status --porcelain)
}

icode_sha256() { hgit_sha256 "$@"; }

icode_record_release() {
  local src="$1" name="$2" target hash
  [ -n "${WORKDIR:-}" ] || die "WORKDIR is unset; cannot write icode_release.json"
  target="$src"
  if [ -d "$src" ]; then
    target="$(find "$src" -type f -name icode -print -quit || true)"
    [ -n "$target" ] || die "ICODE_MODE=release: no icode binary to checksum under $src"
  fi
  [ -f "$target" ] || die "ICODE_MODE=release: checksum target is not a file"
  hash="$(icode_sha256 "$target")"
  if [ -n "${ICODE_RELEASE_SHA256:-}" ]; then
    local expect
    expect="$(printf '%s' "$ICODE_RELEASE_SHA256" | tr '[:upper:]' '[:lower:]')"
    [ "$hash" = "$expect" ] || die "ICODE_RELEASE_SHA256=$expect but this file is $hash"
  fi
  python3 - "$WORKDIR/icode_release.json" "$name" "$hash" <<'PY'
import json, sys
path, name, digest = sys.argv[1:4]
doc = {"filename": name, "sha256": digest}
open(path, "w", encoding="utf-8").write(json.dumps(doc, indent=2) + "\n")
PY
  echo "OK icode_release file=${name} sha256=${hash}" >&2
}

icode_docker_platform() { hgit_docker_platform; }
icode_target_platform() { hgit_target_platform; }
icode_platform_cpu() { hgit_platform_cpu "$@"; }

# CPU type a drop's name gives (icode-linux-x86_64-full-v…); darwin for a macOS
# build; empty when the name does not say.
icode_drop_name_cpu() {
  case "$(basename "$1")" in
    *linux-x86_64* | *linux-amd64*) printf '%s\n' x86_64 ;;
    *linux-aarch64* | *linux-arm64*) printf '%s\n' aarch64 ;;
    *macos-* | *darwin-*) printf '%s\n' darwin ;;
  esac
}

# CPU type of an executable from its header: x86_64 or aarch64 for a Linux
# ELF, darwin for Mach-O, empty for a script or anything else.
icode_binary_cpu() {
  local f="$1"
  [ -f "$f" ] || return 0
  case "$(od -An -tx1 -N4 "$f" 2>/dev/null | tr -d ' \n')" in
    7f454c46)
      case "$(od -An -tx1 -j18 -N2 "$f" 2>/dev/null | tr -d ' \n')" in
        3e00) printf '%s\n' x86_64 ;;
        b700) printf '%s\n' aarch64 ;;
      esac
      ;;
    cffaedfe | cefaedfe | cafebabe | bebafeca) printf '%s\n' darwin ;;
  esac
}

# True when this host cannot exec a Linux binary of CPU type $1 itself.
icode_cpu_foreign() {
  [ -n "$1" ] && [ "$1" != darwin ] || return 1
  [ "$(uname -s)" = Linux ] || return 0
  [ "$1" != "$(icode_platform_cpu "$(icode_docker_platform)")" ]
}

# The icode under an unpacked release for this build's task containers. One
# upload may hold a drop per CPU type (a .tar.gz of icode-linux-x86_64-full-v…
# and icode-linux-aarch64-full-v… of one version); each build takes its own.
# A binary of another CPU type, or a macOS build, is refused; a script is
# taken as is.
icode_pick_release_bin() {
  local unpack="$1" want plat f cpu script="" found=""
  plat="$(icode_target_platform)"
  want="$(icode_platform_cpu "$plat")"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    cpu="$(icode_binary_cpu "$f")"
    if [ "$cpu" = "$want" ]; then
      printf '%s\n' "$f"
      return 0
    fi
    if [ -z "$cpu" ]; then
      [ -n "$script" ] || script="$f"
    else
      case " $found " in *" $cpu "*) ;; *) found="${found:+$found }$cpu" ;; esac
    fi
  done < <(find "$unpack" -type f -name icode 2>/dev/null | LC_ALL=C sort)
  if [ -n "$script" ]; then
    printf '%s\n' "$script"
    return 0
  fi
  [ -n "$found" ] || die "could not find icode inside release"
  die "the iCode release is built for ${found}, but this build's task containers are ${plat} (linux-${want}). Use icode-linux-${want}-full-vX.Y.Z, or upload one .tar.gz holding icode-linux-x86_64-full-vX.Y.Z and icode-linux-aarch64-full-vX.Y.Z of the same version; each build takes the one it needs. A worker can also keep both drops in one folder: mac-k3d set --icode-release <folder>."
}

ICODE_LINUX_UV="$HGIT_LINUX_UV"

# uv sync runs in ICODE_LINUX_BUILD_IMAGE when this host cannot run the task
# containers' Linux runtime itself (hgit_container_build_needed).
icode_container_build_needed() { hgit_container_build_needed; }

# A venv uv built in the container links its python into the container's uv
# cache; embed, sanitize and the probes then run there too.
icode_tree_built_in_container() {
  case "$(readlink "$1/.venv/bin/python" 2>/dev/null || true)" in
    "$ICODE_LINUX_UV"/*) return 0 ;;
  esac
  return 1
}

icode_linux_image() { hgit_linux_image; }
icode_linux_run() { hgit_linux_run "$@"; }

# Check a release icode answers --help: on this host, or in the Linux build
# image when the binary is a Linux build this host cannot exec.
icode_release_help() {
  local bin="$1"
  if icode_cpu_foreign "$(icode_binary_cpu "$bin")"; then
    icode_linux_run "$(cd "$(dirname "$bin")" && pwd)" "$bin" --help
  else
    "$bin" --help
  fi
}

icode_force_rm() { hgit_force_rm "$@"; }
icode_reclaim_host_tree() { hgit_reclaim_tree "$@"; }

icode_git_checkout() {
  HGIT_LABEL=ICODE_GIT hgit_checkout "$@"
  icode_assert_expect_sha "$1"
}

icode_write_wrapper() {
  local dest="$1"
  cat >"$dest" <<'WRAP'
#!/bin/sh
# Bind-mounted git venv: host uv CPython is not in the sandbox. tasks/harness copies it to
# .venv/sandbox-cpython. Always use the mount path so Harbor/Pier `cp` of this
# file to /usr/local/bin/icode still works.
ROOT="${ICODE_SOURCE_HOST:-/opt/icode-host}"
PY="$ROOT/.venv/sandbox-cpython/bin/python3.13"
[ -x "$PY" ] || PY="$ROOT/.venv/sandbox-cpython/bin/python3"
[ -x "$PY" ] || PY="$ROOT/.venv/sandbox-cpython/bin/python"
if [ ! -x "$PY" ]; then
  echo "icode git sandbox python missing under $ROOT/.venv/sandbox-cpython" >&2
  exit 127
fi
# icode-host.pth in the sandbox interpreter's site-packages gives iCode its clone
# root and venv site-packages. Nothing is exported, so the agent's python, pytest
# and pip never load iCode's packages.
exec "$PY" "$ROOT/.venv/bin/icode" "$@"
WRAP
  chmod +x "$dest"
}

ICODE_MOUNT_TARGET="/opt/icode-host"

# Paths inside the mounted tree, as the sandbox interpreter sees them in a container.
icode_write_sandbox_pth() {
  local dest="${1:-}" stdlib site
  [ -n "$dest" ] && [ -d "$dest/.venv/sandbox-cpython/lib" ] || return 0
  stdlib="$dest/.venv/sandbox-cpython/lib/python3.13"
  if [ ! -d "$stdlib" ]; then
    stdlib="$(ls -d "$dest/.venv/sandbox-cpython/lib"/python3.* 2>/dev/null | head -1 || true)"
  fi
  [ -n "$stdlib" ] && [ -d "$stdlib" ] || return 0
  site="$dest/.venv/lib/python3.13/site-packages"
  if [ ! -d "$site" ]; then
    site="$(ls -d "$dest/.venv/lib"/python3.*/site-packages 2>/dev/null | head -1 || true)"
  fi
  mkdir -p "$stdlib/site-packages"
  {
    printf '%s\n' "$ICODE_MOUNT_TARGET"
    if [ -n "$site" ]; then
      printf '%s/.venv/lib/%s/site-packages\n' "$ICODE_MOUNT_TARGET" "$(basename "$(dirname "$site")")"
    fi
  } >"$stdlib/site-packages/icode-host.pth"
}

# Sourceless stdlib is on for every run, so all suites and models see the same runtime.
icode_sourceless_enabled() {
  return 0
}

# Strip task deliverables from the runtime the agent can read at /opt/icode-host.
# Writes .venv/SANITIZED_MANIFEST.json; a second run changes nothing.
# A manifest from another sanitizer version means the stdlib sources are already
# gone, so the sandbox CPython is copied again from the host and sanitized fresh.
icode_sanitize_host_tree() {
  local dest="${1:-}" lib rc=0
  local -a args runner=()
  [ -n "$dest" ] && [ -d "$dest/.venv/sandbox-cpython" ] || return 0
  lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  args=(--tree "$dest" --sourceless)
  if icode_tree_built_in_container "$dest"; then
    runner=(icode_linux_run "$dest")
  fi
  "${runner[@]}" python3 "$lib/icode_sanitize.py" "${args[@]}" >&2 || rc=$?
  if [ "$rc" = 3 ]; then
    echo "icode sanitize: re-embedding $dest/.venv/sandbox-cpython for the current sanitizer" >&2
    icode_force_rm "$dest/.venv/sandbox-cpython"
    icode_embed_sandbox_cpython "$dest"
    [ -d "$dest/.venv/sandbox-cpython" ] \
      || die "cannot re-embed the sandbox CPython under $dest (host uv python missing?); rerun the tasks phase"
    icode_write_sandbox_pth "$dest"
    rc=0
    "${runner[@]}" python3 "$lib/icode_sanitize.py" "${args[@]}" --refresh >&2 || rc=$?
  fi
  [ "$rc" = 0 ] || die "iCode sanitizer failed for $dest"
}

# One probe command with the sandbox's import path, from an empty directory, on
# this host or, for a container-built tree, in the Linux build image.
icode_probe_run() {
  local dest="$1" tmp="$2" site="$3"
  shift 3
  if icode_tree_built_in_container "$dest"; then
    ICODE_LINUX_WORKDIR=/tmp icode_linux_run "$dest" env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 \
      PYTHONPATH="${dest}${site:+:$site}" "$@" >"$tmp/probe.log" 2>&1
  else
    (cd "$tmp" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${dest}${site:+:$site}" \
      "$@") >"$tmp/probe.log" 2>&1
  fi
}

# Start the sanitized sandbox interpreter on the host before any rollout.
# The .pth paths only exist in containers, so this one process gets host paths.
icode_probe_sandbox() {
  local dest="${1:-}" py="" name site tmp
  [ -n "$dest" ] && [ -x "$dest/.venv/bin/icode" ] || return 0
  for name in python3.13 python3 python; do
    if [ -x "$dest/.venv/sandbox-cpython/bin/$name" ]; then
      py="$dest/.venv/sandbox-cpython/bin/$name"
      break
    fi
  done
  [ -n "$py" ] || die "sandbox python missing under $dest/.venv/sandbox-cpython (tasks/harness embed failed)"
  site="$(ls -d "$dest/.venv/lib"/python3.*/site-packages 2>/dev/null | head -1 || true)"
  tmp="$(mktemp -d)"
  if ! icode_probe_run "$dest" "$tmp" "$site" "$py" "$dest/.venv/bin/icode" --help; then
    tail -n 20 "$tmp/probe.log" >&2 || true
    rm -rf "$tmp"
    die "sanitized iCode runtime does not start: $py $dest/.venv/bin/icode --help failed"
  fi
  # --help builds no model; pydantic imports zoneinfo only when it builds its first schema.
  if ! icode_probe_run "$dest" "$tmp" "$site" "$py" -c 'import importlib.util
if importlib.util.find_spec("pydantic"):
    import pydantic
    class Probe(pydantic.BaseModel):
        value: int = 0
    Probe()'; then
    tail -n 20 "$tmp/probe.log" >&2 || true
    rm -rf "$tmp"
    die "sanitized iCode runtime cannot build a pydantic model (stdlib module missing?)"
  fi
  # icode run imports the agent stack that --help skips (alembic needs tomllib on 3.11+).
  # A layout without these modules is skipped; any other missing module fails.
  if ! icode_probe_run "$dest" "$tmp" "$site" "$py" -c 'import importlib
for name in ("openjiuwen_icode.agent.factory", "openjiuwen_icode.host.bootstrap"):
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if not exc.name or not (name == exc.name or name.startswith(exc.name + ".")):
            raise'; then
    tail -n 20 "$tmp/probe.log" >&2 || true
    rm -rf "$tmp"
    die "sanitized iCode runtime cannot import its run path (stdlib module missing?)"
  fi
  rm -rf "$tmp"
  echo "OK sanitized iCode runtime starts ($py)" >&2
}

# Host uv venv python is a symlink to ~/.local/share/uv/python/... which Docker
# does not have. Copy that CPython prefix into the clone for bind-mount eval.
# A container-built venv's CPython is in the build image's uv cache, so the copy
# runs there.
icode_embed_sandbox_cpython() {
  local dest="$1" lib
  [ -n "$dest" ] && [ -d "$dest" ] || return 0
  dest="$(cd "$dest" && pwd)"
  if icode_tree_built_in_container "$dest"; then
    lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    icode_linux_run "$dest" bash -c 'source "$1/icode_input.sh" && icode_embed_host_cpython "$2"' _ "$lib" "$dest" >&2 \
      || die "cannot copy the build image's CPython into $dest/.venv/sandbox-cpython"
    return 0
  fi
  icode_embed_host_cpython "$dest"
}

icode_embed_host_cpython() {
  local dest="$1" py prefix sandbox
  [ -n "$dest" ] && [ -d "$dest" ] || return 0
  dest="$(cd "$dest" && pwd)"
  [ -x "$dest/.venv/bin/icode" ] || return 0
  if [ -x "$dest/.venv/sandbox-cpython/bin/python3.13" ] || [ -x "$dest/.venv/sandbox-cpython/bin/python3" ] || [ -x "$dest/.venv/sandbox-cpython/bin/python" ]; then
    return 0
  fi
  py="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$dest/.venv/bin/python" 2>/dev/null || true)"
  [ -n "$py" ] && [ -x "$py" ] || return 0
  case "$py" in
    "$dest"/*) return 0 ;;
  esac
  prefix="$(cd "$(dirname "$py")/.." && pwd)"
  [ -d "$prefix/lib" ] && [ -d "$prefix/bin" ] || return 0
  sandbox="$dest/.venv/sandbox-cpython"
  mkdir -p "$sandbox"
  cp -a "$prefix/bin" "$sandbox/bin"
  cp -a "$prefix/lib" "$sandbox/lib"
  [ -d "$prefix/include" ] && cp -a "$prefix/include" "$sandbox/include" || true
}

icode_write_host_root() {
  printf '%s\n' "$1" >"$WORKDIR/icode_host_root.txt"
}

icode_write_askpass() { hgit_write_askpass; }
icode_token_env_for_host() { HGIT_LABEL=ICODE_GIT hgit_token_env_for_host "$@"; }
icode_resolve_git_token() { hgit_resolve_token "$@"; }
icode_git_username_for_host() { hgit_username_for_host "$@"; }
icode_git() { hgit_git "$@"; }
icode_git_clone_fail() { hgit_clone_fail "$@"; }

icode_paths_file() {
  if [ -n "${MAC_K3D_ICODE_PATHS:-}" ]; then
    printf '%s\n' "$MAC_K3D_ICODE_PATHS"
    return 0
  fi
  printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/mac-k3d/icode-paths.yaml"
}

# Read source|release from worker icode-paths.yaml. Empty / missing → return 1.
icode_paths_get() {
  local key="$1" file val
  file="$(icode_paths_file)"
  [ -f "$file" ] || return 1
  val="$(python3 - "$file" "$key" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
try:
    text = open(path, encoding="utf-8").read()
except OSError:
    sys.exit(1)
for line in text.splitlines():
    s = line.strip()
    if not s or s.startswith("#") or ":" not in s:
        continue
    k, _, v = s.partition(":")
    if k.strip() != key:
        continue
    v = v.strip().strip("'").strip('"')
    if v:
        print(v)
        sys.exit(0)
    sys.exit(1)
sys.exit(1)
PY
)" || return 1
  [ -n "$val" ] || return 1
  printf '%s\n' "$val"
}

# Parent folder of a *-full-* drop (e.g. iCode-binary/) → the child drop, the
# one named for this build's container CPU type when the folder holds several.
icode_resolve_release_src() {
  local src="$1" c want first=""
  [ -n "$src" ] || return 1
  if [ -d "$src" ]; then
    if declare -F _icode_dir_has_icode >/dev/null 2>&1 && _icode_dir_has_icode "$src"; then
      printf '%s\n' "$src"
      return 0
    fi
    if [ -f "$src/icode" ]; then
      printf '%s\n' "$src"
      return 0
    fi
    want="$(icode_platform_cpu "$(icode_target_platform)")"
    for c in "$src"/*-full-*.tar.gz "$src"/*.tgz "$src"/*-full-*; do
      [ -e "$c" ] || continue
      if declare -F _icode_release_ok >/dev/null 2>&1; then
        _icode_release_ok "$c" || continue
      fi
      if [ "$(icode_drop_name_cpu "$c")" = "$want" ]; then
        printf '%s\n' "$c"
        return 0
      fi
      [ -n "$first" ] || first="$c"
    done
    if [ -n "$first" ]; then
      printf '%s\n' "$first"
      return 0
    fi
    die "ICODE_RELEASE directory has no icode and no *-full-* drop: $src. Point at the *-full-* file or folder, or run: mac-k3d set --icode-release <path>"
  fi
  printf '%s\n' "$src"
}

# Official *-full-* path/URL/persist/discover. Prints ICODE_BIN path.
get_release_icode() {
  local unpack src bin rel_name
  if [ -z "${ICODE_RELEASE:-}" ] && [ "${ICODE_RELEASE_UPLOADED:-}" != 1 ]; then
    ICODE_RELEASE="$(icode_paths_get release || true)"
  fi
  if [ -z "${ICODE_RELEASE:-}" ] && [ "${ICODE_RELEASE_UPLOADED:-}" != 1 ] && declare -F discover_icode_release >/dev/null 2>&1; then
    ICODE_RELEASE="$(discover_icode_release || true)"
  fi
  if [ -z "${ICODE_RELEASE:-}" ]; then
    die "ICODE_MODE=release: Jenkins: upload ICODE_RELEASE_FILE on Build with Parameters. Local: set ICODE_RELEASE, run mac-k3d set --icode-release <path>, or place icode / icode-<os>-<arch>-full-vX.Y.Z at ${MAC_K3D_SHARE:-$HOME/.local/share/mac-k3d}/ (or /opt/mac-k3d/)"
  fi
  unpack="$WORKDIR/icode-bin"
  src="$ICODE_RELEASE"
  rm -rf "$unpack"
  mkdir -p "$unpack"
  case "$src" in
    http://* | https://*)
      curl -fsSL "$src" -o "$WORKDIR/icode-release.bin"
      src="$WORKDIR/icode-release.bin"
      ;;
    *)
      src="$(icode_resolve_release_src "$src")"
      ;;
  esac
  rel_name=""
  case "${ICODE_RELEASE:-}" in
    http://* | https://*)
      rel_name="${ICODE_RELEASE%%\?*}"
      rel_name="${rel_name%/}"
      rel_name="$(basename "$rel_name")"
      ;;
  esac
  if [ -z "$rel_name" ]; then
    rel_name="$(basename "$src")"
  fi
  icode_record_release "$src" "$rel_name"
  install_icode_release "$src" "$unpack"
  bin="$(icode_pick_release_bin "$unpack")" || exit 1
  icode_write_host_root "$(cd "$(dirname "$bin")" && pwd)"
  printf '%s\n' "$bin"
}

# iCode's lock may pin a git dependency as ssh://git@<host>/… (PR 2 pins
# agent-core that way). With a PAT for the iCode host, fetch it over https with
# the askpass the clone used, so a worker needs no SSH key. Without one, or when
# the PAT cannot read it, use this machine's SSH key, never prompting.
# Everything goes to stderr: the caller captures stdout as ICODE_BIN.
# On a host that cannot run the task containers' runtime, uv runs in the Linux
# build image of their platform (icode_linux_run) with the same credentials.
icode_uv_sync() {
  local dest="$1" host="$2" token="$3" askpass
  local -a runner=()
  if icode_container_build_needed; then
    runner=(icode_linux_run "$dest")
    echo "icode: building the git-mode runtime in $(icode_linux_image) for $(icode_target_platform)" >&2
  fi
  if [ -n "$token" ]; then
    askpass="$(icode_write_askpass)"
    if (cd "$dest" && MAC_K3D_GIT_TOKEN="$token" GIT_ASKPASS="$askpass" \
      GIT_CONFIG_COUNT=2 \
      GIT_CONFIG_KEY_0="url.https://${host}/.insteadOf" GIT_CONFIG_VALUE_0="ssh://git@${host}/" \
      GIT_CONFIG_KEY_1="url.https://${host}/.insteadOf" GIT_CONFIG_VALUE_1="git@${host}:" \
      "${runner[@]}" uv sync --no-editable >&2); then
      echo "OK icode dependencies fetched over https with the ${host} PAT" >&2
      return 0
    fi
    echo "WARNING: uv sync over https with the ${host} PAT failed; retrying with this machine's SSH key" >&2
  fi
  if (cd "$dest" && GIT_SSH_COMMAND="ssh -o BatchMode=yes" "${runner[@]}" uv sync --no-editable >&2); then
    return 0
  fi
  if grep -qsF -e "ssh://git@${host}/" -e "git@${host}:" "$dest/pyproject.toml" "$dest/uv.lock"; then
    die "uv sync failed: iCode pins a git dependency over SSH (ssh://git@${host}/…). Give the account behind the ${host} PAT (Jenkins gitcode-pat / github-pat, or GITCODE_TOKEN / GITHUB_TOKEN in .env) read access to it, or add an SSH key for ${host} on this worker. Token not printed."
  fi
  die "uv sync failed in ${dest}"
}

icode_begin_git_auth() { hgit_begin_auth "$@"; }
icode_end_git_auth() { hgit_end_auth; }

# Fetch the form's ref, or ICODE_GIT_SHA when a dispatcher pinned one commit.
icode_checkout_requested() {
  HGIT_LABEL=ICODE_GIT hgit_checkout_requested "$1" "$2" "$3" "$4" "${ICODE_GIT_SHA:-}"
  icode_assert_expect_sha "$1"
}

# Print the commit the form names. Stdout is only the SHA; checkout chatter goes to stderr.
icode_resolve_sha_into() {
  local dest="$1" url="$2" ref="$3" kind="$4"
  icode_git_checkout "$dest" "$url" "$ref" "$kind" >&2
  git -C "$dest" rev-parse HEAD
}

# Clone allow-listed URL at tag/commit/branch, uv sync, wrapper. Prints ICODE_BIN path.
# Tests: MAC_K3D_ICODE_FETCH_DIR skips clone (must already contain .venv/bin/icode or pyproject).
get_bin_icode() {
  local dest="$WORKDIR/icode-src" url ref kind host token
  host=""
  token=""
  if [ -n "${MAC_K3D_ICODE_FETCH_DIR:-}" ]; then
    [ -d "$MAC_K3D_ICODE_FETCH_DIR" ] || die "MAC_K3D_ICODE_FETCH_DIR is not a directory"
    dest="$(cd "$MAC_K3D_ICODE_FETCH_DIR" && pwd)"
  else
    url="${ICODE_GIT_URL:-}"
    icode_validate_git_url "$url"
    ref="${ICODE_GIT_REF:-main}"
    [ -n "$ref" ] || ref=main
    icode_validate_git_ref "$ref"
    kind="$(icode_normalize_ref_kind "${ICODE_GIT_REF_KIND:-auto}" "$ref")"
    icode_validate_ref_for_kind "$kind" "$ref"
    host="${url#https://}"
    host="${host%%/*}"
    host="${host%%:*}"
    token="$(icode_resolve_git_token "$host")"
    mkdir -p "$(dirname "$dest")"
    icode_begin_git_auth "$url"
    icode_checkout_requested "$dest" "$url" "$ref" "$kind"
    icode_end_git_auth
    icode_record_git_meta "$dest" "$url" "$kind" "$ref"
  fi
  [ -d "$dest" ] || die "icode git checkout missing: $dest"
  if [ ! -x "$dest/.venv/bin/icode" ]; then
    icode_container_build_needed || have uv || die "need uv or $dest/.venv/bin/icode"
    icode_uv_sync "$dest" "${host:-}" "${token:-}"
  fi
  [ -x "$dest/.venv/bin/icode" ] || die "uv sync did not produce $dest/.venv/bin/icode"
  icode_embed_sandbox_cpython "$dest"
  icode_write_sandbox_pth "$dest"
  icode_sanitize_host_tree "$dest"
  icode_write_wrapper "$dest/icode"
  icode_assert_clean_checkout "$dest"
  icode_write_host_root "$dest"
  printf '%s\n' "$dest/icode"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    --validate-url)
      icode_validate_git_url "${2:-}"
      echo OK
      ;;
    --validate-ref)
      icode_validate_git_ref "${2:-}"
      echo OK
      ;;
    --normalize-mode)
      icode_normalize_mode
      ;;
    --token-env-for-host)
      icode_token_env_for_host "${2:-}"
      ;;
    --normalize-ref-kind)
      ICODE_GIT_REF_KIND="${2:-auto}"
      icode_normalize_ref_kind "${2:-auto}" "${3:-}"
      ;;
      --validate-ref-kind)
      kind="$(icode_normalize_ref_kind "${2:-}" "${3:-}")"
      icode_validate_ref_for_kind "$kind" "${3:-}"
      echo OK
      ;;
    --force-rm)
      icode_force_rm "${2:-}"
      echo OK
      ;;
    --resolve-sha)
      url="${ICODE_GIT_URL:-}"
      ref="${ICODE_GIT_REF:-main}"
      [ -n "$ref" ] || ref=main
      if [ "${MAC_K3D_ICODE_GIT_ALLOW_FILE:-}" = 1 ] && [ -d "$url" ]; then
        :
      else
        icode_validate_git_url "$url"
      fi
      icode_validate_git_ref "$ref"
      kind="$(icode_normalize_ref_kind "${ICODE_GIT_REF_KIND:-auto}" "$ref")"
      icode_validate_ref_for_kind "$kind" "$ref"
      if [ "${MAC_K3D_ICODE_GIT_ALLOW_FILE:-}" != 1 ] || [ ! -d "$url" ]; then
        icode_begin_git_auth "$url"
      fi
      dest="$(mktemp -d)"
      trap 'icode_end_git_auth; icode_force_rm "$dest"' EXIT
      sha="$(icode_resolve_sha_into "$dest" "$url" "$ref" "$kind")"
      trap - EXIT
      icode_end_git_auth
      icode_force_rm "$dest"
      [ -n "$sha" ] || die "could not resolve the iCode git ref"
      printf '%s\n' "$sha"
      ;;
    *)
      echo "usage: icode_input.sh --validate-url URL | --validate-ref REF | --normalize-mode | --token-env-for-host HOST | --normalize-ref-kind KIND [REF] | --validate-ref-kind KIND REF | --force-rm PATH | --resolve-sha" >&2
      exit 2
      ;;
  esac
fi
