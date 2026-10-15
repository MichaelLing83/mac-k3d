#!/usr/bin/env python3
"""Harness labels for the pinned iCode commit (report risks H1 and H2).

``tuned_on`` lists tasks the iCode improve loop tuned on. ``hinted`` lists tasks
whose instruction triggers one of iCode's keyword-matched hints. P0.11 splits
the score by both labels.

    harness_labels.py generate --icode-src DIR --loop DIR \
        --deepswe-tasks DIR --lolbench-tasks DIR --out FILE
    harness_labels.py show TASK_ID [--file FILE]

``generate`` reads iCode read-only. It extracts the matcher functions with
``ast`` instead of importing iCode, because importing iCode's SDK creates a
``logs/`` folder. Regenerate the file whenever the pinned iCode commit changes.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

LABELS_VERSION = "mac-k3d-harness-labels-v1"
DEFAULT_FILE = Path(__file__).resolve().parents[1] / "config" / "harness" / "icode-pr2-eea9d66.yaml"
NUDGE_MATCHERS = (
    "_needs_state_transition_review",
    "_needs_config_transition_review",
    "_needs_active_selection_review",
)
GATE_MATCHERS = ("canonical_mapping_task",)


def load(path: Path | None = None) -> dict:
    """Labels as sets. A missing file or missing PyYAML gives empty lists and ``available`` false."""
    target = Path(path) if path else DEFAULT_FILE
    empty = {"available": False, "file": str(target), "icode_sha": "", "tuned_on": set(), "hinted": set()}
    if not target.is_file():
        return empty
    try:
        import yaml
    except ImportError:
        return empty
    doc = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        return empty
    tuned: set[str] = set()
    hinted: set[str] = set()
    for suite in ("deepswe", "lolbench", "swebenchpro"):
        block = doc.get(suite) or {}
        if isinstance(block, dict):
            tuned |= {str(t) for t in block.get("tuned_on") or []}
            hinted |= {str(t) for t in block.get("hinted") or []}
    return {
        "available": True,
        "file": str(target),
        "icode_sha": str(doc.get("icode_sha") or ""),
        "tuned_on": tuned,
        "hinted": hinted,
    }


def labels_for(task_id: str, labels: dict) -> dict:
    return {
        "harness_tuned_on": task_id in labels.get("tuned_on", set()),
        "harness_hint": task_id in labels.get("hinted", set()),
    }


def _extract(path: Path, names: tuple[str, ...]) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    defs[target.id] = node
    need: set[str] = set()
    todo = list(names)
    while todo:
        key = todo.pop()
        if key in defs and key not in need:
            need.add(key)
            todo += [n.id for n in ast.walk(defs[key]) if isinstance(n, ast.Name) and n.id in defs]
    keep = [node for node in tree.body if any(node is defs[k] for k in need)]
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.Import)
        or (isinstance(node, ast.ImportFrom) and not node.level and not (node.module or "").startswith("openjiuwen"))
    ]
    ns: dict = {}
    exec(compile(ast.Module(body=imports + keep, type_ignores=[]), str(path), "exec"), ns)
    return ns


def matchers(icode_src: Path) -> list:
    pkg = icode_src / "openjiuwen_icode"
    nudge = _extract(pkg / "rails" / "code_edit_nudge.py", NUDGE_MATCHERS)
    gate = _extract(pkg / "features" / "implement_gate.py", GATE_MATCHERS)
    return [nudge[n] for n in NUDGE_MATCHERS] + [gate[n] for n in GATE_MATCHERS]


def hinted_tasks(tasks_dir: Path, checks: list) -> list[str]:
    hits = set()
    for path in sorted(tasks_dir.glob("*/instruction.md")):
        text = path.read_text(encoding="utf-8")
        if any(check(text) for check in checks):
            hits.add(path.parent.name)
    return sorted(hits)


def tuned_tasks(loop_config: Path) -> list[str]:
    import yaml

    doc = yaml.safe_load(loop_config.read_text(encoding="utf-8")) or {}
    return sorted(str(t) for t in doc.get("task_allowlist") or [])


def _yaml_list(key: str, items: list[str]) -> list[str]:
    if not items:
        return [f"  {key}: []"]
    return [f"  {key}:"] + [f"    - {item}" for item in items]


def generate(args: argparse.Namespace) -> int:
    icode_src = Path(args.icode_src)
    sha = subprocess.run(
        ["git", "-C", str(icode_src), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    checks = matchers(icode_src)
    loop = Path(args.loop)
    deepswe_tuned = tuned_tasks(loop / "config" / "optimize-deepswe.yaml")
    lolbench_tuned = tuned_tasks(loop / "config" / "optimize-lolbench.yaml")
    deepswe_hinted = hinted_tasks(Path(args.deepswe_tasks), checks)
    lolbench_hinted = hinted_tasks(Path(args.lolbench_tasks), checks)
    lines = [
        "# Generated by pipeline/lib/harness_labels.py generate; do not edit by hand.",
        "# tuned_on: improve loop config/optimize-<suite>.yaml task_allowlist. An empty LoLBench",
        "# allowlist means every LoLBench task was eligible, which this file cannot express.",
        f"# hinted: tasks whose instruction.md triggers {', '.join(NUDGE_MATCHERS + GATE_MATCHERS)}.",
        f"version: {LABELS_VERSION}",
        f"icode_sha: {sha}",
        "deepswe:",
        *_yaml_list("tuned_on", deepswe_tuned),
        *_yaml_list("hinted", deepswe_hinted),
        "lolbench:",
        *_yaml_list("tuned_on", lolbench_tuned),
        *_yaml_list("hinted", lolbench_hinted),
        "",
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {out}: deepswe tuned {len(deepswe_tuned)} hinted {len(deepswe_hinted)}; "
        f"lolbench tuned {len(lolbench_tuned)} hinted {len(lolbench_hinted)}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--icode-src", required=True)
    gen.add_argument("--loop", required=True)
    gen.add_argument("--deepswe-tasks", required=True)
    gen.add_argument("--lolbench-tasks", required=True)
    gen.add_argument("--out", default=str(DEFAULT_FILE))
    show = sub.add_parser("show")
    show.add_argument("task_id")
    show.add_argument("--file", default="")
    args = ap.parse_args()
    if args.cmd == "generate":
        return generate(args)
    labels = load(Path(args.file) if args.file else None)
    if not labels["available"]:
        print(f"labels unavailable: {labels['file']}", file=sys.stderr)
        return 1
    print(labels_for(args.task_id, labels))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
