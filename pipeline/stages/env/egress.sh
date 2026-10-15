#!/usr/bin/env bash
# env/egress: can Harbor enforce the agent allowlist and `no-network` on this
# Docker host? Runs Harbor's own egress-control kernel probe visibly (Harbor
# itself only refuses isolation, with no Docker error). When Docker cannot run
# Harbor's probe image, the same script runs in a pinned fallback image and the
# substitution is recorded in $WORKDIR/egress_probe.json for evaluate.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

export PATH="${HOME}/.local/bin:${PATH}"
python3 "$PIPELINE_LIB/harbor_egress.py" check --out "$WORKDIR/egress_probe.json" \
  || die "Harbor cannot enforce network isolation on this worker (see the lines above)"
