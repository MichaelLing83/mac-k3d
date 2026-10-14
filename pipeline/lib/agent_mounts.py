#!/usr/bin/env python3
"""Harbor --mounts for the harness's agent, and the allowlist tasks/isolation checks before any rollout.

The only mount is the harness tree, read-only, at the harness's target
(harness.py: /opt/icode-host for iCode, /opt/jiuwenswarm-host for
JiuwenSwarm). It must not be, contain, or sit inside a benchmark checkout
(solution/ and tests/private).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import harness

ICODE_TARGET = harness.get("icode").mount


def build_mounts(root: Path, target: str = ICODE_TARGET) -> list[dict]:
    return [{"type": "bind", "source": str(root), "target": target, "read_only": True}]


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def check_mounts(mounts: object, root: Path, forbidden: list[Path], target: str = ICODE_TARGET) -> list[str]:
    errors: list[str] = []
    if not isinstance(mounts, list):
        return ["mounts must be a JSON list"]
    if len(mounts) != 1:
        errors.append(f"expected exactly one mount (the harness tree), got {len(mounts)}")
    root = root.resolve()
    for index, mount in enumerate(mounts):
        if not isinstance(mount, dict):
            errors.append(f"mount {index} is not an object")
            continue
        if mount.get("type") != "bind":
            errors.append(f"mount {index} type must be bind")
        if mount.get("target") != target:
            errors.append(f"mount {index} target must be {target}")
        if mount.get("read_only") is not True:
            errors.append(f"mount {index} must set read_only: true")
        source = str(mount.get("source") or "")
        if not source or Path(source).resolve() != root:
            errors.append(f"mount {index} source {source or '<empty>'} is not the harness tree {root}")
    for path in forbidden:
        if not str(path):
            continue
        bench = path.resolve()
        if _overlaps(root, bench):
            errors.append(f"harness tree {root} overlaps benchmark path {bench}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent mounts for the evaluate phase")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("build", "check"):
        p = sub.add_parser(name)
        p.add_argument("--root", "--icode-root", dest="root", required=True, help="the harness tree")
        p.add_argument("--target", default="", help="mount target (default: $HARNESS's, harness.py)")
        if name == "check":
            p.add_argument("--mounts", required=True, help="JSON list passed to harbor --mounts")
            p.add_argument("--forbid", action="append", default=[], help="benchmark dir the tree must not overlap")
    args = parser.parse_args(argv)
    try:
        target = args.target or harness.current().mount
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.cmd == "build":
        print(json.dumps(build_mounts(Path(args.root), target)))
        return 0
    try:
        mounts = json.loads(args.mounts)
    except json.JSONDecodeError as exc:
        print(f"ERROR: --mounts is not JSON: {exc}", file=sys.stderr)
        return 1
    errors = check_mounts(mounts, Path(args.root), [Path(p) for p in args.forbid if p], target)
    for err in errors:
        print(f"ERROR: agent mounts: {err}", file=sys.stderr)
    if errors:
        return 1
    print(f"OK agent mounts: one read-only bind of {Path(args.root).resolve()} at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
