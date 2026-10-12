#!/usr/bin/env bash
# evaluate/harbor_run: one `harbor run` for every selected task x rollout.
# Harbor schedules the trials, applies each task.toml, enforces the agent's
# network allowlist and runs the verifier. This step watches it, then writes
# meta.json and gives the iCode host tree back to the build user.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"
# shellcheck source=../../lib/icode_input.sh
source "$PIPELINE_LIB/icode_input.sh"
# shellcheck source=./harbor_cmd.sh
source "$(cd "$(dirname "$0")" && pwd)/harbor_cmd.sh"

write_harness_meta() {
  python3 - "$HARNESS_DIR" "$DEEPSEEK_MODEL" "$STARTED_AT" "$FINISHED_AT" "$DURATION" "$1" "$PIPELINE_LIB" <<'PY'
import json, re, sys
from pathlib import Path

harness, model, started, finished, duration, rc, lib = sys.argv[1:8]
sys.path.insert(0, lib)
from icode_usage import find_icode_usage

root = Path(harness)
scope = root
jobs_file = root / "harbor_jobs_dir.txt"
if jobs_file.is_file():
    raw = jobs_file.read_text(encoding="utf-8").strip()
    candidate = Path(raw)
    if candidate.is_dir():
        scope = candidate
usage = find_icode_usage(scope)
blob = ""
if usage is None:
    for p in scope.rglob("*"):
        if p.is_file() and p.suffix in {".log", ".json", ".jsonl", ".txt"} and p.stat().st_size < 2_000_000:
            try:
                blob += p.read_text(encoding="utf-8", errors="ignore") + "\n"
            except OSError:
                pass

def first_int(*pats):
    for pat in pats:
        m = re.search(pat, blob, re.I)
        if m:
            return int(m.group(1))
    return 0

if usage:
    prompt = int(usage.get("prompt") or 0)
    completion = int(usage.get("completion") or 0)
    total = int(usage.get("total") or 0)
else:
    prompt = first_int(
        r'"prompt_tokens"\s*:\s*(\d+)',
        r"\bprompt_tokens[=:\s]+(\d+)",
        r'"input_tokens"\s*:\s*(\d+)',
    )
    completion = first_int(
        r'"completion_tokens"\s*:\s*(\d+)',
        r"\bcompletion_tokens[=:\s]+(\d+)",
        r'"output_tokens"\s*:\s*(\d+)',
    )
    total = first_int(r'"total_tokens"\s*:\s*(\d+)', r"\btotal_tokens[=:\s]+(\d+)")
if total == 0:
    total = prompt + completion
served = None
m = re.search(r'"model"\s*:\s*"([^"]+)"', blob)
if m:
    served = m.group(1)
if usage and usage.get("model"):
    served = usage["model"]
meta = {
    "access_date_utc": started,
    "started_at": started,
    "finished_at": finished,
    "duration_seconds": int(duration),
    "llm_model_id": model,
    "llm_model_served": served,
    "exit_code": int(rc),
    "token_usage": {"prompt": prompt, "completion": completion, "total": total},
}
(root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
PY
}

# Harbor owns the trial loop, so progress is "how many rewards exist so far".
write_progress() {
  local done_units
  done_units="$(find "$JOBS_DIR" -name reward.json -type f 2>/dev/null | wc -l | tr -d ' ')"
  python3 "$PIPELINE_LIB/eval_progress.py" write \
    --out "$HARNESS_DIR/progress.json" \
    --done "${done_units:-0}" \
    --needed "$NEEDED" \
    --inflight "$EVAL_SLOTS" \
    --slots "${EVAL_SLOTS:-1}" \
    --mean "" \
    --started-at "$STARTED_AT"
}

# The last `SomethingError: …` line of a Harbor log, without Rich's box drawing.
harbor_last_error() {
  python3 - "$1" <<'PY' || true
import re, sys
from pathlib import Path

try:
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
except OSError:
    text = ""
hits = [
    re.sub(r"[│╭╮╰╯─]+", " ", line).strip()
    for line in text.splitlines()
    if re.search(r"\b\w+(Error|Exception)\b(:|$)", line)
]
print(re.sub(r"\s+", " ", hits[-1])[:300] if hits else "no error line in the log")
PY
}

# Trials whose iCode the kernel killed (exit 137) or whose container Harbor
# reported out of memory, one trial name per line.
oom_trials() {
  python3 - "$JOBS_DIR" "$PIPELINE_LIB" <<'PY' || true
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from icode_usage import icode_exit_code
from score_results import OOM_CAUSE, infra_cause, trial_exception

for result in sorted(Path(sys.argv[1]).glob("*/*/result.json")):
    trial = result.parent
    exception = trial_exception(trial)
    if icode_exit_code(trial) == 137 or (exception is not None and infra_cause(exception) == OOM_CAUSE):
        print(trial.name)
PY
}

run_harbor() {
  local tid rc rewards finished heartbeat_pid oom
  REPO_CANDIDATES="$(declared_repo_candidates)"
  mkdir -p "$JOBS_DIR"
  printf '%s\n' "${ROLLOUT:-1}" >"$JOBS_DIR/rollout.txt"
  python3 "$PIPELINE_LIB/trial_network.py" owner --out "$JOBS_DIR/owner.json" || true
  printf '%s\n' "$JOBS_DIR" >"$HARNESS_DIR/harbor_jobs_dir.txt"
  build_run_cmd
  echo "harbor: -p ${unit_task_path} tasks=${#TASK_IDS[@]} -k ${N_ROLLOUTS} -n ${EVAL_SLOTS} (${NEEDED} trials)"
  echo "harbor: --jobs-dir $JOBS_DIR log=$unit_log"
  [ -z "$REPO_CANDIDATES" ] || echo "harbor: declared repos $REPO_CANDIDATES"
  write_progress
  (
    while true; do
      sleep 60
      python3 "$PIPELINE_LIB/eval_progress.py" heartbeat \
        --progress "$HARNESS_DIR/progress.json" --elapsed "$SECONDS" || true
      python3 "$PIPELINE_LIB/task_resources.py" sample \
        --harness-dir "$HARNESS_DIR" --slots "$EVAL_SLOTS" || true
      write_progress || true
    done
  ) &
  heartbeat_pid=$!
  set +e
  run_cmd 2>&1 | tee -a "$unit_log"
  rc="${PIPESTATUS[0]}"
  set -e
  kill "$heartbeat_pid" 2>/dev/null || true
  wait "$heartbeat_pid" 2>/dev/null || true
  cat "$unit_log" >>"$HARNESS_DIR/harbor.log" || true
  if grep -Eiq 'No such option|unexpected argument|unrecognized arguments' "$unit_log"; then
    die "harbor CLI rejected flags (see $unit_log). Not recording as a successful stage."
  fi
  echo "$rc" >"$HARNESS_DIR/exit_code.txt"
  LAST_RC="$rc"
  rewards="$(find "$JOBS_DIR" -name reward.json -type f 2>/dev/null | wc -l | tr -d ' ')"
  echo "harbor: ${rewards:-0}/${NEEDED} trials scored (exit $rc)"
  # A trial's result.json sits at <jobs>/<job>/<trial>/; the job's own one level up.
  finished="$(find "$JOBS_DIR" -mindepth 3 -maxdepth 3 -name result.json -type f 2>/dev/null | wc -l | tr -d ' ')"
  oom="$(oom_trials)"
  # Harbor deletes a failed attempt before retrying, but the log keeps its
  # text. A retry that scored (a reward.json exists) is not an OOM to re-queue.
  if [ -z "$oom" ] && [ "${rewards:-0}" -eq 0 ] && grep -Eiq \
    'out of memory|Cannot allocate memory|oom-kill|oom_kill|exit(ed)?[[:space:]]+(with[[:space:]]+)?(status|code)[[:space:]]*137([^0-9]|$)|ExitCode[=:[:space:]]*137([^0-9]|$)' \
    "$unit_log"; then
    oom="log"
  fi
  if [ -n "$oom" ]; then
    printf '%s\n' "$oom" >"$HARNESS_DIR/oom_trials.txt"
    echo "WARNING: $(printf '%s\n' "$oom" | wc -l | tr -d ' ') trials ran out of memory (iCode exit 137 or the container OOM-killed): $(printf '%s' "$oom" | tr '\n' ' ')"
    echo "  Removing the container now. The rollout is queued again at the end of the run."
    python3 "$PIPELINE_LIB/trial_network.py" teardown --jobs-dir "$JOBS_DIR" || true
  elif [ "$rc" -ne 0 ] && [ "${rewards:-0}" -eq 0 ] && [ "${finished:-0}" -eq 0 ]; then
    icode_reclaim_host_tree "$HOST_ICODE"
    die "harbor run exited $rc before any trial finished: $(harbor_last_error "$unit_log") (see $unit_log)"
  elif [ "${rewards:-0}" -eq 0 ]; then
    echo "WARNING: no reward.json under $JOBS_DIR (unscored / no-response; see $unit_log)"
  elif [ "$rc" -ne 0 ]; then
    echo "WARNING: harbor run exited $rc. Rewards are present; treating them as scores."
  fi
  for tid in "${TASK_IDS[@]}"; do
    python3 "$PIPELINE_LIB/provenance.py" record-image \
      --inputs "$PROTOCOL_INPUTS" \
      --task-id "$tid" \
      --task-toml "$TASKS_DIR/${tid}/task.toml" || true
  done
  write_progress
}

load_eval_state
write_harbor_env

# MAC_K3D_HARBOR_DRY_RUN=1: print the harbor command with secret values masked; run nothing.
if harbor_dry_run; then
  REPO_CANDIDATES="$(declared_repo_candidates)"
  build_run_cmd
  echo "harbor dry-run (cwd $unit_run_dir): $(masked_cmd)"
  exit 0
fi
if [ "$(canary_mode)" = only ]; then
  echo "harbor: CANARY=only, no rollouts (report $CANARY_DIR/report.md)"
  exit 0
fi

ensure_harbor_egress
ensure_trial_network
teardown_on_exit "$JOBS_DIR"
warn_docker_mtu
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SECONDS=0
: >"$HARNESS_DIR/harbor.log"
LAST_RC=0
run_harbor
python3 "$PIPELINE_LIB/trial_network.py" teardown --jobs-dir "$JOBS_DIR" || true
icode_reclaim_host_tree "$HOST_ICODE"

DURATION="$SECONDS"
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_harness_meta "$LAST_RC"
