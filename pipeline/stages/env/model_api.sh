#!/usr/bin/env bash
# env/model_api: the model API key is present and DEEPSEEK_MODEL is served.
# Never prints the key.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "NOTE: no DEEPSEEK_API_KEY yet. For local runs copy .env.example to .env (chmod 600). Jenkins binds deepseek-api-key. GET /models check skipped."
  exit 0
fi
[ -f "$PIPELINE_LIB/openai_compat.py" ] || die "missing pipeline/lib/openai_compat.py"
python3 "$PIPELINE_LIB/openai_compat.py" --check-model "${DEEPSEEK_MODEL}" \
  || die "DEEPSEEK_MODEL '${DEEPSEEK_MODEL}' is not returned by GET /models"
echo "OK model ${DEEPSEEK_MODEL} served by GET /models"
