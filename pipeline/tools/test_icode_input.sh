#!/usr/bin/env bash
# No-network URL/ref validation for get_bin_icode (pipeline/lib/icode_input.sh).
set -euo pipefail

LIB="$(cd "$(dirname "$0")/../lib" && pwd)"
IN="$LIB/icode_input.sh"
[ -f "$IN" ] || {
  echo "FAIL: missing $IN" >&2
  exit 1
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

ok() {
  echo "OK $*"
}

must_fail() {
  local msg="$1"
  shift
  if bash "$IN" "$@" >/dev/null 2>&1; then
    fail "expected failure: $msg"
  fi
  ok "reject $msg"
}

must_ok() {
  local msg="$1"
  shift
  bash "$IN" "$@" >/dev/null || fail "expected OK: $msg"
  ok "accept $msg"
}

must_fail "file://" --validate-url "file:///tmp/icode"
must_fail "ssh://" --validate-url "ssh://github.com/org/icode.git"
must_fail "http://" --validate-url "http://github.com/org/icode.git"
must_fail "userinfo" --validate-url "https://user:token@github.com/org/icode.git"
must_fail "evil host" --validate-url "https://github.com.evil.com/org/icode.git"
must_ok "github https" --validate-url "https://github.com/org/icode.git"
must_ok "gitcode https" --validate-url "https://gitcode.com/org/icode.git"

must_fail "leading dash" --validate-ref "--upload-pack"
must_fail "semicolon" --validate-ref "foo;rm"
must_fail "dotdot" --validate-ref "foo/../bar"
must_ok "tag" --validate-ref "v0.1.41"
must_ok "branch" --validate-ref "main"
must_ok "commit" --validate-ref "0123456789abcdef0123456789abcdef01234567"

bash "$IN" --normalize-ref-kind auto main | grep -qx branch || fail "auto main → branch"
bash "$IN" --normalize-ref-kind auto 0123456789abcdef0123456789abcdef01234567 | grep -qx commit || fail "auto sha → commit"
bash "$IN" --normalize-ref-kind TAG v0.1.41 | grep -qx tag || fail "TAG → tag"
must_ok "commit kind + sha" --validate-ref-kind commit deadbee
must_fail "commit kind + branch" --validate-ref-kind commit main
must_ok "pr kind + number" --validate-ref-kind pr 7
must_fail "pr kind + branch" --validate-ref-kind pr main
must_fail "bad kind" --normalize-ref-kind sha main
bash "$IN" --normalize-ref-kind pr 7 | grep -qx pr || fail "pr kind"
ok "ref kind auto/tag/commit"

# Local git checkout by kind (no network). protocol.file is blocked unless this flag.
KIND_TMP="$(mktemp -d)"
trap 'rm -rf "$KIND_TMP"' EXIT
ORIGIN="$KIND_TMP/origin"
git init -q "$ORIGIN"
git -C "$ORIGIN" symbolic-ref HEAD refs/heads/main
git -C "$ORIGIN" config user.email t@t
git -C "$ORIGIN" config user.name t
echo a >"$ORIGIN/f"
git -C "$ORIGIN" add f
git -C "$ORIGIN" commit -q -m base
BASE_SHA="$(git -C "$ORIGIN" rev-parse HEAD)"
git -C "$ORIGIN" tag v9.9.9
git -C "$ORIGIN" checkout -q -b feature
echo b >>"$ORIGIN/f"
git -C "$ORIGIN" commit -q -am pr
FEAT_SHA="$(git -C "$ORIGIN" rev-parse HEAD)"
git -C "$ORIGIN" checkout -q main
export MAC_K3D_ICODE_GIT_ALLOW_FILE=1
export WORKDIR="$KIND_TMP/work"
mkdir -p "$WORKDIR"
# shellcheck disable=SC1090
source "$IN"
icode_git_checkout "$KIND_TMP/c-branch" "$ORIGIN" feature branch
[ "$(git -C "$KIND_TMP/c-branch" rev-parse HEAD)" = "$FEAT_SHA" ] || fail "branch checkout sha"
icode_git_checkout "$KIND_TMP/c-tag" "$ORIGIN" v9.9.9 tag
[ "$(git -C "$KIND_TMP/c-tag" rev-parse HEAD)" = "$BASE_SHA" ] || fail "tag checkout sha"
icode_git_checkout "$KIND_TMP/c-commit" "$ORIGIN" "$FEAT_SHA" commit
[ "$(git -C "$KIND_TMP/c-commit" rev-parse HEAD)" = "$FEAT_SHA" ] || fail "commit checkout sha"
git -C "$ORIGIN" update-ref refs/merge-requests/7/head "$FEAT_SHA"
icode_git_checkout "$KIND_TMP/c-pr" "$ORIGIN" 7 pr 2>"$KIND_TMP/pr.err"
[ "$(git -C "$KIND_TMP/c-pr" rev-parse HEAD)" = "$FEAT_SHA" ] || fail "pr checkout sha"
grep -q 'DEBUG icode pr' "$KIND_TMP/pr.err" && fail "pr debug line still present"
icode_git_pr_refs "https://github.com/org/icode.git" 12 | grep -qx 'refs/pull/12/head' || fail "github pr ref"
icode_git_pr_refs "https://gitcode.com/org/icode.git" 12 | head -n 1 | grep -qx 'refs/merge-requests/12/head' || fail "gitcode pr ref"
SHORT="$(git -C "$ORIGIN" rev-parse --short=8 "$FEAT_SHA")"
icode_git_checkout "$KIND_TMP/c-abbr" "$ORIGIN" "$SHORT" commit
[ "$(git -C "$KIND_TMP/c-abbr" rev-parse HEAD)" = "$FEAT_SHA" ] || fail "abbrev commit checkout sha"
icode_record_git_meta "$KIND_TMP/c-commit" "https://github.com/org/icode.git" commit "$FEAT_SHA"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["kind"]=="commit" and d["sha"]==sys.argv[2]' "$WORKDIR/icode_git.json" "$FEAT_SHA" || fail "icode_git.json"
ok "checkout branch/tag/commit + icode_git.json"
grep -q 'icode_force_rm' "$IN" || fail "missing icode_force_rm"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  plat="$(icode_docker_platform)"
  LEFT="$KIND_TMP/docker-left"
  mkdir -p "$LEFT"
  docker run --rm --platform "$plat" -v "$LEFT:/w" alpine:3.20 sh -c 'mkdir -p /w/icode-src/.venv && echo x>/w/icode-src/.venv/r && mkdir -p /w/icode-src/__pycache__ && echo p>/w/icode-src/__pycache__/x.pyc && chown -R 0:0 /w/icode-src/.venv && chown -R 65534:65534 /w/icode-src/__pycache__'
  icode_git_checkout "$LEFT/icode-src" "$ORIGIN" main branch
  [ "$(git -C "$LEFT/icode-src" rev-parse HEAD)" = "$BASE_SHA" ] || fail "docker leftover wipe then checkout sha"
  ok "docker leftover wipe then checkout"
  RELF="$KIND_TMP/ICODE_RELEASE_FILE"
  docker run --rm --platform "$plat" -v "$KIND_TMP:/w" alpine:3.20 sh -c 'echo leftover >/w/ICODE_RELEASE_FILE && chown 0:0 /w/ICODE_RELEASE_FILE'
  bash "$IN" --force-rm "$RELF" >/dev/null || fail "force-rm leftover ICODE_RELEASE_FILE"
  [ ! -e "$RELF" ] || fail "ICODE_RELEASE_FILE still present after force-rm"
  ok "force-rm leftover ICODE_RELEASE_FILE"
else
  ok "skip docker leftover wipe (docker unavailable)"
fi
if ( ICODE_EXPECT_SHA="$BASE_SHA" icode_git_checkout "$KIND_TMP/c-expect-bad" "$ORIGIN" "$FEAT_SHA" commit ); then
  fail "expected ICODE_EXPECT_SHA mismatch to fail"
fi
ok "reject ICODE_EXPECT_SHA mismatch"
ICODE_EXPECT_SHA="$FEAT_SHA" icode_git_checkout "$KIND_TMP/c-expect-ok" "$ORIGIN" "$FEAT_SHA" commit
[ "$(git -C "$KIND_TMP/c-expect-ok" rev-parse HEAD)" = "$FEAT_SHA" ] || fail "expect sha checkout"
unset ICODE_EXPECT_SHA
ok "ICODE_EXPECT_SHA match"
icode_git_checkout "$KIND_TMP/c-clean" "$ORIGIN" main branch
icode_assert_clean_checkout "$KIND_TMP/c-clean"
echo extra >"$KIND_TMP/c-clean/extra.txt"
if ( icode_assert_clean_checkout "$KIND_TMP/c-clean" ); then
  fail "dirty tree should fail"
fi
rm -f "$KIND_TMP/c-clean/extra.txt"
printf '%s\n' '#!/bin/sh' >"$KIND_TMP/c-clean/icode"
icode_assert_clean_checkout "$KIND_TMP/c-clean"
ok "porcelain allows only the icode launcher"
resolve_kind() {
  ICODE_GIT_URL="$ORIGIN" ICODE_GIT_REF="$1" ICODE_GIT_REF_KIND="$2" \
    MAC_K3D_ICODE_GIT_ALLOW_FILE=1 bash "$IN" --resolve-sha
}
[ "$(resolve_kind feature branch)" = "$FEAT_SHA" ] || fail "resolve-sha branch"
[ "$(resolve_kind v9.9.9 tag)" = "$BASE_SHA" ] || fail "resolve-sha tag"
[ "$(resolve_kind "$FEAT_SHA" commit)" = "$FEAT_SHA" ] || fail "resolve-sha commit"
[ "$(resolve_kind 7 pr)" = "$FEAT_SHA" ] || fail "resolve-sha pr"
git -C "$ORIGIN" checkout -q feature
echo c >>"$ORIGIN/f"
git -C "$ORIGIN" commit -q -am later
MOVED="$(git -C "$ORIGIN" rev-parse HEAD)"
git -C "$ORIGIN" checkout -q main
[ "$(resolve_kind feature branch)" = "$MOVED" ] || fail "resolve-sha follows a moved branch"
WORKDIR="$KIND_TMP/pinwork"
mkdir -p "$WORKDIR"
ICODE_GIT_SHA="$FEAT_SHA" icode_checkout_requested "$KIND_TMP/c-pin" "$ORIGIN" feature branch
[ "$(git -C "$KIND_TMP/c-pin" rev-parse HEAD)" = "$FEAT_SHA" ] || fail "ICODE_GIT_SHA checkout stayed on the pin"
icode_record_git_meta "$KIND_TMP/c-pin" "https://gitcode.com/org/icode.git" branch feature
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["kind"]=="branch" and d["ref"]=="feature" and d["sha"]==sys.argv[2]' \
  "$WORKDIR/icode_git.json" "$FEAT_SHA" || fail "pinned checkout records the form and the pinned sha"
unset ICODE_GIT_SHA
ok "resolve-sha branch/tag/commit/pr; a later branch move does not change a pinned checkout"
unset MAC_K3D_ICODE_GIT_ALLOW_FILE

PTH_TREE="$KIND_TMP/pth-tree"
mkdir -p "$PTH_TREE/.venv/lib/python3.13/site-packages" "$PTH_TREE/.venv/sandbox-cpython/lib/python3.13"
icode_write_wrapper "$PTH_TREE/icode"
grep -q 'export PYTHONPATH' "$PTH_TREE/icode" && fail "launcher must not export PYTHONPATH"
grep -q 'export VIRTUAL_ENV' "$PTH_TREE/icode" && fail "launcher must not export VIRTUAL_ENV"
grep -qF 'exec "$PY" "$ROOT/.venv/bin/icode" "$@"' "$PTH_TREE/icode" || fail "launcher exec line"
icode_write_sandbox_pth "$PTH_TREE"
PTH="$PTH_TREE/.venv/sandbox-cpython/lib/python3.13/site-packages/icode-host.pth"
[ "$(cat "$PTH")" = "$(printf '%s\n%s' /opt/icode-host /opt/icode-host/.venv/lib/python3.13/site-packages)" ] \
  || fail "icode-host.pth must list exactly the clone root and its venv site-packages"
ok "launcher exports nothing; icode-host.pth lists the two iCode paths"

# icode_uv_sync: a fake uv records the git env it was given and fails on the
# call numbers listed in FAKE_UV_FAIL.
FAKE_UV_DIR="$KIND_TMP/fake-uv"
mkdir -p "$FAKE_UV_DIR/bin"
cat >"$FAKE_UV_DIR/bin/uv" <<'UV'
#!/usr/bin/env bash
n=$(( $(cat "$FAKE_UV_DIR/calls" 2>/dev/null || echo 0) + 1 ))
echo "$n" >"$FAKE_UV_DIR/calls"
{
  env | grep -E '^(GIT_CONFIG_(COUNT|KEY_|VALUE_)|GIT_ASKPASS=|GIT_SSH_COMMAND=)' | sort
  [ -n "${MAC_K3D_GIT_TOKEN:-}" ] && echo token_set=1
} >"$FAKE_UV_DIR/env.$n"
echo "fake uv stdout"
case " ${FAKE_UV_FAIL:-} " in *" $n "*) exit 1 ;; esac
exit 0
UV
chmod 755 "$FAKE_UV_DIR/bin/uv"
export FAKE_UV_DIR
UV_TREE="$KIND_TMP/uv-tree"
mkdir -p "$UV_TREE"
SENTINEL="gc-sentinel-not-for-logs"
run_uv_sync() {
  rm -f "$FAKE_UV_DIR"/calls "$FAKE_UV_DIR"/env.*
  # get_bin_icode unsets these after the clone; an editor's git askpass must not leak in.
  # The host uv path is the one under test here, also on a macOS host.
  ( unset GIT_ASKPASS MAC_K3D_GIT_TOKEN; export PATH="$FAKE_UV_DIR/bin:$PATH" FAKE_UV_FAIL="$1"
    icode_container_build_needed() { return 1; }
    icode_uv_sync "$UV_TREE" gitcode.com "$2" ) \
    >"$FAKE_UV_DIR/out" 2>"$FAKE_UV_DIR/err"
}
uv_env() { printf '%s\n' "$FAKE_UV_DIR/env.$1"; }
token_leaked() { grep -qF "$SENTINEL" "$FAKE_UV_DIR/out" "$FAKE_UV_DIR/err"; }

run_uv_sync "" "$SENTINEL" || fail "uv sync with a PAT should pass"
[ ! -s "$FAKE_UV_DIR/out" ] || fail "icode_uv_sync stdout must stay empty: the caller captures ICODE_BIN"
[ "$(cat "$FAKE_UV_DIR/calls")" = 1 ] || fail "PAT success: expected one uv call"
grep -qx 'GIT_CONFIG_KEY_0=url.https://gitcode.com/.insteadOf' "$(uv_env 1)" || fail "PAT: insteadOf key"
grep -qx 'GIT_CONFIG_VALUE_0=ssh://git@gitcode.com/' "$(uv_env 1)" || fail "PAT: ssh:// rewritten"
grep -qx 'GIT_CONFIG_VALUE_1=git@gitcode.com:' "$(uv_env 1)" || fail "PAT: scp-style rewritten"
grep -q '^GIT_ASKPASS=' "$(uv_env 1)" || fail "PAT: askpass set"
grep -qx 'token_set=1' "$(uv_env 1)" || fail "PAT: token reaches askpass"
token_leaked && fail "PAT printed by icode_uv_sync"
grep -q 'OK icode dependencies fetched over https with the gitcode.com PAT' "$FAKE_UV_DIR/err" \
  || fail "PAT: OK line"
ok "uv sync rewrites ssh:// deps to https with the iCode host PAT"

run_uv_sync "1" "$SENTINEL" || fail "SSH retry should pass"
[ "$(cat "$FAKE_UV_DIR/calls")" = 2 ] || fail "PAT failure: expected an SSH retry"
grep -q '^GIT_CONFIG_' "$(uv_env 2)" && fail "SSH retry must not rewrite to https"
grep -q 'token_set=1' "$(uv_env 2)" && fail "SSH retry must not carry the PAT"
grep -qx 'GIT_SSH_COMMAND=ssh -o BatchMode=yes' "$(uv_env 2)" || fail "SSH retry must not prompt"
grep -q 'retrying with this machine.s SSH key' "$FAKE_UV_DIR/err" || fail "SSH retry warning"
token_leaked && fail "PAT printed on retry"
ok "uv sync falls back to this machine's SSH key when the PAT fails"

run_uv_sync "" "" || fail "uv sync without a PAT should pass"
[ "$(cat "$FAKE_UV_DIR/calls")" = 1 ] || fail "no PAT: expected one uv call"
grep -q '^GIT_CONFIG_' "$(uv_env 1)" && fail "no PAT: nothing to rewrite with"
grep -q '^GIT_ASKPASS=' "$(uv_env 1)" && fail "no PAT: no askpass"
grep -qx 'GIT_SSH_COMMAND=ssh -o BatchMode=yes' "$(uv_env 1)" || fail "no PAT: SSH must not prompt"
ok "uv sync without a PAT uses SSH only"

printf '%s\n' 'source = { git = "ssh://git@gitcode.com/michaelling/agent-core.git?branch=icode#0f25d4d" }' >"$UV_TREE/uv.lock"
if run_uv_sync "1 2" "$SENTINEL"; then
  fail "both uv sync attempts failing must fail"
fi
grep -q 'iCode pins a git dependency over SSH' "$FAKE_UV_DIR/err" || fail "SSH-pinned dependency message"
token_leaked && fail "PAT printed in the failure message"
rm -f "$UV_TREE/uv.lock"
if run_uv_sync "1" ""; then
  fail "a failing uv sync must fail"
fi
grep -q "uv sync failed in $UV_TREE" "$FAKE_UV_DIR/err" || fail "plain uv sync failure message"
grep -q 'over SSH' "$FAKE_UV_DIR/err" && fail "no SSH pin: do not blame SSH"
ok "uv sync failure names the SSH-pinned dependency only when there is one"
for bench in deepswe lolbench swebenchpro; do
  BENCHMARK="$bench" icode_sourceless_enabled || fail "sourceless must default on for $bench"
done
ICODE_SOURCELESS_STDLIB=0 icode_sourceless_enabled || fail "ICODE_SOURCELESS_STDLIB=0 must not turn sourceless off"
ICODE_SOURCELESS_STDLIB=off icode_sourceless_enabled || fail "ICODE_SOURCELESS_STDLIB=off must not turn sourceless off"
ok "sourceless stdlib stays on for every benchmark"

REL_TMP="$(mktemp -d)"
printf 'drop' >"$REL_TMP/icode"
HASH="$(python3 -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$REL_TMP/icode")"
WORKDIR="$REL_TMP" icode_record_release "$REL_TMP/icode" icode-drop
grep -q "\"sha256\": \"$HASH\"" "$REL_TMP/icode_release.json" || fail "release json must record the file sha256"
if ( WORKDIR="$REL_TMP" ICODE_RELEASE_SHA256="$(printf '0%.0s' {1..64})" icode_record_release "$REL_TMP/icode" icode-drop ); then
  fail "expected ICODE_RELEASE_SHA256 mismatch to fail"
fi
WORKDIR="$REL_TMP" ICODE_RELEASE_SHA256="$HASH" icode_record_release "$REL_TMP/icode" icode-drop
rm -rf "$REL_TMP"
ok "release sha256 is recorded and a mismatch dies"

ICODE_MODE=binary bash "$IN" --normalize-mode | grep -qx release || fail "binary alias"
ICODE_MODE=git bash "$IN" --normalize-mode | grep -qx git || fail "git mode"
if ICODE_MODE=source bash "$IN" --normalize-mode >/dev/null 2>&1; then
  fail "expected source mode to fail"
fi
ok "normalize binary→release git→git; reject source"

bash "$IN" --token-env-for-host gitcode.com | grep -qx GITCODE_TOKEN || fail "gitcode token env"
bash "$IN" --token-env-for-host github.com | grep -qx GITHUB_TOKEN || fail "github token env"
must_fail "evil token host" --token-env-for-host github.com.evil.com
grep -q 'sandbox-cpython' "$IN" || fail "missing sandbox-cpython git wrapper"
grep -q 'icode_embed_sandbox_cpython' "$IN" || fail "missing embed helper"
grep -q 'gitignored .env' "$IN" || fail "missing private-clone PAT hint"
ok "token env names + fail-fast clone hints"

if grep -q 'get_local_icode' "$IN"; then
  fail "get_local_icode must be removed"
fi
grep -q 'icode_paths_get' "$IN" || fail "missing icode_paths_get"
ok "no local source helper; persist remains for release"

COMMON="$(cd "$(dirname "$0")/../stages" && pwd)/_common.sh"
grep -q 'icode_release_is_uploaded' "$COMMON" || fail "missing icode_release_is_uploaded"
grep -q 'ICODE_RELEASE_UPLOADED' "$COMMON" || fail "missing ICODE_RELEASE_UPLOADED"
ok "Jenkins unnamed upload helper"

# Release drops by CPU type: ELF e_machine at byte 18, Mach-O magic, scripts.
ARCH_TMP="$(mktemp -d)"
elf() { mkdir -p "$(dirname "$1")"; printf "\177ELF\002\001\001\000\000\000\000\000\000\000\000\000\002\000$2" >"$1"; chmod 755 "$1"; }
elf "$ARCH_TMP/x86/icode" '\076\000'
elf "$ARCH_TMP/arm/icode" '\267\000'
printf '\317\372\355\376rest' >"$ARCH_TMP/mac-icode"
printf '#!/bin/sh\nexit 0\n' >"$ARCH_TMP/script-icode"
[ "$(icode_binary_cpu "$ARCH_TMP/x86/icode")" = x86_64 ] || fail "ELF x86-64"
[ "$(icode_binary_cpu "$ARCH_TMP/arm/icode")" = aarch64 ] || fail "ELF aarch64"
[ "$(icode_binary_cpu "$ARCH_TMP/mac-icode")" = darwin ] || fail "Mach-O"
[ -z "$(icode_binary_cpu "$ARCH_TMP/script-icode")" ] || fail "a script has no CPU type"
[ "$(icode_drop_name_cpu icode-linux-x86_64-full-v0.1.49.tar.gz)" = x86_64 ] || fail "x86_64 drop name"
[ "$(icode_drop_name_cpu /x/icode-linux-aarch64-full-v0.1.49)" = aarch64 ] || fail "aarch64 drop name"
[ "$(icode_drop_name_cpu icode-macos-arm64-full-v0.1.49)" = darwin ] || fail "macOS drop name"
[ -z "$(icode_drop_name_cpu ICODE_RELEASE_FILE)" ] || fail "an upload's name gives no CPU type"
ok "CPU type of release binaries and drop names"

BUNDLE="$ARCH_TMP/bundle"
elf "$BUNDLE/icode-linux-aarch64-full-v1/icode" '\267\000'
elf "$BUNDLE/icode-linux-x86_64-full-v1/icode" '\076\000'
pick_for() { ( PLAT="$1"; task_container_platform() { printf '%s\n' "$PLAT"; }; icode_pick_release_bin "$2" ); }
[ "$(pick_for linux/amd64 "$BUNDLE")" = "$BUNDLE/icode-linux-x86_64-full-v1/icode" ] || fail "bundle: amd64 build takes the x86_64 drop"
[ "$(pick_for linux/arm64 "$BUNDLE")" = "$BUNDLE/icode-linux-aarch64-full-v1/icode" ] || fail "bundle: arm64 build takes the aarch64 drop"
if pick_for linux/arm64 "$ARCH_TMP/x86" 2>"$ARCH_TMP/pick.err"; then
  fail "an x86_64-only release must not run in arm64 containers"
fi
grep -q 'built for x86_64' "$ARCH_TMP/pick.err" || fail "CPU mismatch message names the drop's CPU"
grep -q 'icode-linux-aarch64-full-vX.Y.Z' "$ARCH_TMP/pick.err" || fail "CPU mismatch message names the drop to use"
mkdir -p "$ARCH_TMP/mac"
cp "$ARCH_TMP/mac-icode" "$ARCH_TMP/mac/icode"
if pick_for linux/arm64 "$ARCH_TMP/mac" 2>/dev/null; then
  fail "a macOS build must not run in Linux containers"
fi
mkdir -p "$ARCH_TMP/stub"
cp "$ARCH_TMP/script-icode" "$ARCH_TMP/stub/icode"
[ "$(pick_for linux/arm64 "$ARCH_TMP/stub")" = "$ARCH_TMP/stub/icode" ] || fail "a script icode is taken as is"
if ( icode_pick_release_bin "$ARCH_TMP/empty-$$" ) 2>"$ARCH_TMP/pick.err"; then
  fail "no icode must fail"
fi
grep -q 'could not find icode inside release' "$ARCH_TMP/pick.err" || fail "no icode message"
ok "release pick: per-CPU drop from one bundle; a mismatch or macOS build fails early"

DROPS="$ARCH_TMP/drops"
mkdir -p "$DROPS"
: >"$DROPS/icode-linux-aarch64-full-v1.tar.gz"
: >"$DROPS/icode-linux-x86_64-full-v1.tar.gz"
resolve_for() { ( PLAT="$1"; task_container_platform() { printf '%s\n' "$PLAT"; }; icode_resolve_release_src "$2" ); }
[ "$(resolve_for linux/amd64 "$DROPS")" = "$DROPS/icode-linux-x86_64-full-v1.tar.gz" ] || fail "folder: amd64 takes the x86_64 drop"
[ "$(resolve_for linux/arm64 "$DROPS")" = "$DROPS/icode-linux-aarch64-full-v1.tar.gz" ] || fail "folder: arm64 takes the aarch64 drop"
rm -f "$DROPS/icode-linux-aarch64-full-v1.tar.gz"
[ "$(resolve_for linux/arm64 "$DROPS")" = "$DROPS/icode-linux-x86_64-full-v1.tar.gz" ] || fail "folder: one drop is taken whatever its name"
ok "release folder: the drop named for the containers' CPU type first"

# Container build: a fake docker records its argv and environment.
FAKE_DOCKER_DIR="$ARCH_TMP/fake-docker"
mkdir -p "$FAKE_DOCKER_DIR/bin"
cat >"$FAKE_DOCKER_DIR/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
n=$(( $(cat "$FAKE_DOCKER_DIR/calls" 2>/dev/null || echo 0) + 1 ))
echo "$n" >"$FAKE_DOCKER_DIR/calls"
printf '%s\n' "$@" >"$FAKE_DOCKER_DIR/argv.$n"
[ -n "${MAC_K3D_GIT_TOKEN:-}" ] && echo token_set=1 >"$FAKE_DOCKER_DIR/env.$n"
case " ${FAKE_DOCKER_FAIL:-} " in *" $n "*) exit 1 ;; esac
exit 0
DOCKER
chmod 755 "$FAKE_DOCKER_DIR/bin/docker"
export FAKE_DOCKER_DIR
run_container_sync() {
  rm -f "$FAKE_DOCKER_DIR"/calls "$FAKE_DOCKER_DIR"/argv.* "$FAKE_DOCKER_DIR"/env.*
  ( unset GIT_ASKPASS MAC_K3D_GIT_TOKEN
    export PATH="$FAKE_DOCKER_DIR/bin:$PATH" FAKE_DOCKER_FAIL="$1" MAC_K3D_ICODE_CONTAINER_BUILD=1 \
      MAC_K3D_SHARE="$ARCH_TMP/share"
    task_container_platform() { printf '%s\n' linux/arm64; }
    icode_uv_sync "$UV_TREE" gitcode.com "$2" ) \
    >"$FAKE_DOCKER_DIR/out" 2>"$FAKE_DOCKER_DIR/err"
}
argv() { printf '%s\n' "$FAKE_DOCKER_DIR/argv.$1"; }
run_container_sync "" "$SENTINEL" || fail "container uv sync with a PAT should pass"
[ ! -s "$FAKE_DOCKER_DIR/out" ] || fail "container uv sync stdout must stay empty"
[ "$(cat "$FAKE_DOCKER_DIR/calls")" = 1 ] || fail "container PAT success: one docker run"
grep -qxF -- '--platform' "$(argv 1)" && grep -qx 'linux/arm64' "$(argv 1)" || fail "container build runs on the task platform"
grep -qx "$(sed -n 's/^ICODE_LINUX_BUILD_IMAGE=//p' "$LIB/../config/toolchain.env")" "$(argv 1)" || fail "container build uses the pinned image"
grep -qx "$UV_TREE:$UV_TREE" "$(argv 1)" || fail "tree mounted at its own path"
grep -qx "$ARCH_TMP/share/uv-cache-linux-arm64:/mac-k3d-uv" "$(argv 1)" || fail "uv cache per platform"
grep -qx 'MAC_K3D_GIT_TOKEN' "$(argv 1)" || fail "token passed by name"
grep -qx 'GIT_CONFIG_VALUE_0' "$(argv 1)" || fail "insteadOf rules passed by name"
grep -qF "$SENTINEL" "$(argv 1)" && fail "token on docker argv"
grep -qx 'token_set=1' "$FAKE_DOCKER_DIR/env.1" || fail "token reaches docker's environment"
tail -n 3 "$(argv 1)" | tr '\n' ' ' | grep -q '^uv sync --no-editable $' || fail "container runs uv sync --no-editable"
grep -q 'building the git-mode runtime in' "$FAKE_DOCKER_DIR/err" || fail "container build is announced"
grep -qF "$SENTINEL" "$FAKE_DOCKER_DIR/out" "$FAKE_DOCKER_DIR/err" && fail "PAT printed by the container build"
run_container_sync "1" "$SENTINEL" || fail "container SSH retry should pass"
[ "$(cat "$FAKE_DOCKER_DIR/calls")" = 2 ] || fail "container PAT failure: SSH retry"
grep -qx 'GIT_SSH_COMMAND' "$(argv 2)" || fail "container SSH retry passes GIT_SSH_COMMAND"
grep -qx 'MAC_K3D_GIT_TOKEN' "$(argv 2)" && fail "container SSH retry must not carry the PAT"
ok "git mode builds in the Linux image of the task platform; the PAT never reaches argv"

VENV_TREE="$ARCH_TMP/venv-tree"
mkdir -p "$VENV_TREE/.venv/bin"
ln -s /mac-k3d-uv/python/cpython-3.13-linux-aarch64-gnu/bin/python3.13 "$VENV_TREE/.venv/bin/python"
icode_tree_built_in_container "$VENV_TREE" || fail "a venv linked into /mac-k3d-uv is container-built"
rm "$VENV_TREE/.venv/bin/python"
ln -s "$HOME/.local/share/uv/python/cpython-3.13/bin/python3.13" "$VENV_TREE/.venv/bin/python"
if icode_tree_built_in_container "$VENV_TREE"; then
  fail "a host uv venv is not container-built"
fi
ok "container-built venvs are told apart from host ones"
rm -rf "$ARCH_TMP"

echo "OK test_icode_input.sh"
