#!/usr/bin/env bash
# Print sorted SWE-bench Pro instance ids (first line = empty-TASK default).
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
  [ -d "$base/swebenchpro/tasks" ] || continue
  found="$base/swebenchpro/tasks"
  break
done

if [ -z "$found" ]; then
  echo "ERROR: no swebenchpro/tasks tree. Set MAC_K3D_EVAL_WORKDIR or run: mac-k3d eval --stage tasks --local --benchmark swebenchpro --n-tasks 1" >&2
  exit 2
fi

echo "OK tasks dir $found" >&2
find "$found" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||' | sort
