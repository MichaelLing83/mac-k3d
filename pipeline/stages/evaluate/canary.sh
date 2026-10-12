#!/usr/bin/env bash
# evaluate/canary: the isolation canary. A CanaryAgent trial with iCode's exact
# flags, mounts and env probes what the agent can reach and read: on the first
# question whose trial starts (CANARY=only: on every question). A failure stops
# the build before any rollout. Never calls the model.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"
# shellcheck source=./harbor_cmd.sh
source "$(cd "$(dirname "$0")" && pwd)/harbor_cmd.sh"

check_canary_settings
CANARY_MODE="$(canary_mode)"
load_eval_state
write_harbor_env

# Host lists and gold file names for one task; never gold content.
canary_spec() {
  local tid="$1" spec="$CANARY_DIR/$1/canary_spec.json"
  mkdir -p "$CANARY_DIR/$tid"
  python3 "$PIPELINE_LIB/canary_verdict.py" spec \
    --task-dir "$TASKS_DIR/$tid" \
    --benchmark "${BENCHMARK:-deepswe}" \
    --allow-host "${CANARY_ALLOW_HOST:-}" \
    --out "$spec" >&2 || die "canary spec failed for $tid"
  printf '%s\n' "$spec"
}

if harbor_dry_run; then
  build_canary_cmd "${TASK_IDS[0]}" "$(canary_spec "${TASK_IDS[0]}")"
  echo "canary dry-run (cwd $unit_run_dir): $(masked_cmd)"
  exit 0
fi

# The canary proves this worker's setup for this question, not the rollout
# number. The first rollout of the question to land here runs it; the rest of
# this run reuse that pass. A direct build (no RUN_GROUP) always runs it.
canary_marker() {
  python3 - "$MAC_K3D_ROOT/pipeline/BUILD.json" "$1" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
pipe = {}
path = Path(sys.argv[1])
if path.is_file():
    try:
        pipe = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pipe = {}
raw = "|".join([
    os.environ.get("RUN_GROUP", ""),
    sys.argv[2],
    str(pipe.get("pipeline_hash") or pipe.get("commit") or ""),
    os.environ.get("ICODE_GIT_SHA") or os.environ.get("ICODE_RELEASE_SHA256") or "",
])
digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
workspace = os.environ.get("WORKSPACE", "")
if workspace:
    base = Path(workspace).resolve().parent.parent / ".mac-k3d" / "canary-markers"
else:
    base = Path.home() / ".mac-k3d" / "canary-markers"
print(base / digest)
PY
}

CANARY_MARKER=""
if [ -n "${RUN_GROUP:-}" ] && [ "$CANARY_MODE" != only ]; then
  CANARY_MARKER="$(canary_marker "${TASK_IDS[0]}")"
  if [ -s "$CANARY_MARKER" ]; then
    source_build="$(tr -d '[:space:]' <"$CANARY_MARKER")"
    echo "canary: reused from $source_build"
    python3 "$PIPELINE_LIB/provenance.py" record-canary-reuse \
      --inputs "$PROTOCOL_INPUTS" --source "$source_build" || true
    exit 0
  fi
fi

ensure_harbor_egress
ensure_trial_network
teardown_on_exit "$CANARY_DIR"
warn_docker_mtu
rm -rf "$CANARY_DIR"
mkdir -p "$CANARY_DIR"
python3 "$PIPELINE_LIB/trial_network.py" owner --out "$CANARY_DIR/owner.json" || true
FALLBACK="$CANARY_DIR/fallback.tsv"
: >"$FALLBACK"
if [ -n "${CANARY_ALLOW_HOST:-}" ]; then
  echo "WARNING: CANARY_ALLOW_HOST=$CANARY_ALLOW_HOST opens that host for the canary only; the canary must fail"
fi

run_canary() {
  local tid="$1" spec rc
  spec="$(canary_spec "$tid")"
  build_canary_cmd "$tid" "$spec"
  echo "canary: task=$tid -a canary_harbor_agent:CanaryAgent (iCode's flags, mounts and env) log=$unit_log"
  set +e
  run_cmd >"$unit_log" 2>&1
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    echo "WARNING: canary harbor run exited $rc for $tid (see $unit_log)"
  fi
}

expect=()
if [ "$CANARY_MODE" = only ]; then
  for tid in "${TASK_IDS[@]}"; do
    run_canary "$tid"
    expect+=(--task "$tid")
  done
else
  # The first question whose trial reaches the probe proves isolation. A trial
  # that never started (image pull, compose) shows nothing about isolation, so
  # the next question takes its place. A probe that finds a breach still fails.
  for tid in "${TASK_IDS[@]}"; do
    run_canary "$tid"
    set +e
    why="$(python3 "$PIPELINE_LIB/canary_verdict.py" started --jobs-dir "$CANARY_DIR" --task "$tid")"
    started_rc=$?
    set -e
    if [ "$started_rc" = 0 ]; then
      expect=(--task "$tid")
      break
    fi
    [ "$started_rc" = 3 ] || die "canary verdict failed (exit $started_rc)"
    echo "WARNING: canary trial for $tid did not start ($why); trying the next question"
    printf '%s\t%s\n' "$tid" "$why" >>"$FALLBACK"
    expect+=(--task "$tid")
  done
fi
set +e
python3 "$PIPELINE_LIB/canary_verdict.py" summarize \
  --jobs-dir "$CANARY_DIR" \
  --out-dir "$CANARY_DIR" \
  --fallback-file "$FALLBACK" \
  "${expect[@]}"
rc=$?
set -e
case "$rc" in
  0 | 2) ;;
  *) die "canary verdict failed (exit $rc)" ;;
esac
python3 "$PIPELINE_LIB/provenance.py" record-canary --inputs "$PROTOCOL_INPUTS" --summary "$CANARY_DIR/summary.json"
if [ "$rc" != 0 ] && [ "$CANARY_MODE" != only ] && [ "$(wc -l <"$FALLBACK" | tr -d ' ')" -ge "${#TASK_IDS[@]}" ]; then
  die "no question's canary trial started (see $CANARY_DIR/report.md), so isolation is not proven; the run stops here"
fi
[ "$rc" = 0 ] || die "isolation canary failed (see $CANARY_DIR/report.md); the run stops here"
if [ -n "$CANARY_MARKER" ]; then
  mkdir -p "$(dirname "$CANARY_MARKER")"
  printf '%s\n' "${JOB_NAME:-local}#${BUILD_NUMBER:-0}" >"$CANARY_MARKER"
fi
python3 "$PIPELINE_LIB/trial_network.py" teardown --jobs-dir "$CANARY_DIR" || true
echo "canary: pass ($(( ${#expect[@]} / 2 )) tasks, report $CANARY_DIR/report.md)"
