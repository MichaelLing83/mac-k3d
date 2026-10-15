#!/usr/bin/env bash
# tasks/select: write selected_tasks.txt from TASK / TASKS / N_TASKS / TASK_OFFSET.
# Every later step reads that file instead of re-deciding the selection.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

write_selected_tasks
printf '%s\n' "${TASK_OFFSET:-0}" >"$WORKDIR/selected_tasks_offset.txt"
echo "OK selected $(tr '\n' ' ' <"$WORKDIR/selected_tasks.txt")"
