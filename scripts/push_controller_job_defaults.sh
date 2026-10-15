#!/usr/bin/env bash
# Interactive: scp release binary to controller, patch jenkins_job defaults, refresh jobs.
# Usage (from checkout): bash scripts/push_controller_job_defaults.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/target/release/mac-k3d"
HOST="${CONTROLLER_HOST:-root@43.107.42.252}"
CFG_REMOTE='${HOME}/.config/mac-k3d/config.yaml'

if [[ ! -x "$BIN" ]]; then
  echo "missing $BIN — run: cargo build --release" >&2
  exit 1
fi

echo "==> scp $BIN -> $HOST:/tmp/mac-k3d"
scp "$BIN" "$HOST:/tmp/mac-k3d"

echo "==> install binary, patch jenkins_job, mac-k3d config --skip-secrets"
# shellcheck disable=SC2087
ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cp -f /tmp/mac-k3d "$HOME/.local/bin/mac-k3d"
chmod +x "$HOME/.local/bin/mac-k3d"
hash -r
mac-k3d --help >/dev/null
CFG="$HOME/.config/mac-k3d/config.yaml"
test -f "$CFG"

python3 - <<'PY'
from pathlib import Path
import subprocess
import sys

try:
    import yaml
except ImportError:
    subprocess.check_call(["apt-get", "install", "-y", "python3-yaml"])
    import yaml

path = Path.home() / ".config/mac-k3d/config.yaml"
doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
job = dict(doc.get("jenkins_job") or {})
job["default_task"] = ""
job["default_tasks"] = []
job["default_n_tasks"] = 1
job["default_n_rollouts"] = 4
job["default_eval_mode"] = "git"
job["default_icode_release"] = job.get("default_icode_release") or ""
job["default_icode_git_url"] = "https://gitcode.com/michaelling/jiuwenicode"
job["default_icode_git_ref"] = "2"
job["default_icode_git_ref_kind"] = "pr"
job["default_jiuwenswarm_git_url"] = "https://gitcode.com/michaelling/jiuwenswarm"
job["default_jiuwenswarm_git_ref"] = "2"
job["default_jiuwenswarm_git_ref_kind"] = "pr"
doc["jenkins_job"] = job
path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("patched", path)
for k in (
    "default_task",
    "default_tasks",
    "default_n_tasks",
    "default_n_rollouts",
    "default_eval_mode",
    "default_icode_git_url",
    "default_icode_git_ref",
    "default_icode_git_ref_kind",
    "default_jiuwenswarm_git_url",
    "default_jiuwenswarm_git_ref",
    "default_jiuwenswarm_git_ref_kind",
):
    print(f"  {k}={job.get(k)!r}")
PY

mac-k3d config -c "$HOME/.config/mac-k3d/config.yaml" --skip-secrets
echo "OK controller job defaults refreshed"
REMOTE

echo
echo "Done. In Jenkins UI confirm Build with Parameters on lolbench_one_task / deepswe_one_task:"
echo "  TASK/TASKS empty, N_TASKS=1, N_ROLLOUTS=4; lolbench keeps ICODE_MODE=git and ICODE_GIT_*;"
echo "  deepswe_* share MODE / RELEASE_FILE / GIT_* (icode: URL=jiuwenicode REF=2 KIND=pr;"
echo "  HARNESS=jiuwenswarm redraws git defaults to jiuwenswarm PR 2 and shows AGENT_MODE / MAX_TURNS)."
echo "Jenkins: http://43.107.42.252:17070"
