#!/usr/bin/env bash
# tasks/leakscan: the mounted iCode tree must not contain any selected task's
# gold patch. A hit question is listed in skipped_tasks.txt and left out of
# the evaluate phase; the build stops here only when every selected question
# is hit.
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
# The workspace is reused; an earlier build's list is not this build's.
rm -f "$SKIPPED_TASKS"
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
out.write_text("".join(f"{tid}\tleak scan hit\n" for tid in skip), encoding="utf-8")
print(len(skip), len(ids), ",".join(skip))
PY
    )"
    read -r n_hit n_selected hit_ids <<<"$counts"
    if [ "$n_hit" -ge "$n_selected" ]; then
      die "leak scan found task gold for every selected question ($hit_ids; see $LEAKSCAN_OUT); the run stops here"
    fi
    echo "WARNING: leak scan found task gold for $n_hit of $n_selected questions ($hit_ids; see $LEAKSCAN_OUT)."
    echo "leak scan: skipping those questions (listed in $SKIPPED_TASKS); the other $((n_selected - n_hit)) run"
    ;;
  *) die "leak scanner failed (exit $leak_rc)" ;;
esac
