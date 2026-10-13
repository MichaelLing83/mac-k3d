#!/usr/bin/env bash
# tasks/images: an image this worker can run for every selected question, and
# for DeepSWE the proof that it works. Runs before tasks/icode, which picks the
# iCode build for the platform settled here.
#
#   LoLBench: the task image for this worker's architecture.
#   DeepSWE: the published image on an x86-64 host. On an arm64 host, a native
#     build of the task Dockerfile on the pinned base (DEEPSWE_BASE_IMAGE), or
#     the published image, emulated, when the Dockerfile downloads an
#     x86-64-only build or the native build fails its check.
#     DEEPSWE_DOCKER_PLATFORM forces one platform, with no fallback. Each
#     question's image is checked once per machine with Harbor's oracle and nop
#     agents (pipeline/lib/env_check.py); a question with no proven image is
#     listed in skipped_tasks.txt and kept out of Harbor.
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
# This build's lists; tasks/leakscan appends its hits after the check's.
rm -f "$WORKDIR/skipped_tasks.txt" "$WORKDIR/env_check.json"

ENV_CHECK_DIR="$WORKDIR/env-check"
ENV_CHECK_STORE="$MAC_K3D_SHARE/env-check/${BENCHMARK:-deepswe}"
BUILD_RECORD="$WORKDIR/env_check.json"
NATIVE_REPO="mac-k3d/deepswe-native"
# Every DeepSWE Dockerfile starts FROM this name; the build swaps in the pin.
DEEPSWE_BASE_FROM="${DEEPSWE_BASE_IMAGE%@*}:latest"
# A first-time check holds the lock for one question: a build, then two test runs.
IMAGES_LOCK_WAIT="${MAC_K3D_IMAGES_LOCK_WAIT:-10800}"
HOST_PLATFORM=""
NETWORK_READY=0
EMULATION_READY=0

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

# The official name on the published linux/amd64 image: a native build is
# untagged (docker image rm only removes the tag), a missing image pulled.
use_published() {
  local tid="$1" official="$2" id
  id="$(image_field "$official" '{{.Id}}')"
  if [ -n "$id" ] && { [ "$id" = "$(image_field "$NATIVE_REPO:$tid" '{{.Id}}')" ] \
    || [ "$(image_field "$official" '{{.Architecture}}')" != amd64 ]; }; then
    echo "images: $official names a native build; untagging it" >&2
    docker image rm "$official" >/dev/null || return 1
    id=""
  fi
  if [ -z "$id" ]; then
    echo "images: docker pull --platform linux/amd64 $official" >&2
    docker pull -q --platform linux/amd64 "$official" >&2 || return 1
    id="$(image_field "$official" '{{.Id}}')"
  fi
  [ -n "$id" ] || return 1
  printf '%s\n' "$id"
}

# A native build of question $1 on the pinned base. Rebuilt only when the
# Dockerfile or the pin changed. check_question tags it with the official name.
use_built() {
  local tid="$1" official="$2" plat="$3" native="$NATIVE_REPO:$1" want log
  local -a platform=()
  log="$ENV_CHECK_DIR/$tid/build.log"
  mkdir -p "$ENV_CHECK_DIR/$tid"
  want="$(file_sha256 "$TASKS_DIR/$tid/environment/Dockerfile")"
  [ "$plat" = "$HOST_PLATFORM" ] || platform=(--platform "$plat")
  if [ "$(image_field "$native" '{{index .Config.Labels "mac-k3d.dockerfile-sha256"}}')" = "$want" ] \
    && [ "$(image_field "$native" '{{index .Config.Labels "mac-k3d.base-image"}}')" = "$DEEPSWE_BASE_IMAGE" ]; then
    echo "images: using native build $native" >&2
  else
    echo "images: docker build $native for $plat on $DEEPSWE_BASE_IMAGE (log $log)" >&2
    if ! docker build --progress=plain "${platform[@]}" \
      --build-context "$DEEPSWE_BASE_FROM=docker-image://$DEEPSWE_BASE_IMAGE" \
      --label "mac-k3d.dockerfile-sha256=$want" \
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

# One Harbor trial of question $1 with agent $2 (OracleAgent or NopAgent) on platform $3.
run_check_agent() {
  local tid="$1" agent="$2" plat="$3" jobs="$4" rc name
  name="${agent%Agent}"
  name="${name,,}"
  if [ "$plat" != "$HOST_PLATFORM" ] && [ "$EMULATION_READY" = 0 ]; then
    ensure_platform_runs "$plat"
    EMULATION_READY=1
  fi
  check_network
  rm -rf "$jobs"
  mkdir -p "$jobs"
  build_env_check_cmd "$tid" "$agent" "$jobs"
  echo "env check: $tid $name on $plat (log $unit_log)"
  set +e
  RUN_PLATFORM="$plat" run_cmd >"$unit_log" 2>&1
  rc=$?
  set -e
  [ "$rc" = 0 ] || echo "WARNING: harbor run exited $rc for the $name check of $tid (see $unit_log)"
  python3 "$PIPELINE_LIB/trial_network.py" teardown --jobs-dir "$jobs" || true
}

# Prove image $5 of question $1 from source $2 on platform $3: the stored
# verdict when its key matches, else an oracle trial and, when that passes, a
# nop trial. $6, a native build, gets the official name $4 only when it is
# checked or proven: other builds of this question may be running on it. Exit
# 1 when the image fails.
check_question() {
  local tid="$1" source="$2" plat="$3" official="$4" id="$5" tag="${6:-}" dir key verdict hit
  local -a base=() reused=()
  dir="$ENV_CHECK_DIR/$tid"
  key="$dir/$source-key.json"
  verdict="$dir/$source-verdict.json"
  mkdir -p "$dir"
  [ "$source" != built ] || base=(--base-image "$DEEPSWE_BASE_IMAGE")
  python3 "$PIPELINE_LIB/env_check.py" key --task-dir "$TASKS_DIR/$tid" --source "$source" \
    --platform "$plat" --image-id "$id" "${base[@]}" --out "$key" || die "env check key failed for $tid"
  set +e
  python3 "$PIPELINE_LIB/env_check.py" cached --store "$ENV_CHECK_STORE" --task "$tid" \
    --key "$key" --out "$verdict"
  hit=$?
  set -e
  if [ "$hit" = 0 ] || [ "$hit" = 2 ]; then
    reused=(--reused)
    if [ "$hit" = 0 ] && [ -n "$tag" ]; then
      docker tag "$tag" "$official" || die "docker tag $tag $official failed"
    fi
  elif [ "$hit" != 1 ]; then
    die "env check cache lookup failed for $tid (exit $hit)"
  elif harbor_dry_run; then
    build_env_check_cmd "$tid" OracleAgent "$dir/$source-oracle"
    echo "env check dry-run (cwd $unit_run_dir): $(masked_cmd)"
    return 0
  else
    if [ -n "$tag" ]; then
      docker tag "$tag" "$official" || die "docker tag $tag $official failed"
    fi
    run_check_agent "$tid" OracleAgent "$plat" "$dir/$source-oracle"
    if python3 "$PIPELINE_LIB/env_check.py" judge --oracle-only --oracle "$dir/$source-oracle" --key "$key"; then
      run_check_agent "$tid" NopAgent "$plat" "$dir/$source-nop"
    fi
    python3 "$PIPELINE_LIB/env_check.py" judge --oracle "$dir/$source-oracle" --nop "$dir/$source-nop" \
      --key "$key" --out "$verdict" || true
  fi
  python3 "$PIPELINE_LIB/env_check.py" record --store "$ENV_CHECK_STORE" --task "$tid" --key "$key" \
    --verdict "$verdict" --build-record "$BUILD_RECORD" --image "$official" "${reused[@]}"
}

# Question $1 has no image from source $2 to check: record why. Not stored, so
# the next build tries again. Always exit 1.
image_failed() {
  local tid="$1" source="$2" plat="$3" official="$4" why="$5" dir="$ENV_CHECK_DIR/$1"
  local -a base=()
  mkdir -p "$dir"
  [ "$source" != built ] || base=(--base-image "$DEEPSWE_BASE_IMAGE")
  python3 "$PIPELINE_LIB/env_check.py" key --task-dir "$TASKS_DIR/$tid" --source "$source" \
    --platform "$plat" --image-id "" "${base[@]}" --out "$dir/$source-key.json" || die "env check key failed for $tid"
  python3 "$PIPELINE_LIB/env_check.py" judge --failed "$why" --key "$dir/$source-key.json" \
    --out "$dir/$source-verdict.json" || [ -s "$dir/$source-verdict.json" ] || die "env check judge failed for $tid"
  python3 "$PIPELINE_LIB/env_check.py" record --store "$ENV_CHECK_STORE" --task "$tid" \
    --key "$dir/$source-key.json" --verdict "$dir/$source-verdict.json" \
    --build-record "$BUILD_RECORD" --image "$official" || true
  return 1
}

prove_published() {
  local tid="$1" official="$2" id
  if ! id="$(use_published "$tid" "$official")"; then
    image_failed "$tid" published linux/amd64 "$official" "docker pull of $official failed"
    return 1
  fi
  check_question "$tid" published linux/amd64 "$official" "$id"
}

prove_built() {
  local tid="$1" official="$2" plat="$3" id
  if ! id="$(use_built "$tid" "$official" "$plat")"; then
    image_failed "$tid" built "$plat" "$official" "native build failed (see $ENV_CHECK_DIR/$tid/build.log)"
    return 1
  fi
  check_question "$tid" built "$plat" "$official" "$id" "$NATIVE_REPO:$tid"
}

official_image() {
  python3 "$PIPELINE_LIB/env_check.py" image --task-dir "$TASKS_DIR/$1" || die "no docker_image for $1"
}

deepswe_images() {
  local plat fallback=0 tid official why rc
  local -a emulate=()
  HOST_PLATFORM="$(docker_host_platform)"
  if [ -n "${DEEPSWE_DOCKER_PLATFORM:-}" ]; then
    plat="$DEEPSWE_DOCKER_PLATFORM"
    echo "images: DEEPSWE_DOCKER_PLATFORM=$plat on a $HOST_PLATFORM host"
  elif [ "$HOST_PLATFORM" = linux/amd64 ]; then
    plat=linux/amd64
    echo "images: $HOST_PLATFORM host, published images"
  else
    plat="$HOST_PLATFORM"
    fallback=1
    echo "images: $HOST_PLATFORM host, native builds on $DEEPSWE_BASE_IMAGE"
  fi
  [ "$plat" = linux/amd64 ] || [ -n "${DEEPSWE_BASE_IMAGE:-}" ] || die "DEEPSWE_BASE_IMAGE missing from $TOOLCHAIN_ENV"
  rm -rf "$ENV_CHECK_DIR"
  mkdir -p "$ENV_CHECK_DIR" "$ENV_CHECK_STORE"
  echo "images: environment checks are stored under $ENV_CHECK_STORE"
  for tid in "${TASK_IDS[@]}"; do
    official="$(official_image "$tid")"
    node_lock images "$IMAGES_LOCK_WAIT"
    if [ "$plat" = linux/amd64 ]; then
      prove_published "$tid" "$official" || true
    elif why="$(native_blocker "$tid")" && [ -n "$why" ]; then
      if [ "$fallback" = 1 ]; then
        echo "images: $tid: $why; the published linux/amd64 image, emulated"
        emulate+=("$tid")
      else
        image_failed "$tid" built "$plat" "$official" "$why" || true
      fi
    elif ! prove_built "$tid" "$official" "$plat" && [ "$fallback" = 1 ]; then
      emulate+=("$tid")
    fi
    node_unlock
  done
  if [ "${#emulate[@]}" -gt 0 ]; then
    # One iCode build serves the run, so a question on the published image
    # moves every question there.
    echo "images: the published image is needed for ${emulate[*]}; this run's task containers are linux/amd64 (emulated)"
    for tid in "${TASK_IDS[@]}"; do
      official="$(official_image "$tid")"
      node_lock images "$IMAGES_LOCK_WAIT"
      prove_published "$tid" "$official" || true
      node_unlock
    done
  fi
  if harbor_dry_run; then
    return 0
  fi
  set +e
  python3 "$PIPELINE_LIB/env_check.py" finish --build-record "$BUILD_RECORD" \
    --selected "$WORKDIR/selected_tasks.txt" --skipped "$WORKDIR/skipped_tasks.txt" \
    --host-platform "$HOST_PLATFORM"
  rc=$?
  set -e
  case "$rc" in
    0) ;;
    3) die "no selected question has a proven environment (why: $WORKDIR/skipped_tasks.txt; logs under $ENV_CHECK_DIR); the run stops here" ;;
    *) die "environment check bookkeeping failed (exit $rc)" ;;
  esac
}

case "${BENCHMARK:-deepswe}" in
  lolbench) lolbench_images ;;
  deepswe | "") deepswe_images ;;
esac
echo "OK task images ready for $BENCHMARK"
