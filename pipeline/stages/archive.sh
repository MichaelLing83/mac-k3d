#!/usr/bin/env bash
# archive phase: cost/token analysis into the run folder, then the backup and
# its tar.gz. Analysis runs first so the archive contains it.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

STEPS=(archive/analysis archive/backup)

progress 96 "archive: analysis and backup"
run_steps "${STEPS[@]}"
progress 100 "archive complete; evaluation output ready"
