#!/usr/bin/env bash
# Host-side JiuwenSwarm inputs (sourced by harness_lib.sh). JiuwenSwarm is the
# `workswarm` Python package; a trial runs its headless `jiuwenswarm-process
# --run-json`. Either input becomes one tree, mounted read-only in the trial at
# /opt/jiuwenswarm-host:
#
#   python/  uv-managed CPython        venv/  workswarm and its dependencies
#   bin/jiuwenswarm-run, bin/jiuwenswarm-python   runtime.json  freeze.txt
#
# The tree is always built in ICODE_LINUX_BUILD_IMAGE on the task platform
# with the tree mounted at /opt/jiuwenswarm-host (jiuwenswarm_build.sh).
#
#   git      JIUWENSWARM_GIT_URL / _REF / _REF_KIND, or the dispatcher's
#            JIUWENSWARM_GIT_SHA; uv sync --frozen --no-dev --no-editable
#   release  JIUWENSWARM_RELEASE (a path or https URL; a Jenkins upload is
#            JIUWENSWARM_RELEASE_FILE), told apart by its content:
#              wheel       a workswarm-*.whl; dependencies come from PyPI
#              wheelhouse  a .tar.gz holding workswarm-*.whl and its dependencies
#              bundle      a mac-k3d harness bundle (harness_store.py pack); used as is
#
# Writes the identity tasks/harness records: icode_git.json {url, kind, ref,
# sha, subject} or icode_release.json {filename, sha256, format}. Never prints
# a token.

if ! declare -F die >/dev/null 2>&1; then
  die() {
    echo "ERROR: $*" >&2
    exit 1
  }
fi
# shellcheck source=./harness_git.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harness_git.sh"

JIUWENSWARM_TARGET=/opt/jiuwenswarm-host
JIUWENSWARM_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

jiuwenswarm_normalize_mode() {
  case "$(printf '%s' "${JIUWENSWARM_MODE:-git}" | tr 'A-Z' 'a-z')" in
    git) printf '%s\n' git ;;
    release | binary | wheel) printf '%s\n' release ;;
    *) die "unknown JIUWENSWARM_MODE=${JIUWENSWARM_MODE} (use git or release)" ;;
  esac
}

jiuwenswarm_python() {
  printf '%s\n' "${JIUWENSWARM_PYTHON:-3.11}"
}

# The oldest glibc the runtime's wheels may need; DeepSWE images are Debian 10 or newer.
jiuwenswarm_python_platform() {
  if [ -n "${JIUWENSWARM_PYTHON_PLATFORM:-}" ]; then
    printf '%s\n' "$JIUWENSWARM_PYTHON_PLATFORM"
    return 0
  fi
  printf '%s-manylinux_2_28\n' "$(hgit_platform_cpu "$(hgit_target_platform)")"
}

# Part of the store key: a change here builds a new tree.
jiuwenswarm_recipe() {
  printf 'jiuwenswarm-v1 python=%s wheels=%s image=%s\n' \
    "$(jiuwenswarm_python)" "$(jiuwenswarm_python_platform)" "$(hgit_linux_image)"
}

# Sets JW_URL, JW_REF and JW_KIND from the form, checked.
jiuwenswarm_git_inputs() {
  JW_URL="${JIUWENSWARM_GIT_URL:-}"
  JW_REF="${JIUWENSWARM_GIT_REF:-}"
  if [ "${MAC_K3D_ICODE_GIT_ALLOW_FILE:-}" = 1 ] && [ -d "$JW_URL" ]; then
    :
  else
    HGIT_LABEL=JIUWENSWARM_GIT hgit_validate_url "$JW_URL"
  fi
  HGIT_LABEL=JIUWENSWARM_GIT hgit_validate_ref "$JW_REF"
  JW_KIND="$(HGIT_LABEL=JIUWENSWARM_GIT hgit_normalize_ref_kind "${JIUWENSWARM_GIT_REF_KIND:-auto}" "$JW_REF")"
  HGIT_LABEL=JIUWENSWARM_GIT hgit_validate_ref_for_kind "$JW_KIND" "$JW_REF"
}

jiuwenswarm_begin_auth() {
  if [ "${MAC_K3D_ICODE_GIT_ALLOW_FILE:-}" = 1 ] && [ -d "$1" ]; then
    return 0
  fi
  hgit_begin_auth "$1"
}

# The commit this build evaluates: the dispatcher's pin, else what the form's ref names now.
jiuwenswarm_resolve_sha() {
  local pin sha
  jiuwenswarm_git_inputs
  pin="$(printf '%s' "${JIUWENSWARM_GIT_SHA:-}" | tr 'A-Z' 'a-z')"
  if [ -n "$pin" ]; then
    hgit_looks_like_commit "$pin" || die "JIUWENSWARM_GIT_SHA must be a git SHA (7–40 hex chars), not '${pin}'."
    if [ "${#pin}" = 40 ]; then
      printf '%s\n' "$pin"
      return 0
    fi
  fi
  jiuwenswarm_begin_auth "$JW_URL"
  if [ -n "$pin" ]; then
    sha="$(HGIT_LABEL=JIUWENSWARM_GIT hgit_resolve_sha "$JW_URL" "$pin" commit)"
  else
    sha="$(HGIT_LABEL=JIUWENSWARM_GIT hgit_resolve_sha "$JW_URL" "$JW_REF" "$JW_KIND")"
  fi
  hgit_end_auth
  [ -n "$sha" ] || die "could not resolve JIUWENSWARM_GIT_REF ${JW_REF} (${JW_KIND})"
  printf '%s\n' "$sha"
}

jiuwenswarm_record_git() {
  local dest="$1" sha subject
  sha="$(git -C "$dest" rev-parse HEAD)"
  subject="$(git -C "$dest" log -1 --format=%s 2>/dev/null || true)"
  python3 - "$WORKDIR/icode_git.json" "$JW_URL" "$JW_KIND" "$JW_REF" "$sha" "$subject" <<'PY'
import json, sys
path, url, kind, ref, sha, subject = sys.argv[1:7]
doc = {"url": url, "kind": kind, "ref": ref, "sha": sha, "subject": subject}
open(path, "w", encoding="utf-8").write(json.dumps(doc, indent=2) + "\n")
PY
}

# The release file on this worker: a path, an https URL (downloaded once per
# build into WORKDIR; tasks/harness removes the last build's copy), or the
# Jenkins upload. Prints its path.
jiuwenswarm_release_file() {
  local src="${JIUWENSWARM_RELEASE:-}" out
  if [ -z "$src" ]; then
    if [ "${JIUWENSWARM_RELEASE_UPLOADED:-}" = 1 ]; then
      die "JIUWENSWARM_MODE=release: the JIUWENSWARM_RELEASE_FILE upload is empty"
    fi
    die "JIUWENSWARM_MODE=release: upload JIUWENSWARM_RELEASE_FILE on Build with Parameters, or set JIUWENSWARM_RELEASE to a .whl or .tar.gz"
  fi
  case "$src" in
    https://*)
      out="$WORKDIR/jiuwenswarm-release.bin"
      if [ ! -s "$out" ] || [ "$(cat "$out.url" 2>/dev/null || true)" != "$src" ]; then
        rm -f "$out" "$out.url"
        curl -fsSL "$src" -o "$out" || die "cannot download JIUWENSWARM_RELEASE ${src%%\?*}"
        printf '%s\n' "$src" >"$out.url"
      fi
      printf '%s\n' "$out"
      ;;
    http://*) die "JIUWENSWARM_RELEASE must be https:// or a file on this worker" ;;
    *)
      [ -f "$src" ] || die "JIUWENSWARM_RELEASE is not a file: $src"
      printf '%s\n' "$src"
      ;;
  esac
}

jiuwenswarm_release_name() {
  local src="${JIUWENSWARM_RELEASE:-}"
  case "$src" in
    https://*) src="${src%%\?*}"; basename "${src%/}" ;;
    *) basename "$1" ;;
  esac
}

# wheel, wheelhouse or bundle, from the file's content (an upload has no useful name).
jiuwenswarm_release_kind() {
  python3 - "$1" "$JIUWENSWARM_LIB" <<'PY'
import json, sys, tarfile, zipfile
from pathlib import PurePosixPath

path, lib = sys.argv[1:3]
sys.path.insert(0, lib)
from harness_store import META, SCHEMA

with open(path, "rb") as fh:
    head = fh.read(4)
seen: list[str] = []
if head == b"PK\x03\x04":
    with zipfile.ZipFile(path) as zf:
        seen = zf.namelist()
    if any(PurePosixPath(n).parts[0].startswith("workswarm-") and PurePosixPath(n).parts[0].endswith(".dist-info") for n in seen):
        print("wheel")
        raise SystemExit(0)
elif head[:2] == b"\x1f\x8b":
    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                seen.append(member.name)
                if member.name == META:
                    fh = tar.extractfile(member)
                    doc = json.loads(fh.read().decode("utf-8")) if fh else {}
                    if doc.get("schema") == SCHEMA:
                        print("bundle")
                        raise SystemExit(0)
                if PurePosixPath(member.name).name.startswith("workswarm-") and member.name.endswith(".whl"):
                    print("wheelhouse")
                    raise SystemExit(0)
    except (tarfile.TarError, OSError, ValueError) as exc:
        print(f"not a readable .tar.gz ({exc})", file=sys.stderr)
listing = ", ".join(seen[:12]) + (" ..." if len(seen) > 12 else "")
print(
    "JIUWENSWARM_RELEASE is not a workswarm wheel, a .tar.gz of wheels with workswarm-*.whl, "
    f"or a mac-k3d harness bundle; it holds: {listing or 'nothing readable'}",
    file=sys.stderr,
)
raise SystemExit(1)
PY
}

jiuwenswarm_record_release() {
  local file="$1" name="$2" kind="$3" hash expect
  hash="$(hgit_sha256 "$file")"
  if [ -n "${JIUWENSWARM_RELEASE_SHA256:-}" ]; then
    expect="$(printf '%s' "$JIUWENSWARM_RELEASE_SHA256" | tr 'A-Z' 'a-z')"
    [ "$hash" = "$expect" ] || die "JIUWENSWARM_RELEASE_SHA256=$expect but this file is $hash"
  fi
  python3 - "$WORKDIR/icode_release.json" "$name" "$hash" "$kind" <<'PY'
import json, sys
path, name, digest, kind = sys.argv[1:5]
doc = {"filename": name, "sha256": digest, "format": kind}
open(path, "w", encoding="utf-8").write(json.dumps(doc, indent=2) + "\n")
PY
  echo "OK jiuwenswarm release file=${name} format=${kind} sha256=${hash}" >&2
}

# A wheel needs its own file name for pip; an upload is called JIUWENSWARM_RELEASE_FILE.
jiuwenswarm_wheel_name() {
  python3 - "$1" <<'PY'
import sys, zipfile
from pathlib import PurePosixPath

with zipfile.ZipFile(sys.argv[1]) as zf:
    names = zf.namelist()
    info = next(n for n in names if PurePosixPath(n).parts[0].endswith(".dist-info") and n.endswith("/WHEEL"))
    dist = PurePosixPath(info).parts[0][: -len(".dist-info")]
    tags = [ln.split(":", 1)[1].strip() for ln in zf.read(info).decode().splitlines() if ln.startswith("Tag:")]
print(f"{dist}-{tags[0] if tags else 'py3-none-any'}.whl")
PY
}

# One build in the Linux image: the tree at /opt/jiuwenswarm-host, the input at
# /mac-k3d-src (git) or /mac-k3d-in (release), and pipeline/lib.
jiuwenswarm_container_build() {
  local tree="$1" input="$2" input_target="$3"
  shift 3
  local -a HGIT_LINUX_EXTRA
  HGIT_LINUX_EXTRA=(-v "$tree:$JIUWENSWARM_TARGET" -v "$input:$input_target"
    -e "JIUWENSWARM_PYTHON=$(jiuwenswarm_python)"
    -e "JIUWENSWARM_PYTHON_PLATFORM=$(jiuwenswarm_python_platform)")
  echo "jiuwenswarm: building the runtime in $(hgit_linux_image) for $(hgit_target_platform) (python $(jiuwenswarm_python), wheels $(jiuwenswarm_python_platform))" >&2
  HGIT_LINUX_WORKDIR=/tmp hgit_linux_run "$tree" bash "$JIUWENSWARM_LIB/jiuwenswarm_build.sh" "$@" >&2
}

# Git dependencies over https with the PAT of the JiuwenSwarm host, as iCode's uv sync does.
jiuwenswarm_build_git() {
  local tree="$1" src="$2" host
  host="$(hgit_url_host "$JW_URL")"
  jiuwenswarm_begin_auth "$JW_URL"
  if [ -n "${MAC_K3D_GIT_TOKEN:-}" ]; then
    export GIT_CONFIG_COUNT=2 \
      GIT_CONFIG_KEY_0="url.https://${host}/.insteadOf" GIT_CONFIG_VALUE_0="ssh://git@${host}/" \
      GIT_CONFIG_KEY_1="url.https://${host}/.insteadOf" GIT_CONFIG_VALUE_1="git@${host}:"
  fi
  jiuwenswarm_container_build "$tree" "$src" /mac-k3d-src git || {
    hgit_end_auth
    unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_CONFIG_KEY_1 GIT_CONFIG_VALUE_1
    die "JiuwenSwarm build failed for ${JW_URL}@$(git -C "$src" rev-parse --short HEAD) (see the uv output above)"
  }
  hgit_end_auth
  unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_CONFIG_KEY_1 GIT_CONFIG_VALUE_1
}

# Build the runtime for this build's input into WORKDIR/jiuwenswarm-runtime and
# record its identity. Prints the tree. A bundle upload is not built here:
# tasks/harness stores it as it is.
jiuwenswarm_prepare_workspace() {
  local tree="$WORKDIR/jiuwenswarm-runtime" src="$WORKDIR/jiuwenswarm-src" in="$WORKDIR/jiuwenswarm-in"
  local mode sha file kind name wheel inner
  mode="$(jiuwenswarm_normalize_mode)"
  hgit_force_rm "$tree"
  mkdir -p "$tree"
  case "$mode" in
    git)
      jiuwenswarm_git_inputs
      sha="$(jiuwenswarm_resolve_sha)"
      jiuwenswarm_begin_auth "$JW_URL"
      HGIT_LABEL=JIUWENSWARM_GIT hgit_checkout "$src" "$JW_URL" "$JW_REF" "$JW_KIND" >&2
      if [ "$(git -C "$src" rev-parse HEAD)" != "$sha" ]; then
        # The ref moved since it was resolved, or the form names a pinned commit.
        HGIT_LABEL=JIUWENSWARM_GIT hgit_checkout "$src" "$JW_URL" "$sha" commit >&2
      fi
      hgit_end_auth
      [ "$(git -C "$src" rev-parse HEAD)" = "$sha" ] || die "JiuwenSwarm checkout HEAD is not $sha"
      [ -f "$src/uv.lock" ] || die "JiuwenSwarm ${sha} has no uv.lock; uv sync --frozen needs one"
      jiuwenswarm_record_git "$src"
      jiuwenswarm_build_git "$tree" "$src"
      ;;
    release)
      file="$(jiuwenswarm_release_file)"
      kind="$(jiuwenswarm_release_kind "$file")" || die "JIUWENSWARM_RELEASE is not a JiuwenSwarm release (see above)"
      [ "$kind" != bundle ] || die "a mac-k3d bundle is stored as it is, not built (tasks/harness)"
      name="$(jiuwenswarm_release_name "$file")"
      jiuwenswarm_record_release "$file" "$name" "$kind"
      hgit_force_rm "$in"
      mkdir -p "$in"
      if [ "$kind" = wheel ]; then
        wheel="$(jiuwenswarm_wheel_name "$file")"
        cp "$file" "$in/$wheel"
        jiuwenswarm_container_build "$tree" "$in" /mac-k3d-in wheel "/mac-k3d-in/$wheel" \
          || die "JiuwenSwarm install of $name failed (see the uv output above)"
      else
        tar -xzf "$file" -C "$in"
        inner="$(cd "$in" && find . -type f -name 'workswarm-*.whl' | LC_ALL=C sort | head -n 1)"
        [ -n "$inner" ] || die "no workswarm-*.whl in $name"
        jiuwenswarm_container_build "$tree" "$in" /mac-k3d-in wheelhouse \
          "/mac-k3d-in/$(dirname "${inner#./}")" "/mac-k3d-in/${inner#./}" \
          || die "JiuwenSwarm install of $name failed; a wheelhouse must hold every dependency (see the uv output above)"
      fi
      ;;
  esac
  [ -x "$tree/bin/jiuwenswarm-run" ] || die "the JiuwenSwarm build left no $tree/bin/jiuwenswarm-run"
  printf '%s\n' "$tree"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    --normalize-mode) jiuwenswarm_normalize_mode ;;
    --release-kind) jiuwenswarm_release_kind "${2:-}" ;;
    --validate)
      jiuwenswarm_git_inputs
      printf '%s %s %s\n' "$JW_URL" "$JW_KIND" "$JW_REF"
      ;;
    --resolve-sha) jiuwenswarm_resolve_sha ;;
    *)
      echo "usage: jiuwenswarm_input.sh --normalize-mode | --release-kind FILE | --validate | --resolve-sha" >&2
      exit 2
      ;;
  esac
fi
