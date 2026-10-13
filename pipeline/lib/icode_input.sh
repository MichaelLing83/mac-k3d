#!/usr/bin/env bash
# Host-side iCode inputs for the tasks phase (sourced by stages/tasks/icode.sh,
# tasks/icode_sandbox.sh and evaluate/harbor_run.sh).
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

icode_git_host_allowed() {
  case "$1" in
    github.com | www.github.com | gitcode.com) return 0 ;;
    *) return 1 ;;
  esac
}

icode_validate_git_url() {
  local url="${1:-}" rest host
  [ -n "$url" ] || die "ICODE_GIT_URL is empty"
  case "$url" in
    https://*) ;;
    *) die "ICODE_GIT_URL must be https:// (not file://, ssh://, or http://)" ;;
  esac
  rest="${url#https://}"
  case "$rest" in
    *@*) die "ICODE_GIT_URL must not contain userinfo (no tokens in the URL)" ;;
  esac
  host="${rest%%/*}"
  host="${host%%:*}"
  icode_git_host_allowed "$host" || die "ICODE_GIT_URL host '${host}' is not allow-listed (github.com, www.github.com, gitcode.com)"
}

icode_validate_git_ref() {
  local ref="${1:-}"
  [ -n "$ref" ] || die "ICODE_GIT_REF is empty"
  case "$ref" in
    -*) die "ICODE_GIT_REF must not start with -" ;;
  esac
  case "$ref" in
    *..*) die "ICODE_GIT_REF must not contain .." ;;
  esac
  [[ "$ref" =~ ^[A-Za-z0-9._/+\-]+$ ]] || die "ICODE_GIT_REF is not a tag, commit, or branch name"
}

icode_looks_like_commit() {
  [[ "${1:-}" =~ ^[0-9a-fA-F]{7,40}$ ]]
}

icode_looks_like_pr_number() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

icode_normalize_ref_kind() {
  local k="${1:-auto}" ref="${2:-}"
  k="$(printf '%s' "$k" | tr 'A-Z' 'a-z')"
  case "$k" in
    branch | tag | commit | pr)
      printf '%s\n' "$k"
      ;;
    auto | "")
      if icode_looks_like_commit "$ref"; then
        printf '%s\n' commit
      else
        printf '%s\n' branch
      fi
      ;;
    *)
      die "ICODE_GIT_REF_KIND must be branch, tag, commit, or pr (got ${k})"
      ;;
  esac
}

icode_validate_ref_for_kind() {
  local kind="$1" ref="$2"
  case "$kind" in
    commit)
      icode_looks_like_commit "$ref" || die "ICODE_GIT_REF_KIND=commit requires a git SHA (7–40 hex chars), not a branch name. Use kind=branch for '${ref}'."
      ;;
    pr)
      icode_looks_like_pr_number "$ref" || die "ICODE_GIT_REF_KIND=pr requires a pull-request number (positive integer), not '${ref}'."
      ;;
  esac
}

# GitHub: refs/pull/N/head. GitCode (and a local test remote): merge-requests, then pull.
icode_git_pr_refs() {
  local url="$1" number="$2" host
  host="${url#https://}"
  host="${host%%/*}"
  host="${host%%:*}"
  case "$host" in
    github.com | www.github.com)
      printf '%s\n' "refs/pull/${number}/head"
      ;;
    *)
      printf '%s\n' "refs/merge-requests/${number}/head"
      printf '%s\n' "refs/pull/${number}/head"
      ;;
  esac
}

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
    die "ICODE_EXPECT_SHA is set but tasks/icode did not record an iCode SHA"
  fi
  [ "$head" = "$expect" ] || die "ICODE_EXPECT_SHA=$expect but recorded SHA is $head"
}

# The generated launcher is the only file tasks/icode may add. .venv is gitignored upstream.
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

icode_sha256() {
  python3 - "$1" <<'PY'
import hashlib, sys
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
}

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

icode_docker_platform() {
  case "$(uname -m)" in
    aarch64 | arm64) printf '%s\n' linux/arm64 ;;
    *) printf '%s\n' linux/amd64 ;;
  esac
}

# Platform the iCode runtime runs on: this build's task containers
# (task_container_platform in _common.sh), else this host's CPU.
icode_target_platform() {
  if declare -F task_container_platform >/dev/null 2>&1; then
    task_container_platform
  else
    icode_docker_platform
  fi
}

# x86_64 or aarch64 for a linux/<arch> platform, as iCode drop names spell it.
icode_platform_cpu() {
  case "${1#linux/}" in
    arm64 | aarch64) printf '%s\n' aarch64 ;;
    *) printf '%s\n' x86_64 ;;
  esac
}

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

ICODE_LINUX_UV=/mac-k3d-uv

# uv sync runs in ICODE_LINUX_BUILD_IMAGE when this host cannot run the task
# containers' Linux runtime itself: macOS, or a Linux host of another CPU type.
# MAC_K3D_ICODE_CONTAINER_BUILD=1 forces it (tests).
icode_container_build_needed() {
  [ "${MAC_K3D_ICODE_CONTAINER_BUILD:-}" = 1 ] && return 0
  [ "$(uname -s)" = Linux ] || return 0
  [ "$(icode_docker_platform)" != "$(icode_target_platform)" ]
}

# A venv uv built in the container links its python into the container's uv
# cache; embed, sanitize and the probes then run there too.
icode_tree_built_in_container() {
  case "$(readlink "$1/.venv/bin/python" 2>/dev/null || true)" in
    "$ICODE_LINUX_UV"/*) return 0 ;;
  esac
  return 1
}

icode_linux_image() {
  local img="${ICODE_LINUX_BUILD_IMAGE:-}" lib
  if [ -z "$img" ]; then
    lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    img="$(sed -n 's/^ICODE_LINUX_BUILD_IMAGE=//p' "$lib/../config/toolchain.env" 2>/dev/null | head -n 1)"
  fi
  [ -n "$img" ] || die "ICODE_LINUX_BUILD_IMAGE is missing from pipeline/config/toolchain.env"
  printf '%s\n' "$img"
}

# Run a command in ICODE_LINUX_BUILD_IMAGE on the task platform, as this user.
# $1 and pipeline/lib are mounted at their own paths, so venv links, .pth files
# and the sanitizer manifest name the same paths as a host-built tree. uv's
# cache and managed CPython live in one cache per platform under MAC_K3D_SHARE,
# always mounted at ICODE_LINUX_UV. ICODE_LINUX_WORKDIR sets the working
# directory (default $1). Git credentials pass by name, never on the command line.
icode_linux_run() {
  local dest="$1" plat lib cache name passwd
  local -a args
  shift
  have docker || die "docker is required to build the iCode runtime for $(icode_target_platform) on this host"
  plat="$(icode_target_platform)"
  lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cache="${MAC_K3D_SHARE:-$HOME/.local/share/mac-k3d}/uv-cache-linux-${plat#linux/}"
  mkdir -p "$cache"
  args=(run --rm --platform "$plat" --user "$(id -u):$(id -g)"
    -e HOME=/tmp
    -e "UV_CACHE_DIR=$ICODE_LINUX_UV/cache"
    -e "UV_PYTHON_INSTALL_DIR=$ICODE_LINUX_UV/python"
    -e UV_PYTHON_PREFERENCE=only-managed
    -e UV_LINK_MODE=copy
    -v "$cache:$ICODE_LINUX_UV"
    -v "$dest:$dest"
    -v "$lib:$lib:ro"
    -w "${ICODE_LINUX_WORKDIR:-$dest}")
  for name in MAC_K3D_GIT_TOKEN MAC_K3D_GIT_USERNAME GIT_TERMINAL_PROMPT GIT_CONFIG_NOSYSTEM \
    GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_CONFIG_KEY_1 GIT_CONFIG_VALUE_1 GIT_SSH_COMMAND; do
    if printenv "$name" >/dev/null 2>&1; then
      args+=(-e "$name")
    fi
  done
  if [ -n "${GIT_ASKPASS:-}" ] && [ -f "$GIT_ASKPASS" ]; then
    args+=(-e GIT_ASKPASS -v "$GIT_ASKPASS:$GIT_ASKPASS:ro")
  fi
  # ssh refuses to run for a uid without a passwd entry.
  if [ -n "${GIT_SSH_COMMAND:-}" ] && [ -d "$HOME/.ssh" ]; then
    passwd="${cache}.passwd"
    printf 'root:x:0:0:root:/root:/bin/sh\nmac-k3d:x:%s:%s:mac-k3d:/tmp:/bin/sh\n' "$(id -u)" "$(id -g)" >"$passwd"
    args+=(-v "$passwd:/etc/passwd:ro" -v "$HOME/.ssh:/tmp/.ssh:ro")
  fi
  docker "${args[@]}" "$(icode_linux_image)" "$@"
}

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

# Delete a leftover tree/file even when Docker left root/nobody-owned files.
# Plain rm first; if that fails, alpine as root on a parent bind-mount (no sudo).
icode_force_rm() {
  local path="${1:-}" parent base plat
  [ -n "$path" ] || return 0
  [ -e "$path" ] || [ -L "$path" ] || return 0
  rm -rf "$path" 2>/dev/null || true
  [ -e "$path" ] || [ -L "$path" ] || return 0
  have docker || die "cannot remove leftover ${path} (root/nobody files from a prior sandbox). Docker is required to delete it."
  parent="$(cd "$(dirname "$path")" && pwd)" || die "cannot resolve parent of ${path}"
  base="$(basename "$path")"
  case "$base" in
    '' | . | .. | /) die "refusing to docker-rm unsafe path ${path}" ;;
  esac
  [ "$parent" != "/" ] || die "refusing to docker-rm under /"
  plat="$(icode_docker_platform)"
  echo "removing leftover ${path} via docker (prior sandbox left root/nobody files)" >&2
  docker run --rm --platform "$plat" -v "${parent}:/mac-k3d-wipe" alpine:3.20 rm -rf "/mac-k3d-wipe/${base}"
  if [ -e "$path" ] || [ -L "$path" ]; then
    die "cannot remove leftover ${path} (docker rm of root/nobody sandbox files failed)"
  fi
}

# After Pier/Harbor, give the bind-mounted host tree back to the invoking user.
icode_reclaim_host_tree() {
  local dest="${1:-}" parent base plat uid gid
  [ -n "$dest" ] && [ -d "$dest" ] || return 0
  have docker || return 0
  parent="$(cd "$(dirname "$dest")" && pwd)" || return 0
  base="$(basename "$dest")"
  case "$base" in
    '' | . | .. | /) return 0 ;;
  esac
  [ "$parent" != "/" ] || return 0
  uid="$(id -u)"
  gid="$(id -g)"
  plat="$(icode_docker_platform)"
  docker run --rm --platform "$plat" -v "${parent}:/mac-k3d-wipe" alpine:3.20 chown -R "${uid}:${gid}" "/mac-k3d-wipe/${base}" >/dev/null 2>&1 || true
}

icode_git_checkout() {
  local dest="$1" url="$2" ref="$3" kind="$4" tok fetched spec
  local -a git_cmd
  tok="${MAC_K3D_GIT_TOKEN:-}"
  git_cmd=(git -c core.hooksPath=/dev/null -c init.defaultBranch=main -c advice.defaultBranchName=false -c credential.helper= -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30)
  if [ "${MAC_K3D_ICODE_GIT_ALLOW_FILE:-}" != 1 ]; then
    git_cmd+=(-c protocol.file.allow=never)
  fi
  if [ -e "$dest" ]; then
    icode_force_rm "$dest"
  fi
  mkdir -p "$dest"
  icode_git "${git_cmd[@]}" init "$dest" >/dev/null || icode_git_clone_fail "$url" "$ref" "$tok"
  icode_git "${git_cmd[@]}" -C "$dest" remote add origin "$url" || icode_git_clone_fail "$url" "$ref" "$tok"
  case "$kind" in
    pr)
      icode_validate_ref_for_kind pr "$ref"
      fetched=0
      while IFS= read -r spec; do
        [ -n "$spec" ] || continue
        if icode_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "$spec"; then
          fetched=1
          break
        fi
      done < <(icode_git_pr_refs "$url" "$ref")
      [ "$fetched" -eq 1 ] || icode_git_clone_fail "$url" "$ref" "$tok"
      icode_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD || icode_git_clone_fail "$url" "$ref" "$tok"
      ;;
    commit)
      fetched=0
      if icode_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "$ref" >/dev/null 2>&1 \
        || icode_git "${git_cmd[@]}" -C "$dest" fetch origin "$ref" >/dev/null 2>&1 \
        || icode_git "${git_cmd[@]}" -C "$dest" fetch origin "+${ref}:refs/commits/${ref}" >/dev/null 2>&1; then
        fetched=1
      fi
      if [ "$fetched" -eq 0 ]; then
        # Abbreviated SHA: fetch advertised heads/tags, then resolve the object locally.
        icode_git "${git_cmd[@]}" -C "$dest" fetch origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*' || icode_git_clone_fail "$url" "$ref" "$tok"
      fi
      icode_git "${git_cmd[@]}" -C "$dest" checkout --detach "$ref" || icode_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD || icode_git_clone_fail "$url" "$ref" "$tok"
      ;;
    tag)
      if ! icode_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "refs/tags/${ref}:refs/tags/${ref}"; then
        icode_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin tag "$ref" || icode_git "${git_cmd[@]}" -C "$dest" fetch origin "refs/tags/${ref}:refs/tags/${ref}" || icode_git_clone_fail "$url" "$ref" "$tok"
      fi
      icode_git "${git_cmd[@]}" -C "$dest" checkout --detach "refs/tags/${ref}" || icode_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD || icode_git_clone_fail "$url" "$ref" "$tok"
      ;;
    *)
      if ! icode_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "${ref}:refs/remotes/origin/${ref}"; then
        icode_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "$ref" || icode_git "${git_cmd[@]}" -C "$dest" fetch origin "$ref" || icode_git_clone_fail "$url" "$ref" "$tok"
      fi
      icode_git "${git_cmd[@]}" -C "$dest" checkout -B "$ref" FETCH_HEAD || icode_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD || icode_git_clone_fail "$url" "$ref" "$tok"
      ;;
  esac
  icode_assert_expect_sha "$dest"
}

icode_write_wrapper() {
  local dest="$1"
  cat >"$dest" <<'WRAP'
#!/bin/sh
# Bind-mounted git venv: host uv CPython is not in the sandbox. tasks/icode copies it to
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
  [ -n "$py" ] || die "sandbox python missing under $dest/.venv/sandbox-cpython (tasks/icode embed failed)"
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

icode_write_askpass() {
  local f="$WORKDIR/.git-askpass"
  umask 077
  cat >"$f" <<'ASK'
#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s\n' "${MAC_K3D_GIT_USERNAME:-x-access-token}" ;;
  *) printf '%s\n' "${MAC_K3D_GIT_TOKEN:-}" ;;
esac
ASK
  chmod 700 "$f"
  printf '%s\n' "$f"
}

icode_token_env_for_host() {
  case "$1" in
    github.com | www.github.com) printf '%s\n' GITHUB_TOKEN ;;
    gitcode.com) printf '%s\n' GITCODE_TOKEN ;;
    *) die "ICODE_GIT_URL host '${1}' is not allow-listed (github.com, www.github.com, gitcode.com)" ;;
  esac
}

icode_resolve_git_token() {
  local host="$1"
  case "$host" in
    github.com | www.github.com) printf '%s\n' "${GITHUB_TOKEN:-${MAC_K3D_GITHUB_PAT:-}}" ;;
    gitcode.com) printf '%s\n' "${GITCODE_TOKEN:-${MAC_K3D_GITCODE_PAT:-}}" ;;
    *) printf '%s\n' "" ;;
  esac
}

icode_git_username_for_host() {
  if [ -n "${MAC_K3D_GIT_USERNAME:-}" ]; then
    printf '%s\n' "$MAC_K3D_GIT_USERNAME"
    return 0
  fi
  case "$1" in
    gitcode.com) printf '%s\n' oauth2 ;;
    *) printf '%s\n' x-access-token ;;
  esac
}

# Fail-fast: no credential helper prompt, abort stalled HTTPS instead of hanging.
icode_git() {
  local rc
  if have timeout; then
    timeout 90 "$@"
    rc=$?
    if [ "$rc" -eq 124 ]; then
      echo "ERROR: git timed out after 90s (often a private repo with no PAT)" >&2
      return 124
    fi
    return "$rc"
  fi
  "$@"
}

icode_git_clone_fail() {
  local url="$1" ref="$2" token="$3"
  if [ -z "$token" ]; then
    die "git clone failed for ${url}. If the repo is private, store GITCODE_TOKEN (gitcode.com) or GITHUB_TOKEN (github.com) in gitignored .env (chmod 600), or Jenkins credential gitcode-pat / github-pat. Do not put the token in the URL or paste it into chat. GitCode username override: MAC_K3D_GIT_USERNAME in .env."
  fi
  die "git clone failed for ${url} (ref ${ref}). Check URL/ref and that the PAT can read the repo. Token not printed."
}

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

icode_begin_git_auth() {
  local url="$1" host token
  host="${url#https://}"
  host="${host%%/*}"
  host="${host%%:*}"
  export GIT_TERMINAL_PROMPT=0
  export GIT_CONFIG_NOSYSTEM=1
  token="$(icode_resolve_git_token "$host")"
  export MAC_K3D_GIT_USERNAME="$(icode_git_username_for_host "$host")"
  if [ -n "$token" ]; then
    export MAC_K3D_GIT_TOKEN="$token"
    GIT_ASKPASS="$(icode_write_askpass)"
    export GIT_ASKPASS
  fi
}

icode_end_git_auth() {
  unset MAC_K3D_GIT_TOKEN GIT_ASKPASS || true
}

# Fetch the form's ref, or ICODE_GIT_SHA when a dispatcher pinned one commit.
icode_checkout_requested() {
  local dest="$1" url="$2" ref="$3" kind="$4" pin head want
  pin="${ICODE_GIT_SHA:-}"
  if [ -n "$pin" ]; then
    icode_looks_like_commit "$pin" || die "ICODE_GIT_SHA must be a git SHA (7–40 hex chars), not '${pin}'."
    icode_git_checkout "$dest" "$url" "$pin" commit
    head="$(git -C "$dest" rev-parse HEAD | tr '[:upper:]' '[:lower:]')"
    want="$(printf '%s' "$pin" | tr '[:upper:]' '[:lower:]')"
    [ "$head" = "$want" ] || die "ICODE_GIT_SHA=$want but checkout HEAD is $head"
  else
    icode_git_checkout "$dest" "$url" "$ref" "$kind"
  fi
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
