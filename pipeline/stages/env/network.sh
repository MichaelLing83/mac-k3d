#!/usr/bin/env bash
# env/network: can every Harbor trial on this worker get a Docker network?
# First removes this worker's own stopped leftovers (compose projects named
# after a trial folder in this agent's workspaces; nothing of other users),
# then checks that the trial subnet pool of pipeline/lib/trial_network.py has a
# free subnet. evaluate checks again for its parallel degree and records it.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

run_dirs=("$WORKDIR")
if [ -n "${WORKSPACE:-}" ]; then
  run_dirs+=("$(dirname "$WORKSPACE")"/*/eval-runs)
fi
roots=()
for runs in "${run_dirs[@]}"; do
  for dir in "$runs/harness/harbor_runs" "$runs/canary"; do
    if [ -d "$dir" ]; then
      roots+=(--root "$dir")
    fi
  done
done

python3 "$PIPELINE_LIB/trial_network.py" sweep "${roots[@]}" \
  || echo "WARNING: could not sweep trials whose owner is gone (see above); continuing" >&2
python3 "$PIPELINE_LIB/trial_network.py" cleanup "${roots[@]}" \
  || echo "WARNING: could not remove this worker's leftover trials (see above); continuing" >&2
python3 "$PIPELINE_LIB/trial_network.py" check --need 1 --out "$WORKDIR/trial_network.json" \
  || die "Harbor trials on this worker cannot get a Docker network (see the lines above)"
