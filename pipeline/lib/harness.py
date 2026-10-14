"""The harness under test (HARNESS): one row per harness for the Python steps.

pipeline/stages/_common.sh (harness_display, harness_mount_target) and
harness_lib.sh (harness_check_supported) are the same table for the shell
steps; test_jiuwenswarm.py keeps them in step.

  python3 harness.py get [NAME] FIELD    one field; NAME defaults to $HARNESS
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Harness:
    name: str
    display: str
    # Where a trial sees the harness tree, read-only.
    mount: str
    # Harbor `-a` import paths, relative to pipeline/lib.
    agent: str
    canary: str
    # None: every benchmark.
    benchmarks: tuple[str, ...] | None
    # Secret-like names the agent's run shell holds: the model key.
    key_names: tuple[str, ...]


DEFAULT = "icode"
HARNESSES = {
    "icode": Harness(
        name="icode",
        display="iCode",
        mount="/opt/icode-host",
        agent="icode_harbor_agent:ICodeAgent",
        canary="canary_harbor_agent:CanaryAgent",
        benchmarks=None,
        key_names=("DEEPSEEK_API_KEY",),
    ),
    "jiuwenswarm": Harness(
        name="jiuwenswarm",
        display="JiuwenSwarm",
        mount="/opt/jiuwenswarm-host",
        agent="jiuwenswarm_harbor_agent:JiuwenSwarmAgent",
        canary="jiuwenswarm_harbor_agent:JiuwenSwarmCanaryAgent",
        benchmarks=("deepswe",),
        key_names=("API_KEY",),
    ),
}


def get(name: str | None = None) -> Harness:
    key = (name or "").strip() or DEFAULT
    if key not in HARNESSES:
        raise ValueError(f"unknown harness {key!r} (use {' or '.join(sorted(HARNESSES))})")
    return HARNESSES[key]


def current(env: dict[str, str] | None = None) -> Harness:
    return get((os.environ if env is None else env).get("HARNESS"))


def from_record(record: dict | None) -> Harness:
    """The harness a run used: harness.json's name, or iCode for runs older than it."""
    name = record.get("name") if isinstance(record, dict) else None
    return get(name if isinstance(name, str) and name in HARNESSES else None)


def supports(harness: Harness, benchmark: str) -> bool:
    return harness.benchmarks is None or benchmark in harness.benchmarks


def load_record(workdir: Path) -> dict | None:
    """$WORKDIR/harness.json from tasks/harness, if any."""
    path = Path(workdir) / "harness.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("get")
    p.add_argument("args", nargs="+", metavar="[NAME] FIELD")
    args = parser.parse_args(argv)
    if len(args.args) > 2:
        parser.error("get takes [NAME] FIELD")
    name, field = (args.args if len(args.args) == 2 else (None, args.args[0]))
    try:
        harness = get(name) if name is not None else current()
    except ValueError as exc:
        print(f"harness.py: {exc}", file=sys.stderr)
        return 2
    if field not in Harness.__dataclass_fields__:
        print(f"harness.py: unknown field {field!r}", file=sys.stderr)
        return 2
    value = getattr(harness, field)
    if isinstance(value, tuple):
        value = " ".join(value)
    print("" if value is None else value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
