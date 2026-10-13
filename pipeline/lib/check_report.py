#!/usr/bin/env python3
"""Validate eval JSON has required report fields (no network)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REQUIRED_TOP = (
    "harness",
    "suite",
    "n_tasks",
    "n_rollouts",
    "tasks",
    "access_date_utc",
    "model",
    "model_served",
    "api_base",
    "wall_seconds",
    "wall_minutes",
    "pass_at_1",
    "macro",
    "tokens",
)
REQUIRED_MACRO = ("f2p", "p2p", "reward")
REQUIRED_TOKEN = ("in", "out", "total")
REQUIRED_TASK = (
    "id",
    "c",
    "n",
    "pass_frac",
    "first",
    "reward",
    "f2p",
    "p2p",
    "tok_in",
    "tok_out",
    "dur_s",
    "dur_min",
    "baseline",
)
REQUIRED_BASELINE = ("reward", "tok_in", "tok_out", "dur_s", "dur_min")


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _present_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def tasks_with_trials(doc: dict) -> set[str]:
    """Questions with at least one trial in the report."""
    arm = doc.get("icode") if isinstance(doc.get("icode"), dict) else {}
    out = set()
    for row in arm.get("tasks") or []:
        if not isinstance(row, dict):
            continue
        rollouts = [a for a in row.get("rollouts") or [] if isinstance(a, dict)]
        if row.get("n_scored") or any(a.get("trial") for a in rollouts):
            out.add(str(row.get("id")))
    return out


def _ran_shards(protocol: dict) -> list[dict]:
    shards = protocol.get("shards") if isinstance(protocol.get("shards"), list) else []
    return [s for s in shards if isinstance(s, dict) and not s.get("not_run") and not s.get("not_run_count")]


def official_isolation_errors(isolation: object, doc: dict | None = None) -> list[str]:
    """The same isolation protocol must hold for every benchmark and model (docs/evaluation.md).

    A merged report keeps each shard's leak scan and canary hashes in its shard rows.
    """
    if not isinstance(isolation, dict):
        return ["missing eval_protocol.isolation"]
    doc = doc or {}
    protocol = doc.get("eval_protocol") if isinstance(doc.get("eval_protocol"), dict) else {}
    ran = _ran_shards(protocol)
    errors: list[str] = []
    loc = "eval_protocol.isolation"
    mode = isolation.get("mode")
    if mode == "git":
        if not _nonempty_str(isolation.get("sanitizer")):
            errors.append(f"missing {loc}.sanitizer")
        if isolation.get("sourceless") is not True:
            errors.append(f"{loc}.sourceless must be true")
        for key in ("tree_sha256", "runtime_sha256"):
            if not _SHA256.match(str(isolation.get(key) or "")):
                errors.append(f"{loc}.{key} must be a sha256")
        if isolation.get("manifest_matches_tree") is not True:
            errors.append(f"{loc}.manifest_matches_tree must be true (the tree changed after sanitizing)")
    elif mode != "release":
        errors.append(f"{loc}.mode must be git or release")
    mount = isolation.get("mount") if isinstance(isolation.get("mount"), dict) else {}
    if mount.get("count") != 1 or mount.get("target") != "/opt/icode-host" or mount.get("read_only") is not True:
        errors.append(f"{loc}.mount must be one read-only mount at /opt/icode-host")
    leak = isolation.get("leak_scan") if isinstance(isolation.get("leak_scan"), dict) else {}
    leak_hashed = _nonempty_str(leak.get("report_sha256")) or (
        bool(ran) and all(_nonempty_str(s.get("leak_scan_sha256")) for s in ran)
    )
    hits = leak.get("hit_tasks")
    if not _nonempty_str(leak.get("scanner")) or not leak_hashed:
        errors.append(f"missing {loc}.leak_scan")
    elif not isinstance(hits, list):
        errors.append(f"{loc}.leak_scan.hit_tasks must be a list")
    else:
        # A leak hit is skipped (tasks/leakscan); one that still has trials leaked.
        leaked = sorted({str(t) for t in hits} & tasks_with_trials(doc))
        if leaked:
            errors.append(f"{loc}.leak_scan: hit questions have trials ({', '.join(leaked)})")
    canary = isolation.get("canary") if isinstance(isolation.get("canary"), dict) else {}
    canary_hashed = _nonempty_str(canary.get("summary_sha256")) or (
        bool(ran)
        and all(isinstance(s.get("canary"), dict) and _nonempty_str(s["canary"].get("summary_sha256")) for s in ran)
    )
    if not _nonempty_str(canary.get("version")) or not canary_hashed:
        errors.append(f"missing {loc}.canary (evaluate/canary isolation canary)")
    elif canary.get("status") != "pass":
        errors.append(f"{loc}.canary.status must be pass")
    return errors


def _environment_units(protocol: dict) -> list[tuple[str, dict, dict, dict]]:
    """(label, worker, env_check, image platforms) per build that ran: each shard of a merged run, else the run."""
    units = []
    for row in _ran_shards(protocol):
        worker = row.get("worker") if isinstance(row.get("worker"), dict) else {}
        checks = row.get("env_check") if isinstance(row.get("env_check"), dict) else {}
        platforms = row.get("image_platforms") if isinstance(row.get("image_platforms"), dict) else {}
        units.append((f"eval_protocol.shards[{row.get('shard')}]", worker, checks, platforms))
    if units:
        return units
    worker = protocol.get("worker") if isinstance(protocol.get("worker"), dict) else {}
    checks = protocol.get("env_check") if isinstance(protocol.get("env_check"), dict) else {}
    images = protocol.get("images") if isinstance(protocol.get("images"), dict) else {}
    platforms = {
        tid: image.get("platform") for tid, image in images.items() if isinstance(image, dict) and image.get("platform")
    }
    return [("eval_protocol", worker, checks, platforms)]


def _native(entry: dict) -> bool:
    return (entry.get("mode") or ("native" if entry.get("source") == "built" else "emulated")) == "native"


def environment_errors(doc: dict) -> list[str]:
    """Every image ran on the task containers' platform, and a scored question that ran a native
    DeepSWE build had a passing check. The published image under emulation needs no check."""
    protocol = doc.get("eval_protocol") if isinstance(doc.get("eval_protocol"), dict) else {}
    errors: list[str] = []
    scored = tasks_with_trials(doc)
    for label, worker, checks, platforms in _environment_units(protocol):
        want = str(worker.get("container_platform") or "")
        proven = set()
        for tid, entry in sorted(checks.items()):
            if not isinstance(entry, dict):
                continue
            got = str(entry.get("platform") or "")
            if want and got != want:
                errors.append(f"{label}.env_check.{tid}: {got or '-'} image, but the task containers ran {want}")
            if not _native(entry):
                continue
            if entry.get("pass") is True:
                proven.add(str(tid))
            elif str(tid) in scored:
                errors.append(f"{label}.env_check.{tid}: native build without a passing check (mac-k3d eval prepare)")
        if not want:
            continue
        for tid, got in sorted(platforms.items()):
            if str(tid) in scored and str(got) != want:
                errors.append(f"{label}: image of {tid} is {got}, but the task containers ran {want} (container_platform.json)")
        if doc.get("suite") == "deepswe" and want != "linux/amd64":
            unproven = sorted(str(tid) for tid in platforms if str(tid) in scored and str(tid) not in proven)
            if unproven:
                errors.append(
                    f"{label}: scored DeepSWE questions ran {want} images with no passing native check "
                    f"(mac-k3d eval prepare): {', '.join(unproven)}"
                )
    return errors


def official_provenance_errors(doc: dict) -> list[str]:
    """Every comparison artifact must record the same provenance. There is no smoke exception."""
    errors: list[str] = []
    protocol = doc.get("eval_protocol")
    if not isinstance(protocol, dict):
        protocol = {}
    harbor = protocol.get("harbor") if isinstance(protocol.get("harbor"), dict) else {}
    if not _nonempty_str(harbor.get("version")):
        errors.append("missing eval_protocol.harbor.version")
    bench = protocol.get("benchmark") if isinstance(protocol.get("benchmark"), dict) else {}
    for key in ("url", "sha"):
        if not _nonempty_str(bench.get(key)):
            errors.append(f"missing eval_protocol.benchmark.{key}")
    if not _present_int(bench.get("task_count")):
        errors.append("missing eval_protocol.benchmark.task_count")
    if not isinstance(bench.get("tasks"), dict):
        errors.append("missing eval_protocol.benchmark.tasks")
    overlay = protocol.get("grader_overlay") if isinstance(protocol.get("grader_overlay"), dict) else {}
    for key in ("marker", "sha256"):
        if not _nonempty_str(overlay.get(key)):
            errors.append(f"missing eval_protocol.grader_overlay.{key}")
    if not isinstance(protocol.get("images"), dict):
        errors.append("missing eval_protocol.images")
    errors.extend(environment_errors(doc))
    worker = protocol.get("worker") if isinstance(protocol.get("worker"), dict) else {}
    for key in ("node", "docker_version", "kernel", "cpu_model"):
        if not _nonempty_str(worker.get(key)):
            errors.append(f"missing eval_protocol.worker.{key}")
    for key in ("nproc", "memory_kb"):
        if not _present_int(worker.get(key)):
            errors.append(f"missing eval_protocol.worker.{key}")
    requester = protocol.get("requester") if isinstance(protocol.get("requester"), dict) else {}
    for key in ("user", "build_url"):
        if not _nonempty_str(requester.get(key)):
            errors.append(f"missing eval_protocol.requester.{key}")
    pipe = protocol.get("pipeline") if isinstance(protocol.get("pipeline"), dict) else {}
    if not _nonempty_str(pipe.get("commit")):
        errors.append("missing eval_protocol.pipeline.commit")
    if not isinstance(pipe.get("dirty"), bool):
        errors.append("missing eval_protocol.pipeline.dirty")
    elif pipe.get("dirty"):
        errors.append("eval_protocol.pipeline.dirty: a run needs a clean pipeline commit")
    if doc.get("pipeline_status") == "mixed":
        errors.append("pipeline_status mixed: the shards ran different pipeline builds")
    errors.extend(official_isolation_errors(protocol.get("isolation"), doc))
    anticheat = doc.get("anticheat") if isinstance(doc.get("anticheat"), dict) else {}
    if anticheat.get("status") != "ok" or not _nonempty_str(anticheat.get("version")):
        errors.append("anticheat must have run (anticheat/verdict)")
    isolation = protocol.get("isolation") if isinstance(protocol.get("isolation"), dict) else {}
    icode = protocol.get("icode") if isinstance(protocol.get("icode"), dict) else {}
    if isolation.get("mode") == "release" or icode.get("mode") == "release":
        release = icode.get("release") if isinstance(icode.get("release"), dict) else {}
        if not _SHA256.match(str(release.get("sha256") or "")):
            errors.append("eval_protocol.icode.release.sha256 must be a sha256")
    else:
        git = doc.get("icode_git")
        if not isinstance(git, dict) or not _nonempty_str(git.get("sha")):
            errors.append("missing icode_git")
    return errors


def _rate_error(loc: str, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return f"{loc} must be a number or null, not a list"
    if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
        return f"{loc} must be null or a number in [0, 1]"
    return None


def _num_or_null_error(loc: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        return f"{loc} must be a number or null"
    return None


def validate_comparison(doc: dict) -> list[str]:
    """Schema for the harness artifact written by render_report.py."""
    errors: list[str] = []
    for key in (
        "suite",
        "model",
        "api_base",
        "run_id",
        "n_tasks",
        "n_rollouts",
        "concurrency",
        "cpus_each",
        "icode",
    ):
        if key not in doc:
            errors.append(f"missing {key}")
    if doc.get("suite") not in (None, "deepswe", "lolbench", "swebenchpro"):
        errors.append("suite should be deepswe, lolbench, or swebenchpro")
    protocol = doc.get("eval_protocol")
    if protocol is not None:
        if not isinstance(protocol, dict):
            errors.append("eval_protocol must be an object")
        else:
            for key in ("icode", "model_params", "resources"):
                if key not in protocol:
                    errors.append(f"missing eval_protocol.{key}")
            params = protocol.get("model_params")
            if isinstance(params, dict):
                for key in ("provider", "reasoning_effort", "thinking"):
                    if key not in params:
                        errors.append(f"missing eval_protocol.model_params.{key}")
    n_roll = doc.get("n_rollouts")
    banned = (
        "n_tasks",
        "n_rollouts",
        "concurrency",
        "first_wilson_low",
        "first_wilson_high",
        "any_pass_hits",
        "best_attempt_pass",
        "requested_rollouts",
        "pass_at",
        "first_pass_at_1",
        "first_pass_hits",
        "macro_pass_at_1",
        "macro_pass@1_sd",
        "macro_pass@1_scored_sd",
        "macro_pass@1_se",
    )
    arm_names = ["icode"]
    if "llm" in doc:
        arm_names.append("llm")
    for arm_name in arm_names:
        arm = doc.get(arm_name)
        if not isinstance(arm, dict):
            errors.append(f"{arm_name} must be an object")
            continue
        for key in banned:
            if key in arm:
                errors.append(f"{arm_name}.{key} is redundant")
        if isinstance(n_roll, int) and n_roll >= 1:
            for i in range(1, n_roll + 1):
                key = f"pass@{i}"
                if key not in arm:
                    errors.append(f"missing {arm_name}.{key}")
                else:
                    err = _rate_error(f"{arm_name}.{key}", arm.get(key))
                    if err:
                        errors.append(err)
        for key in ("macro_pass@1", "macro_pass@1_ci", "any_pass"):
            if key not in arm:
                errors.append(f"missing {arm_name}.{key}")
            else:
                err = _rate_error(f"{arm_name}.{key}", arm.get(key))
                if err:
                    errors.append(err)
        if isinstance(n_roll, int) and n_roll >= 1:
            for i in range(1, n_roll + 1):
                key = f"pass@{i}_scored"
                if key in arm:
                    err = _rate_error(f"{arm_name}.{key}", arm.get(key))
                    if err:
                        errors.append(err)
        if "macro_pass@1_scored" in arm:
            err = _rate_error(f"{arm_name}.macro_pass@1_scored", arm.get("macro_pass@1_scored"))
            if err:
                errors.append(err)
        if "macro_pass@1_scored_ci" in arm:
            err = _rate_error(f"{arm_name}.macro_pass@1_scored_ci", arm.get("macro_pass@1_scored_ci"))
            if err:
                errors.append(err)
        methods = arm.get("pass_methods")
        if methods is not None:
            if not isinstance(methods, dict):
                errors.append(f"{arm_name}.pass_methods must be an object")
            else:
                for name in ("padded", "scored"):
                    if name in methods and not isinstance(methods.get(name), str):
                        errors.append(f"{arm_name}.pass_methods.{name} must be a string")
        if "unscored_tasks" in arm and not isinstance(arm.get("unscored_tasks"), list):
            errors.append(f"{arm_name}.unscored_tasks must be a list")
        if "unscored_rollouts" in arm:
            raw = arm.get("unscored_rollouts")
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                errors.append(f"{arm_name}.unscored_rollouts must be an integer >= 0")
        tokens = arm.get("tokens")
        if not isinstance(tokens, dict) or "total" not in tokens:
            errors.append(f"missing {arm_name}.tokens.total")
        micro = arm.get("micro")
        if not isinstance(micro, dict) or "partial_pass" not in micro or "partial_total" not in micro:
            errors.append(f"missing {arm_name}.micro partial counts")
        timing = arm.get("timing")
        if not isinstance(timing, dict) or "wall_seconds" not in timing:
            errors.append(f"missing {arm_name}.timing.wall_seconds")
        tasks = arm.get("tasks")
        if not isinstance(tasks, list):
            errors.append(f"{arm_name}.tasks must be a list")
            continue
        for i, task in enumerate(tasks):
            if not isinstance(task, dict):
                errors.append(f"{arm_name}.tasks[{i}] must be an object")
                continue
            for key in ("id", "c", "n", "pass_frac", "reward", "dur_s"):
                if key not in task:
                    errors.append(f"missing {arm_name}.tasks[{i}].{key}")
            for key in ("pass_frac_scored", "unscored_frac"):
                if key in task:
                    err = _rate_error(f"{arm_name}.tasks[{i}].{key}", task.get(key))
                    if err:
                        errors.append(err)
    errors.extend(official_provenance_errors(doc))
    return errors


def validate(doc: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["report must be a JSON object"]
    if "icode" in doc and "pass_at_1" not in doc:
        return validate_comparison(doc)
    for key in REQUIRED_TOP:
        if key not in doc:
            errors.append(f"missing {key}")
    if doc.get("harness") not in (None, "icode"):
        errors.append("harness should be icode")
    if doc.get("suite") not in (None, "deepswe", "lolbench", "swebenchpro"):
        errors.append("suite should be deepswe, lolbench, or swebenchpro")
    icode_git = doc.get("icode_git")
    if icode_git is not None:
        if not isinstance(icode_git, dict):
            errors.append("icode_git must be an object")
        else:
            for key in ("url", "kind", "ref", "sha"):
                if key not in icode_git:
                    errors.append(f"missing icode_git.{key}")
            kind = icode_git.get("kind")
            if kind is not None and kind not in ("branch", "tag", "commit", "pr"):
                errors.append("icode_git.kind should be branch, tag, commit, or pr")
    n_rollouts = doc.get("n_rollouts")
    if n_rollouts is not None and (
        isinstance(n_rollouts, bool) or not isinstance(n_rollouts, int) or n_rollouts < 1
    ):
        errors.append("n_rollouts must be an integer >= 1")
    err = _rate_error("pass_at_1", doc.get("pass_at_1"))
    if err:
        errors.append(err)
    wall_s = doc.get("wall_seconds")
    wall_m = doc.get("wall_minutes")
    err = _num_or_null_error("wall_seconds", wall_s)
    if err:
        errors.append(err)
    err = _num_or_null_error("wall_minutes", wall_m)
    if err:
        errors.append(err)
    if isinstance(wall_s, (int, float)) and isinstance(wall_m, (int, float)):
        expected = round(float(wall_s) / 60.0, 3)
        if abs(float(wall_m) - expected) > 0.001:
            errors.append("wall_minutes must be wall_seconds / 60")
    macro = doc.get("macro")
    if not isinstance(macro, dict):
        errors.append("macro must be an object")
    else:
        for key in REQUIRED_MACRO:
            if key not in macro:
                errors.append(f"missing macro.{key}")
            else:
                err = _rate_error(f"macro.{key}", macro[key])
                if err:
                    errors.append(err)
        if "partial" in macro:
            err = _rate_error("macro.partial", macro["partial"])
            if err:
                errors.append(err)
    tok = doc.get("tokens")
    if not isinstance(tok, dict):
        errors.append("tokens must be an object")
    else:
        for key in REQUIRED_TOKEN:
            if key not in tok:
                errors.append(f"missing tokens.{key}")
            else:
                err = _num_or_null_error(f"tokens.{key}", tok[key])
                if err:
                    errors.append(err)
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        errors.append("tasks must be an array")
        return errors
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"tasks[{i}] must be an object")
            continue
        for key in REQUIRED_TASK:
            if key not in task:
                errors.append(f"missing tasks[{i}].{key}")
        for key in ("f2p", "p2p", "partial", "pass_frac", "reward"):
            if key not in task:
                continue
            err = _rate_error(f"tasks[{i}].{key}", task[key])
            if err:
                errors.append(err)
        if "first" in task and task["first"] not in (True, False):
            errors.append(f"tasks[{i}].first must be a boolean")
        if (
            isinstance(n_rollouts, int)
            and not isinstance(n_rollouts, bool)
            and task.get("n") != n_rollouts
        ):
            errors.append(f"tasks[{i}].n must match n_rollouts")
        for key in ("tok_in", "tok_out", "dur_s", "dur_min"):
            if key not in task:
                continue
            err = _num_or_null_error(f"tasks[{i}].{key}", task[key])
            if err:
                errors.append(err)
        dur_s = task.get("dur_s")
        dur_m = task.get("dur_min")
        if isinstance(dur_s, (int, float)) and isinstance(dur_m, (int, float)):
            expected = round(float(dur_s) / 60.0, 3)
            if abs(float(dur_m) - expected) > 0.001:
                errors.append(f"tasks[{i}].dur_min must be dur_s / 60")
        baseline = task.get("baseline")
        if not isinstance(baseline, dict):
            errors.append(f"tasks[{i}].baseline must be an object")
        else:
            for key in REQUIRED_BASELINE:
                if key not in baseline:
                    errors.append(f"missing tasks[{i}].baseline.{key}")
            err = _rate_error(f"tasks[{i}].baseline.reward", baseline.get("reward"))
            if err:
                errors.append(err)
            for key in ("tok_in", "tok_out", "dur_s", "dur_min"):
                err = _num_or_null_error(f"tasks[{i}].baseline.{key}", baseline.get(key))
                if err:
                    errors.append(err)
    n_tasks = doc.get("n_tasks")
    if isinstance(n_tasks, int) and n_tasks < 0:
        errors.append("n_tasks must be >= 0")
    return errors


def _report_file(text: str) -> Path | None:
    raw = text.strip()
    if raw.endswith("/**"):
        raw = raw[:-3]
    path = Path(raw)
    workdir = Path(os.environ.get("MAC_K3D_EVAL_WORKDIR") or "eval-runs")
    candidates = [path]
    if not path.is_absolute():
        candidates.extend([Path.cwd() / path, workdir / path, workdir.parent / path])
    for candidate in candidates:
        artifact = candidate / "artifact.json"
        if artifact.is_file():
            return artifact
        if candidate.is_file():
            return candidate
    return None


def resolve_path(explicit: str | None) -> Path:
    if explicit:
        found = _report_file(explicit)
        if found:
            return found
        raw = explicit.strip()
        if raw.endswith("/**"):
            raw = raw[:-3]
        return Path(raw)
    workdir = Path(os.environ.get("MAC_K3D_EVAL_WORKDIR") or "eval-runs")
    last = workdir / "last_output.txt"
    if last.is_file():
        found = _report_file(last.read_text(encoding="utf-8"))
        if found:
            return found
    out_dir = workdir / "reports"
    jsons = sorted(out_dir.glob("eval-*.json")) if out_dir.is_dir() else []
    if jsons:
        return jsons[-1]
    raise FileNotFoundError(
        "no report path given and no $MAC_K3D_EVAL_WORKDIR/last_output.txt or eval-runs/reports/eval-*.json"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate eval report JSON schema")
    ap.add_argument("path", nargs="?", help="JSON file (default: last eval output)")
    args = ap.parse_args()
    try:
        path = resolve_path(args.path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"ERROR: not a file: {path}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 1
    errors = validate(doc)
    if errors:
        print(f"FAIL report schema ({path})")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"OK report schema ({path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
