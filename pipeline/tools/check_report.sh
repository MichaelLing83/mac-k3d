#!/usr/bin/env bash
# Validate named eval JSON (or last output) has required report fields. No LLM.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export MAC_K3D_ROOT="$ROOT"
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  WORKDIR="${MAC_K3D_EVAL_WORKDIR:-$ROOT/eval-runs}"
  if [ -f "$WORKDIR/last_output.txt" ]; then
    TARGET="$(cat "$WORKDIR/last_output.txt")"
  fi
fi
exec python3 "$ROOT/pipeline/lib/check_report.py" ${TARGET:+"$TARGET"}
