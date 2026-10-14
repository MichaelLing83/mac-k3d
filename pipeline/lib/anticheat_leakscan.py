#!/usr/bin/env python3
"""Report files in the mounted harness tree (iCode or JiuwenSwarm) that contain a task's gold patch.

Gold lines follow the report's H3 audit rule: unique stripped lines of at least
25 characters, not starting with '#', added in non-test files of
solution/solution.patch. A file is a hit when it holds at least 30% of a task's
gold lines and at least 20 of them. tasks/leakscan runs this before the first rollout.

Exit codes: 0 no hits, 2 hits, 1 error. Only paths and counts are printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SCANNER_VERSION = "mac-k3d-leakscan-v1"
MIN_LINE_LEN = 25
MIN_SHARE = 0.30
MIN_LINES = 20
SKIP_DIRS = {".git"}
_SNIFF = 8192
_TEST_DIRS = {"test", "tests", "testing", "testdata", "__tests__", "spec", "specs"}


def is_test_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    lower = name.lower()
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if any(part.lower() in _TEST_DIRS for part in parts[:-1]):
        return True
    if lower.startswith("test_") or lower == "conftest.py":
        return True
    if stem.lower().endswith(("_test", "_tests", ".test", ".spec", "_spec")):
        return True
    return stem.endswith(("Test", "Tests"))


def _distinctive(text: str) -> str | None:
    line = text.strip()
    if len(line) < MIN_LINE_LEN or line.startswith("#"):
        return None
    return line


def gold_lines(patch_text: str) -> set[str]:
    lines: set[str] = set()
    current: str | None = None
    for raw in patch_text.splitlines():
        if raw.startswith("diff --git "):
            current = None
            continue
        if raw.startswith("+++ "):
            target = raw[4:].strip().split("\t", 1)[0]
            if target == "/dev/null":
                current = None
            else:
                current = target[2:] if target.startswith(("a/", "b/")) else target
            continue
        if current is None or not raw.startswith("+") or is_test_path(current):
            continue
        line = _distinctive(raw[1:])
        if line is not None:
            lines.add(line)
    return lines


def task_ids(tasks_dir: Path, selected: Path | None) -> list[str]:
    if selected is None:
        return sorted(p.name for p in tasks_dir.iterdir() if p.is_dir() and not p.is_symlink())
    return [line.strip() for line in selected.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_gold(tasks_dir: Path, ids: list[str]) -> dict[str, set[str] | None]:
    gold: dict[str, set[str] | None] = {}
    for tid in ids:
        task = tasks_dir / tid
        if not task.is_dir():
            raise FileNotFoundError(f"task {tid} not found under {tasks_dir}")
        patch = task / "solution" / "solution.patch"
        gold[tid] = gold_lines(patch.read_text(encoding="utf-8", errors="ignore")) if patch.is_file() else None
    return gold


def _file_lines(path: Path) -> set[str] | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(_SNIFF)
            if b"\0" in head:
                return None
            data = head + handle.read()
    except OSError:
        return None
    found: set[str] = set()
    for raw in data.decode("utf-8", errors="ignore").splitlines():
        line = _distinctive(raw)
        if line is not None:
            found.add(line)
    return found


def scan_tree(tree: Path, gold: dict[str, set[str] | None]) -> dict[str, list[dict]]:
    index: dict[str, list[str]] = {}
    for tid, lines in gold.items():
        if lines is None or len(lines) < MIN_LINES:
            continue
        for line in lines:
            index.setdefault(line, []).append(tid)
    hits: dict[str, list[dict]] = {}
    if not index:
        return hits
    for dirpath, dirs, files in os.walk(tree, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        here = Path(dirpath)
        for name in sorted(files):
            path = here / name
            if path.is_symlink():
                continue
            seen = _file_lines(path)
            if not seen:
                continue
            counts: Counter[str] = Counter()
            for line in seen:
                for tid in index.get(line, ()):
                    counts[tid] += 1
            for tid, matched in counts.items():
                total = len(gold[tid] or ())
                if matched >= MIN_LINES and matched >= MIN_SHARE * total:
                    hits.setdefault(tid, []).append(
                        {
                            "path": path.relative_to(tree).as_posix(),
                            "matched": matched,
                            "share": round(matched / total, 4),
                        }
                    )
    return hits


def scan(tree: Path, tasks_dir: Path, ids: list[str]) -> dict:
    gold = load_gold(tasks_dir, ids)
    hits = scan_tree(tree, gold)
    tasks: dict[str, dict] = {}
    for tid in ids:
        lines = gold[tid]
        if lines is None:
            status = "no_gold"
        elif tid in hits:
            status = "hit"
        elif len(lines) < MIN_LINES:
            status = "too_small"
        else:
            status = "clean"
        tasks[tid] = {
            "status": status,
            "gold_lines": len(lines or ()),
            "hits": hits.get(tid, []),
        }
    return {
        "scanner": SCANNER_VERSION,
        "tree": str(tree),
        "tasks_dir": str(tasks_dir),
        "thresholds": {"min_line_len": MIN_LINE_LEN, "min_share": MIN_SHARE, "min_lines": MIN_LINES},
        "tasks": tasks,
        "hit_tasks": sorted(hits),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan the mounted harness tree for task gold patches")
    parser.add_argument("--tree", required=True)
    parser.add_argument("--tasks-dir", required=True)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--selected", help="file with one task id per line")
    which.add_argument("--all", action="store_true", help="every task directory under --tasks-dir")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    tree = Path(args.tree)
    tasks_dir = Path(args.tasks_dir)
    try:
        if not tree.is_dir():
            raise FileNotFoundError(f"missing harness tree {tree}")
        if not tasks_dir.is_dir():
            raise FileNotFoundError(f"missing tasks dir {tasks_dir}")
        ids = task_ids(tasks_dir, Path(args.selected) if args.selected else None)
        report = scan(tree, tasks_dir, ids)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: leakscan: {exc}", file=sys.stderr)
        return 1
    statuses = Counter(task["status"] for task in report["tasks"].values())
    for tid in report["hit_tasks"]:
        task = report["tasks"][tid]
        for hit in task["hits"]:
            print(f"LEAK task={tid} file={hit['path']} matched={hit['matched']}/{task['gold_lines']}")
    print(
        "leakscan tasks={n} clean={c} hit={h} too_small={s} no_gold={g} out={out}".format(
            n=len(ids),
            c=statuses["clean"],
            h=statuses["hit"],
            s=statuses["too_small"],
            g=statuses["no_gold"],
            out=args.out,
        )
    )
    return 2 if report["hit_tasks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
