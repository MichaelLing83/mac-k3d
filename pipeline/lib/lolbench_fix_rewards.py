#!/usr/bin/env python3
"""Patch LoLBench harbor_tasks verifier scripts so reward.json always has F2P/P2P counts.

Harbor bind-mounts host harbor_tasks/*/tests as /tests. DeepSWE is untouched; this only
rewrites LoLBench report_to_reward.py and test.sh so empty-patch / early-exit paths emit
integer f2p_pass/f2p_total/p2p_pass/p2p_total (DeepSWE-shaped) instead of rates-only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MARKER = "mac-k3d-lolbench-fix-rewards-v1"

_REPORT_TO_REWARD = '''\
#!/usr/bin/env python3
# {marker}
import argparse
import json
from pathlib import Path


def rate(bucket):
    total = float(bucket.get("total", 0) or 0)
    if total <= 0:
        return 0.0
    return float(bucket.get("passed", 0) or 0) / total


def counts(bucket):
    bucket = bucket if isinstance(bucket, dict) else {{}}
    total = int(bucket.get("total", 0) or 0)
    passed = int(bucket.get("passed", 0) or 0)
    return passed, total


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")


def zero_reward(harness_ok=1.0):
    return {{
        "reward": 0.0,
        "resolved": 0.0,
        "applied": 0.0,
        "build_ok": 0.0,
        "f2p_pass_rate": 0.0,
        "p2p_pass_rate": 0.0,
        "f2p_pass": 0,
        "f2p_total": 0,
        "p2p_pass": 0,
        "p2p_total": 0,
        "harness_ok": harness_ok,
    }}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="union")
    parser.add_argument("--missing-patch")
    parser.add_argument("--invalid-suite", action="store_true")
    parser.add_argument("--runner-rc", type=int, default=0)
    parser.add_argument("agent_report")
    parser.add_argument("reward_file")
    args = parser.parse_args()

    report_path = Path(args.agent_report)
    reward_path = Path(args.reward_file)
    instance_id = {instance_id!r}

    if args.invalid_suite:
        reward = zero_reward(harness_ok=1.0)
        sanitized = {{
            "instance_id": instance_id,
            "suite": args.suite,
            "applied": False,
            "resolved": False,
            "error_categories": ["invalid_lolbench_suite"],
        }}
        write_json(report_path, sanitized)
        write_json(reward_path, reward)
        print(f"LoLBench reward=0 invalid suite {{args.suite!r}}")
        return 0

    if args.missing_patch:
        reward = zero_reward(harness_ok=1.0)
        sanitized = {{
            "instance_id": instance_id,
            "suite": args.suite,
            "applied": False,
            "resolved": False,
            "error_categories": ["missing_solution_patch"],
        }}
        write_json(report_path, sanitized)
        write_json(reward_path, reward)
        print("LoLBench reward=0 missing solution patch")
        return 0

    if not report_path.exists():
        reward = zero_reward(harness_ok=0.0)
        write_json(reward_path, reward)
        print(f"LoLBench reward=0 no agent_report.json runner_rc={{args.runner_rc}}")
        return 0

    report = json.loads(report_path.read_text())
    build_status = report.get("build", {{}}).get("status")
    resolved = bool(report.get("resolved"))
    applied = bool(report.get("applied"))
    f2p_pass, f2p_total = counts(report.get("f2p", {{}}))
    p2p_pass, p2p_total = counts(report.get("p2p", {{}}))
    reward = {{
        "reward": 1.0 if resolved else 0.0,
        "resolved": 1.0 if resolved else 0.0,
        "applied": 1.0 if applied else 0.0,
        "build_ok": 1.0 if build_status == "ok" else 0.0,
        "f2p_pass_rate": rate(report.get("f2p", {{}})),
        "p2p_pass_rate": rate(report.get("p2p", {{}})),
        "f2p_pass": f2p_pass,
        "f2p_total": f2p_total,
        "p2p_pass": p2p_pass,
        "p2p_total": p2p_total,
        "harness_ok": 1.0 if args.runner_rc == 0 else 0.0,
    }}
    write_json(reward_path, reward)
    print(
        "LoLBench reward={{reward}} resolved={{resolved}} "
        "f2p={{f2p_pass_rate:.3f}} p2p={{p2p_pass_rate:.3f}}".format(**reward)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_TEST_SH = '''\
#!/usr/bin/env bash
# {marker}
set -euo pipefail

suite=${{LOLBENCH_SUITE:-union}}
mode=${{LOLBENCH_MODE:-eval}}
patch=${{LOLBENCH_SOLUTION_PATCH:-/logs/artifacts/solution.patch}}
private_log=/tmp/lolbench-private-run.log

mkdir -p /logs/verifier /logs/artifacts /in /out

case "$suite" in
    orig|aug|union) ;;
    *)
        python3 /tests/report_to_reward.py \\
            --suite "$suite" \\
            --invalid-suite \\
            /out/agent_report.json \\
            /logs/verifier/reward.json
        if [ -f /out/agent_report.json ]; then
            cp /out/agent_report.json /logs/verifier/agent_report.json
        fi
        exit 0
        ;;
esac

# Do not short-circuit on empty patch: private run_tests emits 0/N after loading selectors.
if [ "$mode" = "eval" ] && [ -s "$patch" ]; then
    cp "$patch" /in/solution.patch
    chmod 0400 /in/solution.patch
fi

set +e
LOLBENCH_MODE="$mode" \\
LOLBENCH_SUITE="$suite" \\
    /opt/lolbench/private/run_tests.sh >"$private_log" 2>&1
runner_rc=$?
set -e

cp "$private_log" /logs/verifier/private_run.log
if [ -f /out/grader_report.json ]; then
    cp /out/grader_report.json /logs/verifier/grader_report.json
fi

python3 /tests/report_to_reward.py \\
    --suite "$suite" \\
    --runner-rc "$runner_rc" \\
    /out/agent_report.json \\
    /logs/verifier/reward.json

if [ -f /out/agent_report.json ]; then
    cp /out/agent_report.json /logs/verifier/agent_report.json
fi
'''


def _instance_id_from_existing(path: Path, fallback: str) -> str:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    match = re.search(r'"instance_id":\s*"([^"]+)"', text)
    if match:
        return match.group(1)
    return fallback


def patch_task(task_dir: Path) -> bool:
    """Rewrite report_to_reward.py and test.sh for one harbor task. Returns True if changed."""
    tests = task_dir / "tests"
    if not tests.is_dir():
        return False
    tid = task_dir.name
    report_path = tests / "report_to_reward.py"
    test_sh = tests / "test.sh"
    instance_id = _instance_id_from_existing(report_path, tid)
    new_report = _REPORT_TO_REWARD.format(marker=MARKER, instance_id=instance_id)
    new_test = _TEST_SH.format(marker=MARKER)
    changed = False
    for path, content in ((report_path, new_report), (test_sh, new_test)):
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        if old == content:
            continue
        path.write_text(content, encoding="utf-8")
        if path.name == "test.sh":
            path.chmod(path.stat().st_mode | 0o111)
        changed = True
    return changed


def patch_harbor_tasks(harbor_tasks: Path) -> int:
    """Patch all task dirs under harbor_tasks. Returns number of tasks updated."""
    if not harbor_tasks.is_dir():
        raise FileNotFoundError(f"missing harbor_tasks dir: {harbor_tasks}")
    updated = 0
    for task_dir in sorted(p for p in harbor_tasks.iterdir() if p.is_dir()):
        if patch_task(task_dir):
            updated += 1
    return updated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "harbor_tasks",
        type=Path,
        help="Path to lolbench harbor_tasks directory",
    )
    args = ap.parse_args(argv)
    n = patch_harbor_tasks(args.harbor_tasks.resolve())
    print(f"OK lolbench reward fix applied to {n} task(s) under {args.harbor_tasks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
