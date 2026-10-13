#!/usr/bin/env bash
# Shared env and helpers for the phase scripts (pipeline/stages/<phase>.sh) and
# their steps (pipeline/stages/<phase>/<step>.sh). Every step sources this file
# in its own process; steps hand state to each other only through $WORKDIR.
set -euo pipefail

# Repo or share dir that contains pipeline/stages + pipeline/lib.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export MAC_K3D_ROOT="$ROOT"
# Tool pins shared with `mac-k3d setup` (Harbor, compose, buildx, Java, bash,
# python3, host minimums). A value the job or caller already exported wins.
# macOS /bin/bash is 3.2 and rejects ${!name:-}, so the version guard runs
# before that loop. Only this BASH_MIN read has to work on 3.2.
TOOLCHAIN_ENV="$ROOT/pipeline/config/toolchain.env"
[ -f "$TOOLCHAIN_ENV" ] || { echo "ERROR: missing $TOOLCHAIN_ENV" >&2; exit 1; }
if [ -z "${BASH_MIN:-}" ]; then
  BASH_MIN="$(sed -n 's/^BASH_MIN=//p' "$TOOLCHAIN_ENV" | head -n 1)"
  export BASH_MIN
fi

# The steps use bash 4.4 features (mapfile, empty arrays under set -u).
require_bash_min() {
  local want="${BASH_MIN:?BASH_MIN missing from $TOOLCHAIN_ENV}" major minor
  major="${want%%.*}"
  minor="${want#*.}"
  if [ "${BASH_VERSINFO[0]}" -lt "$major" ] \
    || { [ "${BASH_VERSINFO[0]}" -eq "$major" ] && [ "${BASH_VERSINFO[1]}" -lt "$minor" ]; }; then
    echo "ERROR: bash ${BASH_VERSION} is older than ${want} (BASH_MIN in pipeline/config/toolchain.env)." >&2
    echo "macOS: brew install bash (mac-k3d setup does this), then make sure the Homebrew bin comes before /bin on PATH." >&2
    exit 1
  fi
}
require_bash_min

while IFS='=' read -r _pin_key _pin_value || [ -n "$_pin_key" ]; do
  [[ "$_pin_key" =~ ^[A-Z][A-Z0-9_]*$ ]] || continue
  if [ -z "${!_pin_key:-}" ]; then
    export "${_pin_key}=${_pin_value}"
  else
    export "${_pin_key?}"
  fi
done <"$TOOLCHAIN_ENV"
unset _pin_key _pin_value

export PIPELINE_LIB="$ROOT/pipeline/lib"
# shellcheck source=../lib/parallel_degree.sh
source "$PIPELINE_LIB/parallel_degree.sh"
export PIPELINE_STAGES="$ROOT/pipeline/stages"
export WORKDIR="${MAC_K3D_EVAL_WORKDIR:-$ROOT/eval-runs}"
mkdir -p "$WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd)"
export WORKDIR
export OUTPUT_DIR="${MAC_K3D_EVAL_OUTPUT:-$WORKDIR/reports}"
export DEEPSWE_DIR="${DEEPSWE_DIR:-$WORKDIR/deep-swe}"
export LOLBENCH_DIR="${LOLBENCH_DIR:-$WORKDIR/lolbench}"
export LOLBENCH_GIT_URL="${LOLBENCH_GIT_URL:-https://github.com/MichaelLing83/LoLBench-Preview.git}"
# Benchmark pins. Checked out on this worker on 2026-10-02: DeepSWE 113 tasks,
# LoLBench 20 harbor tasks. Override per run; do not float.
export DEEPSWE_GIT_URL="${DEEPSWE_GIT_URL:-https://github.com/datacurve-ai/deep-swe}"
export DEEPSWE_REF="${DEEPSWE_REF:-0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea}"
export DEEPSWE_TASK_COUNT="${DEEPSWE_TASK_COUNT:-113}"
export LOLBENCH_REF="${LOLBENCH_REF:-1b10d10bb4a10cea54374ac34b8f76b69dc8ce75}"
export LOLBENCH_TASK_COUNT="${LOLBENCH_TASK_COUNT:-20}"
export ICODE_EXPECT_SHA="${ICODE_EXPECT_SHA:-}"
export SWEBENCHPRO_DIR="${SWEBENCHPRO_DIR:-$WORKDIR/swebenchpro}"
export SWEBENCHPRO_GIT_URL="${SWEBENCHPRO_GIT_URL:-https://github.com/scaleapi/SWE-bench_Pro-os}"
export ICODE_MODE="${ICODE_MODE:-binary}"
export ICODE_RELEASE="${ICODE_RELEASE:-}"
export ICODE_GIT_URL="${ICODE_GIT_URL:-}"
export ICODE_GIT_REF="${ICODE_GIT_REF:-main}"
export ICODE_GIT_REF_KIND="${ICODE_GIT_REF_KIND:-branch}"
export N_TASKS="${N_TASKS:-1}"
export TASK="${TASK:-}"
export HARNESS="${HARNESS:-icode}"
export LLM="${LLM:-deepseek}"
export BENCHMARK="${BENCHMARK:-deepswe}"
export BASELINE_DIR="$WORKDIR/baseline"
export HARNESS_DIR="$WORKDIR/harness"
export RESULTS_DIR="$WORKDIR/results"
export MAC_K3D_SHARE="${MAC_K3D_SHARE:-${XDG_DATA_HOME:-$HOME/.local/share}/mac-k3d}"

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$BASELINE_DIR" "$HARNESS_DIR" "$RESULTS_DIR"

progress() {
  local pct="$1"
  shift
  echo "PROGRESS ${pct}% $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

# One lock directory per worker, outside any executor's workspace. mkdir is
# the lock, so it works on macOS (no flock). A holder whose pid is gone is stale.
# $2 is how many seconds to wait for it (default 1800).
node_lock() {
  local name="$1" wait="${2:-1800}" root dir i=0 owner=""
  if [ -n "${WORKSPACE:-}" ]; then
    root="$(cd "$(dirname "$WORKSPACE")/.." && pwd)/.mac-k3d/locks"
  else
    root="${HOME}/.mac-k3d/locks"
  fi
  mkdir -p "$root"
  dir="$root/${name}.lock"
  while ! mkdir "$dir" 2>/dev/null; do
    owner="$(cat "$dir/owner" 2>/dev/null || true)"
    if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
      rm -rf "$dir"
      continue
    fi
    i=$((i + 1))
    [ "$i" -le $((wait / 2)) ] || die "timed out waiting for the node lock $name"
    sleep 2
  done
  echo $$ >"$dir/owner"
  NODE_LOCK_DIR="$dir"
}

node_unlock() {
  if [ -n "${NODE_LOCK_DIR:-}" ]; then
    rm -rf "$NODE_LOCK_DIR"
    NODE_LOCK_DIR=""
  fi
}

# Run each named step (stages/<phase>/<step>.sh) in its own process, in order.
run_steps() {
  local step
  for step in "$@"; do
    echo "== $step"
    bash "$PIPELINE_STAGES/$step.sh"
  done
}

# Docker daemon architecture in image terms (amd64 / arm64); empty if unknown.
docker_host_arch() {
  case "$(docker info --format '{{.Architecture}}' 2>/dev/null || true)" in
    x86_64 | amd64) echo amd64 ;;
    aarch64 | arm64) echo arm64 ;;
  esac
}

# linux/<arch> of the containers Docker runs natively. Docker Desktop on Apple
# silicon runs linux/arm64 in its VM; without a reachable daemon, the host CPU.
docker_host_platform() {
  local arch
  arch="$(docker_host_arch)"
  if [ -z "$arch" ]; then
    case "$(uname -m)" in
      aarch64 | arm64) arch=arm64 ;;
      *) arch=amd64 ;;
    esac
  fi
  printf 'linux/%s\n' "$arch"
}

# A task Dockerfile that downloads an x86-64-only build outside a comment.
dockerfile_needs_amd64() {
  [ -f "$1" ] || return 1
  grep -v '^[[:space:]]*#' "$1" \
    | grep -Eq 'x86_64-unknown-linux|linux-x64[.-]|linux-amd64|_amd64\.deb|arch=amd64\]'
}

# Platform of this build's task containers. SWE-bench Pro runs its prebuilt
# images, published for linux/amd64 only (Rosetta on Apple silicon).
# DeepSWE: DEEPSWE_DOCKER_PLATFORM when set; linux/amd64 on an x86-64 host (the
# published images); on an arm64 host the host platform only when tasks/images
# found a prepared, proven native build for every selected question
# ($WORKDIR/env_check.json), else linux/amd64 (the published images, emulated).
# LoLBench builds for the Docker host unless a selected Dockerfile downloads an
# x86-64-only build. An x86-64 host always gets linux/amd64.
task_container_platform() {
  local host tasks_dir tid settled
  case "${BENCHMARK:-deepswe}" in
    swebenchpro)
      printf '%s\n' "${SWEBENCHPRO_DOCKER_PLATFORM:-linux/amd64}"
      return 0
      ;;
    deepswe | "")
      if [ -n "${DEEPSWE_DOCKER_PLATFORM:-}" ]; then
        printf '%s\n' "$DEEPSWE_DOCKER_PLATFORM"
        return 0
      fi
      host="$(docker_host_platform)"
      settled=""
      if [ "$host" != linux/amd64 ]; then
        settled="$(env_check_platform)"
      fi
      printf '%s\n' "${settled:-linux/amd64}"
      return 0
      ;;
  esac
  host="$(docker_host_platform)"
  if [ "$host" != linux/amd64 ] && [ -s "$WORKDIR/selected_tasks.txt" ]; then
    tasks_dir="$(benchmark_tasks_dir)"
    while IFS= read -r tid || [ -n "$tid" ]; do
      case "$tid" in *[![:space:]]*) ;; *) continue ;; esac
      if dockerfile_needs_amd64 "$tasks_dir/$tid/environment/Dockerfile"; then
        printf '%s\n' linux/amd64
        return 0
      fi
    done <"$WORKDIR/selected_tasks.txt"
  fi
  printf '%s\n' "$host"
}

# The platform tasks/images settled for the selected DeepSWE questions on an
# arm64 host. Empty before tasks/images ran, or when env_check.json is for
# another selection.
env_check_platform() {
  [ -s "$WORKDIR/env_check.json" ] || return 0
  python3 "$PIPELINE_LIB/env_check.py" platform \
    --record "$WORKDIR/env_check.json" --selected "$WORKDIR/selected_tasks.txt" 2>/dev/null || true
}

# True when this build's task containers run emulated (another CPU type than Docker's).
task_platform_emulated() {
  [ "$(task_container_platform)" != "$(docker_host_platform)" ]
}

# Docker must run containers of the task platform: Rosetta (Docker Desktop on
# Apple silicon) or QEMU binfmt (Linux) for linux/amd64 on an arm64 host.
ensure_platform_runs() {
  local plat="$1" want got
  case "${plat#linux/}" in
    arm64 | aarch64) want=aarch64 ;;
    *) want=x86_64 ;;
  esac
  got="$(docker run --rm --platform "$plat" alpine:3.20 uname -m 2>/dev/null || true)"
  if [ "$got" != "$want" ]; then
    if [ "$(uname -s)" = Darwin ]; then
      die "Docker cannot run ${plat} containers (uname -m gave '${got:-nothing}'). In Docker Desktop > Settings > General turn on \"Use Rosetta for x86_64/amd64 emulation on Apple Silicon\", Apply & restart, then rerun."
    fi
    die "Docker cannot run ${plat} containers (uname -m gave '${got:-nothing}'). Install the QEMU handlers: docker run --privileged --rm tonistiigi/binfmt --install ${plat#linux/}"
  fi
  if [ "$(uname -s)" = Darwin ] && [ "$want" = x86_64 ] && docker_desktop_rosetta_off; then
    echo "WARNING: Docker Desktop runs ${plat} with QEMU, not Rosetta: slower and less stable. Turn on Docker Desktop > Settings > General > \"Use Rosetta for x86_64/amd64 emulation on Apple Silicon\"." >&2
  fi
  echo "OK Docker runs ${plat} containers"
}

# True when Docker Desktop's settings turn Rosetta off.
docker_desktop_rosetta_off() {
  local f
  for f in "$HOME/Library/Group Containers/group.com.docker/settings-store.json" \
    "$HOME/Library/Group Containers/group.com.docker/settings.json"; do
    [ -f "$f" ] || continue
    python3 - "$f" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if isinstance(doc, dict) and doc.get("UseVirtualizationFrameworkRosetta") is False else 1)
PY
    return
  done
  return 1
}

# $WORKDIR/container_platform.json for provenance: the task containers'
# platform, the one Docker runs natively, and whether they differ.
write_container_platform() {
  local want host
  want="$(task_container_platform)"
  host="$(docker_host_platform)"
  python3 - "$WORKDIR/container_platform.json" "$want" "$host" <<'PY'
import json, sys
out, want, host = sys.argv[1:4]
doc = {"container_platform": want, "docker_platform": host, "emulated": want != host}
with open(out, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(doc, indent=2) + "\n")
PY
  if [ "$want" = "$host" ]; then
    echo "OK task containers ${want} (native)"
  else
    echo "OK task containers ${want} (emulated on ${host})"
  fi
}

# LoLBench task image: local tag, else Harbor's leftover build, else docker build.
# A local tag for another architecture than the task containers' is rebuilt: the
# x86-64 iCode runtime cannot exec in an emulated arm64 image (no
# ld-linux-x86-64), and some Hub tags are arm64-only.
ensure_task_image() {
  local tid="$1" image="$2" env_dir="$3" leftover image_arch host_arch want_arch
  local -a platform=()
  host_arch="$(docker_host_arch)"
  want_arch="$host_arch"
  if [ -n "$host_arch" ]; then
    want_arch="$(task_container_platform)"
    want_arch="${want_arch#linux/}"
    [ "$want_arch" = "$host_arch" ] || platform=(--platform "linux/$want_arch")
  fi
  if docker image inspect "$image" >/dev/null 2>&1; then
    image_arch="$(docker image inspect "$image" --format '{{.Architecture}}' 2>/dev/null || true)"
    if [ -z "$image_arch" ] || [ -z "$want_arch" ] || [ "$image_arch" = "$want_arch" ]; then
      echo "images: using local image $image"
      return 0
    fi
    [ -f "$env_dir/Dockerfile" ] || die "local $image is $image_arch, this worker is $want_arch, and $env_dir/Dockerfile is missing"
    echo "images: local $image is $image_arch, this worker is $want_arch; docker build --progress=plain $image"
    docker build --progress=plain "${platform[@]}" -t "$image" "$env_dir"
    return 0
  fi
  leftover="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "^${tid}__.*__env-main" | head -n 1 || true)"
  if [ -n "$leftover" ]; then
    echo "images: retag $leftover -> $image (skip Harbor force build; it hangs after tagging)"
    docker tag "$leftover" "$image"
    return 0
  fi
  [ -f "$env_dir/Dockerfile" ] || die "missing $env_dir/Dockerfile and no local $image"
  echo "images: Hub tag is arm64-only; docker build --progress=plain $image"
  docker build --progress=plain "${platform[@]}" -t "$image" "$env_dir"
}

# A VPN tunnel (WireGuard etc.) with a smaller MTU than Docker's bridge black-holes
# large TLS replies: containers connect, then every HTTPS handshake times out
# (model API, canary hosts). Warns only; a host that clamps TCP MSS is fine.
warn_docker_mtu() {
  local dev host_mtu docker_mtu
  case "$(uname -s)" in
    Darwin)
      have route || return 0
      dev="$(route -n get 1.1.1.1 2>/dev/null | sed -n 's/^ *interface: *//p' | head -n 1 || true)"
      [ -n "$dev" ] || return 0
      host_mtu="$(ifconfig "$dev" 2>/dev/null | sed -n 's/.* mtu \([0-9]*\).*/\1/p' | head -n 1 || true)"
      ;;
    *)
      have ip || return 0
      dev="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n 1 || true)"
      [ -n "$dev" ] || return 0
      host_mtu="$(ip -o link show dev "$dev" 2>/dev/null | sed -n 's/.* mtu \([0-9]*\).*/\1/p' || true)"
      ;;
  esac
  docker_mtu="$(docker network inspect bridge --format '{{index .Options "com.docker.network.driver.mtu"}}' 2>/dev/null || true)"
  docker_mtu="${docker_mtu:-1500}"
  [ "$host_mtu" -lt "$docker_mtu" ] 2>/dev/null || return 0
  echo "WARNING: egress via $dev has MTU $host_mtu but Docker's bridge uses $docker_mtu; HTTPS from containers (model API, canary) will time out." >&2
  case "$(uname -s)" in
    Darwin)
      echo "WARNING: disconnect the VPN, or in Docker Desktop > Settings > Docker Engine add \"mtu\": $host_mtu and \"default-network-opts\": {\"bridge\": {\"com.docker.network.driver.mtu\": \"$host_mtu\"}}, then Apply & restart." >&2
      ;;
    *)
      echo "WARNING: disconnect the VPN, or set \"mtu\": $host_mtu and \"default-network-opts\": {\"bridge\": {\"com.docker.network.driver.mtu\": \"$host_mtu\"}} in /etc/docker/daemon.json and restart Docker." >&2
      ;;
  esac
}

# Isolation canary. Every scored run probes once before the rollouts.
# CANARY=only (pipeline/tools/canary.sh) probes every selected task and stops.
canary_mode() {
  local raw
  raw="$(printf '%s' "${CANARY:-on}" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    only) echo only ;;
    on) echo on ;;
    *) return 1 ;;
  esac
}

# A scored run cannot open an extra host. CANARY_ALLOW_HOST is the negative
# test in pipeline/tools/canary.sh (CANARY=only). Checked in env and again
# where the canary runs.
check_canary_settings() {
  local mode
  mode="$(canary_mode)" || die "CANARY must be on, or only for pipeline/tools/canary.sh (got ${CANARY:-})"
  if [ -n "${CANARY_ALLOW_HOST:-}" ] && [ "$mode" != only ]; then
    die "CANARY_ALLOW_HOST is only for pipeline/tools/canary.sh (CANARY=only); a scored run cannot open a host"
  fi
}

# Check out url at sha. A directory already at that commit is kept.
# Any other commit is fetched and force-detached so the pin cannot drift.
pin_benchmark_sha() {
  local dir="$1" url="$2" sha="$3" head
  [ -n "$dir" ] && [ -n "$url" ] && [ -n "$sha" ] || die "pin_benchmark_sha needs dir, url, and sha"
  if [ ! -d "$dir/.git" ]; then
    rm -rf "$dir"
    mkdir -p "$dir"
    git -c init.defaultBranch=main -c advice.defaultBranchName=false init -q "$dir"
    git -C "$dir" remote add origin "$url"
    GIT_TERMINAL_PROMPT=0 git -C "$dir" fetch --depth 1 origin "$sha"
    git -c advice.detachedHead=false -C "$dir" checkout --detach FETCH_HEAD
  else
    head="$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
    if [ "$head" != "$sha" ]; then
      echo "benchmark: $dir is at ${head:-unknown}; fetching $sha"
      GIT_TERMINAL_PROMPT=0 git -C "$dir" fetch --depth 1 origin "$sha"
      git -c advice.detachedHead=false -C "$dir" checkout --force --detach "$sha" \
        || git -c advice.detachedHead=false -C "$dir" checkout --force --detach FETCH_HEAD
    fi
  fi
  head="$(git -C "$dir" rev-parse HEAD)"
  [ "$head" = "$sha" ] || die "benchmark $dir HEAD $head does not match pin $sha"
}

missing_deepseek_key_hint() {
  if [ -n "${JENKINS_URL:-}" ] || [ -n "${BUILD_ID:-}" ] || [ -n "${WORKSPACE:-}" ]; then
    echo "DEEPSEEK_API_KEY missing. On the Jenkins controller store credential id deepseek-api-key (mac-k3d setup / config). Workers do not use a local .env."
  else
    echo "DEEPSEEK_API_KEY missing. Developer local run: copy .env.example to .env (gitignored), chmod 600. Workers: use Jenkins job deepswe_one_task, lolbench_one_task, or swebenchpro_one_task (controller credential). Do not export the key or paste it into chat."
  fi
}

_env_key_allowed() {
  case "$1" in
    DEEPSEEK_API_KEY | DEEPSEEK_MODEL | MAC_K3D_DEEPSEEK_API_KEY) return 0 ;;
    GITCODE_TOKEN | MAC_K3D_GITCODE_PAT | GITHUB_TOKEN | MAC_K3D_GITHUB_PAT | MAC_K3D_GIT_USERNAME) return 0 ;;
    *) return 1 ;;
  esac
}

# KEY=value only. Does not source the file as shell.
_apply_env_line() {
  local line="$1" key value
  [[ "$line" =~ ^[[:space:]]*# ]] && return 0
  [[ -z "${line//[[:space:]]/}" ]] && return 0
  [[ "$line" == *=* ]] || return 0
  key="${line%%=*}"
  key="${key#"${key%%[![:space:]]*}"}"
  key="${key%"${key##*[![:space:]]}"}"
  _env_key_allowed "$key" || return 0
  value="${line#*=}"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value#\"}"
    value="${value%\"}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value#\'}"
    value="${value%\'}"
  fi
  if [ -z "${!key:-}" ]; then
    export "${key}=${value}"
  fi
}

# If DEEPSEEK_API_KEY is already set (Jenkins / rare export), leave it.
# Else load the first existing allowlisted file. Never print secret values.
# Developer-only: workers should get the key from Jenkins credentials.
load_local_env() {
  local f mode
  local candidates=()
  [ -n "${MAC_K3D_ENV_FILE:-}" ] && candidates+=("$MAC_K3D_ENV_FILE")
  candidates+=("$ROOT/.env")
  candidates+=("${XDG_CONFIG_HOME:-$HOME/.config}/mac-k3d/.env")

  for f in "${candidates[@]}"; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    mode="$(stat -c '%a' "$f" 2>/dev/null || stat -f '%OLp' "$f" 2>/dev/null || echo "")"
    if [ -n "$mode" ] && [ "$mode" != "600" ] && [ "$mode" != "400" ]; then
      echo "WARNING: $f is mode $mode (expected 600). chmod 600 recommended." >&2
    fi
    while IFS= read -r line || [ -n "$line" ]; do
      _apply_env_line "$line"
    done <"$f"
    if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -n "${MAC_K3D_DEEPSEEK_API_KEY:-}" ]; then
      export DEEPSEEK_API_KEY="$MAC_K3D_DEEPSEEK_API_KEY"
    fi
    if [ -z "${GITCODE_TOKEN:-}" ] && [ -n "${MAC_K3D_GITCODE_PAT:-}" ]; then
      export GITCODE_TOKEN="$MAC_K3D_GITCODE_PAT"
    fi
    if [ -z "${GITHUB_TOKEN:-}" ] && [ -n "${MAC_K3D_GITHUB_PAT:-}" ]; then
      export GITHUB_TOKEN="$MAC_K3D_GITHUB_PAT"
    fi
    echo "OK loaded local env from $f (values not printed)"
    return 0
  done
}

load_local_env
export DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-flash}"

# What the agent talks to. tasks/isolation.sh checks this host against
# pipeline/config/network-allowlist-v1.json; the report records it.
export ICODE_MODEL="${ICODE_MODEL:-$DEEPSEEK_MODEL}"
export ICODE_API_BASE="${ICODE_API_BASE:-https://api.deepseek.com/v1}"
if [ -z "${ICODE_PROVIDER:-}" ]; then
  case "$ICODE_API_BASE" in
    *deepseek.com*) export ICODE_PROVIDER=DeepSeek ;;
    *) export ICODE_PROVIDER=OpenAI ;;
  esac
fi
export ICODE_REASONING_EFFORT="${ICODE_REASONING_EFFORT:-high}"
# Unset, iCode allows 8192 output tokens per reply, which cuts a long
# write_file call off mid-JSON. iCode caps iterations at 50 per continuation.
export ICODE_MAX_TOKENS="${ICODE_MAX_TOKENS:-65536}"
export ICODE_MAX_ITERATIONS="${ICODE_MAX_ITERATIONS:-500}"

# The iCode tree Harbor mounts read-only at /opt/icode-host (tasks/icode.sh).
icode_host_root() {
  local bin
  if [ -s "$WORKDIR/icode_host_root.txt" ]; then
    cat "$WORKDIR/icode_host_root.txt"
    return 0
  fi
  [ -s "$WORKDIR/icode_bin_path.txt" ] || die "no iCode in $WORKDIR; run the tasks phase first"
  bin="$(cat "$WORKDIR/icode_bin_path.txt")"
  (cd "$(dirname "$bin")" && pwd)
}

# The run folder report/render.sh wrote; the archive steps read it.
report_dir() {
  local dir
  dir="$(cat "$WORKDIR/report_dir.txt" 2>/dev/null || true)"
  [ -n "$dir" ] && [ -f "$dir/artifact.json" ] || die "no report in $WORKDIR; run the report phase first"
  printf '%s\n' "$dir"
}

# RAM the task containers can use, in kB. Linux containers share the host's
# RAM (/proc/meminfo). On macOS they run in Docker Desktop's VM, whose memory
# limit is below the host's; without a reachable daemon, sysctl hw.memsize.
host_mem_kb() {
  local bytes
  case "$(uname -s)" in
    Darwin)
      bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
      case "$bytes" in
        '' | *[!0-9]*) bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)" ;;
      esac
      echo $((bytes / 1024))
      ;;
    *) awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0 ;;
  esac
}

# Memory (GB) and disk (GB) fail-fast. The minimums come from toolchain.env
# (MIN_RAM_GB, WORKER_MIN_DISK_GB), the same values `mac-k3d setup` checks;
# MAC_K3D_MIN_RAM_GB / MAC_K3D_MIN_DISK_GB override them for one run.
ensure_eval_preflight() {
  local min_ram="${MAC_K3D_MIN_RAM_GB:-$MIN_RAM_GB}"
  local min_disk="${MAC_K3D_MIN_DISK_GB:-$WORKER_MIN_DISK_GB}"
  local mem_kb mem_gb disk_kb disk_gb check_path
  mem_kb="$(host_mem_kb)"
  if [ "${mem_kb:-0}" -gt 0 ]; then
    mem_gb=$((mem_kb / 1024 / 1024))
    if [ "$mem_gb" -lt "$min_ram" ]; then
      case "$(uname -s)" in
        Darwin) die "Docker Desktop has only ${mem_gb} GB memory; need at least ${min_ram} GB for eval (N=1). Raise Docker Desktop > Settings > Resources > Memory." ;;
        *) die "only ${mem_gb} GB RAM; need at least ${min_ram} GB for eval (N=1). Free memory or use a larger machine." ;;
      esac
    fi
    echo "OK RAM ${mem_gb} GB (min ${min_ram})"
  fi
  check_path="$WORKDIR"
  mkdir -p "$check_path"
  disk_kb="$(df -Pk "$check_path" 2>/dev/null | awk 'NR==2 {print $4}')"
  if [ -n "${disk_kb:-}" ] && [ "$disk_kb" -gt 0 ] 2>/dev/null; then
    disk_gb=$((disk_kb / 1024 / 1024))
    if [ "$disk_gb" -lt "$min_disk" ]; then
      die "only ${disk_gb} GB free at $check_path; need at least ${min_disk} GB. Free disk (docker system df) and keep N_TASKS=1."
    fi
    echo "OK disk ${disk_gb} GB free at $check_path (min ${min_disk})"
  fi
}

_icode_tree_ok() {
  [ -d "$1" ] || return 1
  [ -x "$1/.venv/bin/icode" ] || [ -f "$1/pyproject.toml" ]
}

_icode_gzip_magic() {
  local hex
  [ -f "$1" ] || return 1
  hex="$(od -An -tx1 -N2 "$1" 2>/dev/null | tr -d ' \n')"
  [ "$hex" = "1f8b" ]
}

_icode_ustar_magic() {
  [ -f "$1" ] || return 1
  dd if="$1" bs=1 skip=257 count=5 2>/dev/null | grep -q ustar
}

# Gzip/tar by suffix or magic (HTTP saves as icode-release.bin; browsers drop .tar.gz).
_icode_is_archive() {
  local base
  [ -f "$1" ] || return 1
  base="$(basename "$1")"
  case "$base" in
    *.tar.gz|*.tgz) return 0 ;;
  esac
  _icode_gzip_magic "$1" || _icode_ustar_magic "$1"
}

_icode_dir_has_icode() {
  local f
  [ -d "$1" ] || return 1
  [ -f "$1/icode" ] && return 0
  f="$(find "$1" -type f -name icode -print -quit 2>/dev/null || true)"
  [ -n "$f" ]
}

# Discoverable drop: icode file, archive, *-full-* file, or *-full-* dir with icode inside.
_icode_release_ok() {
  local p="$1" base
  [ -n "$p" ] || return 1
  if [ -d "$p" ]; then
    _icode_dir_has_icode "$p" || return 1
    [[ "$(basename "$p")" == *-full-* ]] || return 1
    return 0
  fi
  [ -f "$p" ] || return 1
  base="$(basename "$p")"
  [ "$base" = "icode" ] && return 0
  case "$base" in
    *.tar.gz|*.tgz) return 0 ;;
  esac
  [[ "$base" == *-full-* ]]
}

_icode_bin_or_tarball() {
  local p="$1"
  if _icode_release_ok "$p"; then
    echo "$p"
    return 0
  fi
  return 1
}

# Official user drop: icode-*-full-* first (the one named for this build's
# container CPU type when there are several), then a file named icode.
# Fallback /opt/mac-k3d/.
discover_icode_release() {
  local c d want="" first
  if [ -n "${ICODE_RELEASE:-}" ]; then
    echo "$ICODE_RELEASE"
    return 0
  fi
  if declare -F icode_drop_name_cpu >/dev/null 2>&1; then
    want="$(icode_platform_cpu "$(icode_target_platform)")"
  fi
  for d in "$MAC_K3D_SHARE" /opt/mac-k3d; do
    [ -d "$d" ] || continue
    first=""
    for c in "$d"/*-full-*.tar.gz "$d"/*.tgz "$d"/*full*.tar.gz "$d"/*-full-*; do
      [ -e "$c" ] || continue
      _icode_release_ok "$c" || continue
      if [ -n "$want" ] && [ "$(icode_drop_name_cpu "$c")" = "$want" ]; then
        echo "$c"
        return 0
      fi
      [ -n "$first" ] || first="$c"
    done
    if [ -n "$first" ]; then
      echo "$first"
      return 0
    fi
    if _icode_bin_or_tarball "$d/icode"; then
      return 0
    fi
  done
  return 1
}

# Jenkins File Parameter lands in the workspace as ICODE_RELEASE_FILE (original *-full-* name is lost).
icode_release_is_uploaded() {
  [ "${ICODE_RELEASE_UPLOADED:-}" = 1 ] && return 0
  [ "$(basename "${1:-}")" = "ICODE_RELEASE_FILE" ] && return 0
  return 1
}

# Copy or unpack ICODE_RELEASE into $2. Explicit dirs need a file named icode (any folder name).
# Local CLI keeps named-path rules (icode or *-full-*). Jenkins uploads use gzip/tar magic, else copy as icode.
install_icode_release() {
  local src="$1" unpack="$2" base
  [ -e "$src" ] || die "ICODE_RELEASE not found: $src"
  if [ -d "$src" ]; then
    _icode_dir_has_icode "$src" || die "ICODE_RELEASE directory has no file named icode: $src"
    cp -a "$src"/. "$unpack"/
    return 0
  fi
  [ -f "$src" ] || die "ICODE_RELEASE not found: $src"
  if _icode_is_archive "$src"; then
    if icode_release_is_uploaded "$src"; then
      echo "Jenkins upload ICODE_RELEASE_FILE (archive magic): $src" >&2
    fi
    if _icode_gzip_magic "$src" || [[ "$(basename "$src")" == *.tar.gz || "$(basename "$src")" == *.tgz ]]; then
      tar -xzf "$src" -C "$unpack"
    else
      tar -xf "$src" -C "$unpack"
    fi
    return 0
  fi
  base="$(basename "$src")"
  if icode_release_is_uploaded "$src"; then
    echo "Jenkins upload (unnamed) as icode binary: $src" >&2
    cp "$src" "$unpack/icode"
    chmod +x "$unpack/icode" || true
    return 0
  fi
  if [ "$base" != "icode" ] && [[ "$base" != *-full-* ]]; then
    die "ICODE_RELEASE is not an icode binary or *-full-* release: $src"
  fi
  cp "$src" "$unpack/icode"
  chmod +x "$unpack/icode" || true
}

# ICODE_RELEASE stays empty unless the job/CLI set it.
# get_release_icode then reads icode-paths.yaml and share discover (local).
# Jenkins release sets ICODE_RELEASE_UPLOADED=1 and points at WORKSPACE/ICODE_RELEASE_FILE.
export ICODE_RELEASE="${ICODE_RELEASE:-}"
export ICODE_RELEASE_UPLOADED="${ICODE_RELEASE_UPLOADED:-}"

# DeepSWE: eval-runs/deep-swe/tasks/<id>
# LoLBench: eval-runs/lolbench/harbor_tasks/<id>
# SWE-bench Pro: eval-runs/swebenchpro/tasks/<instance_id>
benchmark_tasks_dir() {
  case "${BENCHMARK:-deepswe}" in
    lolbench) echo "$LOLBENCH_DIR/harbor_tasks" ;;
    swebenchpro) echo "$SWEBENCHPRO_DIR/tasks" ;;
    deepswe | "") echo "$DEEPSWE_DIR/tasks" ;;
    *) die "unknown BENCHMARK=${BENCHMARK} (use deepswe, lolbench, or swebenchpro)" ;;
  esac
}

# One question id per line from a comma-separated string (trim blanks).
task_ids_from_csv() {
  printf '%s' "$1" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | awk 'NF'
}

# Explicit list: TASKS, or TASK when it contains commas.
selected_tasks_csv_source() {
  if [ -n "${TASKS:-}" ]; then
    printf '%s' "$TASKS"
    return 0
  fi
  case "${TASK:-}" in
    *,*) printf '%s' "$TASK" ;;
  esac
}

write_selected_tasks() {
  local list="$WORKDIR/selected_tasks.txt" suite="$WORKDIR/suite_tasks.txt" n root tid csv skip
  root="$(benchmark_tasks_dir)"
  [ -d "$root" ] || die "run the tasks phase first (missing $root)"
  # Byte order, so every worker cuts the same offset slices whatever its locale;
  # the dispatcher's merge names a failed shard's questions from this list.
  find "$root" -mindepth 1 -maxdepth 1 -type d | sed 's#.*/##' | LC_ALL=C sort >"$suite"
  csv="$(selected_tasks_csv_source || true)"
  if [ -n "$csv" ]; then
    : >"$list"
    while IFS= read -r tid; do
      [ -n "$tid" ] || continue
      [ -d "$root/$tid" ] || die "task id '$tid' not found under $root"
      printf '%s\n' "$tid" >>"$list"
    done <<EOF
$(task_ids_from_csv "$csv")
EOF
    [ -s "$list" ] || die "TASKS/TASK list is empty"
    export N_TASKS
    N_TASKS="$(grep -c . "$list" || true)"
    return 0
  fi
  if [ -n "${TASK:-}" ]; then
    tid="$(printf '%s' "$TASK" | tr -d '[:space:]')"
    [ -n "$tid" ] || die "TASK is empty"
    [ -d "$root/$tid" ] || die "TASK=$tid not found under $root"
    printf '%s\n' "$tid" >"$list"
    export N_TASKS=1
    return 0
  fi
  n="${N_TASKS:-1}"
  # TASK_OFFSET lets the full-suite dispatcher hand each shard a disjoint slice
  # of the same sorted id list without naming every id on the command line.
  skip="${TASK_OFFSET:-0}"
  case "$skip" in
    "" | *[!0-9]*) die "TASK_OFFSET must be an integer >= 0 (got '$skip')" ;;
  esac
  tail -n "+$((skip + 1))" "$suite" | head -n "$n" >"$list"
  [ -s "$list" ] || die "no tasks to select under $root (N_TASKS=$n TASK_OFFSET=$skip)"
}

ensure_selected_tasks() {
  local list="$WORKDIR/selected_tasks.txt" stamp="$WORKDIR/selected_tasks_offset.txt" have_n want csv
  csv="$(selected_tasks_csv_source || true)"
  # A reused workspace can hold another shard's slice of the same size.
  if [ "$(cat "$stamp" 2>/dev/null || echo 0)" != "${TASK_OFFSET:-0}" ]; then
    rm -f "$list"
    printf '%s\n' "${TASK_OFFSET:-0}" >"$stamp"
  fi
  if [ -n "$csv" ]; then
    want="$(task_ids_from_csv "$csv" | grep -c . || true)"
    if [ -f "$list" ]; then
      have_n="$(grep -c . "$list" || true)"
      if [ "$have_n" = "$want" ] && [ "$(cat "$list")" = "$(task_ids_from_csv "$csv")" ]; then
        return 0
      fi
    fi
    write_selected_tasks
    return 0
  fi
  if [ -n "${TASK:-}" ]; then
    want=1
  else
    want="${N_TASKS:-1}"
  fi
  if [ -f "$list" ]; then
    have_n="$(grep -c . "$list" || true)"
    if [ "$have_n" = "$want" ]; then
      if [ -z "${TASK:-}" ]; then
        return 0
      fi
      if [ "$(tr -d '[:space:]' <"$list")" = "$(printf '%s' "$TASK" | tr -d '[:space:]')" ]; then
        return 0
      fi
    fi
  fi
  write_selected_tasks
}
