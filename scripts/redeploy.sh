#!/usr/bin/env bash
# Build mac-k3d from this checkout and install it everywhere, after each commit + push:
#
#   bash scripts/redeploy.sh --controller root@<controller> --worker <user>@<worker>
#
#   --controller HOST   install, then `mac-k3d config --skip-secrets` (rewrites the jobs)
#   --start             run `mac-k3d start` on the controller instead (plugin list changed)
#   --worker HOST       install, then `mac-k3d config -c worker.yaml`; repeat per worker
#   --no-local-worker   skip this PC (by default it is a worker too)
#
# Each worker's Jenkins builds run the pipeline embedded in its installed binary,
# so this is the only step between a pushed commit and a build that runs it.
# SSH asks each host's password once. Every host must end on this build's version.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }
warn() { echo "WARNING: $*" >&2; }
die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

CONTROLLER=""
CONTROLLER_CMD="config"
WORKERS=()
LOCAL_WORKER=1
while [ $# -gt 0 ]; do
  case "$1" in
    --controller) CONTROLLER="${2:?--controller needs user@host}"; shift 2 ;;
    --start) CONTROLLER_CMD="start"; shift ;;
    --worker) WORKERS+=("${2:?--worker needs user@host}"); shift 2 ;;
    --local-worker) LOCAL_WORKER=1; shift ;;
    --no-local-worker) LOCAL_WORKER=0; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done
if [ -z "$CONTROLLER" ] && [ "${#WORKERS[@]}" -eq 0 ] && [ "$LOCAL_WORKER" = 0 ]; then
  usage >&2
  die "nothing to deploy: pass --controller, --worker, or drop --no-local-worker"
fi
[ "$CONTROLLER_CMD" = "start" ] && [ -z "$CONTROLLER" ] && die "--start needs --controller"

# Remote paths stay unexpanded here; each host's shell expands its own ~.
CONTROLLER_YAML='~/.config/mac-k3d/config.yaml'
WORKER_YAML='~/.config/mac-k3d/worker.yaml'
if [ "$LOCAL_WORKER" = 1 ] && [ ! -f "$HOME/.config/mac-k3d/worker.yaml" ]; then
  die "this PC has no ~/.config/mac-k3d/worker.yaml: run \`mac-k3d setup -c ~/.config/mac-k3d/worker.yaml\` or pass --no-local-worker. No host was changed."
fi

step "Checking the checkout"
if [ -n "$(git status --porcelain -- src pipeline Cargo.toml Cargo.lock build.rs)" ]; then
  warn "uncommitted changes in src/ or pipeline/: every host will report a dirty build, and every Jenkins build refuses it."
fi
if ahead="$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null)"; then
  [ "$ahead" -gt 0 ] && warn "$(git rev-parse --abbrev-ref HEAD) is $ahead commit(s) ahead of its upstream: push so the commit in each result can be found."
else
  warn "$(git rev-parse --abbrev-ref HEAD) has no upstream: push it so the commit in each result can be found."
fi

step "Building (cargo build --release)"
export CARGO_TARGET_DIR="$ROOT/target"
cargo build --release
BIN="$CARGO_TARGET_DIR/release/mac-k3d"
WANT="$("$BIN" --version)"
LOCAL_ARCH="$(uname -sm)"
echo "built: $WANT ($LOCAL_ARCH)"

SOCK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mac-k3d-redeploy.XXXXXX")"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$SOCK_DIR/%C" -o ControlPersist=600)
HOSTS=()
[ -n "$CONTROLLER" ] && HOSTS+=("$CONTROLLER")
HOSTS+=(${WORKERS[@]+"${WORKERS[@]}"})
cleanup() {
  for host in ${HOSTS[@]+"${HOSTS[@]}"}; do
    ssh "${SSH_OPTS[@]}" -O exit "$host" >/dev/null 2>&1 || true
  done
  rm -rf "$SOCK_DIR"
}
trap cleanup EXIT

# A plain command on HOST (its own shell expands ~).
rsh() {
  local host="$1"
  shift
  ssh "${SSH_OPTS[@]}" "$host" "$@"
}

# Run CMD on HOST in a login shell with ~/.local/bin first on PATH, so `config`
# finds the same docker / java / helm an interactive session does. A Mac's
# bash login shell does not read the zsh profile that puts Homebrew on PATH.
remote() {
  local host="$1" cmd="$2"
  shift 2
  ssh "${SSH_OPTS[@]}" "$@" "$host" "bash -lc $(printf '%q' "export PATH=\"\$HOME/.local/bin:\$PATH\"; [ \"\$(uname -s)\" != Darwin ] || export PATH=\"/opt/homebrew/bin:/usr/local/bin:\$PATH\"; $cmd")"
}

# Copy the build to HOST as mac-k3d.new and check that it runs there. YAML is
# the file `config -c` will read: without it `config` has nothing to apply.
stage_remote() {
  local host="$1" yaml="$2" arch
  rsh "$host" "test -f $yaml" ||
    die "$host has no $yaml: run \`mac-k3d setup -c $yaml\` on it first. No host was changed."
  arch="$(rsh "$host" 'uname -sm')"
  [ "$arch" = "$LOCAL_ARCH" ] || die "$host is $arch but this build is $LOCAL_ARCH; build on a matching machine. No host was changed."
  rsh "$host" 'mkdir -p ~/.local/bin'
  scp "${SSH_OPTS[@]}" -q "$BIN" "$host:.local/bin/mac-k3d.new"
  if ! rsh "$host" 'chmod 755 ~/.local/bin/mac-k3d.new && ~/.local/bin/mac-k3d.new --version >/dev/null'; then
    rsh "$host" 'rm -f ~/.local/bin/mac-k3d.new' || true
    die "the new binary does not run on $host (often an older glibc than this PC's). No host was changed."
  fi
  echo "staged on $host"
}

install_remote() {
  local host="$1" which
  rsh "$host" 'mv -f ~/.local/bin/mac-k3d.new ~/.local/bin/mac-k3d'
  which="$(remote "$host" 'command -v mac-k3d' 2>/dev/null | tail -n 1 || true)"
  case "$which" in
    */.local/bin/mac-k3d) ;;
    *) warn "on $host a login shell runs '$which', not ~/.local/bin/mac-k3d. Jenkins builds use ~/.local/bin; fix PATH or remove the other copy." ;;
  esac
  echo "installed $WANT on $host"
}

for host in ${HOSTS[@]+"${HOSTS[@]}"}; do
  step "Connecting to $host (password asked once)"
  ssh "${SSH_OPTS[@]}" -fN "$host"
done

if [ "${#HOSTS[@]}" -gt 0 ]; then
  step "Staging the binary on every host before switching any"
  if [ -n "$CONTROLLER" ]; then
    stage_remote "$CONTROLLER" "$CONTROLLER_YAML"
  fi
  for host in ${WORKERS[@]+"${WORKERS[@]}"}; do
    stage_remote "$host" "$WORKER_YAML"
  done
fi

if [ -n "$CONTROLLER" ]; then
  step "Controller $CONTROLLER"
  install_remote "$CONTROLLER"
  if [ "$CONTROLLER_CMD" = "start" ]; then
    remote "$CONTROLLER" "mac-k3d start -c $CONTROLLER_YAML" -t
  else
    remote "$CONTROLLER" "mac-k3d config -c $CONTROLLER_YAML --skip-secrets" -t
  fi
fi

for host in ${WORKERS[@]+"${WORKERS[@]}"}; do
  step "Worker $host"
  install_remote "$host"
  remote "$host" "mac-k3d config -c $WORKER_YAML" -t
done

if [ "$LOCAL_WORKER" = 1 ]; then
  step "Worker on this PC"
  mkdir -p "$HOME/.local/bin"
  install -m 755 "$BIN" "$HOME/.local/bin/mac-k3d"
  "$HOME/.local/bin/mac-k3d" config -c "$HOME/.config/mac-k3d/worker.yaml"
fi

step "Versions"
fail=0
check() {
  local where="$1" got="$2"
  printf '  %-28s %s\n' "$where" "$got"
  [ "$got" = "$WANT" ] || fail=1
}
for host in ${HOSTS[@]+"${HOSTS[@]}"}; do
  check "$host" "$(rsh "$host" '~/.local/bin/mac-k3d --version' 2>&1 || true)"
done
[ "$LOCAL_WORKER" = 1 ] && check "this PC" "$("$HOME/.local/bin/mac-k3d" --version 2>&1 || true)"
[ "$fail" = 0 ] || die "a host does not run $WANT"
echo "All hosts run $WANT."
