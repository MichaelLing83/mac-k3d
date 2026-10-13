#!/usr/bin/env bash
# report/render: this run's artifact.json, summary.md, report.html and
# provenance.md under $WORKDIR/output/<benchmark>/<run folder>. Records the folder in
# report_dir.txt for the archive phase, and the number of rollouts without a
# score in unscored_rollouts.txt (Jenkins prints a warning above 0; the build
# result stays as it is).
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

UTC="$(date -u +%Y%m%dT%H%M%SZ)"
eval_parallel_degree || die "N_ROLLOUTS and CPU_LOCK_QTY must be integers >= 1"
RUN_ID="local-${UTC}"
if [ -n "${BUILD_NUMBER:-}" ]; then
  RUN_ID="jenkins-${BUILD_NUMBER}"
fi

FOLDER="$(
  PYTHONPATH="$PIPELINE_LIB${PYTHONPATH:+:$PYTHONPATH}" python3 - "$UTC" "$WORKDIR/selected_tasks.txt" <<'PY'
import os
import sys
from pathlib import Path

from render_report import run_folder_name

utc, task_file = sys.argv[1], sys.argv[2]
ids = []
path = Path(task_file)
if path.is_file():
    ids = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
print(run_folder_name(utc, ids, os.environ.get("BUILD_NUMBER", "")))
PY
)"

ART_DIR="$WORKDIR/output/${BENCHMARK}/${FOLDER}"

API_BASE="${ICODE_API_BASE:-https://api.deepseek.com/v1}"
if [ -f "$WORKDIR/eval_protocol_inputs.json" ]; then
  protocol_base="$(
    python3 - "$WORKDIR/eval_protocol_inputs.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
base = doc.get("api_base") if isinstance(doc, dict) else ""
print(base if isinstance(base, str) else "")
PY
  )"
  if [ -n "$protocol_base" ]; then
    API_BASE="$protocol_base"
  fi
fi

python3 "$PIPELINE_LIB/render_report.py" \
  --harness-dir "$HARNESS_DIR" \
  --baseline-dir "$BASELINE_DIR" \
  --task-file "$WORKDIR/selected_tasks.txt" \
  --suite "$BENCHMARK" \
  --model "${DEEPSEEK_MODEL:-deepseek-flash}" \
  --api-base "$API_BASE" \
  --run-id "$RUN_ID" \
  --n-rollouts "${N_ROLLOUTS:-1}" \
  --concurrency "${EVAL_SLOTS:-1}" \
  --cpus-each "${EVAL_CPUS_EACH:-1}" \
  --workdir "$WORKDIR" \
  --utc "$UTC" \
  --run-folder "$FOLDER" \
  --out-dir "$ART_DIR" \
  --unscored-out "$WORKDIR/unscored_rollouts.txt"

printf '%s\n' "$ART_DIR" >"$WORKDIR/report_dir.txt"
rel="$ART_DIR"
if [ -n "${WORKSPACE:-}" ] && [[ "$ART_DIR" == "$WORKSPACE/"* ]]; then
  rel="${ART_DIR#"$WORKSPACE"/}"
fi
printf '%s/**\n' "$rel" >"$WORKDIR/last_output.txt"
echo "OK wrote $ART_DIR/artifact.json"
