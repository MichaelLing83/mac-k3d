#!/usr/bin/env bash
# Git and build-container helpers every harness input shares (sourced by
# icode_input.sh and jiuwenswarm_input.sh). Messages name the caller's inputs:
# HGIT_LABEL is the variable prefix (ICODE_GIT by default, JIUWENSWARM_GIT for
# JiuwenSwarm). Never prints a token.
#
#   hgit_validate_url / hgit_validate_ref / hgit_normalize_ref_kind /
#   hgit_validate_ref_for_kind      the https allowlist and the ref rules
#   hgit_checkout DEST URL REF KIND  init + fetch of one ref, detached
#                                    (commit: the SHA, else the tip that points at it)
#   hgit_begin_auth URL / hgit_end_auth  the host's PAT through GIT_ASKPASS
#   hgit_resolve_sha URL REF KIND   the commit a ref names (ls-remote, else a fetch)
#   hgit_linux_run DEST CMD...      CMD in the Linux build image on the task platform
#   hgit_force_rm PATH / hgit_reclaim_tree PATH  root-owned leftovers of a sandbox

if ! declare -F die >/dev/null 2>&1; then
  die() {
    echo "ERROR: $*" >&2
    exit 1
  }
fi
if ! declare -F have >/dev/null 2>&1; then
  have() { command -v "$1" >/dev/null 2>&1; }
fi

HGIT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HGIT_LINUX_UV=/mac-k3d-uv

hgit_label() {
  printf '%s\n' "${HGIT_LABEL:-ICODE_GIT}"
}

hgit_host_allowed() {
  case "$1" in
    github.com | www.github.com | gitcode.com) return 0 ;;
    *) return 1 ;;
  esac
}

hgit_url_host() {
  local host="${1#https://}"
  host="${host%%/*}"
  printf '%s\n' "${host%%:*}"
}

hgit_validate_url() {
  local url="${1:-}" rest host label
  label="$(hgit_label)"
  [ -n "$url" ] || die "${label}_URL is empty"
  case "$url" in
    https://*) ;;
    *) die "${label}_URL must be https:// (not file://, ssh://, or http://)" ;;
  esac
  rest="${url#https://}"
  case "$rest" in
    *@*) die "${label}_URL must not contain userinfo (no tokens in the URL)" ;;
  esac
  host="${rest%%/*}"
  host="${host%%:*}"
  hgit_host_allowed "$host" || die "${label}_URL host '${host}' is not allow-listed (github.com, www.github.com, gitcode.com)"
}

hgit_validate_ref() {
  local ref="${1:-}" label
  label="$(hgit_label)"
  [ -n "$ref" ] || die "${label}_REF is empty"
  case "$ref" in
    -*) die "${label}_REF must not start with -" ;;
  esac
  case "$ref" in
    *..*) die "${label}_REF must not contain .." ;;
  esac
  [[ "$ref" =~ ^[A-Za-z0-9._/+\-]+$ ]] || die "${label}_REF is not a tag, commit, or branch name"
}

hgit_looks_like_commit() {
  [[ "${1:-}" =~ ^[0-9a-fA-F]{7,40}$ ]]
}

hgit_looks_like_pr_number() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

hgit_normalize_ref_kind() {
  local k="${1:-auto}" ref="${2:-}"
  k="$(printf '%s' "$k" | tr 'A-Z' 'a-z')"
  case "$k" in
    branch | tag | commit | pr)
      printf '%s\n' "$k"
      ;;
    auto | "")
      if hgit_looks_like_commit "$ref"; then
        printf '%s\n' commit
      else
        printf '%s\n' branch
      fi
      ;;
    *)
      die "$(hgit_label)_REF_KIND must be branch, tag, commit, or pr (got ${k})"
      ;;
  esac
}

hgit_validate_ref_for_kind() {
  local kind="$1" ref="$2" label
  label="$(hgit_label)"
  case "$kind" in
    commit)
      hgit_looks_like_commit "$ref" || die "${label}_REF_KIND=commit requires a git SHA (7–40 hex chars), not a branch name. Use kind=branch for '${ref}'."
      ;;
    pr)
      hgit_looks_like_pr_number "$ref" || die "${label}_REF_KIND=pr requires a pull-request number (positive integer), not '${ref}'."
      ;;
  esac
}

# GitHub: refs/pull/N/head. GitCode (and a local test remote): merge-requests, then pull.
hgit_pr_refs() {
  local url="$1" number="$2"
  case "$(hgit_url_host "$url")" in
    github.com | www.github.com)
      printf '%s\n' "refs/pull/${number}/head"
      ;;
    *)
      printf '%s\n' "refs/merge-requests/${number}/head"
      printf '%s\n' "refs/pull/${number}/head"
      ;;
  esac
}

hgit_sha256() {
  python3 - "$1" <<'PY'
import hashlib, sys
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
}

hgit_docker_platform() {
  case "$(uname -m)" in
    aarch64 | arm64) printf '%s\n' linux/arm64 ;;
    *) printf '%s\n' linux/amd64 ;;
  esac
}

# Platform the harness runtime runs on: this build's task containers
# (task_container_platform in _common.sh), else this host's CPU.
hgit_target_platform() {
  if declare -F task_container_platform >/dev/null 2>&1; then
    task_container_platform
  else
    hgit_docker_platform
  fi
}

# x86_64 or aarch64 for a linux/<arch> platform.
hgit_platform_cpu() {
  case "${1#linux/}" in
    arm64 | aarch64) printf '%s\n' aarch64 ;;
    *) printf '%s\n' x86_64 ;;
  esac
}

# True when this host cannot run the task containers' Linux runtime itself:
# macOS, or a Linux host of another CPU type. MAC_K3D_ICODE_CONTAINER_BUILD=1
# forces the container (tests).
hgit_container_build_needed() {
  [ "${MAC_K3D_ICODE_CONTAINER_BUILD:-}" = 1 ] && return 0
  [ "$(uname -s)" = Linux ] || return 0
  [ "$(hgit_docker_platform)" != "$(hgit_target_platform)" ]
}

hgit_linux_image() {
  local img="${ICODE_LINUX_BUILD_IMAGE:-}"
  if [ -z "$img" ]; then
    img="$(sed -n 's/^ICODE_LINUX_BUILD_IMAGE=//p' "$HGIT_LIB/../config/toolchain.env" 2>/dev/null | head -n 1)"
  fi
  [ -n "$img" ] || die "ICODE_LINUX_BUILD_IMAGE is missing from pipeline/config/toolchain.env"
  printf '%s\n' "$img"
}

# uv's cache and managed CPython for one platform, under MAC_K3D_SHARE.
hgit_uv_cache() {
  local plat="${1:-$(hgit_target_platform)}" cache
  cache="${MAC_K3D_SHARE:-$HOME/.local/share/mac-k3d}/uv-cache-linux-${plat#linux/}"
  mkdir -p "$cache"
  printf '%s\n' "$cache"
}

# Run a command in the Linux build image on the task platform, as this user.
# $1 and pipeline/lib are mounted at their own paths, so venv links, .pth files
# and the sanitizer manifest name the same paths as a host-built tree. uv's
# cache and managed CPython live in one cache per platform under MAC_K3D_SHARE,
# always mounted at HGIT_LINUX_UV. HGIT_LINUX_WORKDIR (or ICODE_LINUX_WORKDIR)
# sets the working directory (default $1); HGIT_LINUX_EXTRA adds docker run
# arguments (more mounts, env). Git credentials pass by name, never on the
# command line.
hgit_linux_run() {
  local dest="$1" plat cache name passwd
  local -a args
  shift
  have docker || die "docker is required to build the harness runtime for $(hgit_target_platform) on this host"
  plat="$(hgit_target_platform)"
  cache="$(hgit_uv_cache "$plat")"
  args=(run --rm --platform "$plat" --user "$(id -u):$(id -g)"
    -e HOME=/tmp
    -e "UV_CACHE_DIR=$HGIT_LINUX_UV/cache"
    -e "UV_PYTHON_INSTALL_DIR=$HGIT_LINUX_UV/python"
    -e UV_PYTHON_PREFERENCE=only-managed
    -e UV_LINK_MODE=copy
    -v "$cache:$HGIT_LINUX_UV"
    -v "$dest:$dest"
    -v "$HGIT_LIB:$HGIT_LIB:ro"
    -w "${HGIT_LINUX_WORKDIR:-${ICODE_LINUX_WORKDIR:-$dest}}")
  if [ -n "${HGIT_LINUX_EXTRA+x}" ]; then
    args+=("${HGIT_LINUX_EXTRA[@]}")
  fi
  # A worker whose bridge network cannot reach PyPI (a VPN with a smaller MTU)
  # can build on the host's network; trials keep their own.
  if [ -n "${MAC_K3D_BUILD_NETWORK:-}" ]; then
    args+=(--network "$MAC_K3D_BUILD_NETWORK")
  fi
  for name in MAC_K3D_GIT_TOKEN MAC_K3D_GIT_USERNAME GIT_TERMINAL_PROMPT GIT_CONFIG_NOSYSTEM \
    GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_CONFIG_KEY_1 GIT_CONFIG_VALUE_1 GIT_SSH_COMMAND \
    UV_PYTHON_INSTALL_MIRROR UV_DEFAULT_INDEX UV_INDEX_URL HTTPS_PROXY HTTP_PROXY NO_PROXY; do
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
  docker "${args[@]}" "$(hgit_linux_image)" "$@"
}

# Delete a leftover tree/file even when Docker left root/nobody-owned files.
# Plain rm first; if that fails, alpine as root on a parent bind-mount (no sudo).
hgit_force_rm() {
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
  plat="$(hgit_docker_platform)"
  echo "removing leftover ${path} via docker (prior sandbox left root/nobody files)" >&2
  docker run --rm --platform "$plat" -v "${parent}:/mac-k3d-wipe" alpine:3.20 rm -rf "/mac-k3d-wipe/${base}"
  if [ -e "$path" ] || [ -L "$path" ]; then
    die "cannot remove leftover ${path} (docker rm of root/nobody sandbox files failed)"
  fi
}

# After Harbor, give a bind-mounted host tree back to the invoking user.
hgit_reclaim_tree() {
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
  plat="$(hgit_docker_platform)"
  docker run --rm --platform "$plat" -v "${parent}:/mac-k3d-wipe" alpine:3.20 chown -R "${uid}:${gid}" "/mac-k3d-wipe/${base}" >/dev/null 2>&1 || true
}

hgit_write_askpass() {
  local f="${WORKDIR:-${TMPDIR:-/tmp}}/.git-askpass"
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

hgit_token_env_for_host() {
  case "$1" in
    github.com | www.github.com) printf '%s\n' GITHUB_TOKEN ;;
    gitcode.com) printf '%s\n' GITCODE_TOKEN ;;
    *) die "$(hgit_label)_URL host '${1}' is not allow-listed (github.com, www.github.com, gitcode.com)" ;;
  esac
}

hgit_resolve_token() {
  case "$1" in
    github.com | www.github.com) printf '%s\n' "${GITHUB_TOKEN:-${MAC_K3D_GITHUB_PAT:-}}" ;;
    gitcode.com) printf '%s\n' "${GITCODE_TOKEN:-${MAC_K3D_GITCODE_PAT:-}}" ;;
    *) printf '%s\n' "" ;;
  esac
}

hgit_username_for_host() {
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
hgit_git() {
  local rc
  if have timeout; then
    timeout "${HGIT_TIMEOUT:-90}" "$@"
    rc=$?
    if [ "$rc" -eq 124 ]; then
      echo "ERROR: git timed out after ${HGIT_TIMEOUT:-90}s (often a private repo with no PAT)" >&2
      return 124
    fi
    return "$rc"
  fi
  "$@"
}

hgit_clone_fail() {
  local url="$1" ref="$2" token="$3"
  if [ -z "$token" ]; then
    die "git clone failed for ${url}. If the repo is private, store GITCODE_TOKEN (gitcode.com) or GITHUB_TOKEN (github.com) in gitignored .env (chmod 600), or Jenkins credential gitcode-pat / github-pat. Do not put the token in the URL or paste it into chat. GitCode username override: MAC_K3D_GIT_USERNAME in .env."
  fi
  die "git clone failed for ${url} (ref ${ref}). Check URL/ref and that the PAT can read the repo. Token not printed."
}

hgit_cmd() {
  HGIT_CMD=(git -c core.hooksPath=/dev/null -c init.defaultBranch=main -c advice.defaultBranchName=false -c credential.helper= -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30)
  if [ "${MAC_K3D_ICODE_GIT_ALLOW_FILE:-}" != 1 ]; then
    HGIT_CMD+=(-c protocol.file.allow=never)
  fi
}

# From ls-remote lines on stdin, print the first tip ref whose commit equals
# WANT (a 7–40 hex SHA). Prefer heads, then tags, then merge-request heads,
# then pull heads. A short SHA that matches two different tips fails. Several
# refs with the same tip are the same tree; the first in that order is enough.
# Script is -c so stdin stays the piped listing (a heredoc would consume it).
hgit_commit_matching_tip() {
  local want="$1"
  python3 -c '
import sys

want = sys.argv[1].lower()
lines = sys.stdin.read().splitlines()
heads, tags_peeled, tags_direct, mrs, pulls = [], {}, {}, [], []
for line in lines:
    parts = line.split()
    if len(parts) < 2:
        continue
    sha, ref = parts[0].lower(), parts[1]
    if ref.startswith("refs/heads/"):
        heads.append((sha, ref))
    elif ref.startswith("refs/merge-requests/") and ref.endswith("/head"):
        mrs.append((sha, ref))
    elif ref.startswith("refs/pull/") and ref.endswith("/head"):
        pulls.append((sha, ref))
    elif ref.startswith("refs/tags/"):
        if ref.endswith("^{}"):
            tags_peeled[ref[len("refs/tags/") : -3]] = sha
        else:
            tags_direct[ref[len("refs/tags/") :]] = sha
tags = []
for name, sha in tags_peeled.items():
    tags.append((sha, "refs/tags/%s" % name))
for name, sha in tags_direct.items():
    if name not in tags_peeled:
        tags.append((sha, "refs/tags/%s" % name))
tips = heads + tags + mrs + pulls
matches = []
seen = set()
for sha, ref in tips:
    ok = sha == want if len(want) == 40 else sha.startswith(want)
    if not ok or sha in seen:
        continue
    matches.append((sha, ref))
    seen.add(sha)
if not matches:
    raise SystemExit(0)
if len(want) < 40 and len(matches) > 1:
    print(
        "ERROR: commit %s matches more than one tip (%s); use a longer SHA"
        % (want, ", ".join("%s…" % s[:12] for s, _ in matches)),
        file=sys.stderr,
    )
    raise SystemExit(1)
print(matches[0][1])
' "$want"
}

hgit_checkout() {
  local dest="$1" url="$2" ref="$3" kind="$4" tok fetched spec listed head want
  local -a git_cmd
  tok="${MAC_K3D_GIT_TOKEN:-}"
  hgit_cmd
  git_cmd=("${HGIT_CMD[@]}")
  if [ -e "$dest" ]; then
    hgit_force_rm "$dest"
  fi
  mkdir -p "$dest"
  hgit_git "${git_cmd[@]}" init "$dest" >/dev/null || hgit_clone_fail "$url" "$ref" "$tok"
  hgit_git "${git_cmd[@]}" -C "$dest" remote add origin "$url" || hgit_clone_fail "$url" "$ref" "$tok"
  case "$kind" in
    pr)
      hgit_validate_ref_for_kind pr "$ref"
      fetched=0
      while IFS= read -r spec; do
        [ -n "$spec" ] || continue
        if hgit_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "$spec"; then
          fetched=1
          break
        fi
      done < <(hgit_pr_refs "$url" "$ref")
      [ "$fetched" -eq 1 ] || hgit_clone_fail "$url" "$ref" "$tok"
      hgit_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD || hgit_clone_fail "$url" "$ref" "$tok"
      ;;
    commit)
      # One shallow fetch of the SHA when the server allows it (local remotes,
      # GitHub). When it does not (GitCode often refuses a raw SHA that is only
      # a merge-request tip), list tips and shallow-fetch the one that matches.
      # Never download every branch and tag.
      hgit_validate_ref_for_kind commit "$ref"
      if ! hgit_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "$ref"; then
        listed="$(hgit_git "${git_cmd[@]}" ls-remote "$url" \
          'refs/heads/*' 'refs/tags/*' 'refs/merge-requests/*/head' 'refs/pull/*/head' \
          2>/dev/null </dev/null || true)"
        spec="$(printf '%s\n' "$listed" | hgit_commit_matching_tip "$ref")" || hgit_clone_fail "$url" "$ref" "$tok"
        [ -n "$spec" ] || die "$(hgit_label)_REF ${ref} is not a tip of any branch, tag, or pull request on ${url}, and the server did not return that commit"
        hgit_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "$spec" || hgit_clone_fail "$url" "$ref" "$tok"
      fi
      hgit_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD \
        || hgit_git "${git_cmd[@]}" -C "$dest" checkout --detach "$ref" \
        || hgit_clone_fail "$url" "$ref" "$tok"
      head="$(git -C "$dest" rev-parse HEAD | tr '[:upper:]' '[:lower:]')"
      want="$(printf '%s' "$ref" | tr '[:upper:]' '[:lower:]')"
      case "$head" in
        "$want"*) ;;
        *) die "$(hgit_label)_REF ${ref} checked out as ${head}" ;;
      esac
      ;;
    tag)
      if ! hgit_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "refs/tags/${ref}:refs/tags/${ref}"; then
        hgit_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin tag "$ref" || hgit_git "${git_cmd[@]}" -C "$dest" fetch origin "refs/tags/${ref}:refs/tags/${ref}" || hgit_clone_fail "$url" "$ref" "$tok"
      fi
      hgit_git "${git_cmd[@]}" -C "$dest" checkout --detach "refs/tags/${ref}" || hgit_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD || hgit_clone_fail "$url" "$ref" "$tok"
      ;;
    *)
      if ! hgit_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "${ref}:refs/remotes/origin/${ref}"; then
        hgit_git "${git_cmd[@]}" -C "$dest" fetch --depth 1 origin "$ref" || hgit_git "${git_cmd[@]}" -C "$dest" fetch origin "$ref" || hgit_clone_fail "$url" "$ref" "$tok"
      fi
      hgit_git "${git_cmd[@]}" -C "$dest" checkout -B "$ref" FETCH_HEAD || hgit_git "${git_cmd[@]}" -C "$dest" checkout --detach FETCH_HEAD || hgit_clone_fail "$url" "$ref" "$tok"
      ;;
  esac
}

# The form's ref, or PIN (a dispatcher's resolved commit) when one is given.
hgit_checkout_requested() {
  local dest="$1" url="$2" ref="$3" kind="$4" pin="${5:-}" head want label
  label="$(hgit_label)"
  if [ -n "$pin" ]; then
    hgit_looks_like_commit "$pin" || die "${label}_SHA must be a git SHA (7–40 hex chars), not '${pin}'."
    hgit_checkout "$dest" "$url" "$pin" commit
    head="$(git -C "$dest" rev-parse HEAD | tr '[:upper:]' '[:lower:]')"
    want="$(printf '%s' "$pin" | tr '[:upper:]' '[:lower:]')"
    [ "$head" = "$want" ] || die "${label}_SHA=$want but checkout HEAD is $head"
  else
    hgit_checkout "$dest" "$url" "$ref" "$kind"
  fi
}

hgit_begin_auth() {
  local url="$1" host token
  host="$(hgit_url_host "$url")"
  export GIT_TERMINAL_PROMPT=0
  export GIT_CONFIG_NOSYSTEM=1
  token="$(hgit_resolve_token "$host")"
  export MAC_K3D_GIT_USERNAME="$(hgit_username_for_host "$host")"
  if [ -n "$token" ]; then
    export MAC_K3D_GIT_TOKEN="$token"
    GIT_ASKPASS="$(hgit_write_askpass)"
    export GIT_ASKPASS
  fi
}

hgit_end_auth() {
  unset MAC_K3D_GIT_TOKEN GIT_ASKPASS || true
}

# The commit KIND/REF names on URL, without a checkout when the server says it:
# a full SHA is itself; a branch, tag or PR head is asked with ls-remote. An
# abbreviated SHA, or a ref ls-remote does not list, is fetched into a scratch
# repo. Stdout is only the SHA. The caller has run hgit_begin_auth.
hgit_resolve_sha() {
  local url="$1" ref="$2" kind="$3" spec sha="" scratch listed
  local -a specs=()
  hgit_cmd
  case "$kind" in
    commit)
      if [[ "$ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
        printf '%s\n' "$ref" | tr '[:upper:]' '[:lower:]'
        return 0
      fi
      ;;
    pr) while IFS= read -r spec; do specs+=("$spec"); done < <(hgit_pr_refs "$url" "$ref") ;;
    # An annotated tag's commit is its peeled ^{} line.
    tag) specs=("refs/tags/${ref}^{}" "refs/tags/${ref}") ;;
    *) specs=("refs/heads/${ref}") ;;
  esac
  if [ "${#specs[@]}" -gt 0 ]; then
    # ls-remote prints a tag's peeled line only for a glob pattern.
    if [ "$kind" = tag ]; then
      listed="$(hgit_git "${HGIT_CMD[@]}" ls-remote "$url" "refs/tags/${ref}*" 2>/dev/null </dev/null || true)"
    else
      listed="$(hgit_git "${HGIT_CMD[@]}" ls-remote "$url" "${specs[@]}" 2>/dev/null </dev/null || true)"
    fi
    for spec in "${specs[@]}"; do
      sha="$(printf '%s\n' "$listed" | awk -v want="$spec" '$2 == want {print $1; exit}')"
      if [ -n "$sha" ]; then
        printf '%s\n' "$sha" | tr '[:upper:]' '[:lower:]'
        return 0
      fi
    done
  fi
  scratch="$(mktemp -d)"
  hgit_checkout "$scratch" "$url" "$ref" "$kind" >&2
  sha="$(git -C "$scratch" rev-parse HEAD 2>/dev/null || true)"
  hgit_force_rm "$scratch"
  [ -n "$sha" ] || die "could not resolve $(hgit_label)_REF ${ref} (${kind}) on ${url}"
  printf '%s\n' "$sha" | tr '[:upper:]' '[:lower:]'
}
