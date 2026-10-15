#!/usr/bin/env bash
# report phase: this run's artifact.json, summary.md, report.html and provenance.md.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(report/render)

progress 92 "report: artifact, summary, html"
run_steps "${STEPS[@]}"
progress 95 "report complete"
