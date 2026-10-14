#!/usr/bin/env bash
# tasks/agent: Harbor's Python can import the harness's agent and canary
# (pipeline/lib/harness.py) and the patch agent (no LLM).
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

agent="$(python3 "$PIPELINE_LIB/harness.py" get "$HARNESS" agent)" || die "unknown HARNESS=$HARNESS"
canary="$(python3 "$PIPELINE_LIB/harness.py" get "$HARNESS" canary)" || die "unknown HARNESS=$HARNESS"
[ -f "$PIPELINE_LIB/${agent%%:*}.py" ] || die "missing $PIPELINE_LIB/${agent%%:*}.py"
[ -f "$PIPELINE_LIB/icode_capture.sh" ] || die "missing $PIPELINE_LIB/icode_capture.sh"
have harbor || die "harbor not on PATH (run the env phase)"

py="python3"
shebang="$(head -n 1 "$(command -v harbor)" 2>/dev/null || true)"
case "$shebang" in
  "#!"*)
    py="${shebang:2}"
    py="${py#"${py%%[![:space:]]*}"}"
    ;;
esac
PYTHONPATH="$PIPELINE_LIB${PYTHONPATH:+:$PYTHONPATH}" "$py" - "$agent" "$canary" "$HARNESS" <<'PY' \
  || die "$agent, $canary, patch_harbor_agent:PatchAgent or icode_capture.sh missing"
import importlib
import sys

from icode_harbor_agent import CAPTURE_SRC
from patch_harbor_agent import PatchAgent  # noqa: F401


def load(path):
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


agent, canary, harness = sys.argv[1:4]
assert load(agent).name() == harness, f"{agent}.name() is not {harness}"
load(canary)
assert CAPTURE_SRC.is_file(), CAPTURE_SRC
PY

echo "OK Harbor adapter $PIPELINE_LIB/${agent%%:*}.py ($agent; canary $canary)"
