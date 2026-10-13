#!/usr/bin/env bash
# One evaluation build, as seven phases. Each phase is a Jenkins stage and a
# script next to this one (<phase>.sh) that runs its steps (<phase>/<step>.sh):
#
#   env        bare-metal checks: host, compose, pinned Harbor, model API
#   tasks      per-task setup and checks: benchmark, selection, iCode, isolation
#   evaluate   canary + one `harbor run`        (the only phase under the CPU lock)
#   anticheat  post-Harbor receipts and verdicts
#   score      F2P/P2P per rollout
#   report     artifact.json, summary.md, report.html, provenance.md
#   archive    cost/token analysis, backup, tar.gz
#
# MAC_K3D_PHASE=<phase> runs one phase (Jenkins runs them as separate stages);
# all, the default, runs every phase in order. State lives under $WORKDIR, so
# the phases need not share a shell. With CANARY=only the build stops after
# evaluate: there are no rollouts to judge.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/_common.sh"

PHASES=(env tasks evaluate anticheat score report archive)
PHASE="$(printf '%s' "${MAC_K3D_PHASE:-all}" | tr '[:upper:]' '[:lower:]')"
case " all ${PHASES[*]} " in
  *" $PHASE "*) ;;
  *) die "MAC_K3D_PHASE must be all or one of: ${PHASES[*]} (got $PHASE)" ;;
esac

after_evaluate=0
for phase in "${PHASES[@]}"; do
  if [ "$PHASE" = all ] || [ "$PHASE" = "$phase" ]; then
    if [ "$after_evaluate" = 1 ] && [ "$(canary_mode || true)" = only ]; then
      progress 100 "canary only: no rollouts to score (report $WORKDIR/canary/jenkins-${BUILD_NUMBER:-local}/report.md)"
      exit 0
    fi
    bash "$DIR/$phase.sh"
  fi
  if [ "$phase" = evaluate ]; then
    after_evaluate=1
  fi
done

if { [ "$PHASE" = all ] || [ "$PHASE" = archive ]; } && [ -f "$WORKDIR/last_output.txt" ]; then
  echo "RESULT $(cat "$WORKDIR/last_output.txt")"
fi
