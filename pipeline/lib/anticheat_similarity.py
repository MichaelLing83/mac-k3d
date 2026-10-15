#!/usr/bin/env python3
"""Line and path Jaccard of a rollout patch against the task's gold patch.

Only added lines count. Test files, generated files (per-suite globs in
pipeline/config/anticheat-v1.json) and trivial lines are dropped first, as
LoLBench's policy advises. Lines are compared stripped, as sets.

    anticheat_similarity.py --patch PATCH --gold GOLD [--suite lolbench]

Prints one JSON object: line_j, path_j, n_added, n_gold_added, top_files.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import Path

from anticheat_leakscan import is_test_path

CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "anticheat-v1.json"
TOP_FILES = 5
_TRIVIAL = {
    "pass",
    "else:",
    "try:",
    "finally:",
    "return",
    "break",
    "continue",
    "end",
    "fi",
    "done",
    "else {",
    "} else {",
    '"""',
    "'''",
}
_ALNUM = re.compile(r"[A-Za-z0-9]")


def load_config(path: Path | None = None) -> dict:
    return json.loads(Path(path or CONFIG_FILE).read_text(encoding="utf-8"))


def generated_globs(config: dict, suite: str) -> list[str]:
    gen = config.get("generated") or {}
    return list(gen.get("common") or []) + list(gen.get(suite) or [])


def is_generated(path: str, globs: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(path, g) or fnmatch.fnmatchcase(name, g) for g in globs)


def is_trivial(line: str) -> bool:
    return line in _TRIVIAL or not _ALNUM.search(line)


def _target(header: str) -> str | None:
    path = header[4:].strip().split("\t", 1)[0]
    if path == "/dev/null":
        return None
    return path[2:] if path.startswith(("a/", "b/")) else path


def parse_patch(text: str) -> dict[str, dict]:
    """Per file: added lines (stripped, in order) and whether the file is new."""
    files: dict[str, dict] = {}
    current: dict | None = None
    new_file = False
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            current = None
            new_file = False
            continue
        if raw.startswith("new file mode"):
            new_file = True
            continue
        if raw.startswith("--- "):
            new_file = new_file or raw[4:].strip().split("\t", 1)[0] == "/dev/null"
            continue
        if raw.startswith("+++ "):
            path = _target(raw)
            if path is None:
                current = None
                continue
            current = files.setdefault(path, {"added": [], "new": new_file})
            current["new"] = current["new"] or new_file
            continue
        if current is not None and raw.startswith("+"):
            line = raw[1:].strip()
            if line:
                current["added"].append(line)
    return files


def counted_files(parsed: dict[str, dict], globs: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path, info in parsed.items():
        if is_test_path(path) or is_generated(path, globs):
            continue
        out[path] = {line for line in info["added"] if not is_trivial(line)}
    return out


def jaccard(a: set, b: set) -> float:
    union = a | b
    return round(len(a & b) / len(union), 4) if union else 0.0


def similarity(patch_text: str, gold_text: str, globs: list[str]) -> dict:
    mine = counted_files(parse_patch(patch_text), globs)
    gold = counted_files(parse_patch(gold_text), globs)
    mine_lines = set().union(*mine.values()) if mine else set()
    gold_lines = set().union(*gold.values()) if gold else set()
    top = sorted(
        ({"path": p, "overlap": len(lines & gold_lines), "added": len(lines)} for p, lines in mine.items()),
        key=lambda row: (-row["overlap"], row["path"]),
    )
    return {
        "line_j": jaccard(mine_lines, gold_lines),
        "path_j": jaccard(set(mine), set(gold)),
        "n_added": len(mine_lines),
        "n_gold_added": len(gold_lines),
        "top_files": [row for row in top[:TOP_FILES] if row["overlap"]],
    }


def deliverable_names(gold_text: str, globs: list[str], stop: set[str], aliases: list[str]) -> set[str]:
    """Names of modules and files that the gold patch creates: new directories and new file stems."""
    parsed = parse_patch(gold_text)
    names = set(aliases)
    touched_dirs = {p.rsplit("/", 1)[0] for p, info in parsed.items() if not info["new"] and "/" in p}
    for path, info in parsed.items():
        if not info["new"] or is_test_path(path) or is_generated(path, globs):
            continue
        parts = path.split("/")
        stem = parts[-1].split(".", 1)[0]
        if len(stem) >= 4 and stem not in stop:
            names.add(stem)
        if len(parts) > 1 and "/".join(parts[:-1]) not in touched_dirs:
            parent = parts[-2]
            if len(parent) >= 4 and parent not in stop:
                names.add(parent)
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--patch", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--suite", default="deepswe")
    ap.add_argument("--config", default="")
    args = ap.parse_args()
    config = load_config(Path(args.config) if args.config else None)
    patch = Path(args.patch).read_text(encoding="utf-8", errors="replace")
    gold = Path(args.gold).read_text(encoding="utf-8", errors="replace")
    print(json.dumps(similarity(patch, gold, generated_globs(config, args.suite)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
