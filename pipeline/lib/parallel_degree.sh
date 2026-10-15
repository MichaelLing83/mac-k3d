#!/usr/bin/env bash
# EVAL_SLOTS = how many trials Harbor runs at once; EVAL_CPUS_EACH = what each
# task declares. evaluate/slots decides both and writes them to eval_resources.json, so the
# later stages read that file instead of re-deriving anything: a report must
# describe the run that happened, not a fresh guess about the current machine.
eval_parallel_degree() {
  N_ROLLOUTS="${N_ROLLOUTS:-1}"
  CPU_LOCK_QTY="${CPU_LOCK_QTY:-1}"
  case "$N_ROLLOUTS" in
    ''|*[!0-9]*) echo "N_ROLLOUTS must be an integer >= 1" >&2; return 1 ;;
  esac
  case "$CPU_LOCK_QTY" in
    ''|*[!0-9]*) echo "CPU_LOCK_QTY must be an integer >= 1" >&2; return 1 ;;
  esac
  if [ "$N_ROLLOUTS" -lt 1 ] || [ "$CPU_LOCK_QTY" -lt 1 ]; then
    echo "N_ROLLOUTS and CPU_LOCK_QTY must be integers >= 1" >&2
    return 1
  fi
  local plan="${WORKDIR:-.}/eval_resources.json"
  local out=""

  if [ -f "$plan" ]; then
    out="$(python3 - "$plan" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
applied = doc.get("applied") or {}
declared = (doc.get("declared") or {}).get("peak") or {}
cpus = float(applied.get("cpus_each") or declared.get("cpus") or 1)
print(f"EVAL_SLOTS={applied.get('slots') or 1}")
print(f"EVAL_CPUS_EACH={int(cpus) if cpus.is_integer() else cpus}")
PY
    )" || out=""
  fi
  # No plan means evaluate/slots has not run in this workdir yet (a report-only replay of a
  # tree that never evaluated). 1x1 is the honest answer, not a probe.
  if [ -n "$out" ]; then
    eval "$out"
  fi
  EVAL_SLOTS="${EVAL_SLOTS:-1}"
  EVAL_CPUS_EACH="${EVAL_CPUS_EACH:-1}"
  export N_ROLLOUTS CPU_LOCK_QTY EVAL_SLOTS EVAL_CPUS_EACH
}
