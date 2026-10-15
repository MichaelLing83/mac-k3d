#!/usr/bin/env bash
# archive/analysis: cost-token-report.md (tokens, cache hits, estimated cost)
# next to artifact.json, so the backup carries it.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

ART_DIR="$(report_dir)"
python3 "$PIPELINE_LIB/cost_token_report.py" \
  --run-dir "$ART_DIR" \
  --out "$ART_DIR/cost-token-report.md" \
  || echo "WARNING: cost/token analysis failed; the backup goes ahead without cost-token-report.md"
