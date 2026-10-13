#!/usr/bin/env bash
# tasks/images: the image each selected question runs on. Runs before
# tasks/icode, which picks the iCode build for the platform settled here.
#
#   LoLBench: the task image for this worker's architecture.
#   DeepSWE on an x86-64 host: nothing to do; Harbor pulls the published
#     linux/amd64 images at the trial.
#   DeepSWE on an arm64 host: a question runs natively on the build that
#     `mac-k3d eval prepare --benchmark deepswe` made and proved with Harbor's
#     oracle and nop agents (pipeline/lib/env_check.py). Every other question,
#     and every question of a run that has one, runs the published image under
#     emulation, with the reason in env_check.json. An eval never builds, pulls
#     or checks; MAC_K3D_DEEPSWE_PREPARE=1 (the prepare engine) does.
#     DEEPSWE_DOCKER_PLATFORM forces one platform.
#   SWE-bench Pro: Harbor pulls the published images.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"
# shellcheck source=../evaluate/harbor_cmd.sh
source "$PIPELINE_STAGES/evaluate/harbor_cmd.sh"

TASKS_DIR="$(benchmark_tasks_dir)"
ensure_selected_tasks
TASK_IDS=()
while IFS= read -r tid || [ -n "$tid" ]; do
  case "$tid" in *[![:space:]]*) TASK_IDS+=("$tid") ;; esac
done <"$WORKDIR/selected_tasks.txt"
for tid in ${TASK_IDS[@]+"${TASK_IDS[@]}"}; do
  [ -d "$TASKS_DIR/$tid" ] || die "no task under $TASKS_DIR (wanted $tid)"
done
rm -f "$WORKDIR/env_check.json"

ENV_CHECK_DIR="$WORKDIR/env-check"
ENV_CHECK_STORE="$MAC_K3D_SHARE/env-check/${BENCHMARK:-deepswe}"
BUILD_RECORD="$WORKDIR/env_check.json"
NATIVE_REPO="mac-k3d/deepswe-native"
# Every DeepSWE Dockerfile starts FROM this name; the build swaps in the pin.
DEEPSWE_BASE_FROM="${DEEPSWE_BASE_IMAGE%@*}:latest"
PREPARE="${MAC_K3D_DEEPSWE_PREPARE:-0}"
# Prepare holds the lock for one question: a build, then two test runs.
IMAGES_LOCK_WAIT="${MAC_K3D_IMAGES_LOCK_WAIT:-10800}"
HOST_PLATFORM=""
NETWORK_READY=0
WHY=""
HINT=0
VERDICT=""

lolbench_images() {
  local tid image
  node_lock images
  for tid in "${TASK_IDS[@]}"; do
    image="$(
      python3 - "$TASKS_DIR/$tid/task.toml" "$tid" <<'PY'
import re, sys
path, tid = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read() if path else ""
m = re.search(r'^docker_image\s*=\s*"([^"]+)"', text, re.M)
print(m.group(1) if m else f"smartdub26/lolbench:{tid}-1.0.0")
PY
    )"
    ensure_task_image "$tid" "$image" "$TASKS_DIR/$tid/environment"
  done
  node_unlock
}

image_field() {
  docker image inspect --format "$2" "$1" 2>/dev/null || true
}

file_sha256() {
  python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
}

# Why question $1 cannot be built natively on the pinned base; nothing when it can.
native_blocker() {
  local dockerfile="$TASKS_DIR/$1/environment/Dockerfile" from
  if [ ! -f "$dockerfile" ]; then
    echo "it has no environment/Dockerfile"
    return 0
  fi
  if dockerfile_needs_amd64 "$dockerfile"; then
    echo "its Dockerfile downloads an x86-64-only build"
    return 0
  fi
  from="$(grep -iE '^[[:space:]]*FROM[[:space:]]' "$dockerfile" | awk '{print $2}' | sort -u)"
  if [ "$from" != "$DEEPSWE_BASE_FROM" ]; then
    echo "its Dockerfile is not FROM $DEEPSWE_BASE_FROM ($(printf '%s' "${from:-no FROM}" | tr '\n' ' '))"
  fi
}

# The native build of question $1 exists and was built from its Dockerfile on the pinned base.
native_current() {
  local native="$NATIVE_REPO:$1"
  [ -n "$(image_field "$native" '{{.Id}}')" ] \
    && [ "$(image_field "$native" '{{index .Config.Labels "mac-k3d.dockerfile-sha256"}}')" \
      = "$(file_sha256 "$TASKS_DIR/$1/environment/Dockerfile")" ] \
    && [ "$(image_field "$native" '{{index .Config.Labels "mac-k3d.base-image"}}')" = "$DEEPSWE_BASE_IMAGE" ]
}

# Point the official name $2 at the native build of question $1.
claim_official() {
  local native="$NATIVE_REPO:$1" official="$2"
  if [ "$(image_field "$official" '{{.Id}}')" != "$(image_field "$native" '{{.Id}}')" ]; then
    docker tag "$native" "$official" || die "docker tag $native $official failed"
  fi
}

# Take the official name $2 off a native build of question $1, so Harbor uses
# (or pulls) the published linux/amd64 image. docker image rm only removes the tag.
release_official() {
  local tid="$1" official="$2" id
  id="$(image_field "$official" '{{.Id}}')"
  [ -n "$id" ] || return 0
  if [ "$id" = "$(image_field "$NATIVE_REPO:$tid" '{{.Id}}')" ] \
    || [ "$(image_field "$official" '{{.Architecture}}')" != amd64 ]; then
    echo "images: $official names a native build; untagging it so Harbor uses the published image" >&2
    docker image rm "$official" >/dev/null || die "docker image rm $official failed"
  fi
}

# Question $1 (official name $2) runs on the published image, emulated; the
# rest is env_check.py emulated's --reason/--verdict/--hint.
run_emulated() {
  local tid="$1" official="$2"
  shift 2
  release_official "$tid" "$official"
  python3 "$PIPELINE_LIB/env_check.py" emulated --build-record "$BUILD_RECORD" --task "$tid" \
    --image "$official" "$@" || die "env check record failed for $tid"
}

# The env-check key of native build $2 of question $1, in $3.
native_key() {
  python3 "$PIPELINE_LIB/env_check.py" key --task-dir "$TASKS_DIR/$1" --source built \
    --platform "$HOST_PLATFORM" --image-id "$2" --base-image "$DEEPSWE_BASE_IMAGE" --out "$3" \
    || die "env check key failed for $1"
}

# The stored verdict for native build $2 of question $1, in $ENV_CHECK_DIR/$1:
# exit 0 for a pass, 2 for a fail, 1 when none matches.
native_cached() {
  local tid="$1" dir="$ENV_CHECK_DIR/$1" rc
  mkdir -p "$dir"
  native_key "$tid" "$2" "$dir/built-key.json"
  set +e
  python3 "$PIPELINE_LIB/env_check.py" cached --store "$ENV_CHECK_STORE" --task "$tid" \
    --key "$dir/built-key.json" --out "$dir/built-verdict.json"
  rc=$?
  set -e
  case "$rc" in
    0 | 1 | 2) return "$rc" ;;
    *) die "env check cache lookup failed for $tid (exit $rc)" ;;
  esac
}

# This build's native entry for question $1, from $ENV_CHECK_DIR/$1; the rest
# is env_check.py record's --image/--reused. Exit 1 when the check failed.
record_native() {
  local tid="$1" dir="$ENV_CHECK_DIR/$1"
  shift
  python3 "$PIPELINE_LIB/env_check.py" record --store "$ENV_CHECK_STORE" --task "$tid" \
    --key "$dir/built-key.json" --verdict "$dir/built-verdict.json" --build-record "$BUILD_RECORD" "$@"
}

# Eval: 0 when question $1 has a current native build with a stored pass. Else
# 1, with WHY, and HINT=1 when mac-k3d eval prepare would fix it, or VERDICT,
# the stored fail.
native_ready() {
  local tid="$1" id rc
  WHY="" HINT=0 VERDICT=""
  WHY="$(native_blocker "$tid")"
  [ -z "$WHY" ] || return 1
  id="$(image_field "$NATIVE_REPO:$tid" '{{.Id}}')"
  if [ -z "$id" ]; then
    WHY="not prepared on this Mac" HINT=1
    return 1
  fi
  if ! native_current "$tid"; then
    WHY="prepared for another Dockerfile or base pin" HINT=1
    return 1
  fi
  rc=0
  native_cached "$tid" "$id" || rc=$?
  case "$rc" in
    0) return 0 ;;
    2) VERDICT="$ENV_CHECK_DIR/$tid/built-verdict.json" ;;
    *) WHY="prepared for other task files or another Harbor version (now ${HARBOR_VERSION:-unknown})" HINT=1 ;;
  esac
  return 1
}

# Prepare: the native build of question $1, built when missing, out of date or
# DEEPSWE_REBUILD=1; prints its image id.
use_built() {
  local tid="$1" native="$NATIVE_REPO:$1" log
  log="$ENV_CHECK_DIR/$tid/build.log"
  mkdir -p "$ENV_CHECK_DIR/$tid"
  if [ "${DEEPSWE_REBUILD:-0}" != 1 ] && native_current "$tid"; then
    echo "images: using native build $native" >&2
  else
    echo "images: docker build $native for $HOST_PLATFORM on $DEEPSWE_BASE_IMAGE (log $log)" >&2
    if ! docker build --progress=plain \
      --build-context "$DEEPSWE_BASE_FROM=docker-image://$DEEPSWE_BASE_IMAGE" \
      --label "mac-k3d.dockerfile-sha256=$(file_sha256 "$TASKS_DIR/$tid/environment/Dockerfile")" \
      --label "mac-k3d.base-image=$DEEPSWE_BASE_IMAGE" \
      -t "$native" "$TASKS_DIR/$tid/environment" >"$log" 2>&1; then
      tail -n 20 "$log" >&2
      return 1
    fi
  fi
  image_field "$native" '{{.Id}}'
}

# Harbor's network setup for the check's trials, once per build: the egress
# probe and trial subnets the canary and the rollouts use. No model, no key.
check_network() {
  [ "$NETWORK_READY" = 0 ] || return 0
  NETWORK_READY=1
  have harbor || die "harbor not on PATH (run the env phase)"
  PROTOCOL_INPUTS="$ENV_CHECK_DIR/protocol_inputs.json"
  HARBOR_ENV="$ENV_CHECK_DIR/.harbor-env"
  EVAL_SLOTS=1
  ensure_harbor_egress
  ensure_trial_network
  teardown_on_exit "$ENV_CHECK_DIR"
  python3 "$PIPELINE_LIB/trial_network.py" owner --out "$ENV_CHECK_DIR/owner.json" || true
}

# One native Harbor trial of question $1 with agent $2 (OracleAgent or NopAgent), jobs under $3.
run_check_agent() {
  local tid="$1" agent="$2" jobs="$3" rc name
  name="$(printf '%s' "${agent%Agent}" | tr '[:upper:]' '[:lower:]')"
  check_network
  rm -rf "$jobs"
  mkdir -p "$jobs"
  build_env_check_cmd "$tid" "$agent" "$jobs"
  echo "env check: $tid $name on $HOST_PLATFORM (log $unit_log)"
  set +e
  RUN_PLATFORM="$HOST_PLATFORM" run_cmd >"$unit_log" 2>&1
  rc=$?
  set -e
  [ "$rc" = 0 ] || echo "WARNING: harbor run exited $rc for the $name check of $tid (see $unit_log)"
  python3 "$PIPELINE_LIB/trial_network.py" teardown --jobs-dir "$jobs" || true
}

# Prepare: prove native build $3 of question $1, now under the official name
# $2: the stored verdict when its key matches, else an oracle trial and, when
# that passes, a nop trial. Exit 1 when it fails.
check_native() {
  local tid="$1" official="$2" id="$3" dir="$ENV_CHECK_DIR/$1" rc=0
  native_cached "$tid" "$id" || rc=$?
  if [ "$rc" = 0 ] || [ "$rc" = 2 ]; then
    record_native "$tid" --image "$official" --reused
    return
  fi
  if harbor_dry_run; then
    build_env_check_cmd "$tid" OracleAgent "$dir/built-oracle"
    echo "env check dry-run (cwd $unit_run_dir): $(masked_cmd)"
    return 0
  fi
  run_check_agent "$tid" OracleAgent "$dir/built-oracle"
  if python3 "$PIPELINE_LIB/env_check.py" judge --oracle-only --oracle "$dir/built-oracle" --key "$dir/built-key.json"; then
    run_check_agent "$tid" NopAgent "$dir/built-nop"
  fi
  python3 "$PIPELINE_LIB/env_check.py" judge --oracle "$dir/built-oracle" --nop "$dir/built-nop" \
    --key "$dir/built-key.json" --out "$dir/built-verdict.json" || true
  record_native "$tid" --image "$official"
}

# Prepare: build question $1 natively, give it the official name $2, and prove it.
prepare_question() {
  local tid="$1" official="$2" id why
  why="$(native_blocker "$tid")"
  if [ -n "$why" ]; then
    run_emulated "$tid" "$official" --reason "$why"
    return 0
  fi
  if ! id="$(use_built "$tid")"; then
    run_emulated "$tid" "$official" --reason "native build failed (see $ENV_CHECK_DIR/$tid/build.log)" --hint
    return 0
  fi
  claim_official "$tid" "$official"
  if ! check_native "$tid" "$official" "$id"; then
    run_emulated "$tid" "$official" --verdict "$ENV_CHECK_DIR/$tid/built-verdict.json"
  fi
}

official_image() {
  python3 "$PIPELINE_LIB/env_check.py" image --task-dir "$TASKS_DIR/$1" || die "no docker_image for $1"
}

# Eval: every selected question native on its prepared build, or every one
# emulated (one iCode build serves the run).
choose_images() {
  local tid official forced="${DEEPSWE_DOCKER_PLATFORM:-}"
  local -a ready=() emulated=()
  if [ -n "$forced" ] && [ "$forced" != "$HOST_PLATFORM" ] && [ "$forced" != linux/amd64 ]; then
    die "DEEPSWE_DOCKER_PLATFORM must be linux/amd64 or this host's $HOST_PLATFORM (got $forced)"
  fi
  for tid in "${TASK_IDS[@]}"; do
    official="$(official_image "$tid")"
    if [ "$forced" = linux/amd64 ]; then
      run_emulated "$tid" "$official" --reason "DEEPSWE_DOCKER_PLATFORM=linux/amd64"
      emulated+=("$tid")
    elif native_ready "$tid"; then
      ready+=("$tid")
    elif [ -n "$VERDICT" ]; then
      run_emulated "$tid" "$official" --verdict "$VERDICT"
      emulated+=("$tid: native check failed")
    elif [ "$HINT" = 1 ]; then
      run_emulated "$tid" "$official" --reason "$WHY" --hint
      emulated+=("$tid: $WHY")
    else
      run_emulated "$tid" "$official" --reason "$WHY"
      emulated+=("$tid: $WHY")
    fi
  done
  if [ "${#emulated[@]}" -gt 0 ] && [ "$forced" = "$HOST_PLATFORM" ]; then
    die "DEEPSWE_DOCKER_PLATFORM=$forced, but these questions have no proven native build: $(printf '%s; ' "${emulated[@]}")"
  fi
  for tid in ${ready[@]+"${ready[@]}"}; do
    official="$(official_image "$tid")"
    if [ "${#emulated[@]}" -gt 0 ]; then
      run_emulated "$tid" "$official" --reason "another selected question runs emulated (one iCode build per run)"
    else
      claim_official "$tid" "$official"
      record_native "$tid" --image "$official" --reused
    fi
  done
}

deepswe_images() {
  local tid
  HOST_PLATFORM="$(docker_host_platform)"
  if [ "$HOST_PLATFORM" = linux/amd64 ]; then
    echo "images: $HOST_PLATFORM host; Harbor pulls the published DeepSWE images"
    return 0
  fi
  [ -n "${DEEPSWE_BASE_IMAGE:-}" ] || die "DEEPSWE_BASE_IMAGE missing from $TOOLCHAIN_ENV"
  if [ "$PREPARE" = 1 ]; then
    # The engine runs one question at a time; earlier questions' build logs stay.
    for tid in "${TASK_IDS[@]}"; do
      rm -rf "${ENV_CHECK_DIR:?}/$tid"
    done
  else
    rm -rf "$ENV_CHECK_DIR"
  fi
  mkdir -p "$ENV_CHECK_DIR" "$ENV_CHECK_STORE"
  node_lock images "$IMAGES_LOCK_WAIT"
  if [ "$PREPARE" = 1 ]; then
    echo "images: $HOST_PLATFORM host, native builds on $DEEPSWE_BASE_IMAGE; checks stored under $ENV_CHECK_STORE"
    for tid in "${TASK_IDS[@]}"; do
      prepare_question "$tid" "$(official_image "$tid")"
    done
  else
    echo "images: $HOST_PLATFORM host, prepared native builds (checks under $ENV_CHECK_STORE)"
    choose_images
  fi
  node_unlock
  if harbor_dry_run && [ "$PREPARE" = 1 ]; then
    return 0
  fi
  python3 "$PIPELINE_LIB/env_check.py" settle --build-record "$BUILD_RECORD" \
    --selected "$WORKDIR/selected_tasks.txt" --host-platform "$HOST_PLATFORM" \
    || die "environment bookkeeping failed (see $BUILD_RECORD)"
}

case "${BENCHMARK:-deepswe}" in
  lolbench) lolbench_images ;;
  deepswe | "") deepswe_images ;;
esac
echo "OK task images ready for $BENCHMARK"
