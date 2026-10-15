#!/usr/bin/env bash
# E1 helper: this PC can reach the cloud Jenkins login page.
set -euo pipefail
URL="${JENKINS_URL:-}"
if [ -z "$URL" ]; then
  echo "Set JENKINS_URL=http://<cloud-ip>:17070" >&2
  exit 2
fi
LOGIN="${URL%/}/login"
code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 "$LOGIN" || true)"
echo "HTTP $code from $LOGIN"
if [ "$code" != "200" ]; then
  echo "FAIL expected HTTP 200 (cloud controller Jenkins UI)" >&2
  exit 1
fi
echo "OK Jenkins reachable"
