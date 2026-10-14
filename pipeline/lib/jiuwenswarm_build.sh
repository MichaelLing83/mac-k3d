#!/usr/bin/env bash
# Runs inside ICODE_LINUX_BUILD_IMAGE (jiuwenswarm_input.sh starts it through
# hgit_linux_run, which mounts the uv cache and sets UV_PYTHON_INSTALL_DIR to
# it): builds the JiuwenSwarm runtime at /opt/jiuwenswarm-host, the path a
# trial mounts it at, so every link and script in it is valid there.
#
#   jiuwenswarm_build.sh git                 the checkout is /mac-k3d-src
#   jiuwenswarm_build.sh wheel WHEEL         one workswarm wheel; dependencies from PyPI
#   jiuwenswarm_build.sh wheelhouse DIR WHEEL  WHEEL and its dependencies from DIR only
#
# JIUWENSWARM_PYTHON is the CPython minor version and JIUWENSWARM_PYTHON_PLATFORM
# the oldest glibc the wheels may need (uv --python-platform), so the runtime
# also starts in task images older than this build image.
set -euo pipefail

ROOT=/opt/jiuwenswarm-host
PY_VERSION="${JIUWENSWARM_PYTHON:?}"
PY_PLATFORM="${JIUWENSWARM_PYTHON_PLATFORM:?}"
PY_CACHE="${UV_PYTHON_INSTALL_DIR:?}"
export UV_PYTHON_PREFERENCE=only-managed
export UV_COMPILE_BYTECODE=1
export UV_LINK_MODE=copy
unset VIRTUAL_ENV

# CPython comes from this worker's uv cache (downloaded once per platform) and
# is copied into the tree, where uv then finds it as its only managed python.
uv python install "$PY_VERSION"
cached="$(uv python find "$PY_VERSION")"
cached="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$cached")"
prefix="$(cd "$(dirname "$cached")/.." && pwd)"
case "$prefix" in
  "$PY_CACHE"/*) ;;
  *) echo "ERROR: uv found python $PY_VERSION at $cached, not in its cache $PY_CACHE" >&2; exit 1 ;;
esac
mkdir -p "$ROOT/python"
rm -rf "${ROOT:?}/python/$(basename "$prefix")"
cp -a "$prefix" "$ROOT/python/"
export UV_PYTHON_INSTALL_DIR="$ROOT/python"

case "${1:-}" in
  git)
    cd /mac-k3d-src
    UV_PROJECT_ENVIRONMENT="$ROOT/venv" uv sync --frozen --no-dev --no-editable \
      --python "$PY_VERSION" --python-platform "$PY_PLATFORM"
    ;;
  wheel)
    uv venv "$ROOT/venv" --python "$PY_VERSION"
    uv pip install --python "$ROOT/venv/bin/python" --python-platform "$PY_PLATFORM" "$2"
    ;;
  wheelhouse)
    uv venv "$ROOT/venv" --python "$PY_VERSION"
    uv pip install --python "$ROOT/venv/bin/python" --python-platform "$PY_PLATFORM" \
      --no-index --find-links "$2" "$3"
    ;;
  *)
    echo "usage: jiuwenswarm_build.sh git | wheel WHEEL | wheelhouse DIR WHEEL" >&2
    exit 2
    ;;
esac

[ -x "$ROOT/venv/bin/jiuwenswarm-process" ] || {
  echo "ERROR: the install has no jiuwenswarm-process console script" >&2
  exit 1
}
mkdir -p "$ROOT/bin"
cat >"$ROOT/bin/jiuwenswarm-run" <<'WRAP'
#!/bin/sh
# JiuwenSwarm's headless runner. The console script keeps the trial's working
# directory off sys.path, and nothing here is exported to the agent's tools.
exec /opt/jiuwenswarm-host/venv/bin/jiuwenswarm-process "$@"
WRAP
cat >"$ROOT/bin/jiuwenswarm-python" <<'WRAP'
#!/bin/sh
exec /opt/jiuwenswarm-host/venv/bin/python "$@"
WRAP
chmod 755 "$ROOT/bin/jiuwenswarm-run" "$ROOT/bin/jiuwenswarm-python"

uv pip freeze --python "$ROOT/venv/bin/python" >"$ROOT/freeze.txt"
"$ROOT/venv/bin/python" - "$ROOT/runtime.json" "$PY_PLATFORM" <<'PY'
import importlib.metadata as md
import json
import platform
import sys

out, wheel_platform = sys.argv[1:3]
doc = {
    "package": "workswarm",
    "version": md.version("workswarm"),
    "python": platform.python_version(),
    "python_platform": wheel_platform,
    "machine": platform.machine(),
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
PY

# The probes run from an empty directory with nothing exported, as in a trial.
cd "$(mktemp -d)"
env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 "$ROOT/bin/jiuwenswarm-run" --help >/dev/null
env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 "$ROOT/bin/jiuwenswarm-python" -c \
  'import jiuwenswarm.channels.process_cli.machine_entry, jiuwenswarm.common.utils'
echo "OK JiuwenSwarm runtime $(sed -n 's/.*"version": "\(.*\)".*/\1/p' "$ROOT/runtime.json") starts in $ROOT"
