#!/usr/bin/env bash
# The harness under test ($HARNESS: icode or jiuwenswarm), prepared once per
# input and platform and kept in this worker's harness store (harness_store.py).
# Sourced after _common.sh by tasks/harness.sh and tools/prepare_harness.sh.
#
#   harness_request_identity  what this build asks for (a commit, a release
#                             sha256), known before anything is built
#   harness_prepare_local     build it in WORKDIR (icode_prepare.sh, jiuwenswarm_input.sh)
#   harness_pack / harness_put  a bundle, and its entry in the store
#   harness_use_entry ENTRY SOURCE   make a stored tree this build's harness
#   harness_use_workspace TREE       the same for a tree that is not stored
#
# Every harness records its identity in WORKDIR/icode_git.json or
# icode_release.json, the files the report has always read for the harness
# under test, and harness.json names the harness and the bundle.

# shellcheck source=./icode_prepare.sh
source "$PIPELINE_LIB/icode_prepare.sh"
# shellcheck source=./jiuwenswarm_input.sh
source "$PIPELINE_LIB/jiuwenswarm_input.sh"

HARNESS_ICODE_RECIPE=icode-v1

harness_store_root() {
  printf '%s\n' "${MAC_K3D_HARNESS_STORE:-$MAC_K3D_SHARE/harness-store}"
}

harness_store() {
  python3 "$PIPELINE_LIB/harness_store.py" "$@"
}

harness_mode() {
  case "$HARNESS" in
    icode) icode_normalize_mode ;;
    jiuwenswarm) jiuwenswarm_normalize_mode ;;
  esac
}

# The scripts that build a harness's tree; editing one changes the store key.
harness_code_files() {
  case "$HARNESS" in
    icode) printf '%s\n' harness_git.sh icode_input.sh icode_prepare.sh icode_sanitize.py ;;
    jiuwenswarm) printf '%s\n' harness_git.sh jiuwenswarm_input.sh jiuwenswarm_build.sh ;;
  esac
}

harness_code_sha() {
  # shellcheck disable=SC2046
  python3 - "$PIPELINE_LIB" $(harness_code_files) <<'PY'
import hashlib, sys
from pathlib import Path

lib, names = Path(sys.argv[1]), sys.argv[2:]
rows = [f"{hashlib.sha256((lib / name).read_bytes()).hexdigest()}  {name}" for name in names]
print(hashlib.sha256(("\n".join(rows) + "\n").encode()).hexdigest()[:16])
PY
}

# How a harness is built: part of the store key, so a change here builds a new tree.
harness_recipe() {
  local build=host
  case "$HARNESS" in
    icode)
      if hgit_container_build_needed; then
        build="$(hgit_linux_image)"
      fi
      printf '%s code=%s build=%s\n' "$HARNESS_ICODE_RECIPE" "$(harness_code_sha)" "$build"
      ;;
    jiuwenswarm) printf '%s code=%s\n' "$(jiuwenswarm_recipe)" "$(harness_code_sha)" ;;
  esac
}

# JiuwenSwarm runs on DeepSWE only, for now.
harness_check_supported() {
  harness_check_name
  if [ "$HARNESS" = jiuwenswarm ] && [ "${BENCHMARK:-deepswe}" != deepswe ]; then
    die "HARNESS=jiuwenswarm runs on BENCHMARK=deepswe only (got ${BENCHMARK})"
  fi
}

# The compact identity this build asks for, before anything is built: the
# commit for git, the release file's sha256. Fails when only preparing can tell
# (an iCode release drop, a test fetch dir).
harness_request_identity() {
  local sha file kind
  case "$HARNESS:$(harness_mode)" in
    icode:git)
      [ -z "${MAC_K3D_ICODE_FETCH_DIR:-}" ] || return 1
      sha="$(printf '%s' "${ICODE_GIT_SHA:-}" | tr 'A-Z' 'a-z')"
      if [ "${#sha}" != 40 ]; then
        icode_validate_git_url "${ICODE_GIT_URL:-}"
        icode_validate_git_ref "${ICODE_GIT_REF:-main}"
        kind="$(icode_normalize_ref_kind "${ICODE_GIT_REF_KIND:-auto}" "${ICODE_GIT_REF:-main}")"
        icode_validate_ref_for_kind "$kind" "${ICODE_GIT_REF:-main}"
        hgit_begin_auth "$ICODE_GIT_URL"
        if [ -n "$sha" ]; then
          sha="$(hgit_resolve_sha "$ICODE_GIT_URL" "$sha" commit)"
        else
          sha="$(hgit_resolve_sha "$ICODE_GIT_URL" "${ICODE_GIT_REF:-main}" "$kind")"
        fi
        hgit_end_auth
      fi
      [ -n "$sha" ] || return 1
      printf '{"mode":"git","sha":"%s"}\n' "$sha"
      ;;
    jiuwenswarm:git)
      sha="$(jiuwenswarm_resolve_sha)"
      printf '{"mode":"git","sha":"%s"}\n' "$sha"
      ;;
    jiuwenswarm:release)
      file="$(jiuwenswarm_release_file)"
      printf '{"mode":"release","sha256":"%s"}\n' "$(hgit_sha256 "$file")"
      ;;
    *) return 1 ;;
  esac
}

harness_key() {
  local identity="$1" platform="$2" tmp key
  tmp="$(mktemp)"
  printf '%s\n' "$identity" >"$tmp"
  key="$(harness_store key --harness "$HARNESS" --identity "$tmp" --platform "$platform" --recipe "$(harness_recipe)")"
  rm -f "$tmp"
  printf '%s\n' "$key"
}

# Build the harness for this build's input in WORKDIR. Prints the tree.
harness_prepare_local() {
  case "$HARNESS" in
    icode) icode_prepare_workspace ;;
    jiuwenswarm) jiuwenswarm_prepare_workspace ;;
  esac
}

# bundle.json for TREE, prepared by this build: OUT TREE PLATFORM KEY_IDENTITY.
harness_write_meta() {
  local out="$1" tree="$2" platform="$3" key_identity="$4"
  python3 - "$out" "$HARNESS" "$(harness_mode)" "$platform" "$(harness_recipe)" "$key_identity" \
    "$WORKDIR/icode_git.json" "$WORKDIR/icode_release.json" "$tree" <<'PY'
import json, os, sys, time
from pathlib import Path

out, harness, mode, platform, recipe, key_identity, git, release, tree = sys.argv[1:10]

def load(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None

identity = {"mode": mode}
if mode == "git" and load(git):
    identity["git"] = load(git)
elif load(release):
    identity["release"] = load(release)
root = Path(tree)
manifest = load(root / ".venv" / "SANITIZED_MANIFEST.json") or {}
runtime = load(root / "runtime.json") or {}
try:
    freeze = [ln for ln in (root / "freeze.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
except OSError:
    freeze = None
doc = {
    "harness": harness,
    "identity": identity,
    "key_identity": json.loads(key_identity) if key_identity else None,
    "platform": platform,
    "recipe": recipe,
    "runtime_sha256": manifest.get("runtime_sha256"),
    "runtime": runtime or None,
    "freeze": freeze,
    "built_by": f"{os.environ.get('JOB_NAME') or 'local'}#{os.environ.get('BUILD_NUMBER') or '0'}",
    "built_on": os.environ.get("NODE_NAME") or os.uname().nodename,
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
Path(out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
PY
}

# Pack TREE with META into OUT; prints the bundle's sha256.
harness_pack() {
  harness_store pack --tree "$1" --meta "$2" --out "$3"
}

# Store BUNDLE (checked against EXPECT when given), indexed by KEY; KEEP=1 keeps
# the tarball in the entry. Prints the entry.
harness_put() {
  local bundle="$1" expect="${2:-}" key="${3:-}" keep="${4:-0}"
  local -a args=(put --store "$(harness_store_root)" --harness "$HARNESS" --bundle "$bundle")
  [ -z "$expect" ] || args+=(--expect-sha "$expect")
  [ -z "$key" ] || args+=(--key "$key")
  [ "$keep" != 1 ] || args+=(--keep-bundle)
  harness_store "${args[@]}" || die "could not store the $HARNESS bundle $bundle (see above)"
}

harness_bundle_platform() {
  harness_store meta "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("platform") or "")'
}

# The compact identity of what prepare built, from the identity files.
harness_built_identity() {
  python3 - "$WORKDIR" "$(harness_mode)" <<'PY'
import json, sys
from pathlib import Path

work, mode = Path(sys.argv[1]), sys.argv[2]

def load(name):
    try:
        doc = json.loads((work / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}

if mode == "git":
    value = str(load("icode_git.json").get("sha") or "").lower()
    doc = {"mode": "git", "sha": value}
else:
    value = str(load("icode_release.json").get("sha256") or "").lower()
    doc = {"mode": "release", "sha256": value}
if not value:
    raise SystemExit(1)
print(json.dumps(doc, separators=(",", ":")))
PY
}

# The platform a bundle is for. An iCode release keeps every drop of the
# upload, and each build picks its own by ELF header.
harness_bundle_platform_for() {
  if [ "$HARNESS:$(harness_mode)" = icode:release ]; then
    printf '%s\n' any
  else
    printf '%s\n' "$1"
  fi
}

harness_platform_fits() {
  [ "$1" = any ] || [ "$1" = "$2" ]
}

# Find or make this build's harness for PLATFORM. Sets HARNESS_ENTRY (empty
# when the tree is not stored), HARNESS_TREE and HARNESS_SOURCE:
#   dispatcher  the bundle HARNESS_BUNDLE_SHA256 names, on this worker or copied
#   upload      a mac-k3d bundle uploaded as the JiuwenSwarm release
#   store       prepared by an earlier build on this worker from the same input
#   worker      prepared by this build, then stored
#   workspace   prepared by this build and not stored (an iCode release drop
#               unpacks in seconds; a test fetch dir)
# KEEP=1 (the dispatcher) always stores, and keeps the tarball to hand on.
harness_resolve() {
  local platform="$1" keep="${2:-0}" want file bp kind identity key="" tree meta bundle sha
  HARNESS_ENTRY="" HARNESS_TREE="" HARNESS_SOURCE=""
  want="$(printf '%s' "${HARNESS_BUNDLE_SHA256:-}" | tr 'A-Z' 'a-z')"
  if [ -n "$want" ]; then
    [[ "$want" =~ ^[0-9a-f]{64}$ ]] || die "HARNESS_BUNDLE_SHA256 must be a sha256 (64 hex chars), not '${want}'"
    if HARNESS_ENTRY="$(harness_get sha "$want")"; then
      echo "harness store: the dispatcher's bundle ${want:0:16} is already on this worker; nothing copied" >&2
    else
      file="${HARNESS_BUNDLE_FILE:-}"
      [ -n "$file" ] && [ -s "$file" ] \
        || die "HARNESS_BUNDLE_SHA256=${want} is not in this worker's harness store and no bundle was copied (HARNESS_BUNDLE_FILE=${file:-<unset>})"
      bp="$(harness_bundle_platform "$file")" || die "HARNESS_BUNDLE_FILE $file is not a mac-k3d harness bundle"
      if harness_platform_fits "$bp" "$platform"; then
        HARNESS_ENTRY="$(harness_put "$file" "$want" "" "$keep")"
        echo "harness store: stored the dispatcher's bundle ${want:0:16}" >&2
      fi
    fi
    if [ -n "$HARNESS_ENTRY" ]; then
      bp="$(harness_entry_platform "$HARNESS_ENTRY")"
      if harness_platform_fits "$bp" "$platform"; then
        HARNESS_SOURCE=dispatcher
        HARNESS_TREE="$HARNESS_ENTRY/tree"
        return 0
      fi
      HARNESS_ENTRY=""
    fi
    echo "harness: the dispatcher's bundle is for ${bp:-another platform}, this worker's task containers are ${platform}; preparing this worker's own from the same input" >&2
  fi

  if [ "$HARNESS" = jiuwenswarm ] && [ "$(harness_mode)" = release ]; then
    file="$(jiuwenswarm_release_file)"
    kind="$(jiuwenswarm_release_kind "$file")" || die "JIUWENSWARM_RELEASE is not a JiuwenSwarm release (see above)"
    if [ "$kind" = bundle ]; then
      bp="$(harness_bundle_platform "$file")"
      harness_platform_fits "$bp" "$platform" \
        || die "the uploaded JiuwenSwarm bundle was built for ${bp}, but this build's task containers are ${platform}"
      jiuwenswarm_record_release "$file" "$(jiuwenswarm_release_name "$file")" bundle
      HARNESS_ENTRY="$(harness_put "$file" "" "" "$keep")"
      HARNESS_SOURCE=upload
      HARNESS_TREE="$HARNESS_ENTRY/tree"
      return 0
    fi
  fi

  if identity="$(harness_request_identity)"; then
    key="$(harness_key "$identity" "$(harness_bundle_platform_for "$platform")")"
    if HARNESS_ENTRY="$(harness_get key "$key")"; then
      if [ "$keep" != 1 ] || [ -s "$HARNESS_ENTRY/bundle.tar.gz" ]; then
        echo "harness store: $(harness_display) ${identity} is already prepared on this worker (bundle $(harness_entry_sha "$HARNESS_ENTRY" | cut -c1-16)); nothing built" >&2
        HARNESS_SOURCE=store
        HARNESS_TREE="$HARNESS_ENTRY/tree"
        return 0
      fi
      echo "harness store: the stored tree for ${identity} has no tarball to hand on; preparing again" >&2
      HARNESS_ENTRY=""
    fi
  fi

  tree="$(harness_prepare_local)"
  [ -n "$tree" ] && [ -d "$tree" ] || die "$(harness_display) prepare left no tree"
  key=""
  if identity="$(harness_built_identity)"; then
    key="$(harness_key "$identity" "$(harness_bundle_platform_for "$platform")")"
  else
    identity=""
  fi
  if [ -z "$key" ] && [ "$keep" != 1 ] || [ "$HARNESS:$(harness_mode):$keep" = icode:release:0 ]; then
    HARNESS_TREE="$tree"
    HARNESS_SOURCE=workspace
    return 0
  fi
  meta="$WORKDIR/.harness-bundle.json"
  bundle="$WORKDIR/.harness-bundle.tar.gz"
  harness_write_meta "$meta" "$tree" "$(harness_bundle_platform_for "$platform")" "$identity"
  echo "harness: packing $tree" >&2
  sha="$(harness_pack "$tree" "$meta" "$bundle")" || die "could not pack $tree"
  HARNESS_ENTRY="$(harness_put "$bundle" "$sha" "$key" "$keep")"
  rm -f "$bundle" "$meta"
  echo "harness store: stored $(harness_display) bundle ${sha:0:16} ($(du -sh "$HARNESS_ENTRY/tree" 2>/dev/null | cut -f1))" >&2
  case "$tree" in
    "$WORKDIR"/*) hgit_force_rm "$tree" ;;
  esac
  HARNESS_SOURCE=worker
  HARNESS_TREE="$HARNESS_ENTRY/tree"
}

# The resolved harness as this build's: the identity files, harness.json, the host root.
harness_use() {
  local platform="$1"
  if [ -n "$HARNESS_ENTRY" ]; then
    harness_use_entry "$HARNESS_ENTRY" "$HARNESS_SOURCE" "$platform"
  else
    harness_use_workspace "$HARNESS_TREE" "$platform"
  fi
}

# The stored entry for SHA or KEY, its tree checked; empty and rc 1 on a miss.
harness_get() {
  local how="$1" value="$2" rc=0 entry
  entry="$(harness_store get --store "$(harness_store_root)" --harness "$HARNESS" "--$how" "$value")" || rc=$?
  case "$rc" in
    0) printf '%s\n' "$entry" ;;
    1) return 1 ;;
    3) echo "harness store: a stored $HARNESS tree was damaged and removed; preparing again" >&2; return 1 ;;
    *) die "harness store lookup failed (exit $rc)" ;;
  esac
}

harness_entry_platform() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("platform") or "")' "$1/bundle.json"
}

harness_entry_sha() {
  basename "$1"
}

# The host root and launcher inside a tree, as the trial mounts it.
harness_tree_root() {
  local tree="$1" bin
  case "$HARNESS:$(harness_mode)" in
    icode:git) HARNESS_ROOT="$tree"; HARNESS_BIN="$tree/icode" ;;
    icode:release)
      bin="$(icode_pick_release_bin "$tree")" || exit 1
      HARNESS_ROOT="$(cd "$(dirname "$bin")" && pwd)"
      HARNESS_BIN="$bin"
      ;;
    jiuwenswarm:*) HARNESS_ROOT="$tree"; HARNESS_BIN="$tree/bin/jiuwenswarm-run" ;;
  esac
  [ -e "$HARNESS_BIN" ] || die "$HARNESS tree $tree has no $HARNESS_BIN"
}

# The identity files from a stored bundle. A git request keeps its own url,
# kind and ref; the commit and subject are the bundle's.
harness_restore_identity() {
  local meta="$1"
  python3 - "$meta" "$WORKDIR" "$(harness_mode)" "$HARNESS" <<'PY'
import json, os, sys
from pathlib import Path

meta, workdir, mode, harness = sys.argv[1:5]
doc = json.loads(Path(meta).read_text(encoding="utf-8"))
identity = doc.get("identity") or {}
work = Path(workdir)
prefix = "ICODE" if harness == "icode" else "JIUWENSWARM"
git = identity.get("git") if isinstance(identity.get("git"), dict) else None
release = identity.get("release") if isinstance(identity.get("release"), dict) else None
if git:
    url = os.environ.get(f"{prefix}_GIT_URL") or git.get("url") or ""
    ref = os.environ.get(f"{prefix}_GIT_REF") or git.get("ref") or ""
    kind = os.environ.get(f"{prefix}_GIT_REF_KIND") or git.get("kind") or ""
    if mode != "git":
        url, ref, kind = git.get("url") or "", git.get("ref") or "", git.get("kind") or ""
    out = {"url": url, "kind": kind, "ref": ref, "sha": git.get("sha") or "", "subject": git.get("subject") or ""}
    (work / "icode_git.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
elif release:
    (work / "icode_release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
PY
}

# harness.json and the host-root files every later step reads, for the
# HARNESS_ROOT and HARNESS_BIN in TREE.
harness_record() {
  local tree="$1" source="$2" bundle_sha="${3:-}" platform="$4"
  printf '%s\n' "$HARNESS_ROOT" >"$WORKDIR/harness_host_root.txt"
  printf '%s\n' "$HARNESS_ROOT" >"$WORKDIR/icode_host_root.txt"
  printf '%s\n' "$HARNESS_BIN" >"$WORKDIR/icode_bin_path.txt"
  python3 - "$WORKDIR/harness.json" "$HARNESS" "$(harness_mode)" "$platform" "$source" "$bundle_sha" \
    "$tree" "$HARNESS_ROOT" "$(harness_recipe)" "$(dirname "$tree")/bundle.json" "$(dirname "$tree")/entry.json" <<'PY'
import json, sys
from pathlib import Path

out, name, mode, platform, source, sha, tree, root, recipe, meta, entry = sys.argv[1:12]

def load(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}

bundle = load(meta) if sha else {}
stored = load(entry) if sha else {}
doc = {
    "name": name,
    "mode": mode,
    "platform": platform,
    "source": source,
    "bundle_sha256": sha or None,
    "tree_sha256": stored.get("tree_sha256"),
    "bundle_platform": bundle.get("platform"),
    "recipe": bundle.get("recipe") or recipe,
    "built_by": bundle.get("built_by"),
    "built_on": bundle.get("built_on"),
    "identity": bundle.get("identity"),
    "tree": tree,
    "host_root": root,
}
Path(out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
PY
}

# A stored ENTRY as this build's harness. SOURCE upload keeps the identity the
# upload recorded (the bundle file itself).
harness_use_entry() {
  local entry="$1" source="$2" platform="$3"
  [ "$source" = upload ] || harness_restore_identity "$entry/bundle.json"
  harness_tree_root "$entry/tree"
  harness_record "$entry/tree" "$source" "$(harness_entry_sha "$entry")" "$platform"
}

# A tree prepared in WORKDIR and not stored; iCode keeps the root and binary
# its prepare chose.
harness_use_workspace() {
  local tree="$1" platform="$2"
  if [ "$HARNESS" = icode ] && [ -s "$WORKDIR/icode_host_root.txt" ] && [ -s "$WORKDIR/icode_bin_path.txt" ]; then
    HARNESS_ROOT="$(cat "$WORKDIR/icode_host_root.txt")"
    HARNESS_BIN="$(cat "$WORKDIR/icode_bin_path.txt")"
  else
    harness_tree_root "$tree"
  fi
  harness_record "$tree" workspace "" "$platform"
}

# The identity this build records must be the one it was told to evaluate:
# ICODE_EXPECT_SHA, the dispatcher's ICODE_GIT_SHA / JIUWENSWARM_GIT_SHA pin
# (a prefix for a short one), ICODE_RELEASE_SHA256 / JIUWENSWARM_RELEASE_SHA256.
harness_assert_identity() {
  local pin release_pin prefix recorded
  if [ "$HARNESS" = icode ]; then
    HOST_ROOT="$HARNESS_ROOT" icode_assert_recorded_sha
    prefix=ICODE pin="${ICODE_GIT_SHA:-}" release_pin="${ICODE_RELEASE_SHA256:-}"
  else
    prefix=JIUWENSWARM pin="${JIUWENSWARM_GIT_SHA:-}" release_pin="${JIUWENSWARM_RELEASE_SHA256:-}"
  fi
  pin="$(printf '%s' "$pin" | tr 'A-Z' 'a-z')"
  if [ "$(harness_mode)" = git ] && [ -n "$pin" ]; then
    recorded="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1], encoding="utf-8")).get("sha") or "").lower())' "$WORKDIR/icode_git.json" 2>/dev/null || true)"
    case "$recorded" in
      "$pin"*) ;;
      *) die "this build was pinned to $(harness_display) ${pin} but its harness records ${recorded:-no commit}" ;;
    esac
  fi
  if [ "$(harness_mode)" = release ] && [ -n "$release_pin" ]; then
    recorded="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1], encoding="utf-8")).get("sha256") or "").lower())' "$WORKDIR/icode_release.json" 2>/dev/null || true)"
    release_pin="$(printf '%s' "$release_pin" | tr 'A-Z' 'a-z')"
    [ "$recorded" = "$release_pin" ] || die "${prefix}_RELEASE_SHA256=${release_pin} but this build's release is ${recorded:-unrecorded}"
  fi
}

# One line for the dispatcher to hand on: `git <sha>` or `release <sha256>`.
harness_pin() {
  python3 - "$WORKDIR" <<'PY'
import json, sys
from pathlib import Path

def load(name):
    try:
        doc = json.loads((Path(sys.argv[1]) / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}

git, release = load("icode_git.json"), load("icode_release.json")
if git.get("sha"):
    print(f"git {git['sha'].lower()}")
elif release.get("sha256"):
    print(f"release {release['sha256'].lower()}")
else:
    raise SystemExit("the harness records neither a commit nor a release sha256")
PY
}

# "iCode under test: git pr 2 -> <sha> (subject)" and the bundle it came from.
harness_print_identity() {
  python3 - "$WORKDIR" "$HARNESS" <<'PY'
import json, sys
from pathlib import Path

work, name = Path(sys.argv[1]), sys.argv[2]
display = {"icode": "iCode", "jiuwenswarm": "JiuwenSwarm"}.get(name, name)

def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

git, release, harness = load(work / "icode_git.json"), load(work / "icode_release.json"), load(work / "harness.json") or {}
if isinstance(git, dict) and git.get("sha"):
    print(f"OK {display} git kind={git.get('kind')} ref={git.get('ref')} sha={git.get('sha')}")
    print(f"{display} under test: git {git.get('kind')} {git.get('ref')} -> {git.get('sha')} ({git.get('subject') or ''})")
elif isinstance(release, dict) and release.get("sha256"):
    fmt = f" ({release['format']})" if release.get("format") else ""
    print(f"{display} under test: release {release.get('filename')}{fmt} sha256 {release.get('sha256')}")
sha = harness.get("bundle_sha256")
if sha:
    print(f"harness bundle: {sha} from {harness.get('source')} (built by {harness.get('built_by') or '?'} on {harness.get('built_on') or '?'}, {harness.get('bundle_platform')})")
else:
    print(f"harness: {harness.get('host_root')} (prepared in this workspace, not stored)")
PY
}
