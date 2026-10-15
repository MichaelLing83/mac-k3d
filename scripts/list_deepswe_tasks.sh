#!/usr/bin/env bash
# Print sorted DeepSWE task directory names (first line = empty-TASK default).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

candidates=()
if [ -n "${MAC_K3D_EVAL_WORKDIR:-}" ]; then
  candidates+=("$MAC_K3D_EVAL_WORKDIR")
fi
candidates+=("$ROOT/eval-runs")

found=""
for base in "${candidates[@]}"; do
  [ -d "$base/deep-swe/tasks" ] || continue
  found="$base/deep-swe/tasks"
  break
done

if [ -z "$found" ]; then
  echo "ERROR: no deep-swe/tasks tree. Set MAC_K3D_EVAL_WORKDIR or run: mac-k3d eval --stage tasks --local --benchmark deepswe --n-tasks 2" >&2
  exit 2
fi

echo "OK tasks dir $found" >&2
find "$found" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||' | sort
