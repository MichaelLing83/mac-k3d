#!/usr/bin/env bash
# The one place that builds `harbor run` command lines. Sourced after
# _common.sh by evaluate/canary.sh and evaluate/harbor_run.sh; never run alone.
#
#   load_eval_state    read what the tasks phase and evaluate/slots left in $WORKDIR
#   ensure_harbor_egress  re-run env/egress's probe check right before Harbor runs
#   ensure_trial_network  enough free trial subnets for EVAL_SLOTS trials at once
#                      (env/network); recorded in the protocol
#   teardown_on_exit   when the step's shell exits, remove every trial
#                      container and network under a jobs dir, and the env file
#   write_harbor_env   the 0600 --env-file with the model key and settings,
#                      removed when the step's shell exits
#   build_run_cmd      iCode: every selected task x N_ROLLOUTS trials, one job
#   build_canary_cmd   canary: one trial with iCode's exact flags, mounts and env
#   masked_cmd         the current command with secret values replaced by ***
#   harbor_dry_run     true when MAC_K3D_HARBOR_DRY_RUN=1: print, run nothing
#
# The commands set `cmd`, `unit_run_dir` (where harbor runs), `unit_task_path`
# (its -p), `unit_jobs` and `unit_log`.

# Clone tokens are for tasks/icode on the host. Harbor hands its own
# environment to docker compose, so none of them may reach a trial.
unset GITCODE_TOKEN MAC_K3D_GITCODE_PAT GITHUB_TOKEN MAC_K3D_GITHUB_PAT MAC_K3D_GIT_TOKEN

declare -a cmd=() TASK_IDS=() SKIPPED_IDS=() AGENT_HOSTS=()
EGRESS_PROBE_IMAGE=""
unit_jobs=""
unit_run_dir=""
unit_task_path=""
unit_log=""

harbor_dry_run() {
  [ "${MAC_K3D_HARBOR_DRY_RUN:-0}" = 1 ]
}

load_eval_state() {
  TASKS_DIR="$(benchmark_tasks_dir)"
  [ -d "$TASKS_DIR" ] || die "run the tasks phase first (missing $TASKS_DIR)"
  [ -n "${DEEPSEEK_API_KEY:-}" ] || die "$(missing_deepseek_key_hint)"
  have harbor || die "harbor not on PATH (run the env phase)"
  ensure_selected_tasks
  local line skip_list=""
  # tasks/leakscan lists the questions a leak hit keeps out of Harbor. They
  # stay in selected_tasks.txt, so the report counts them as not scored.
  if [ -s "$WORKDIR/skipped_tasks.txt" ]; then
    skip_list="$(cut -f1 "$WORKDIR/skipped_tasks.txt")"
  fi
  TASK_IDS=()
  SKIPPED_IDS=()
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in *[![:space:]]*) ;; *) continue ;; esac
    if [ -n "$skip_list" ] && printf '%s\n' "$skip_list" | grep -Fxq -- "$line"; then
      SKIPPED_IDS+=("$line")
    else
      TASK_IDS+=("$line")
    fi
  done <"$WORKDIR/selected_tasks.txt"
  if [ "${#SKIPPED_IDS[@]}" -gt 0 ]; then
    echo "evaluate: skipping ${#SKIPPED_IDS[@]} questions from $WORKDIR/skipped_tasks.txt: ${SKIPPED_IDS[*]}" >&2
  fi
  [ "${#TASK_IDS[@]}" -gt 0 ] || die "no task under $TASKS_DIR (wanted ${TASK:-<empty>})"
  [ -s "$WORKDIR/agent_mounts.json" ] || die "run tasks/isolation first (missing $WORKDIR/agent_mounts.json)"
  MOUNTS_JSON="$(cat "$WORKDIR/agent_mounts.json")"
  HOST_ICODE="$(icode_host_root)"
  AGENT_HOSTS=()
  while IFS= read -r line || [ -n "$line" ]; do
    AGENT_HOSTS+=("$line")
  done < <(python3 "$PIPELINE_LIB/network_allowlist.py" hosts)
  [ "${#AGENT_HOSTS[@]}" -gt 0 ] || die "pipeline/config/network-allowlist-v1.json lists no agent hosts"
  [ -f "$WORKDIR/eval_resources.json" ] || die "run evaluate/slots first (missing $WORKDIR/eval_resources.json)"
  eval_parallel_degree || die "N_ROLLOUTS and CPU_LOCK_QTY must be integers >= 1"
  PROTOCOL_INPUTS="$WORKDIR/eval_protocol_inputs.json"
  JOBS_DIR="$HARNESS_DIR/harbor_runs/jenkins-${BUILD_NUMBER:-local}"
  CANARY_DIR="$WORKDIR/canary/jenkins-${BUILD_NUMBER:-local}"
  HARBOR_ENV="$WORKDIR/.harbor-env"
  NEEDED=$(( ${#TASK_IDS[@]} * N_ROLLOUTS ))
}

# The env phase may have run long before the CPU lock was granted, so check
# again. EGRESS_PROBE_IMAGE is set only when Docker cannot run Harbor's own
# probe image; run_cmd hands it to harbor_probe_override.py, and Harbor then
# reuses this check's answer instead of starting its own probe container.
ensure_harbor_egress() {
  local probe="$WORKDIR/egress_probe.json"
  python3 "$PIPELINE_LIB/harbor_egress.py" check --out "$probe" \
    || die "Harbor cannot enforce network isolation on this worker (see the lines above)"
  python3 "$PIPELINE_LIB/provenance.py" record-egress-probe --inputs "$PROTOCOL_INPUTS" --probe "$probe"
  EGRESS_PROBE_IMAGE="$(python3 "$PIPELINE_LIB/harbor_egress.py" override --probe "$probe")"
}

# harbor_network_override.py gives each running trial its own subnet, and
# Harbor runs EVAL_SLOTS trials at once.
ensure_trial_network() {
  local record="$WORKDIR/trial_network.json"
  python3 "$PIPELINE_LIB/trial_network.py" check --need "$EVAL_SLOTS" --out "$record" \
    || die "not enough free trial subnets for $EVAL_SLOTS trials at once (see the lines above)"
  python3 "$PIPELINE_LIB/provenance.py" record-trial-network --inputs "$PROTOCOL_INPUTS" --record "$record"
}

# Harbor removes a trial's containers and network when the trial ends; stopped
# early (Ctrl-C, an aborted build) it can leave them running. Replaces
# write_harbor_env's EXIT trap, so the env file is removed here too.
teardown_on_exit() {
  TEARDOWN_DIR="$1"
  trap 'python3 "$PIPELINE_LIB/trial_network.py" teardown --jobs-dir "$TEARDOWN_DIR" || true; rm -f "$HARBOR_ENV"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

# Harbor loads this file into its own environment; the agents copy the key
# into each trial as a private file (icode_harbor_agent.py). It never goes on
# a command line, and it stays on the worker's disk only while this step runs.
write_harbor_env() {
  trap 'rm -f "$HARBOR_ENV"' EXIT
  (
    umask 077
    {
      printf 'DEEPSEEK_API_KEY=%s\n' "${DEEPSEEK_API_KEY}"
      printf 'DEEPSEEK_MODEL=%s\n' "${DEEPSEEK_MODEL}"
      printf 'ICODE_MODEL=%s\n' "${ICODE_MODEL}"
      printf 'ICODE_API_BASE=%s\n' "${ICODE_API_BASE}"
      printf 'ICODE_PROVIDER=%s\n' "${ICODE_PROVIDER}"
      printf 'ICODE_REASONING_EFFORT=%s\n' "${ICODE_REASONING_EFFORT}"
      printf 'ICODE_MAX_TOKENS=%s\n' "${ICODE_MAX_TOKENS}"
      printf 'ICODE_MAX_ITERATIONS=%s\n' "${ICODE_MAX_ITERATIONS}"
      printf 'PYTHONDONTWRITEBYTECODE=%s\n' "1"
      printf 'MAC_K3D_BENCHMARK=%s\n' "${BENCHMARK:-deepswe}"
    } >"$HARBOR_ENV"
  )
  chmod 600 "$HARBOR_ENV"
}

# Where to run harbor from, and the dataset directory to hand it. LoLBench's
# tasks only resolve relative to its checkout.
unit_location() {
  unit_run_dir="$WORKDIR"
  unit_task_path="$TASKS_DIR"
  if [ "${BENCHMARK:-deepswe}" = "lolbench" ]; then
    unit_run_dir="$LOLBENCH_DIR"
    unit_task_path="harbor_tasks"
  fi
}

# One Harbor job for the whole build: Harbor expands the selected tasks into
# n_attempts trials each and runs EVAL_SLOTS of them at a time.
build_run_cmd() {
  local tid
  unit_location
  unit_jobs="$JOBS_DIR"
  unit_log="$JOBS_DIR/harbor.log"
  cmd=(harbor run)
  cmd+=(-p "$unit_task_path")
  for tid in "${TASK_IDS[@]}"; do
    cmd+=(-i "$tid")
  done
  cmd+=(-a "icode_harbor_agent:ICodeAgent")
  cmd+=(--job-name "icode_${BENCHMARK:-deepswe}_${BUILD_NUMBER:-local}")
  cmd+=(--jobs-dir "$JOBS_DIR")
  cmd+=(--no-delete)
  cmd+=(-k "$N_ROLLOUTS")
  # A wrong answer is never an exception, and iCode's 137 exits 0, so neither
  # is retried. Harbor's one retry covers infrastructure errors, as before.
  cmd+=(-r "${HARBOR_MAX_RETRIES:-1}")
  # The canary keeps the task.toml limit. Only the rollout is squeezed.
  apply_test_memory
  append_agent_flags
}

# The canary runs in iCode's sandbox: only the agent, job location, its spec
# and the skipped verifier differ. CANARY_ALLOW_HOST opens one more host for
# the canary alone, to prove that it notices.
build_canary_cmd() {
  local tid="$1" spec="$2"
  # Same -p/-i resolution as the real run, so the canary proves isolation on the
  # path the rollouts actually take.
  unit_location
  unit_jobs="$CANARY_DIR/$tid"
  unit_log="$unit_jobs/harbor.log"
  cmd=(harbor run)
  cmd+=(-p "$unit_task_path")
  cmd+=(-i "$tid")
  cmd+=(-a "canary_harbor_agent:CanaryAgent")
  cmd+=(--job-name "${tid}_canary_${BUILD_NUMBER:-local}")
  cmd+=(--jobs-dir "$unit_jobs")
  cmd+=(--no-delete)
  append_agent_flags
  cmd+=(--ak "spec=$spec")
  cmd+=(--disable-verification)
  if [ -n "${CANARY_ALLOW_HOST:-}" ]; then
    cmd+=(--allow-agent-host "$CANARY_ALLOW_HOST")
  fi
}

# Developer testing: TEST_MEMORY_MB becomes --override-memory-mb while ATTEMPT
# is at most TEST_MEMORY_ATTEMPTS (default 1). A later try runs as declared.
apply_test_memory() {
  [ -n "${TEST_MEMORY_MB:-}" ] || return 0
  local limit="${TEST_MEMORY_ATTEMPTS:-1}" attempt="${ATTEMPT:-1}"
  case "$limit" in
    ''|*[!0-9]*) limit=1 ;;
  esac
  case "$attempt" in
    ''|*[!0-9]*) attempt=1 ;;
  esac
  if [ "$attempt" -le "$limit" ]; then
    EVAL_OVERRIDE_MEMORY_MB="$TEST_MEMORY_MB"
    echo "harbor: TEST_MEMORY_MB=$TEST_MEMORY_MB on attempt $attempt"
  fi
}

# Everything the agent sees: model, egress allowlist, resources, env file,
# verifier env, mounts and agent env. Shared by iCode and the canary.
append_agent_flags() {
  local host
  cmd+=(-m "${DEEPSEEK_MODEL}")
  for host in "${AGENT_HOSTS[@]}"; do
    cmd+=(--allow-agent-host "$host")
  done
  cmd+=(--agent-setup-timeout-multiplier 10)
  cmd+=(-n "$EVAL_SLOTS")
  # No --override-cpus / --override-memory-mb: Harbor then applies what each
  # task.toml declares. A Jenkins build is one trial; the lock holds its CPUs.
  # build_run_cmd may set EVAL_OVERRIDE_MEMORY_MB from TEST_MEMORY_MB first.
  if [ -n "${EVAL_OVERRIDE_CPUS:-}" ]; then
    cmd+=(--override-cpus "$EVAL_OVERRIDE_CPUS")
    echo "harbor: override-cpus=$EVAL_OVERRIDE_CPUS (EVAL_OVERRIDE_CPUS set; task.toml ignored)"
  fi
  if [ -n "${EVAL_OVERRIDE_MEMORY_MB:-}" ]; then
    cmd+=(--override-memory-mb "$EVAL_OVERRIDE_MEMORY_MB")
    echo "harbor: override-memory-mb=$EVAL_OVERRIDE_MEMORY_MB (EVAL_OVERRIDE_MEMORY_MB set; task.toml ignored)"
  fi
  cmd+=(-y)
  cmd+=(--env-file "$HARBOR_ENV")
  if [ "${BENCHMARK:-deepswe}" = "lolbench" ]; then
    cmd+=(--ve "LOLBENCH_SUITE=union")
  fi
  cmd+=(--mounts "$MOUNTS_JSON")
  cmd+=(--ae "ICODE_MODEL=${ICODE_MODEL}")
  cmd+=(--ae "ICODE_API_BASE=${ICODE_API_BASE}")
  cmd+=(--ae "ICODE_PROVIDER=${ICODE_PROVIDER}")
  cmd+=(--ae "ICODE_REASONING_EFFORT=${ICODE_REASONING_EFFORT}")
  cmd+=(--ae "ICODE_MAX_TOKENS=${ICODE_MAX_TOKENS}")
  cmd+=(--ae "ICODE_MAX_ITERATIONS=${ICODE_MAX_ITERATIONS}")
  cmd+=(--ae "PYTHONDONTWRITEBYTECODE=1")
  cmd+=(--ae "DEEPSEEK_MODEL=${DEEPSEEK_MODEL}")
  cmd+=(--ae "MAC_K3D_BENCHMARK=${BENCHMARK:-deepswe}")
  if [ -n "${REPO_CANDIDATES:-}" ]; then
    cmd+=(--ae "MAC_K3D_REPO_CANDIDATES=$REPO_CANDIDATES")
  fi
}

# Every selected task's declared repo, colon separated and deduplicated. One
# Harbor job covers them all, so the trial picks the one its own image has.
# The declared base commit is checked host-side by anticheat/receipts, where
# the task id of each trial is known.
declared_repo_candidates() {
  local tid declared repo_decl base_decl out=""
  for tid in "${TASK_IDS[@]}"; do
    declared="$(python3 "$PIPELINE_LIB/capture_receipt.py" declared-repo \
      --task-dir "$TASKS_DIR/$tid" --benchmark "${BENCHMARK:-deepswe}")" || declared=""
    { read -r repo_decl; read -r base_decl; } <<<"$declared" || true
    [ -n "$repo_decl" ] || continue
    case ":${out}:" in
      *":${repo_decl}:"*) continue ;;
    esac
    if [ -n "$out" ]; then out="${out}:${repo_decl}"; else out="$repo_decl"; fi
  done
  printf '%s' "$out"
}

masked_cmd() {
  local arg
  local -a masked=()
  for arg in "${cmd[@]}"; do
    case "$arg" in
      *_API_KEY=* | *_TOKEN=* | *_PAT=*) arg="${arg%%=*}=***" ;;
    esac
    masked+=("$arg")
  done
  printf '%s' "${masked[*]}"
}

# Run the current command from unit_run_dir with the agents importable.
run_cmd() {
  (
    cd "$unit_run_dir"
    export PYTHONPATH="$PIPELINE_LIB${PYTHONPATH:+:$PYTHONPATH}"
    export PYTHONUNBUFFERED=1
    if [ -n "$EGRESS_PROBE_IMAGE" ]; then
      export MAC_K3D_EGRESS_PROBE_IMAGE="$EGRESS_PROBE_IMAGE"
    else
      unset MAC_K3D_EGRESS_PROBE_IMAGE
    fi
    if command -v stdbuf >/dev/null 2>&1; then
      stdbuf -oL -eL "${cmd[@]}"
    else
      "${cmd[@]}"
    fi
  )
}
