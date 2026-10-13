#!/usr/bin/env bash
# tasks/leakscan: the mounted iCode tree must not contain any selected task's
# gold patch. A hit question is added to skipped_tasks.txt (after the questions
# tasks/images' environment check kept out) and left out of the evaluate
# phase; the build stops here only when no selected question is left.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

TASKS_DIR="$(benchmark_tasks_dir)"
ensure_selected_tasks
HOST_ICODE="$(icode_host_root)"
PROTOCOL_INPUTS="$WORKDIR/eval_protocol_inputs.json"
[ -f "$PROTOCOL_INPUTS" ] || die "run tasks/isolation first (missing $PROTOCOL_INPUTS)"

LEAKSCAN_OUT="$WORKDIR/anticheat_leakscan.json"
SKIPPED_TASKS="$WORKDIR/skipped_tasks.txt"
set +e
python3 "$PIPELINE_LIB/anticheat_leakscan.py" \
  --tree "$HOST_ICODE" \
  --tasks-dir "$TASKS_DIR" \
  --selected "$WORKDIR/selected_tasks.txt" \
  --out "$LEAKSCAN_OUT"
leak_rc=$?
set -e
if [ "$leak_rc" = 0 ] || [ "$leak_rc" = 2 ]; then
  python3 "$PIPELINE_LIB/provenance.py" record-leakscan --inputs "$PROTOCOL_INPUTS" --report "$LEAKSCAN_OUT"
fi
case "$leak_rc" in
  0) echo "OK leak scan: no task gold in $HOST_ICODE" ;;
  2)
    counts="$(
      python3 - "$LEAKSCAN_OUT" "$WORKDIR/selected_tasks.txt" "$SKIPPED_TASKS" <<'PY'
import json, sys
from pathlib import Path

report, selected, out = (Path(p) for p in sys.argv[1:4])
hits = json.loads(report.read_text(encoding="utf-8")).get("hit_tasks") or []
ids = [ln.strip() for ln in selected.read_text(encoding="utf-8").splitlines() if ln.strip()]
skip = [tid for tid in ids if tid in {str(h) for h in hits}]
listed = set()
if out.is_file():
    listed = {ln.split("\t", 1)[0] for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()}
with out.open("a", encoding="utf-8") as fh:
    fh.write("".join(f"{tid}\tleak scan hit\n" for tid in skip if tid not in listed))
gone = listed | set(skip)
print(len(skip), len(ids), ",".join(skip), sum(1 for tid in ids if tid in gone))
PY
    )"
    read -r n_hit n_selected hit_ids n_out <<<"$counts"
    if [ "$n_out" -ge "$n_selected" ]; then
      if [ "$n_hit" -ge "$n_selected" ]; then
        die "leak scan found task gold for every selected question ($hit_ids; see $LEAKSCAN_OUT); the run stops here"
      fi
      die "leak scan found task gold for $hit_ids, and the environment check kept the other questions out (see $SKIPPED_TASKS); the run stops here"
    fi
    echo "WARNING: leak scan found task gold for $n_hit of $n_selected questions ($hit_ids; see $LEAKSCAN_OUT)."
    echo "leak scan: skipping those questions (listed in $SKIPPED_TASKS); the other $((n_selected - n_out)) run"
    ;;
  *) die "leak scanner failed (exit $leak_rc)" ;;
esac
