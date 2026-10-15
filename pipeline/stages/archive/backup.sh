#!/usr/bin/env bash
# archive/backup: copy the run folder, anti-cheat verdicts and per-trial files
# into the repo's output/<benchmark>/ and pack them as <run folder>.tar.gz.
# MAC_K3D_BACKUP_ROOT or MAC_K3D_OUTPUT_ROOT moves the backup elsewhere.
# Records the archive in last_backup.txt, which Jenkins archives on the build page.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

ART_DIR="$(report_dir)"
BACKUP_ROOT="${MAC_K3D_BACKUP_ROOT:-${MAC_K3D_OUTPUT_ROOT:-$ROOT/output}}"
# The iCode transcript is most of a trial's size, and a shard archives this
# build's trials for its dispatcher. Anti-cheat has read them by now, so they
# are gzipped and masked in place (anticheat_verdict reads .gz too).
python3 "$PIPELINE_LIB/archive_run.py" compress --jobs-dir "$HARNESS_DIR/harbor_runs/jenkins-${BUILD_NUMBER:-local}" \
  || echo "WARNING: could not compress this build's transcripts; they are archived as they are"
python3 "$PIPELINE_LIB/archive_run.py" \
  --report-dir "$ART_DIR" \
  --harness-dir "$HARNESS_DIR" \
  --task-file "$WORKDIR/selected_tasks.txt" \
  --suite "$BENCHMARK" \
  --backup-root "$BACKUP_ROOT"
ARCHIVE="$BACKUP_ROOT/${BENCHMARK}/$(basename "$ART_DIR").tar.gz"
echo "OK backup $ARCHIVE"

# archiveArtifacts takes workspace-relative paths only.
if [ -n "${WORKSPACE:-}" ]; then
  if [[ "$ARCHIVE" == "$WORKSPACE/"* ]]; then
    printf '%s\n' "${ARCHIVE#"$WORKSPACE"/}" >"$WORKDIR/last_backup.txt"
  else
    echo "note: $ARCHIVE is outside the Jenkins workspace, so the build page will not offer it"
  fi
fi
