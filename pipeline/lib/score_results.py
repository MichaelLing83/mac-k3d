#!/usr/bin/env python3
"""Score harness vs baseline into one JSON schema for DeepSWE, LoLBench, and SWE-bench Pro."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import harness as harness_table
from icode_usage import find_icode_usage


def load_json(path: Path):
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def find_bool_resolved(blob: str) -> bool | None:
    for pat in (
        r'"resolved"\s*:\s*(true|false)',
        r"\bRESOLVED\b[:=]\s*(true|false|yes|no|1|0)",
        r"\b(pass|fail|success|failed)\b",
    ):
        m = re.search(pat, blob, re.I)
        if not m:
            continue
        v = m.group(1).lower()
        if v in ("true", "yes", "1", "pass", "success"):
            return True
        if v in ("false", "no", "0", "fail", "failed"):
            return False
    return None


def parse_reward_value(raw) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return float(raw) >= 1.0 - 1e-9
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in ("1", "1.0", "true", "pass", "success"):
            return True
        if text in ("0", "0.0", "false", "fail", "failed"):
            return False
        try:
            return float(text) >= 1.0 - 1e-9
        except ValueError:
            return None
    return None


def harbor_reward_resolved(d: Path) -> bool | None:
    """Harbor verifier writes reward.json (1.0 = resolved). Missing file is unknown."""
    if not d.is_dir():
        return None
    newest = None
    newest_mtime = -1.0
    for path in d.rglob("reward.json"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= newest_mtime:
            newest_mtime = mtime
            newest = path
    if newest is None:
        return None
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        if "reward" in data:
            return parse_reward_value(data["reward"])
        if "resolved" in data:
            return parse_reward_value(data["resolved"])
    return parse_reward_value(data)


def find_harbor_task_dir(harness_dir: Path, tid: str) -> Path | None:
    """Newest Harbor jobs dir named <tid> under harness/harbor_runs/ (not harness/<tid>/)."""
    if not harness_dir.is_dir() or not tid:
        return None
    candidates: list[Path] = []
    for path in harness_dir.rglob("*"):
        if path.is_dir() and path.name == tid and "harbor_runs" in path.parts:
            candidates.append(path)
    if not candidates:
        return None

    def _mtime(path: Path) -> float:
        newest = -1.0
        for reward in path.rglob("reward.json"):
            try:
                newest = max(newest, reward.stat().st_mtime)
            except OSError:
                pass
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            pass
        return newest

    return max(candidates, key=_mtime)


def trial_task_ids(trial: Path) -> set[str]:
    """Every id a Harbor trial recorded for its task; empty when it recorded none.

    Harbor 0.22 writes ``task_name`` as ``<org>/<id>`` (``datacurve/abs-module-cache-flags``)
    and ``task_id`` as ``{"path": ".../<id>"}``, while mac-k3d selects tasks by
    their directory name, so each form is reduced to that name as well.
    """
    ids: set[str] = set()

    def add_dir_name(raw) -> None:
        if isinstance(raw, str) and raw.strip():
            ids.add(Path(raw.strip()).name)

    data = load_json(trial / "result.json")
    if isinstance(data, dict):
        name = data.get("task_name")
        if isinstance(name, str) and name.strip():
            ids.add(name.strip())
            ids.add(name.strip().rsplit("/", 1)[-1])
        got = data.get("task_id")
        if isinstance(got, str) and got.strip():
            ids.add(got.strip())
        elif isinstance(got, dict):
            add_dir_name(got.get("path"))
    data = load_json(trial / "config.json")
    if isinstance(data, dict) and isinstance(data.get("task"), dict):
        add_dir_name(data["task"].get("path"))
    ids.discard("")
    return ids


def harbor_runs_root(harness_dir: Path) -> Path | None:
    if harness_dir.name == "harbor_runs" and harness_dir.is_dir():
        return harness_dir
    runs = harness_dir / "harbor_runs"
    return runs if runs.is_dir() else None


def current_jobs_dir(harness_dir: Path) -> Path | None:
    """This build's Harbor jobs dir, named by the harbor_jobs_dir.txt that evaluate writes.

    A Jenkins workspace keeps every earlier build under harbor_runs/, so per-build
    phases read only this one. None when the file is missing or names no dir under
    harness_dir/harbor_runs, as in the Aggregate's merged harness.
    """
    try:
        raw = (harness_dir / "harbor_jobs_dir.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    runs = harness_dir / "harbor_runs"
    if not raw or not runs.is_dir():
        return None
    root = runs.resolve()
    # A harness copied elsewhere still names its build by the last path part.
    for candidate in (Path(raw), runs / Path(raw).name):
        try:
            jobs = candidate.resolve()
        except OSError:
            continue
        if jobs != root and jobs.is_relative_to(root) and jobs.is_dir():
            return jobs
    return None


def rollout_number(trial: Path) -> int | None:
    """The dispatcher's rollout, written as ``rollout.txt`` beside that build's jobs dir."""
    cursor = trial
    for _ in range(6):
        marker = cursor / "rollout.txt"
        if marker.is_file():
            try:
                number = int(marker.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                return None
            return number if number >= 1 else None
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return None


def _trial_stamp(trial: Path) -> float:
    newest = -1.0
    for reward in trial.rglob("reward.json"):
        try:
            newest = max(newest, reward.stat().st_mtime)
        except OSError:
            pass
    try:
        newest = max(newest, trial.stat().st_mtime)
    except OSError:
        pass
    return newest


def harbor_task_trials(harness_dir: Path, tid: str) -> list[Path]:
    """Trials for one task, oldest attempt first.

    With this build's jobs dir known, only its trials count, so a task this build
    did not score stays unscored instead of borrowing an older build's result.
    Otherwise (the Aggregate's merged harness) every build that ran the task is
    kept, ordered by the rollout number written next to that build's jobs dir.
    Builds up to PF.1 gave every task its own jobs dir named <tid>.
    """
    if not tid:
        return []
    jobs = current_jobs_dir(harness_dir)
    if jobs is not None:
        return [trial for trial in trial_dirs(jobs) if tid in trial_task_ids(trial)]
    runs = harbor_runs_root(harness_dir)
    found: list[Path] = []
    if runs is not None:
        found = [trial for trial in trial_dirs(runs) if tid in trial_task_ids(trial)]
    if not found:
        job = find_harbor_task_dir(harness_dir, tid)
        if job is not None:
            return trial_dirs(job)
        return trial_dirs(harness_dir / tid)

    def rollout_key(trial: Path) -> tuple:
        """Plan order: rollout 1 before rollout 2, so Pass@1 stays the first rollout."""
        number = rollout_number(trial)
        if number is not None:
            return (0, number, _trial_stamp(trial))
        return (1, 0, _trial_stamp(trial))

    found.sort(key=rollout_key)
    return found


def unmatched_trials(jobs_dir: Path, task_ids: list[str]) -> list[Path]:
    """Trials in this build's jobs dir that match none of the selected tasks."""
    wanted = set(task_ids)
    return [trial for trial in trial_dirs(jobs_dir) if not trial_task_ids(trial) & wanted]


def find_scale_task_dir(harness_dir: Path, tid: str) -> Path | None:
    """SWE-bench Pro writes harness/<instance_id>/eval.json (and agent.patch)."""
    if not harness_dir.is_dir() or not tid:
        return None
    direct = harness_dir / tid
    if direct.is_dir():
        return direct
    return None


def scale_eval_resolved(d: Path) -> bool | None:
    if not d.is_dir():
        return None
    for name in ("eval.json", "scale_summary.json"):
        path = d / name
        data = load_json(path)
        if isinstance(data, dict) and "resolved" in data:
            return parse_reward_value(data.get("resolved"))
        if isinstance(data, list):
            for obj in data:
                if isinstance(obj, dict) and obj.get("id") == d.name:
                    return parse_reward_value(obj.get("resolved"))
    newest = None
    for path in d.rglob("eval.json"):
        newest = path
    if newest is not None:
        data = load_json(newest)
        if isinstance(data, dict) and "resolved" in data:
            return parse_reward_value(data.get("resolved"))
    return None


def find_pier_task_dir(harness_dir: Path, tid: str) -> Path | None:
    """Newest Pier trial dir named <tid>__<rand> under timestamped job folders."""
    if not harness_dir.is_dir() or not tid:
        return None
    prefix = f"{tid}__"
    candidates: list[Path] = []
    for path in harness_dir.rglob("*"):
        if path.is_dir() and path.name.startswith(prefix):
            candidates.append(path)
    if not candidates:
        return None

    def _mtime(path: Path) -> float:
        newest = -1.0
        for name in ("result.json", "reward.json"):
            for found in path.rglob(name):
                try:
                    newest = max(newest, found.stat().st_mtime)
                except OSError:
                    pass
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            pass
        return newest

    return max(candidates, key=_mtime)


def load_icode_git() -> dict | None:
    """tasks/harness writes WORKDIR/icode_git.json so a PR SHA eval can be identified later."""
    candidates: list[Path] = []
    for env in ("WORKDIR", "MAC_K3D_EVAL_WORKDIR"):
        raw = os.environ.get(env)
        if raw:
            candidates.append(Path(raw) / "icode_git.json")
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        data = load_json(path)
        if not isinstance(data, dict):
            continue
        sha = str(data.get("sha") or "").strip()
        if not sha:
            continue
        return {
            "url": str(data.get("url") or ""),
            "kind": str(data.get("kind") or ""),
            "ref": str(data.get("ref") or ""),
            "sha": sha,
            "subject": str(data.get("subject") or ""),
        }
    return None


def _norm_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def _as_rate(raw) -> float | None:
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if "/" in text:
            left, _, right = text.partition("/")
            try:
                num = float(left.strip())
                den = float(right.strip())
            except ValueError:
                try:
                    return float(text)
                except ValueError:
                    return None
            if den == 0:
                return None
            return num / den
        try:
            return float(text)
        except ValueError:
            return None
    if isinstance(raw, dict):
        for key in ("mean", "avg", "value", "rate", "reward"):
            if key in raw:
                got = _as_rate(raw[key])
                if got is not None:
                    return got
        passed = _as_count(raw.get("passed") if "passed" in raw else raw.get("pass"))
        total = _as_count(raw.get("total") if "total" in raw else raw.get("n"))
        if passed is not None and total:
            return passed / total
    return None


def _as_count(raw) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer() and 0 <= raw < 1_000_000:
            return int(raw)
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if "/" in text:
            left, _, _right = text.partition("/")
            return _as_count(left)
        try:
            val = float(text)
        except ValueError:
            return None
        return _as_count(val)
    return None


F2P_RATE_KEYS = {
    "fail_to_pass",
    "fail_to_pass_rate",
    "f2p",
    "f2p_rate",
    "f2p_p",
    "f2p_pass_rate",
}
P2P_RATE_KEYS = {
    "pass_to_pass",
    "pass_to_pass_rate",
    "p2p",
    "p2p_rate",
    "p2p_p",
    "p2p_pass_rate",
}
PARTIAL_RATE_KEYS = {"partial", "partial_rate"}
F2P_PASS_KEYS = {"f2p_pass", "f2p_passed", "fail_to_pass_pass", "n_fail_to_pass_passed"}
F2P_TOTAL_KEYS = {"f2p_total", "fail_to_pass_total", "n_fail_to_pass"}
P2P_PASS_KEYS = {"p2p_pass", "p2p_passed", "pass_to_pass_pass", "n_pass_to_pass_passed"}
P2P_TOTAL_KEYS = {"p2p_total", "pass_to_pass_total", "n_pass_to_pass"}


def _metric_key(node: dict) -> str:
    return _norm_key(node.get("key") or node.get("name") or node.get("metric") or "")


def collect_named_rate(obj, aliases: set[str]) -> float | None:
    found: list[float] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            named = _metric_key(node)
            if named in aliases:
                rate = _as_rate(node.get("value") if "value" in node else node)
                if rate is not None and 0 <= rate <= 1:
                    found.append(rate)
            for key, val in node.items():
                if _norm_key(key) in aliases:
                    rate = _as_rate(val)
                    if rate is not None and 0 <= rate <= 1:
                        found.append(rate)
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found[0] if found else None


def collect_named_count(obj, aliases: set[str]) -> int | None:
    found: list[int] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            named = _metric_key(node)
            if named in aliases:
                got = _as_count(node.get("value") if "value" in node else node)
                if got is not None:
                    found.append(got)
            for key, val in node.items():
                if _norm_key(key) in aliases:
                    got = _as_count(val)
                    if got is not None:
                        found.append(got)
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found[0] if found else None


def collect_fraction_counts(obj, rate_aliases: set[str]) -> tuple[int | None, int | None]:
    found: list[tuple[int, int]] = []

    def consider(raw) -> None:
        if isinstance(raw, str) and "/" in raw:
            left, _, right = raw.partition("/")
            num = _as_count(left)
            den = _as_count(right)
            if num is not None and den:
                found.append((num, den))
        if isinstance(raw, dict):
            num = _as_count(raw.get("passed") if "passed" in raw else raw.get("pass"))
            den = _as_count(raw.get("total") if "total" in raw else raw.get("n"))
            if num is not None and den:
                found.append((num, den))

    def walk(node) -> None:
        if isinstance(node, dict):
            named = _metric_key(node)
            if named in rate_aliases:
                consider(node.get("value") if "value" in node else node)
            for key, val in node.items():
                if _norm_key(key) in rate_aliases:
                    consider(val)
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found[0] if found else (None, None)


def no_rates() -> dict:
    return {
        "f2p": None,
        "p2p": None,
        "partial": None,
        "f2p_pass": None,
        "f2p_total": None,
        "p2p_pass": None,
        "p2p_total": None,
    }


def verifier_rates(d: Path) -> dict:
    """F2P / P2P rates and optional counts from Pier or Harbor JSON (not test-name lists)."""
    empty = no_rates()
    if not d.is_dir():
        return empty
    files: list[Path] = []
    for path in d.rglob("*.json"):
        if path.name in {"events.json", "events.jsonl"}:
            continue
        try:
            if path.stat().st_size >= 2_000_000:
                continue
        except OSError:
            continue
        files.append(path)
    files.sort(key=lambda p: (p.name != "result.json", p.name != "reward.json", str(p)))
    out = dict(empty)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if out["f2p"] is None:
            out["f2p"] = collect_named_rate(data, F2P_RATE_KEYS)
        if out["p2p"] is None:
            out["p2p"] = collect_named_rate(data, P2P_RATE_KEYS)
        if out["partial"] is None:
            out["partial"] = collect_named_rate(data, PARTIAL_RATE_KEYS)
        if out["f2p_pass"] is None:
            out["f2p_pass"] = collect_named_count(data, F2P_PASS_KEYS)
        if out["f2p_total"] is None:
            out["f2p_total"] = collect_named_count(data, F2P_TOTAL_KEYS)
        if out["p2p_pass"] is None:
            out["p2p_pass"] = collect_named_count(data, P2P_PASS_KEYS)
        if out["p2p_total"] is None:
            out["p2p_total"] = collect_named_count(data, P2P_TOTAL_KEYS)
        if out["f2p_pass"] is None or out["f2p_total"] is None:
            num, den = collect_fraction_counts(data, F2P_RATE_KEYS)
            if out["f2p_pass"] is None:
                out["f2p_pass"] = num
            if out["f2p_total"] is None:
                out["f2p_total"] = den
        if out["p2p_pass"] is None or out["p2p_total"] is None:
            num, den = collect_fraction_counts(data, P2P_RATE_KEYS)
            if out["p2p_pass"] is None:
                out["p2p_pass"] = num
            if out["p2p_total"] is None:
                out["p2p_total"] = den
        counts_ready = (
            out["f2p_pass"] is not None
            and out["f2p_total"] is not None
            and out["p2p_pass"] is not None
            and out["p2p_total"] is not None
        )
        if out["f2p"] is not None and out["p2p"] is not None and counts_ready:
            break
    return out


def scan_dir(d: Path) -> dict:
    if not d.is_dir():
        return {"resolved": None, "patch_path": None}
    patch = None
    texts = []
    for p in d.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith(".patch") or p.name == "agent.patch":
            patch = str(p)
        if p.suffix in {".json", ".log", ".txt", ".md"} and p.stat().st_size < 2_000_000:
            try:
                texts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    blob = "\n".join(texts)
    reward = harbor_reward_resolved(d)
    text_resolved = find_bool_resolved(blob)
    resolved = reward if reward is not None else text_resolved
    return {"resolved": resolved, "patch_path": patch}


def task_ids(tasks_dir: Path, n: int) -> list[str]:
    if not tasks_dir.is_dir():
        return []
    return [p.name for p in sorted(tasks_dir.iterdir()) if p.is_dir()][:n]


def pass_at_1(ok: int, n: int) -> float:
    if n <= 0:
        return 0.0
    return round(ok / n, 4)


def as_minutes(seconds) -> float | None:
    if not isinstance(seconds, (int, float)):
        return None
    return round(float(seconds) / 60.0, 3)


def current_reward_resolved(task_dir: Path) -> bool | None:
    """This run's baseline grade. Only reward.json next to the patch counts."""
    path = task_dir / "reward.json"
    if not path.is_file():
        return None
    data = load_json(path)
    if isinstance(data, dict):
        if "reward" in data:
            return parse_reward_value(data["reward"])
        if "resolved" in data:
            return parse_reward_value(data["resolved"])
    return parse_reward_value(data)


def partial_from_counts(rates: dict) -> float | None:
    """(f2p passed + p2p passed) / (f2p total + p2p total) when both buckets are known."""
    keys = ("f2p_pass", "f2p_total", "p2p_pass", "p2p_total")
    if any(rates.get(key) is None for key in keys):
        return None
    try:
        passed = int(rates["f2p_pass"]) + int(rates["p2p_pass"])
        total = int(rates["f2p_total"]) + int(rates["p2p_total"])
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return 0.0
    return round(passed / total, 4)


def reward_float(resolved: bool | None) -> float | None:
    if resolved is True:
        return 1.0
    if resolved is False:
        return 0.0
    return None


def usage_pair(usage) -> tuple[int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None
    prompt = usage.get("prompt")
    if prompt is None:
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion")
    if completion is None:
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
    if prompt is None and completion is None:
        return None, None
    try:
        tok_in = int(prompt or 0)
        tok_out = int(completion or 0)
    except (TypeError, ValueError):
        return None, None
    return tok_in, tok_out


def mean_or_none(vals: list) -> float | None:
    nums = [float(v) for v in vals if isinstance(v, (int, float))]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def add_known(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return int(a or 0) + int(b or 0)


def baseline_object(bmeta: dict, b_resolved: bool | None) -> dict:
    tok_in, tok_out = usage_pair(bmeta.get("token_usage"))
    dur = bmeta.get("duration_seconds")
    return {
        "reward": reward_float(b_resolved),
        "tok_in": tok_in,
        "tok_out": tok_out,
        "dur_s": dur if isinstance(dur, (int, float)) else None,
        "dur_min": as_minutes(dur),
    }


def requested_rollouts() -> int:
    raw = (os.environ.get("N_ROLLOUTS") or "1").strip()
    if not raw.isdigit():
        return 1
    return max(int(raw), 1)


def attempt_index(path: Path) -> int | None:
    """1-based attempt from a Harbor job name such as ``task_icode_local_a02``."""
    for part in path.parts:
        match = re.search(r"_a(\d+)(?:_|$)", part)
        if match:
            number = int(match.group(1))
            if number >= 1:
                return number
    return None


def _attempt_sort_key(path: Path, mtime: float) -> tuple:
    index = attempt_index(path)
    return (index if index is not None else 10**9, mtime, str(path))


def reward_files(job_dir: Path | None) -> list[Path]:
    """Attempt order, then oldest reward.json. ``_a01`` wins over finish time."""
    if job_dir is None or not job_dir.is_dir():
        return []
    found: list[tuple[tuple, Path]] = []
    for path in job_dir.rglob("reward.json"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        found.append((_attempt_sort_key(path, mtime), path))
    found.sort(key=lambda item: item[0])
    return [path for _, path in found]


def trial_reward_files(trials: list[Path]) -> list[Path]:
    """One reward.json per trial, keeping the order the trials came in."""
    out: list[Path] = []
    for trial in trials:
        for candidate in (trial / "verifier" / "reward.json", trial / "reward.json"):
            if candidate.is_file():
                out.append(candidate)
                break
        else:
            nested = sorted(trial.rglob("reward.json"))
            if nested:
                out.append(nested[0])
    return out


def trial_dir_for_reward(path: Path) -> Path:
    if path.parent.name == "verifier":
        return path.parent.parent
    return path.parent


def _is_job_summary(path: Path) -> bool:
    """Harbor's job-level result.json (`n_total_trials`, `stats`), not a trial's."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(doc, dict) and "n_total_trials" in doc and "trial_name" not in doc


def trial_dirs(job_dir: Path | None) -> list[Path]:
    """One dir per Harbor trial, oldest first. Includes trials with no reward.json."""
    if job_dir is None or not job_dir.is_dir():
        return []
    found: dict[Path, float] = {}

    def consider(trial: Path, stamp: float) -> None:
        if not trial.is_dir():
            return
        prev = found.get(trial)
        if prev is None or stamp < prev:
            found[trial] = stamp

    for path in job_dir.rglob("reward.json"):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        consider(trial_dir_for_reward(path), stamp)
    for path in job_dir.rglob("result.json"):
        if path.parent.name == "verifier":
            continue
        # The attempt folder task_icode_N_aNN holds a nested trial. That folder
        # also has result.json and must not replace the trial that has reward.json.
        nested = False
        try:
            for child in path.parent.iterdir():
                if child.is_dir() and (
                    (child / "result.json").is_file()
                    or (child / "verifier" / "reward.json").is_file()
                ):
                    nested = True
                    break
        except OSError:
            nested = False
        if nested or _is_job_summary(path):
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        consider(path.parent, stamp)
    return [trial for trial, _ in sorted(found.items(), key=lambda item: _attempt_sort_key(item[0], item[1]))]


def trial_patch_bytes(trial: Path) -> int | None:
    """Bytes the capture receipt recorded for this trial's patch; None when there is no receipt."""
    doc = load_json(trial / "agent" / "capture.json")
    if not isinstance(doc, dict):
        return None
    patch = doc.get("patch")
    if not isinstance(patch, dict):
        return None
    got = patch.get("bytes")
    return got if isinstance(got, int) and not isinstance(got, bool) else None


OOM_CAUSE = "out of memory (OOM-killed)"
# Matched in order against Harbor's exception message, lowercased.
INFRA_CAUSES = (
    (("fully subnetted",), "Docker network pool exhausted"),
    (("extraction snapshot", "missing parent", "parent snapshot"), "Docker image store damaged"),
    (("pull access denied", "manifest unknown", "error pulling", "failed to pull", "toomanyrequests"), "image pull failed"),
    (
        ("oomkilled", "out of memory", "cannot allocate memory", "oom-kill", "oom_kill", "(exit 137)", "exit code 137"),
        OOM_CAUSE,
    ),
    (("docker compose command failed",), "docker compose failed"),
)


def trial_exception(trial: Path) -> dict | None:
    """The exception Harbor recorded in <trial>/result.json (type, message), or None."""
    result = load_json(trial / "result.json")
    info = result.get("exception_info") if isinstance(result, dict) else None
    if not isinstance(info, dict) or not (info.get("exception_type") or info.get("exception_message")):
        return None
    return {"type": str(info.get("exception_type") or ""), "message": str(info.get("exception_message") or "")}


def infra_cause(exception: dict) -> str:
    """Why Harbor stopped a trial, in a few words: a known infra cause, else the exception type."""
    low = str(exception.get("message") or "").lower()
    for needles, cause in INFRA_CAUSES:
        if any(needle in low for needle in needles):
            return cause
    return str(exception.get("type") or "") or "unknown Harbor error"


def rates_for_trial(trial: Path) -> dict:
    rates = verifier_rates(trial)
    if rates["partial"] is None:
        derived = partial_from_counts(rates)
        if derived is not None:
            rates["partial"] = derived
    # A trial that produced no patch leaves the repo untouched, so its P2P
    # measures the base repo, not the agent. Graders disagree on it (DeepSWE
    # passes the base's tests, LoLBench reports 0/N because applied=0), so it is
    # kept out of the averages. The grader's counts stay for the report.
    if trial_patch_bytes(trial) == 0:
        rates["grader_p2p_pass"] = rates.get("p2p_pass")
        rates["grader_p2p_total"] = rates.get("p2p_total")
        rates["p2p"] = None
        rates["p2p_pass"] = None
        rates["p2p_total"] = None
        rates["partial"] = None
        rates["empty_patch"] = True
    return rates


def _mean_attempts(rows: list[dict], key: str) -> float | None:
    vals = [row.get(key) for row in rows if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)]
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    return round(sum(float(v) for v in vals) / len(vals), 4)


def _sum_counts(rows: list[dict], key: str) -> int | None:
    vals = [row.get(key) for row in rows if isinstance(row.get(key), int)]
    if not vals:
        return None
    return int(sum(vals))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness-dir", required=True)
    ap.add_argument("--baseline-dir", required=True)
    ap.add_argument("--tasks-dir", required=True)
    ap.add_argument("--n-tasks", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--task-file", default="", help="newline-separated task dir names")
    args = ap.parse_args()

    harness_dir = Path(args.harness_dir)
    baseline_dir = Path(args.baseline_dir)
    tasks_dir = Path(args.tasks_dir)
    if args.task_file:
        raw = Path(args.task_file).read_text(encoding="utf-8")
        names = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        ids = [n for n in names if (tasks_dir / n).is_dir()]
        if not ids:
            ids = task_ids(tasks_dir, args.n_tasks)
    else:
        ids = task_ids(tasks_dir, args.n_tasks)

    baseline_summary = load_json(baseline_dir / "summary.json") or []
    if not isinstance(baseline_summary, list):
        baseline_summary = []
    baseline_by_id = {x["id"]: x for x in baseline_summary if isinstance(x, dict) and "id" in x}
    baseline_meta = load_json(baseline_dir / "meta.json") or {}
    harness_meta = load_json(harness_dir / "meta.json") or {}
    suite = (os.environ.get("BENCHMARK") or "deepswe").strip().lower()
    lolbench = suite == "lolbench"
    n_rollouts = requested_rollouts()
    jobs = current_jobs_dir(harness_dir)
    d_h = harness_meta.get("duration_seconds")
    meta_h_in, meta_h_out = usage_pair(harness_meta.get("token_usage"))
    if lolbench:
        meta_h_in = meta_h_out = None
    elif (not meta_h_in and not meta_h_out):
        scraped = find_icode_usage(jobs if jobs is not None else harness_dir)
        if scraped:
            meta_h_in = int(scraped.get("prompt") or 0)
            meta_h_out = int(scraped.get("completion") or 0)

    per_task = []
    for tid in ids:
        if jobs is not None:
            # This build's trials for tid are the only evidence; the whole-harness
            # scans below would read other tasks or older builds.
            hdir = None
            h = {"resolved": None, "patch_path": None}
            rates = no_rates()
        else:
            hdir = find_harbor_task_dir(harness_dir, tid)
            h = scan_dir(hdir) if hdir is not None else scan_dir(harness_dir / tid)
            if h["resolved"] is None and h["patch_path"] is None:
                h = scan_dir(harness_dir)
            rates_root = hdir if hdir is not None else harness_dir
            rates = verifier_rates(rates_root)
            if rates["f2p"] is None and rates["p2p"] is None:
                rates = verifier_rates(harness_dir)
        derived_partial = None
        if rates["partial"] is None:
            derived_partial = partial_from_counts(rates)
            if derived_partial is not None:
                rates["partial"] = derived_partial
        b_resolved = current_reward_resolved(baseline_dir / tid)
        bmeta = baseline_by_id.get(tid, {})
        trial_usage = find_icode_usage(hdir) if hdir is not None else None
        files = trial_reward_files(harbor_task_trials(harness_dir, tid))
        attempts: list[dict] = []
        tok_in = tok_out = None
        token_source = "none"
        for path in files:
            trial = trial_dir_for_reward(path)
            data = load_json(path) or {}
            resolved = None
            if isinstance(data, dict):
                if "reward" in data:
                    resolved = parse_reward_value(data.get("reward"))
                elif "resolved" in data:
                    resolved = parse_reward_value(data.get("resolved"))
            verdict = load_json(trial / "agent" / "anticheat.json")
            if isinstance(verdict, dict) and verdict.get("verdict") == "rejected":
                resolved = False
            usage = find_icode_usage(trial)
            row_rates = rates_for_trial(trial)
            if (
                row_rates["f2p"] is None
                and row_rates["p2p"] is None
                and not row_rates.get("empty_patch")
                and hdir is not None
            ):
                row_rates = rates_for_trial(hdir)
            attempts.append(
                {
                    "rates": row_rates,
                    "resolved": resolved is True,
                    "usage": usage,
                }
            )
        if len(attempts) > n_rollouts:
            attempts = attempts[:n_rollouts]
        if attempts:
            c = sum(1 for row in attempts if row["resolved"])
            first = attempts[0]["resolved"]
            reward = pass_at_1(c, n_rollouts)
            attempt_rates = [row["rates"] for row in attempts]
            rates = {
                "f2p": _mean_attempts(attempt_rates, "f2p"),
                "p2p": _mean_attempts(attempt_rates, "p2p"),
                "partial": _mean_attempts(attempt_rates, "partial"),
                "f2p_pass": _sum_counts(attempt_rates, "f2p_pass"),
                "f2p_total": _sum_counts(attempt_rates, "f2p_total"),
                "p2p_pass": _sum_counts(attempt_rates, "p2p_pass"),
                "p2p_total": _sum_counts(attempt_rates, "p2p_total"),
            }
            empty = sum(1 for row in attempt_rates if row.get("empty_patch"))
            if empty:
                rates["empty_patch_attempts"] = empty
            usages = [row["usage"] for row in attempts if row["usage"] is not None]
            if usages:
                tok_in = sum(int(item.get("prompt") or 0) for item in usages)
                tok_out = sum(int(item.get("completion") or 0) for item in usages)
                token_source = "trial"
        else:
            reward = reward_float(h["resolved"])
            c = 1 if reward == 1.0 else 0
            first = reward == 1.0
        if token_source == "none" and trial_usage is not None:
            tok_in = int(trial_usage.get("prompt") or 0)
            tok_out = int(trial_usage.get("completion") or 0)
            token_source = "trial"
        elif token_source == "none" and not lolbench:
            tok_in, tok_out = meta_h_in, meta_h_out
        dur_s = d_h if isinstance(d_h, (int, float)) else None
        per_task.append(
            {
                "id": tid,
                "c": c,
                "n": n_rollouts,
                "pass_frac": pass_at_1(c, n_rollouts),
                "first": first,
                "reward": reward,
                "f2p": rates["f2p"],
                "f2p_pass": rates["f2p_pass"],
                "f2p_total": rates["f2p_total"],
                "p2p": rates["p2p"],
                "p2p_pass": rates["p2p_pass"],
                "p2p_total": rates["p2p_total"],
                "partial": rates["partial"],
                "tok_in": tok_in,
                "tok_out": tok_out,
                "dur_s": dur_s,
                "dur_min": as_minutes(dur_s),
                "baseline": baseline_object(bmeta, b_resolved),
            }
        )

    n = len(per_task)
    d_b = baseline_meta.get("duration_seconds")
    wall_seconds = None
    if isinstance(d_h, (int, float)) or isinstance(d_b, (int, float)):
        wall_seconds = round(float(d_h or 0) + float(d_b or 0), 3)

    tok_in_h = tok_out_h = None
    if any(isinstance(t.get("tok_in"), int) or isinstance(t.get("tok_out"), int) for t in per_task):
        tok_in_h = sum(int(t.get("tok_in") or 0) for t in per_task)
        tok_out_h = sum(int(t.get("tok_out") or 0) for t in per_task)
    elif not lolbench:
        tok_in_h, tok_out_h = meta_h_in, meta_h_out
    tok_in_b = tok_out_b = None
    if baseline_meta.get("token_usage"):
        tok_in_b, tok_out_b = usage_pair(baseline_meta.get("token_usage"))
    if tok_in_b is None:
        for t in per_task:
            tok_in_b = add_known(tok_in_b, t["baseline"].get("tok_in"))
            tok_out_b = add_known(tok_out_b, t["baseline"].get("tok_out"))
    if tok_in_h is not None or tok_out_h is not None:
        tokens_in, tokens_out = tok_in_h, tok_out_h
    else:
        tokens_in, tokens_out = tok_in_b, tok_out_b
    tokens_total = add_known(tokens_in, tokens_out)

    model_id = (
        os.environ.get("DEEPSEEK_MODEL")
        or harness_meta.get("llm_model_id")
        or baseline_meta.get("llm_model_id")
        or "deepseek-flash"
    )
    served = baseline_meta.get("llm_model_served") or harness_meta.get("llm_model_served")
    access = (
        harness_meta.get("access_date_utc")
        or baseline_meta.get("access_date_utc")
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    api_base = (
        os.environ.get("DEEPSEEK_API_BASE")
        or os.environ.get("ICODE_API_BASE")
        or "https://api.deepseek.com"
    )

    doc = {
        "suite": os.environ.get("BENCHMARK") or "deepswe",
        "harness": harness_table.current().name,
        "model": model_id,
        "model_served": served,
        "api_base": api_base,
        "n_tasks": args.n_tasks,
        "n_rollouts": n_rollouts,
        "access_date_utc": access,
        "wall_seconds": wall_seconds,
        "wall_minutes": as_minutes(wall_seconds),
        "pass_at_1": mean_or_none([t["pass_frac"] for t in per_task]) if per_task else 0.0,
        "macro": {
            "f2p": mean_or_none([t["f2p"] for t in per_task]),
            "p2p": mean_or_none([t["p2p"] for t in per_task]),
            "partial": mean_or_none([t["partial"] for t in per_task]),
            "reward": mean_or_none([t["reward"] for t in per_task]),
        },
        "tokens": {"in": tokens_in, "out": tokens_out, "total": tokens_total},
        "tasks": per_task,
    }
    icode_git = load_icode_git()
    if icode_git:
        doc["icode_git"] = icode_git

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
