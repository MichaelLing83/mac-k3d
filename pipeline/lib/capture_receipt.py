#!/usr/bin/env python3
"""Host side of the capture path (icode_capture.sh runs inside the task container).

declared-repo  Print the repo path and base commit a task declares, one per line,
               for the evaluate phase and tools/grade_baseline to pass as MAC_K3D_REPO and MAC_K3D_BASE_COMMIT.
               LoLBench: /workspace/<metadata.project> and the image tag
               lolbench-base. DeepSWE and SWE-bench Pro: the `cd <path>` and the
               commit in the first [[verifier.collect]] command.
check-trials   anticheat/receipts, first. Every trial in this build's Harbor jobs dir must
               belong to a selected task; exit 1 naming any that does not.
annotate       anticheat/receipts. For every trial, compare the patch the grader read with the
               capture receipt and write agent/capture_flags.json. Same check
               for every suite; only the grader's file name differs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

FLAGS_SCHEMA = "mac-k3d-capture-flags-v1"
OVERSIZE_BYTES = 1_048_576
GRADER_PATCH = {
    "lolbench": "solution.patch",
    "deepswe": "model.patch",
    "swebenchpro": "model.patch",
}
_CD = re.compile(r"(?:^|&&|;)\s*cd\s+([^\s;&]+)")
_SHA = re.compile(r"\bgit diff\b.*?\s([0-9a-f]{7,40})\b")


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def declared_repo(task_dir: Path, benchmark: str) -> tuple[str, str]:
    data = _load_toml(task_dir / "task.toml")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if benchmark == "lolbench":
        project = str(meta.get("project") or "").strip()
        return (f"/workspace/{project}" if project else "", "lolbench-base")
    verifier = data.get("verifier") if isinstance(data.get("verifier"), dict) else {}
    collect = verifier.get("collect") if isinstance(verifier.get("collect"), list) else []
    command = ""
    if collect and isinstance(collect[0], dict):
        command = str(collect[0].get("command") or "")
    repo = ""
    match = _CD.search(command)
    if match:
        repo = match.group(1)
    base = ""
    sha = _SHA.search(command)
    if sha:
        base = sha.group(1)
    else:
        base = str(meta.get("base_commit_hash") or meta.get("base_commit") or "").strip()
    return repo, base


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def binary_paths(patch_text: str) -> set[str]:
    """Paths whose hunks are binary in a git diff --binary patch."""
    found: set[str] = set()
    current = ""
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            current = parts[1] if len(parts) == 2 else ""
        elif current and (line.startswith("GIT binary patch") or line.startswith("Binary files ")):
            found.add(current)
    return found


def _gold_binary(tasks_dir: Path, tid: str) -> set[str]:
    gold = tasks_dir / tid / "solution" / "solution.patch"
    try:
        return binary_paths(gold.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return set()


def base_mismatch(receipt_base: str, declared_base: str) -> bool:
    """True when the agent started from a commit the task did not declare.

    The declared value may be abbreviated (``git diff abc1234``), so compare on
    the shorter of the two. ``lolbench-base`` is a sentinel, not a commit, and
    an empty side means there is nothing to check rather than a mismatch.
    """
    a = receipt_base.strip().lower()
    b = declared_base.strip().lower()
    if not a or not b or b == "lolbench-base":
        return False
    if not re.fullmatch(r"[0-9a-f]{7,40}", b) or not re.fullmatch(r"[0-9a-f]{7,40}", a):
        return False
    width = min(len(a), len(b))
    return a[:width] != b[:width]


def trial_flags(trial: Path, benchmark: str, gold_binary: set[str], declared_base: str = "") -> dict:
    """Flags for one trial: capture_missing, capture_error, capture_mismatch, patch_oversize."""
    flags: list[str] = []
    receipt_path = trial / "agent" / "capture.json"
    receipt: dict = {}
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            flags.append("capture_error")
    else:
        flags.append("capture_missing")
    patch = receipt.get("patch") if isinstance(receipt.get("patch"), dict) else {}
    receipt_sha = str(patch.get("sha256") or "")
    receipt_bytes = patch.get("bytes") if isinstance(patch.get("bytes"), int) else None
    if receipt.get("errors"):
        flags.append("capture_error")

    name = GRADER_PATCH.get(benchmark, "model.patch")
    grader = trial / "artifacts" / "logs" / "artifacts" / name
    grader_info: dict = {"path": str(grader.relative_to(trial)), "present": grader.is_file()}
    if grader.is_file():
        grader_info["bytes"] = grader.stat().st_size
        grader_info["sha256"] = sha256_file(grader)
    if receipt:
        if grader.is_file():
            if grader_info["sha256"] != receipt_sha:
                flags.append("capture_mismatch")
        elif receipt_bytes:
            flags.append("capture_mismatch")

    receipt_binary = {str(p) for p in patch.get("binary_paths") or []}
    extra_binary = sorted(receipt_binary - gold_binary)
    if receipt.get("oversize") or (receipt_bytes or 0) > OVERSIZE_BYTES or extra_binary:
        flags.append("patch_oversize")

    # The agent env no longer carries the declared base, so the container cannot
    # check it; task.toml is only readable here on the host.
    receipt_base = str(receipt.get("base_sha") or "")
    if base_mismatch(receipt_base, declared_base):
        flags.append("base_commit_mismatch")

    return {
        "schema": FLAGS_SCHEMA,
        "benchmark": benchmark,
        "flags": sorted(set(flags)),
        "receipt_sha256": receipt_sha or None,
        "receipt_bytes": receipt_bytes,
        "grader_patch": grader_info,
        "binary_not_in_gold": extra_binary,
        "base_sha": receipt_base or None,
        "declared_base": declared_base or None,
    }


def check_trials(harness_dir: Path, task_ids: list[str]) -> int:
    """0 when every trial this build's Harbor run wrote belongs to a selected task.

    A trial no task claims is left out of the anti-cheat, the score and the
    report, which would then publish a lower Pass@1 than Harbor measured.
    """
    from score_results import current_jobs_dir, harbor_task_trials, trial_task_ids, unmatched_trials

    jobs = current_jobs_dir(harness_dir)
    if jobs is None:
        print(f"check trials: no harbor_jobs_dir.txt naming a dir under {harness_dir / 'harbor_runs'}; nothing to check")
        return 0
    stray = unmatched_trials(jobs, task_ids)
    if stray:
        print(
            f"ERROR: {len(stray)} Harbor trial(s) in {jobs} match no selected task, so anti-cheat, "
            "score and report would leave them out:",
            file=sys.stderr,
        )
        for trial in stray:
            names = ", ".join(sorted(trial_task_ids(trial))) or "no task recorded"
            print(f"  {trial.relative_to(jobs)}: {names}", file=sys.stderr)
        return 1
    matched = sum(len(harbor_task_trials(harness_dir, tid)) for tid in task_ids)
    print(f"OK trials: {matched} matched for {len(task_ids)} tasks")
    return 0


def annotate(harness_dir: Path, tasks_dir: Path, task_ids: list[str], benchmark: str) -> dict:
    from score_results import harbor_task_trials

    summary: dict[str, int] = {"trials": 0}
    for tid in task_ids:
        gold_binary = _gold_binary(tasks_dir, tid)
        _, declared_base = declared_repo(tasks_dir / tid, benchmark)
        for trial in harbor_task_trials(harness_dir, tid):
            result = trial_flags(trial, benchmark, gold_binary, declared_base)
            result["task"] = tid
            summary["trials"] += 1
            for flag in result["flags"]:
                summary[flag] = summary.get(flag, 0) + 1
            out = trial / "agent" / "capture_flags.json"
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            except OSError as exc:
                print(f"WARNING: could not write {out}: {exc}", file=sys.stderr)
            if result["flags"]:
                print(f"capture {tid} {trial.name}: {', '.join(result['flags'])}")
    return summary


def _read_task_ids(path: Path) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture path: declared repo and receipt checks")
    sub = parser.add_subparsers(dest="cmd", required=True)

    decl = sub.add_parser("declared-repo")
    decl.add_argument("--task-dir", required=True)
    decl.add_argument("--benchmark", default="deepswe")

    check = sub.add_parser("check-trials")
    check.add_argument("--harness-dir", required=True)
    check.add_argument("--task-file", required=True)

    note = sub.add_parser("annotate")
    note.add_argument("--harness-dir", required=True)
    note.add_argument("--tasks-dir", required=True)
    note.add_argument("--task-file", required=True)
    note.add_argument("--benchmark", default="deepswe")

    args = parser.parse_args(argv)
    if args.cmd == "declared-repo":
        repo, base = declared_repo(Path(args.task_dir), args.benchmark.strip().lower())
        print(repo)
        print(base)
        return 0
    if args.cmd == "check-trials":
        return check_trials(Path(args.harness_dir), _read_task_ids(Path(args.task_file)))
    summary = annotate(
        Path(args.harness_dir),
        Path(args.tasks_dir),
        _read_task_ids(Path(args.task_file)),
        args.benchmark.strip().lower(),
    )
    print("capture receipts: " + " ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
