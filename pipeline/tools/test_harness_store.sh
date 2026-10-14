#!/usr/bin/env bash
# Scenario tests for the harness store (tasks/harness.sh, tools/prepare_harness.sh),
# with no network: git fetches local repos through an insteadOf rewrite of
# https://gitcode.com/, a fake uv makes iCode's venv, and a fake docker answers
# the emulation check, builds the JiuwenSwarm runtime and runs anything else here.
#
#   - a direct build prepares and stores; the same input again reuses it, no build
#   - the dispatcher prepares once; a shard copies and stores its bundle, the
#     next shard on that worker reuses it with no copy and no build
#   - a tree changed on disk is never used: removed, then copied again
#   - a wrong sha256, or a bundle for another commit than the pin, is refused
#   - a shard of another platform prepares its own from the same input
#   - JiuwenSwarm: git PR 2, a wheel, a wheelhouse, a bundle upload, an unknown
#     file, and a benchmark other than DeepSWE
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGES="$ROOT/pipeline/stages"
LIB="$ROOT/pipeline/lib"
TMP="$(mktemp -d)"
cleanup() {
  chmod -R u+w "$TMP" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

ok() {
  echo "OK $*"
}

case "$(uname -m)" in
  aarch64 | arm64) NATIVE=linux/arm64 OTHER=linux/amd64 ;;
  *) NATIVE=linux/amd64 OTHER=linux/arm64 ;;
esac

FAKE="$TMP/bin"
mkdir -p "$FAKE" "$TMP/home"
UV_LOG="$TMP/uv.log"
DOCKER_LOG="$TMP/docker.log"
BUILD_LOG="$TMP/jiuwenswarm-builds.log"
: >"$UV_LOG"
: >"$DOCKER_LOG"
: >"$BUILD_LOG"

cat >"$FAKE/uv" <<'EOF'
#!/bin/sh
# uv sync: the venv a real `uv sync --no-editable` of iCode leaves.
echo "$*" >>"$FAKE_UV_LOG"
[ "$1" = sync ] || exit 0
py="$FAKE_UV_PYTHON"
mkdir -p "$py/bin" "$py/lib/python3.13" .venv/bin .venv/lib/python3.13/site-packages
if [ ! -x "$py/bin/python3.13" ]; then
  printf '#!/bin/sh\nexec python3 "$@"\n' >"$py/bin/python3.13"
  chmod +x "$py/bin/python3.13"
fi
ln -sf "$py/bin/python3.13" .venv/bin/python
printf '#!%s/.venv/bin/python\nprint("usage: icode [--help]")\n' "$PWD" >.venv/bin/icode
chmod +x .venv/bin/icode
echo "x = 1" >.venv/lib/python3.13/site-packages/icode_marker.py
EOF
chmod +x "$FAKE/uv"

cat >"$FAKE/docker" <<'EOF'
#!/usr/bin/env python3
import json, os, subprocess, sys

args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\n")
if not args or args[0] != "run":
    sys.exit(1)
mounts, env, workdir, plat, i = {}, {}, None, "", 1
while i < len(args):
    a = args[i]
    if a == "--rm":
        i += 1
    elif a in ("--platform", "--user"):
        plat = args[i + 1] if a == "--platform" else plat
        i += 2
    elif a == "-v":
        src, dst = args[i + 1].split(":")[:2]
        mounts[dst] = src
        i += 2
    elif a == "-e":
        key, _, value = args[i + 1].partition("=")
        env[key] = value if "=" in args[i + 1] else os.environ.get(key, "")
        i += 2
    elif a == "-w":
        workdir = args[i + 1]
        i += 2
    else:
        break
cmd = args[i + 1:]
if cmd[:2] == ["uname", "-m"]:
    print("aarch64" if plat.endswith("arm64") else "x86_64")
    sys.exit(0)
script = next((c for c in cmd if c.endswith("jiuwenswarm_build.sh")), "")
if script:
    mode = cmd[cmd.index(script) + 1]
    root = mounts["/opt/jiuwenswarm-host"]
    if mode == "git":
        assert os.path.isfile(os.path.join(mounts["/mac-k3d-src"], "uv.lock"))
    else:
        wheel = cmd[-1].replace("/mac-k3d-in", mounts["/mac-k3d-in"], 1)
        assert os.path.isfile(wheel), wheel
    with open(os.environ["FAKE_BUILD_LOG"], "a", encoding="utf-8") as log:
        log.write(f"{mode} {plat} {env.get('JIUWENSWARM_PYTHON_PLATFORM', '')}\n")
    for d in ("venv/bin", "bin", "python/cpython-3.11/bin"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    files = {
        "venv/bin/jiuwenswarm-process": "#!/bin/sh\necho 'usage: jiuwenswarm-process --run-json FILE'\n",
        "bin/jiuwenswarm-run": "#!/bin/sh\nexec /opt/jiuwenswarm-host/venv/bin/jiuwenswarm-process \"$@\"\n",
        "bin/jiuwenswarm-python": "#!/bin/sh\nexec /opt/jiuwenswarm-host/venv/bin/python \"$@\"\n",
    }
    for rel, body in files.items():
        path = os.path.join(root, rel)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o755)
    os.symlink("/opt/jiuwenswarm-host/python/cpython-3.11/bin/python3.11", os.path.join(root, "venv/bin/python"))
    with open(os.path.join(root, "freeze.txt"), "w", encoding="utf-8") as fh:
        fh.write("workswarm==0.2.5b1\n")
    with open(os.path.join(root, "runtime.json"), "w", encoding="utf-8") as fh:
        json.dump({"package": "workswarm", "version": "0.2.5b1", "python": "3.11.14",
                   "python_platform": env.get("JIUWENSWARM_PYTHON_PLATFORM", ""), "machine": "x86_64"}, fh)
    print("OK JiuwenSwarm runtime 0.2.5b1 starts in /opt/jiuwenswarm-host (fake)")
    sys.exit(0)
full = dict(os.environ)
full.update({k: v for k, v in env.items() if k not in ("HOME",)})
sys.exit(subprocess.call(cmd, cwd=workdir or None, env=full))
EOF
chmod +x "$FAKE/docker"

# Two repos on "gitcode.com": main, and pull request 2 one commit ahead.
REMOTE="$TMP/remote"
make_repo() {
  local dir="$1" lock="$2"
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.email t@example.com
  git -C "$dir" config user.name t
  printf '.venv/\n' >"$dir/.gitignore"
  printf '[project]\nname = "fixture"\nversion = "0.1"\n' >"$dir/pyproject.toml"
  [ -z "$lock" ] || printf 'version = 1\n' >"$dir/uv.lock"
  git -C "$dir" add -A
  git -C "$dir" commit -qm "main commit"
  git -C "$dir" checkout -qb pr2
  echo "pr" >"$dir/PR.txt"
  git -C "$dir" add PR.txt
  git -C "$dir" commit -qm "pull request 2"
  git -C "$dir" update-ref refs/merge-requests/2/head HEAD
  git -C "$dir" checkout -q main
}
make_repo "$REMOTE/michaelling/jiuwenicode" ""
make_repo "$REMOTE/michaelling/jiuwenswarm" lock
ICODE_MAIN="$(git -C "$REMOTE/michaelling/jiuwenicode" rev-parse main)"
ICODE_PR="$(git -C "$REMOTE/michaelling/jiuwenicode" rev-parse refs/merge-requests/2/head)"
JW_PR="$(git -C "$REMOTE/michaelling/jiuwenswarm" rev-parse refs/merge-requests/2/head)"

ENV_FILE="$TMP/empty.env"
: >"$ENV_FILE"
chmod 600 "$ENV_FILE"

# run WORKDIR SHARE [VAR=value ...] SCRIPT: one build on the worker that owns SHARE.
run() {
  local work="$1" share="$2"
  shift 2
  mkdir -p "$work"
  env -u GITCODE_TOKEN -u GITHUB_TOKEN -u MAC_K3D_GITCODE_PAT -u MAC_K3D_GITHUB_PAT -u DEEPSEEK_API_KEY \
    -u HARNESS_BUNDLE_SHA256 -u HARNESS_BUNDLE_FILE -u ICODE_GIT_SHA -u JIUWENSWARM_GIT_SHA \
    HOME="$TMP/home" \
    PATH="$FAKE:$PATH" \
    FAKE_UV_LOG="$UV_LOG" FAKE_UV_PYTHON="$TMP/uv-python" \
    FAKE_DOCKER_LOG="$DOCKER_LOG" FAKE_BUILD_LOG="$BUILD_LOG" \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0="url.$REMOTE/.insteadOf" GIT_CONFIG_VALUE_0="https://gitcode.com/" \
    MAC_K3D_ICODE_GIT_ALLOW_FILE=1 \
    MAC_K3D_ENV_FILE="$ENV_FILE" \
    MAC_K3D_ICODE_PATHS="$TMP/no-icode-paths.yaml" \
    MAC_K3D_EVAL_WORKDIR="$work" \
    MAC_K3D_SHARE="$share" \
    BENCHMARK=deepswe \
    DEEPSWE_DOCKER_PLATFORM="$NATIVE" \
    HARNESS=icode \
    ICODE_MODE=git \
    ICODE_GIT_URL=https://gitcode.com/michaelling/jiuwenicode \
    ICODE_GIT_REF=2 \
    ICODE_GIT_REF_KIND=pr \
    ICODE_RELEASE="" \
    "$@"
}

field() {
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [d := d[k] for k in sys.argv[2].split(".")]; print(d)' "$1" "$2"
}

uv_syncs() {
  grep -c '^sync' "$UV_LOG" || true
}

builds() {
  wc -l <"$BUILD_LOG" | tr -d ' '
}

# 1. A direct build prepares, packs and stores; the tree is read-only.
S1="$TMP/share-worker1"
out="$(run "$TMP/w1" "$S1" bash "$STAGES/tasks/harness.sh" 2>&1)" || fail "first build: $out"
[ "$(uv_syncs)" = 1 ] || fail "expected one uv sync, got $(uv_syncs)"
[ "$(field "$TMP/w1/harness.json" source)" = worker ] || fail "first build source $(field "$TMP/w1/harness.json" source)"
ROOT1="$(cat "$TMP/w1/harness_host_root.txt")"
case "$ROOT1" in "$S1"/harness-store/icode/*/tree) ;; *) fail "host root not in the store: $ROOT1" ;; esac
[ "$(cat "$TMP/w1/icode_host_root.txt")" = "$ROOT1" ] || fail "icode_host_root.txt differs from harness_host_root.txt"
[ "$(cat "$TMP/w1/icode_bin_path.txt")" = "$ROOT1/icode" ] || fail "bin path $(cat "$TMP/w1/icode_bin_path.txt")"
[ ! -w "$ROOT1/.venv/bin/icode" ] || fail "stored tree is writable"
[ "$(field "$TMP/w1/icode_git.json" sha)" = "$ICODE_PR" ] || fail "recorded sha is not PR 2's"
grep -q "iCode under test: git pr 2 -> $ICODE_PR" <<<"$out" || fail "no identity line: $out"
grep -q '"runtime_sha256": "[0-9a-f]\{64\}"' "$(dirname "$ROOT1")/bundle.json" || fail "bundle.json has no runtime_sha256"
[ ! -e "$TMP/w1/icode-src" ] || fail "the workspace copy was kept after storing"
ok "a direct build prepares, stores read-only and records PR 2's commit"

# 2. The same input on the same worker: no build.
out="$(run "$TMP/w2" "$S1" bash "$STAGES/tasks/harness.sh" 2>&1)" || fail "second build: $out"
[ "$(uv_syncs)" = 1 ] || fail "the second build ran uv sync"
[ "$(field "$TMP/w2/harness.json" source)" = store ] || fail "second build source $(field "$TMP/w2/harness.json" source)"
[ "$(cat "$TMP/w2/harness_host_root.txt")" = "$ROOT1" ] || fail "second build mounts another tree"
grep -q 'already prepared on this worker' <<<"$out" || fail "no store-hit line"
ok "the same input again reuses the stored tree with no build"

# 3. Another commit is another entry, never the stored one.
out="$(run "$TMP/w2b" "$S1" ICODE_GIT_REF=main ICODE_GIT_REF_KIND=branch bash "$STAGES/tasks/harness.sh" 2>&1)" || fail "main build: $out"
[ "$(uv_syncs)" = 2 ] || fail "main was not built"
[ "$(field "$TMP/w2b/icode_git.json" sha)" = "$ICODE_MAIN" ] || fail "main build records $(field "$TMP/w2b/icode_git.json" sha)"
[ "$(cat "$TMP/w2b/harness_host_root.txt")" != "$ROOT1" ] || fail "main reused PR 2's tree"
ok "another commit builds its own entry"

# 4. The dispatcher prepares once and leaves the bundle to archive.
SD="$TMP/share-dispatcher"
out="$(run "$TMP/wd" "$SD" HARNESS_BUNDLE_PLATFORM="$NATIVE" bash "$ROOT/pipeline/tools/prepare_harness.sh" 2>&1)" || fail "dispatcher: $out"
[ "$(uv_syncs)" = 3 ] || fail "dispatcher did not build"
BUNDLE="$TMP/wd/harness/bundle.tar.gz"
SHA="$(cat "$TMP/wd/harness/bundle.sha256")"
[ -s "$BUNDLE" ] || fail "no bundle.tar.gz to archive"
[ "$(python3 "$LIB/harness_store.py" sha256 "$BUNDLE")" = "$SHA" ] || fail "bundle.sha256 does not match"
[ "$(field "$TMP/wd/harness/bundle.json" platform)" = "$NATIVE" ] || fail "bundle platform"
grep -q "HARNESS_BUNDLE_SHA256=$SHA" <<<"$out" || fail "dispatcher console has no sha"
[ "$(cat "$TMP/wd/harness/pin.txt")" = "git $ICODE_PR" ] || fail "dispatcher pin $(cat "$TMP/wd/harness/pin.txt")"
out="$(run "$TMP/wd2" "$SD" HARNESS_BUNDLE_PLATFORM="$NATIVE" bash "$ROOT/pipeline/tools/prepare_harness.sh" 2>&1)" || fail "dispatcher again: $out"
[ "$(uv_syncs)" = 3 ] || fail "a second dispatcher run rebuilt"
[ "$(cat "$TMP/wd2/harness/bundle.sha256")" = "$SHA" ] || fail "a second dispatcher run made another bundle"
ok "the dispatcher prepares once, and reuses its stored bundle"

# 5. Shard 1 on a new worker copies the bundle and stores it; shard 2 copies nothing.
S2="$TMP/share-worker2"
shard() {
  run "$1" "$S2" HARNESS_BUNDLE_SHA256="$SHA" ICODE_GIT_SHA="$ICODE_PR" "${@:2}" bash "$STAGES/tasks/harness.sh"
}
out="$(shard "$TMP/s1" HARNESS_BUNDLE_FILE="$BUNDLE" 2>&1)" || fail "shard 1: $out"
[ "$(uv_syncs)" = 3 ] || fail "shard 1 built"
[ "$(field "$TMP/s1/harness.json" source)" = dispatcher ] || fail "shard 1 source"
[ "$(field "$TMP/s1/harness.json" bundle_sha256)" = "$SHA" ] || fail "shard 1 bundle sha"
grep -q "stored the dispatcher's bundle" <<<"$out" || fail "shard 1 did not store the copy"
out="$(shard "$TMP/s2" 2>&1)" || fail "shard 2: $out"
[ "$(uv_syncs)" = 3 ] || fail "shard 2 built"
grep -q "already on this worker; nothing copied" <<<"$out" || fail "shard 2 did not reuse: $out"
[ "$(cat "$TMP/s2/harness_host_root.txt")" = "$S2/harness-store/icode/$SHA/tree" ] || fail "shard 2 tree"
[ "$(field "$TMP/s2/icode_git.json" kind)" = pr ] || fail "shard 2 identity kind"
ok "shard 1 stores the dispatcher's bundle; shard 2 reuses it with no copy and no build"

# 6. A tree changed on disk is removed, never mounted; the copy repairs it.
victim="$S2/harness-store/icode/$SHA/tree/.venv/lib/python3.13/site-packages/icode_marker.py"
chmod u+w "$victim" "$(dirname "$victim")"
echo "tampered = True" >>"$victim"
if out="$(shard "$TMP/s3" 2>&1)"; then
  fail "a changed tree was used: $out"
fi
grep -q "changed on disk" <<<"$out" || fail "no tamper message: $out"
grep -q "no bundle was copied" <<<"$out" || fail "no copy message: $out"
out="$(shard "$TMP/s4" HARNESS_BUNDLE_FILE="$BUNDLE" 2>&1)" || fail "repair: $out"
grep -q tampered "$victim" && fail "the changed file came back"
[ "$(uv_syncs)" = 3 ] || fail "the repair built"
ok "a changed tree is removed and copied again"

# 7. A bundle with another sha256 than the dispatcher named, or for another commit than the pin.
WRONG="$(printf '%064d' 0)"
if out="$(run "$TMP/s5" "$TMP/share-worker3" HARNESS_BUNDLE_SHA256="$WRONG" HARNESS_BUNDLE_FILE="$BUNDLE" \
  bash "$STAGES/tasks/harness.sh" 2>&1)"; then
  fail "a bundle with another sha256 was used"
fi
grep -q "not the $WRONG this build needs" <<<"$out" || fail "no sha mismatch message: $out"
if out="$(shard "$TMP/s6" ICODE_GIT_SHA="$ICODE_MAIN" 2>&1)"; then
  fail "a bundle for another commit than the pin was used"
fi
grep -q "pinned to iCode $ICODE_MAIN" <<<"$out" || fail "no pin message: $out"
ok "a wrong sha256 or another commit than the pin is refused"

# 8. A shard of another platform prepares its own from the same input.
out="$(run "$TMP/s7" "$S2" DEEPSWE_DOCKER_PLATFORM="$OTHER" HARNESS_BUNDLE_SHA256="$SHA" ICODE_GIT_SHA="$ICODE_PR" \
  bash "$STAGES/tasks/harness.sh" 2>&1)" || fail "other-platform shard: $out"
[ "$(uv_syncs)" = 4 ] || fail "the other-platform shard did not build"
[ "$(field "$TMP/s7/harness.json" source)" = worker ] || fail "other-platform source"
[ "$(field "$TMP/s7/harness.json" platform)" = "$OTHER" ] || fail "other-platform platform"
[ "$(field "$TMP/s7/icode_git.json" sha)" = "$ICODE_PR" ] || fail "other-platform commit"
grep -q "preparing this worker's own" <<<"$out" || fail "no platform message"
ok "a shard of another platform prepares its own from the pinned commit"

# 9. JiuwenSwarm git: the default input is PR 2 of michaelling/jiuwenswarm.
S3="$TMP/share-worker-jw"
jw() {
  run "$1" "$S3" HARNESS=jiuwenswarm "${@:2}" bash "$STAGES/tasks/harness.sh"
}
out="$(jw "$TMP/j1" 2>&1)" || fail "jiuwenswarm git: $out"
[ "$(builds)" = 1 ] || fail "expected one JiuwenSwarm build, got $(builds)"
grep -q "^git $NATIVE $(case "$NATIVE" in *arm64) echo aarch64 ;; *) echo x86_64 ;; esac)-manylinux_2_28\$" "$BUILD_LOG" \
  || fail "build platform: $(cat "$BUILD_LOG")"
JROOT="$(cat "$TMP/j1/harness_host_root.txt")"
[ -x "$JROOT/bin/jiuwenswarm-run" ] || fail "no jiuwenswarm-run in $JROOT"
[ "$(field "$TMP/j1/icode_git.json" url)" = https://gitcode.com/michaelling/jiuwenswarm ] || fail "jw url"
[ "$(field "$TMP/j1/icode_git.json" kind)" = pr ] || fail "jw kind"
[ "$(field "$TMP/j1/icode_git.json" ref)" = 2 ] || fail "jw ref"
[ "$(field "$TMP/j1/icode_git.json" sha)" = "$JW_PR" ] || fail "jw sha"
[ "$(field "$TMP/j1/harness.json" name)" = jiuwenswarm ] || fail "harness.json name"
grep -q "JiuwenSwarm under test: git pr 2 -> $JW_PR" <<<"$out" || fail "no JiuwenSwarm identity line"
out="$(jw "$TMP/j2" 2>&1)" || fail "jiuwenswarm again: $out"
[ "$(builds)" = 1 ] || fail "the second JiuwenSwarm build rebuilt"
[ "$(field "$TMP/j2/harness.json" source)" = store ] || fail "jw second source"
ok "JiuwenSwarm PR 2 builds once in the Linux image and is reused"

# 10. JiuwenSwarm releases, told apart by content.
REL="$TMP/releases"
mkdir -p "$REL/house"
python3 - "$REL" <<'PY'
import sys, tarfile, zipfile
from pathlib import Path

rel = Path(sys.argv[1])

def wheel(path, dist):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{dist}.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        zf.writestr(f"{dist}.dist-info/METADATA", "Name: x\n")

wheel(rel / "JIUWENSWARM_RELEASE_FILE", "workswarm-0.2.5b1")
wheel(rel / "house" / "workswarm-0.2.5b1-py3-none-any.whl", "workswarm-0.2.5b1")
wheel(rel / "house" / "anyio-4.0.0-py3-none-any.whl", "anyio-4.0.0")
with tarfile.open(rel / "wheelhouse.tar.gz", "w:gz") as tar:
    tar.add(rel / "house", arcname="wheelhouse")
with tarfile.open(rel / "unknown.tar.gz", "w:gz") as tar:
    (rel / "README.md").write_text("hi\n")
    tar.add(rel / "README.md", arcname="README.md")
PY
JWI="$LIB/jiuwenswarm_input.sh"
[ "$(bash "$JWI" --release-kind "$REL/JIUWENSWARM_RELEASE_FILE")" = wheel ] || fail "wheel not detected"
[ "$(bash "$JWI" --release-kind "$REL/wheelhouse.tar.gz")" = wheelhouse ] || fail "wheelhouse not detected"
[ "$(bash "$JWI" --release-kind "$S3/harness-store/jiuwenswarm/$(basename "$(dirname "$JROOT")")/bundle.json" 2>/dev/null || true)" = "" ] \
  || fail "a bare bundle.json passed as a release"
if msg="$(bash "$JWI" --release-kind "$REL/unknown.tar.gz" 2>&1)"; then
  fail "an unknown .tar.gz was accepted"
fi
grep -q "it holds: README.md" <<<"$msg" || fail "unknown release does not list its files: $msg"

out="$(jw "$TMP/j3" JIUWENSWARM_MODE=release JIUWENSWARM_RELEASE="$REL/JIUWENSWARM_RELEASE_FILE" 2>&1)" || fail "wheel: $out"
[ "$(builds)" = 2 ] || fail "the wheel was not installed"
grep -q '^wheel ' "$BUILD_LOG" || fail "no wheel build"
[ "$(field "$TMP/j3/icode_release.json" format)" = wheel ] || fail "wheel format"
[ "$(field "$TMP/j3/icode_release.json" filename)" = JIUWENSWARM_RELEASE_FILE ] || fail "wheel filename"
out="$(jw "$TMP/j4" JIUWENSWARM_MODE=release JIUWENSWARM_RELEASE="$REL/JIUWENSWARM_RELEASE_FILE" 2>&1)" || fail "wheel again: $out"
[ "$(builds)" = 2 ] || fail "the same wheel was installed twice"
[ "$(field "$TMP/j4/harness.json" source)" = store ] || fail "wheel second source"
out="$(jw "$TMP/j5" JIUWENSWARM_MODE=release JIUWENSWARM_RELEASE="$REL/wheelhouse.tar.gz" 2>&1)" || fail "wheelhouse: $out"
grep -q '^wheelhouse ' "$BUILD_LOG" || fail "no wheelhouse build"
[ "$(field "$TMP/j5/icode_release.json" format)" = wheelhouse ] || fail "wheelhouse format"
if out="$(jw "$TMP/j6" JIUWENSWARM_MODE=release JIUWENSWARM_RELEASE="$REL/unknown.tar.gz" 2>&1)"; then
  fail "tasks/harness accepted an unknown release"
fi
ok "JiuwenSwarm releases: a wheel and a wheelhouse install once; an unknown file is refused with its listing"

# 11. A bundle from an earlier run, uploaded as the release, is used as it is.
out="$(run "$TMP/jd" "$TMP/share-jw-dispatcher" HARNESS=jiuwenswarm HARNESS_BUNDLE_PLATFORM="$NATIVE" \
  bash "$ROOT/pipeline/tools/prepare_harness.sh" 2>&1)" || fail "jiuwenswarm dispatcher: $out"
JBUNDLE="$TMP/jd/harness/bundle.tar.gz"
[ "$(cat "$TMP/jd/harness/pin.txt")" = "git $JW_PR" ] || fail "JiuwenSwarm dispatcher pin $(cat "$TMP/jd/harness/pin.txt")"
[ "$(bash "$JWI" --release-kind "$JBUNDLE")" = bundle ] || fail "bundle not detected"
n="$(builds)"
out="$(run "$TMP/j7" "$TMP/share-jw-upload" HARNESS=jiuwenswarm JIUWENSWARM_MODE=release JIUWENSWARM_RELEASE="$JBUNDLE" \
  bash "$STAGES/tasks/harness.sh" 2>&1)" || fail "bundle upload: $out"
[ "$(builds)" = "$n" ] || fail "a bundle upload was built"
[ "$(field "$TMP/j7/harness.json" source)" = upload ] || fail "bundle upload source"
[ "$(field "$TMP/j7/icode_release.json" format)" = bundle ] || fail "bundle upload format"
[ "$(field "$TMP/j7/icode_release.json" sha256)" = "$(cat "$TMP/jd/harness/bundle.sha256")" ] || fail "bundle upload sha256"
# The dispatcher hands a shard the release file only when it is not the bundle itself.
out="$(run "$TMP/jd2" "$TMP/share-jw-dispatcher2" HARNESS=jiuwenswarm JIUWENSWARM_MODE=release JIUWENSWARM_RELEASE="$JBUNDLE" \
  HARNESS_BUNDLE_PLATFORM="$NATIVE" bash "$ROOT/pipeline/tools/prepare_harness.sh" 2>&1)" || fail "bundle dispatcher: $out"
[ "$(cat "$TMP/jd2/harness/pin.txt")" = "release $(cat "$TMP/jd2/harness/bundle.sha256")" ] || fail "a bundle upload pins another sha256"
out="$(run "$TMP/jd3" "$TMP/share-jw-dispatcher3" HARNESS=jiuwenswarm JIUWENSWARM_MODE=release JIUWENSWARM_RELEASE="$REL/JIUWENSWARM_RELEASE_FILE" \
  HARNESS_BUNDLE_PLATFORM="$NATIVE" bash "$ROOT/pipeline/tools/prepare_harness.sh" 2>&1)" || fail "wheel dispatcher: $out"
[ "$(cat "$TMP/jd3/harness/pin.txt")" = "release $(python3 "$LIB/harness_store.py" sha256 "$REL/JIUWENSWARM_RELEASE_FILE")" ] \
  || fail "the wheel dispatcher pin $(cat "$TMP/jd3/harness/pin.txt")"
[ "$(cat "$TMP/jd3/harness/pin.txt")" != "release $(cat "$TMP/jd3/harness/bundle.sha256")" ] || fail "a wheel pin equals its bundle"
ok "a mac-k3d bundle uploaded as the release is stored as it is, no build"

# 12. JiuwenSwarm runs on DeepSWE only.
if out="$(run "$TMP/j8" "$S3" HARNESS=jiuwenswarm BENCHMARK=lolbench bash "$STAGES/tasks/harness.sh" 2>&1)"; then
  fail "JiuwenSwarm ran on lolbench"
fi
grep -q "runs on BENCHMARK=deepswe only" <<<"$out" || fail "no deepswe-only message: $out"
ok "JiuwenSwarm is refused outside DeepSWE"

echo "OK test_harness_store.sh"
