#!/usr/bin/env python3
"""Write one run's artifact.json, summary.md, report.html and provenance.md (report phase).

The archive phase (archive_run.py) backs the run folder up afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from anticheat_verdict import adjust_attempt
from eval_metrics import (
    EMPTY_PATCH_HEADERS,
    EMPTY_PATCH_INTRO,
    EMPTY_PATCH_TITLE,
    attempt_is_scored,
    empty_patch_cause_counts,
    mean_ci,
    pass_total,
    summarize_arm,
)
from score_results import OOM_CAUSE, infra_cause, load_json, parse_reward_value, rates_for_trial, trial_exception
from icode_usage import find_icode_usage, icode_exit_code, icode_log_text, provider_error_marker
import jiuwenswarm_usage
from provenance import PROVENANCE_FILE, harness_line, harness_protocol
from task_resources import cpu_count

# iCode's built-in output cap. A run that recorded no max_tokens predates
# mac-k3d setting it, so this is the limit that run had.
ICODE_DEFAULT_MAX_TOKENS = 8192


def _resolved(data: dict) -> bool:
    if "reward" in data:
        return parse_reward_value(data.get("reward")) is True
    if "resolved" in data:
        return parse_reward_value(data.get("resolved")) is True
    return False


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _seconds_between(start, finish) -> float | None:
    a = _parse_ts(start)
    b = _parse_ts(finish)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds()
    if delta < 0:
        return None
    return delta


def _timing_from_result(result: dict) -> tuple[float | None, str | None, str | None]:
    """Prefer the Harbor agent span, then the trial span. Ignore a bare duration_seconds field."""
    if not isinstance(result, dict):
        return None, None, None
    agent = result.get("agent_execution")
    if isinstance(agent, dict):
        dur = _seconds_between(agent.get("started_at"), agent.get("finished_at"))
        if dur is not None:
            return dur, agent.get("started_at"), agent.get("finished_at")
    dur = _seconds_between(result.get("started_at"), result.get("finished_at"))
    if dur is not None:
        started = result.get("started_at")
        finished = result.get("finished_at")
        return dur, started if isinstance(started, str) else None, finished if isinstance(finished, str) else None
    return None, None, None


def _attempt_from_trial(trial: Path, reward_path: Path | None, max_tokens: int | None = None) -> dict:
    data = load_json(reward_path) if reward_path is not None else None
    data = data if isinstance(data, dict) else {}
    rates = rates_for_trial(trial)
    usage = find_icode_usage(trial)
    usage_file = load_json(trial / "usage.json") or {}
    if usage is None and isinstance(usage_file, dict):
        raw = usage_file.get("usage") or {}
        if raw:
            usage = {
                "prompt": int(raw.get("prompt") or 0),
                "completion": int(raw.get("completion") or 0),
            }
    result = load_json(trial / "result.json")
    dur, started, finished = _timing_from_result(result if isinstance(result, dict) else {})
    if dur is None and isinstance(usage_file, dict) and isinstance(usage_file.get("duration_seconds"), (int, float)):
        dur = float(usage_file["duration_seconds"])
    notes = ""
    note_path = trial / "notes.txt"
    if note_path.is_file():
        notes = note_path.read_text(encoding="utf-8").strip()
    has_reward = bool(data) and ("reward" in data or "resolved" in data)
    if reward_path is None:
        has_reward = False
    if not notes and not has_reward:
        notes = "missing reward.json"
    row = {
        "resolved": _resolved(data),
        "has_reward": has_reward,
        "f2p": rates.get("f2p"),
        "f2p_pass": rates.get("f2p_pass"),
        "f2p_total": rates.get("f2p_total"),
        "p2p": rates.get("p2p"),
        "p2p_pass": rates.get("p2p_pass"),
        "p2p_total": rates.get("p2p_total"),
        "partial": rates.get("partial"),
        "tok_in": None if usage is None else int(usage.get("prompt") or 0),
        "tok_out": None if usage is None else int(usage.get("completion") or 0),
        "dur_s": dur,
        "notes": notes,
        "trial": trial.name,
        "model_calls": None if usage is None else usage.get("model_calls"),
        "last_output_tokens": None if usage is None else usage.get("last_output_tokens"),
        "icode_exit": icode_exit_code(trial),
    }
    cap = max_tokens or ICODE_DEFAULT_MAX_TOKENS
    last_out = row["last_output_tokens"]
    if isinstance(last_out, int) and last_out >= cap:
        row["cut_off_reply"] = True
        row["notes"] = _add_note(row["notes"], "last reply cut off at max_tokens")
    if rates.get("empty_patch"):
        row["empty_patch"] = True
        row["grader_p2p_pass"] = rates.get("grader_p2p_pass")
        row["grader_p2p_total"] = rates.get("grader_p2p_total")
        row["notes"] = _add_note(row["notes"], "empty model.patch")
        marker = provider_error_marker(icode_log_text(trial))
        if row["model_calls"] == 0 or marker:
            row["empty_patch_cause"] = "infra"
            row["infra_failure"] = True
            why = f"model API error ({marker})" if marker else "no model call completed"
            row["notes"] = _add_note(row["notes"], f"infra: {why}")
        elif row.get("cut_off_reply"):
            row["empty_patch_cause"] = "cut_off"
        else:
            row["empty_patch_cause"] = "no_edit"
    # 137 = SIGKILL, in a trial almost always the container's memory limit.
    # The grader's result stands; the note says why the agent stopped.
    if row["icode_exit"] == 137:
        agent = "JiuwenSwarm" if (trial / "agent" / jiuwenswarm_usage.EXIT).is_file() else "iCode"
        row["oom_killed"] = True
        row["notes"] = _add_note(row["notes"], f"{agent} killed (exit 137, likely out of memory)")
    # The grader's reward, F2P and P2P stand. The note says the named tests never ran.
    if has_reward and _suite_did_not_compile(trial):
        row["notes"] = _add_note(row["notes"], "suite did not compile (tests did not run)")
    exception = trial_exception(trial) if not has_reward else None
    if exception is not None:
        row["infra_failure"] = True
        cause = infra_cause(exception)
        if cause == OOM_CAUSE:
            row["oom_killed"] = True
        row["notes"] = _add_note(row["notes"], f"infra: {cause}")
    if isinstance(started, str):
        row["started_at"] = started
    if isinstance(finished, str):
        row["finished_at"] = finished
    verdict = load_json(trial / "agent" / "anticheat.json")
    if isinstance(verdict, dict) and verdict.get("verdict"):
        row["anticheat"] = str(verdict["verdict"])
    return row


def _add_note(notes: str, note: str) -> str:
    return f"{notes}; {note}" if notes else note


def _suite_did_not_compile(trial: Path) -> bool:
    """The grader wrote a score, but `go test` (or the suite) never started because the patch did not build."""
    needles = ("[build failed]", "FailedBuild")
    for name in ("test-stdout.txt", "run.log"):
        path = trial / "verifier" / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in needles):
            return True
    return False


def _missing_attempt() -> dict:
    return {
        "resolved": False,
        "has_reward": False,
        "f2p": None,
        "f2p_pass": None,
        "f2p_total": None,
        "p2p": None,
        "p2p_pass": None,
        "p2p_total": None,
        "partial": None,
        "tok_in": None,
        "tok_out": None,
        "dur_s": None,
        "notes": "missing reward.json",
    }


def harness_task_attempts(
    harness_dir: Path, tid: str, n_rollouts: int = 0, max_tokens: int | None = None
) -> list[dict]:
    """Attempts in `_aNN` order. A missing slot stays put as no-response, not shifted to the end."""
    from score_results import attempt_index, harbor_task_trials, rollout_number

    indexed: dict[int, dict] = {}
    loose: list[dict] = []
    for trial in harbor_task_trials(harness_dir, tid):
        reward = trial / "verifier" / "reward.json"
        if not reward.is_file():
            reward = trial / "reward.json"
        row = _attempt_from_trial(trial, reward if reward.is_file() else None, max_tokens)
        index = rollout_number(trial)
        if index is None:
            index = attempt_index(trial)
        if index is not None:
            previous = indexed.get(index)
            if previous is None:
                indexed[index] = row
            else:
                indexed[index] = row
        else:
            loose.append(row)
    if not indexed and not loose:
        return []
    width = n_rollouts if n_rollouts >= 1 else len(indexed) + len(loose)
    rows: list[dict] = []
    for index in range(1, width + 1):
        if index in indexed:
            rows.append(indexed[index])
        elif loose:
            rows.append(loose.pop(0))
        else:
            rows.append(_missing_attempt())
    return rows


def baseline_task_attempts(baseline_dir: Path, tid: str) -> list[dict]:
    task = baseline_dir / tid
    attempts = sorted(p for p in task.glob("attempt-*") if p.is_dir())
    rows = []
    for attempt in attempts:
        reward = attempt / "verifier" / "reward.json"
        if not reward.is_file():
            reward = attempt / "reward.json"
        rows.append(_attempt_from_trial(attempt, reward if reward.is_file() else None))
    if rows:
        return rows
    reward = task / "reward.json"
    if reward.is_file() or (task / "agent.patch").is_file():
        return [_attempt_from_trial(task, reward if reward.is_file() else None)]
    return []


CHAT_API_BASE = "https://api.deepseek.com/v1"


def eval_model_label(model: str) -> str:
    """Artifact label. The HTTP model id stays the catalog id without this prefix."""
    raw = (model or "").strip() or "deepseek-flash"
    if raw.startswith("openai/"):
        return raw
    return f"openai/{raw}"


HARBOR_TIMEOUT_NOTE = (
    "Harbor agent budget is task.toml agent.timeout_sec times timeout multiplier 1 "
    "(DeepSWE often 10800s). A rollout that stops near 5100s is iCode's own duration, "
    "not a Harbor AgentTimeoutError."
)


def _thinking_type(effort: str) -> str:
    return "disabled" if str(effort or "").strip().lower() == "none" else "enabled"


def _limit(value) -> int | None:
    """A recorded iCode limit, or None. A run that recorded none shows `-`, not a guess."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _recorded_icode_mode() -> str:
    mode = (os.environ.get("ICODE_MODE") or "").strip().lower()
    if mode == "binary":
        return "release"
    return mode


def build_eval_protocol(
    *,
    workdir: Path | None,
    model: str,
    api_base: str,
    n_rollouts: int,
    concurrency: int,
    cpus_each: int | float,
) -> dict:
    """Record the iCode drop and model knobs used for this run."""
    inputs: dict = {}
    bin_path = ""
    git_doc = None
    release_doc = None
    if workdir is not None:
        inputs_path = workdir / "eval_protocol_inputs.json"
        loaded = load_json(inputs_path)
        if isinstance(loaded, dict):
            inputs = loaded
        bin_file = workdir / "icode_bin_path.txt"
        if bin_file.is_file():
            bin_path = bin_file.read_text(encoding="utf-8").strip()
        git_raw = load_json(workdir / "icode_git.json")
        if isinstance(git_raw, dict) and str(git_raw.get("sha") or "").strip():
            git_doc = git_raw
        release_raw = load_json(workdir / "icode_release.json")
        if isinstance(release_raw, dict) and str(release_raw.get("sha256") or "").strip():
            release_doc = {
                "filename": str(release_raw.get("filename") or ""),
                "sha256": str(release_raw.get("sha256") or ""),
            }
    mode = _recorded_icode_mode()
    # Both files means a previous run's record survived. The mode picks one.
    # A single file is this run's record, including when report.sh defaulted
    # ICODE_MODE to binary before this function ran.
    if git_doc is not None and release_doc is not None:
        if mode == "release":
            git_doc = None
        else:
            release_doc = None
    effort = str(
        inputs.get("reasoning_effort")
        or os.environ.get("ICODE_REASONING_EFFORT")
        or "high"
    )
    provider = str(inputs.get("provider") or os.environ.get("ICODE_PROVIDER") or "DeepSeek")
    max_tokens = _limit(inputs.get("max_tokens")) or _limit(os.environ.get("ICODE_MAX_TOKENS"))
    max_iterations = _limit(inputs.get("max_iterations")) or _limit(os.environ.get("ICODE_MAX_ITERATIONS"))
    if git_doc:
        icode = {
            "mode": "git",
            "bin_path": bin_path,
            "source": f"{git_doc.get('url') or ''}@{git_doc.get('ref') or ''}",
            "git": {
                "url": str(git_doc.get("url") or ""),
                "kind": str(git_doc.get("kind") or ""),
                "ref": str(git_doc.get("ref") or ""),
                "sha": str(git_doc.get("sha") or ""),
                "subject": str(git_doc.get("subject") or ""),
            },
        }
    else:
        icode = {
            "mode": mode or "release",
            "bin_path": bin_path,
            "source": bin_path,
        }
        if release_doc:
            icode["release"] = release_doc
    cpu_lock = inputs.get("cpu_lock_qty")
    if not isinstance(cpu_lock, int) or isinstance(cpu_lock, bool):
        raw_lock = os.environ.get("CPU_LOCK_QTY") or ""
        cpu_lock = int(raw_lock) if raw_lock.isdigit() else None
    harness = inputs.get("harness") if isinstance(inputs.get("harness"), dict) else None
    if harness is None:
        harness = harness_protocol(workdir)
    protocol = {
        "harness": harness,
        "icode": icode,
        "model_params": {
            "model": str(inputs.get("model") or model),
            "api_base": str(inputs.get("api_base") or api_base),
            "provider": provider,
            "reasoning_effort": effort,
            "thinking": {"type": "default" if effort == "default" else _thinking_type(effort)},
            "max_tokens": max_tokens,
            "max_iterations": max_iterations,
            "n_rollouts": n_rollouts,
        },
        "resources": {
            "cpu_lock_qty": cpu_lock,
            "concurrency": int(inputs.get("concurrency") or concurrency),
            "cpus_each": cpu_count(inputs.get("cpus_each")) or cpus_each,
            "harbor_agent_timeout_note": HARBOR_TIMEOUT_NOTE,
        },
    }
    for key in (
        "harbor",
        "benchmark",
        "grader_overlay",
        "images",
        "env_check",
        "worker",
        "requester",
        "pipeline",
        "isolation",
    ):
        if key in inputs:
            protocol[key] = inputs[key]
    return protocol


def build_artifact(
    *,
    suite: str,
    model: str,
    api_base: str,
    task_ids: list[str],
    harness_dir: Path,
    baseline_dir: Path,
    n_rollouts: int,
    concurrency: int,
    cpus_each: int | float,
    run_id: str,
    eval_protocol: dict | None = None,
) -> dict:
    del baseline_dir
    params = (eval_protocol or {}).get("model_params")
    max_tokens = _limit(params.get("max_tokens")) if isinstance(params, dict) else None
    raw_rows = [(tid, harness_task_attempts(harness_dir, tid, n_rollouts, max_tokens)) for tid in task_ids]
    icode_rows = [(tid, [adjust_attempt(row) for row in rows]) for tid, rows in raw_rows]
    arm = summarize_arm(icode_rows, n_rollouts, concurrency)
    raw_arm = summarize_arm(raw_rows, n_rollouts, concurrency)
    progress_path = harness_dir / "progress.json"
    if progress_path.is_file():
        prog = load_json(progress_path)
        if isinstance(prog, dict):
            done = prog.get("done")
            needed = prog.get("needed")
            if isinstance(done, int) and isinstance(needed, int) and done < needed:
                note = prog.get("eta_note")
                if isinstance(note, str) and note.strip():
                    timing = arm.get("timing")
                    if not isinstance(timing, dict):
                        timing = {}
                        arm["timing"] = timing
                    timing["eta_note"] = note
    doc = {
        "suite": suite,
        "model": model,
        "api_base": api_base,
        "run_id": run_id,
        "run_label": run_label_for(suite, run_id),
        "n_tasks": len(task_ids),
        "n_rollouts": n_rollouts,
        "concurrency": concurrency,
        "cpus_each": cpus_each,
        "icode": arm,
        "icode_raw": {k: v for k, v in raw_arm.items() if k not in ("tasks", "unscored_tasks", "empty_patches")},
        "anticheat": anticheat_block(harness_dir, raw_arm, arm),
    }
    if eval_protocol:
        doc["eval_protocol"] = eval_protocol
        git = (eval_protocol.get("icode") or {}).get("git")
        if isinstance(git, dict) and git.get("sha"):
            doc["icode_git"] = git
        release = (eval_protocol.get("icode") or {}).get("release")
        if isinstance(release, dict) and release.get("sha256"):
            doc["icode_release"] = {
                "filename": str(release.get("filename") or ""),
                "sha256": str(release.get("sha256") or ""),
            }
    return doc


def anticheat_block(harness_dir: Path, raw_arm: dict, arm: dict) -> dict:
    """The anticheat phase's verdict summary plus raw and official macro Pass@1. ``not_run`` when it wrote none."""
    found = load_json(harness_dir / "anticheat" / "summary.json")
    block = dict(found) if isinstance(found, dict) else {"status": "not_run"}
    block.setdefault("status", "ok")
    block["macro_pass@1_raw"] = raw_arm.get("macro_pass@1")
    block["macro_pass@1_official"] = arm.get("macro_pass@1")
    return block


def anticheat_line(doc: dict) -> str | None:
    block = doc.get("anticheat")
    if not isinstance(block, dict):
        return None
    if block.get("status") != "ok":
        return "- Anti-cheat: **not run** (official metrics equal raw metrics)"
    counts = block.get("counts") or {}
    return (
        f"- Anti-cheat `{block.get('version') or '-'}`: clean **{counts.get('clean', 0)}** · "
        f"flagged **{counts.get('flagged', 0)}** · rejected **{counts.get('rejected', 0)}** · "
        f"not run **{counts.get('not_run', 0)}** · macro Pass@1 raw **{_pct(block.get('macro_pass@1_raw'))}** → official "
        f"**{_pct(block.get('macro_pass@1_official'))}** (metrics in this summary are official; see anticheat/report.md)"
    )


def _pct(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "-"
    return f"{float(value) * 100:.1f}%"


def _num(value, digits=4) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "-"
    return f"{float(value):.{digits}f}"


def _count(rate, passed, total) -> str:
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return "-"
    if isinstance(passed, int) and isinstance(total, int):
        return f"{float(rate):.4f} ({passed}/{total})"
    return f"{float(rate):.4f}"


def _p2p_cell(row: dict) -> str:
    """P2P for one question; an empty model.patch shows what the grader said but is not averaged."""
    if row.get("p2p") is None and row.get("empty_patch"):
        passed, total = row.get("grader_p2p_pass"), row.get("grader_p2p_total")
        if isinstance(passed, int) and isinstance(total, int):
            return f"excluded (grader {passed}/{total})"
        return "excluded"
    return _count(row.get("p2p"), row.get("p2p_pass"), row.get("p2p_total"))


def _fmt_token(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "-"
    n = float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        text = f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return sign + text + "M"
    if n >= 1_000:
        text = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return sign + text + "k"
    if n == int(n):
        return sign + str(int(n))
    return sign + f"{n:.1f}"


def _dur_cell(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "-"
    return str(int(round(float(value))))


def _dur_label(value) -> str:
    cell = _dur_cell(value)
    if cell == "-":
        return "-"
    return cell + "s"


def _span_label(value) -> str:
    """Wall and per-rollout text: 45s, 15m, 2h 5m, 1d 6h."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "-"
    seconds = int(round(float(value)))
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        hours, minutes = divmod(seconds, 3600)
        minutes //= 60
        if minutes == 0:
            return f"{hours}h"
        return f"{hours}h {minutes}m"
    days, hours = divmod(seconds, 86400)
    hours //= 3600
    if hours == 0:
        return f"{days}d"
    return f"{days}d {hours}h"


def _suite_title(suite) -> str:
    names = {"deepswe": "DeepSWE", "swebenchpro": "SWE-bench Pro", "lolbench": "LOLBench"}
    text = "" if suite is None else str(suite)
    return names.get(text.strip().lower(), text or "-")


def run_label_for(suite: str, run_id: str) -> str:
    text = str(run_id or "")
    prefix = "jenkins-"
    if text.startswith(prefix) and text[len(prefix) :]:
        return f"{suite}-{text[len(prefix) :]}"
    return text


def _micro_count(rate, passed, total) -> str:
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return "-"
    if isinstance(passed, int) and isinstance(total, int) and total >= 1000:
        return f"**{float(rate):.4f}** ({_fmt_token(passed)}/{_fmt_token(total)})"
    if isinstance(passed, int) and isinstance(total, int):
        return f"**{float(rate):.4f}** ({passed}/{total})"
    return f"**{float(rate):.4f}**"


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    cells = [headers] + rows
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]

    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(row))) + " |"

    sep = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    return [fmt(headers), sep] + [fmt(row) for row in rows]


def _ci_half(stored, fracs: list) -> float | None:
    if isinstance(stored, (int, float)) and not isinstance(stored, bool):
        return float(stored)
    vals = [float(value) for value in fracs if isinstance(value, (int, float)) and not isinstance(value, bool)]
    _mean, half = mean_ci(vals)
    return half


def empty_patch_cells(item: dict) -> list[str]:
    tok = "-" if item.get("tok_in") is None else f"{_fmt_token(item.get('tok_in'))}/{_fmt_token(item.get('tok_out'))}"
    return [
        str(item.get("id") or ""),
        str(item.get("rollout") or "-"),
        str(item.get("trial") or "-"),
        str(item.get("cause") or "no_edit"),
        "-" if item.get("icode_exit") is None else str(item.get("icode_exit")),
        "-" if item.get("model_calls") is None else str(item.get("model_calls")),
        "-" if item.get("last_output_tokens") is None else str(item.get("last_output_tokens")),
        tok,
        _dur_cell(item.get("dur_s")),
        pass_total(item.get("f2p_pass"), item.get("f2p_total")),
        pass_total(item.get("grader_p2p_pass"), item.get("grader_p2p_total")),
    ]


def _empty_patch_markdown(items: list) -> list[str]:
    items = [item for item in items if isinstance(item, dict)]
    if not items:
        return ["empty-patch rollouts: 0", ""]
    lines = [f"## {EMPTY_PATCH_TITLE}", "", EMPTY_PATCH_INTRO, ""]
    lines.extend(_md_table(EMPTY_PATCH_HEADERS, [[c.replace("|", "/") for c in empty_patch_cells(item)] for item in items]))
    lines.append("")
    return lines


def icode_identity(icode: dict | None) -> str:
    """The commit or release sha256 this run graded. Empty when this run recorded neither."""
    if not isinstance(icode, dict):
        return ""
    git = icode.get("git") if isinstance(icode.get("git"), dict) else {}
    release = icode.get("release") if isinstance(icode.get("release"), dict) else {}
    if icode.get("mode") == "release":
        return str(release.get("sha256") or "")
    if git.get("sha"):
        return str(git.get("sha") or "")
    return str(release.get("sha256") or "")


def harness_display(protocol: dict | None) -> str:
    """The harness's display name from eval_protocol.harness; iCode for runs older than it."""
    harness = protocol.get("harness") if isinstance(protocol, dict) and isinstance(protocol.get("harness"), dict) else {}
    return str(harness.get("display") or "iCode")


def icode_under_test(protocol: dict | None) -> str:
    """One line naming the harness build this run actually graded: git SHA or release sha256."""
    icode = protocol.get("icode") if isinstance(protocol, dict) and isinstance(protocol.get("icode"), dict) else {}
    git = icode.get("git") if isinstance(icode.get("git"), dict) else {}
    if icode.get("mode") != "release" and (git.get("sha") or icode.get("mode") == "git"):
        kind = git.get("kind") or "-"
        ref = git.get("ref") or "-"
        sha = git.get("sha") or "-"
        subject = str(git.get("subject") or "").strip()
        line = f"git {kind} {ref} -> {sha}"
        if subject:
            line += f" ({subject})"
        return line
    release = icode.get("release") if isinstance(icode.get("release"), dict) else {}
    name = release.get("filename") or "-"
    digest = release.get("sha256") or "-"
    return f"release {name} sha256 {digest}"


def summary_markdown(doc: dict) -> str:
    suite = doc.get("suite")
    title = _suite_title(suite)
    n_tasks = doc.get("n_tasks")
    k = int(doc.get("n_rollouts") or 1)
    label = doc.get("run_label") or run_label_for(str(suite or ""), str(doc.get("run_id") or ""))
    lines = [f"# Eval metrics summary — {suite}", ""]
    lines.append(f"- Suite: `{suite}`")
    lines.append(f"- Run: `{label}`")
    lines.append(f"- Run dir: `{doc.get('run_dir') or '-'}`")
    lines.append(f"- Eval model: `{doc.get('model')}`")
    lines.append(f"- Eval API base: `{doc.get('api_base')}`")
    protocol = doc.get("eval_protocol")
    agent = harness_display(protocol)
    if isinstance(protocol, dict):
        icode = protocol.get("icode") if isinstance(protocol.get("icode"), dict) else {}
        params = protocol.get("model_params") if isinstance(protocol.get("model_params"), dict) else {}
        resources = protocol.get("resources") if isinstance(protocol.get("resources"), dict) else {}
        thinking = params.get("thinking") if isinstance(params.get("thinking"), dict) else {}
        harness = protocol.get("harness") if isinstance(protocol.get("harness"), dict) else None
        if harness:
            lines.append(f"- {harness_line(harness)}")
        lines.append(f"- {agent} mode: `{icode.get('mode') or '-'}`")
        lines.append(f"- {agent} source: `{icode.get('source') or '-'}`")
        lines.append(f"- {agent} under test: `{icode_under_test(protocol)}`")
        lines.append(
            "- Model params: "
            f"provider `{params.get('provider') or '-'}` · "
            f"reasoning_effort `{params.get('reasoning_effort') or '-'}` · "
            f"thinking.type `{thinking.get('type') or '-'}` · "
            f"max_tokens `{params.get('max_tokens') or '-'}` · "
            f"max_iterations `{params.get('max_iterations') or '-'}`"
        )
        lines.append(
            "- Resources: "
            f"cpu_lock_qty `{resources.get('cpu_lock_qty')}` · "
            f"concurrency `{resources.get('concurrency')}` · "
            f"cpus_each `{resources.get('cpus_each')}`"
        )
        by_node = resources.get("by_node") if isinstance(resources.get("by_node"), dict) else {}
        for node, res in sorted(by_node.items()):
            res = res if isinstance(res, dict) else {}
            lines.append(
                f"  - node `{node}`: cpu_lock_qty `{res.get('cpu_lock_qty')}` · "
                f"concurrency `{res.get('concurrency')}` · cpus_each `{res.get('cpus_each')}`"
            )
        note = resources.get("harbor_agent_timeout_note")
        if isinstance(note, str) and note.strip():
            lines.append(f"- Agent timeout: {note}")
    lines.append("")
    arm = doc.get("icode")
    if isinstance(arm, dict):
        tasks = arm.get("tasks") or []
        scored = arm.get("scored_rollouts")
        excluded = arm.get("infra_excluded")
        if not isinstance(excluded, int) or isinstance(excluded, bool):
            excluded = 0
        metrics_n = len(tasks) if isinstance(n_tasks, int) else len(tasks)
        if isinstance(n_tasks, int) and not isinstance(n_tasks, bool):
            metrics_n = n_tasks
        lines.append(f"- Tasks with metrics: **{metrics_n}** (missing/excluded: {excluded})")
        from provenance import coverage_line

        coverage = coverage_line(doc.get("coverage"))
        if coverage:
            lines.append(f"- {coverage}")
        ci = _ci_half(arm.get("macro_pass@1_ci"), [row.get("pass_frac") for row in tasks])
        ci_txt = "" if ci is None else f" ±{ci * 100:.1f}% (CI)"
        ci_s = _ci_half(
            arm.get("macro_pass@1_scored_ci"),
            [row.get("pass_frac_scored") for row in tasks if row.get("n_scored")],
        )
        ci_s_txt = "" if ci_s is None else f" ±{ci_s * 100:.1f}% (CI)"
        padded_bits = [f"Pass@{i} **{_pct(arm.get(f'pass@{i}'))}**" for i in range(1, k + 1)]
        scored_bits = [f"Pass@{i} **{_pct(arm.get(f'pass@{i}_scored'))}**" for i in range(1, k + 1)]
        lines.append(
            f"- {title} Pass@1..k (padded, missing=fail): " + " · ".join(padded_bits)
        )
        lines.append(
            f"- {title} Pass@1..k (scored-only, missing omitted): " + " · ".join(scored_bits)
        )
        lines.append(
            f"- {title} macro Pass@1 (padded, mean of c/n): **{_pct(arm.get('macro_pass@1'))}**{ci_txt}"
        )
        lines.append(
            f"- {title} macro Pass@1 (scored-only, mean of c_scored/n_scored): **{_pct(arm.get('macro_pass@1_scored'))}**{ci_s_txt}"
        )
        hits = arm.get("best_attempt_hits")
        if not isinstance(hits, int) or isinstance(hits, bool):
            hits = sum(1 for row in tasks if row.get("best"))
        unscored = arm.get("unscored_rollouts")
        if not isinstance(unscored, int) or isinstance(unscored, bool):
            unscored = "-"
        lines.append(
            f"- {title} detail — first-rollout (= padded Pass@1): **{_pct(arm.get('pass@1'))}** · any-pass: **{_pct(arm.get('any_pass'))}** · scored rollouts: **{scored}** · unscored rollouts: **{unscored}** · questions with no trial: **{excluded}**"
        )
        lines.append(f"- Best-attempt pass (secondary): **{hits}** / {metrics_n} ({_pct(arm.get('any_pass'))})")
        empty_items = arm.get("empty_patches") if isinstance(arm.get("empty_patches"), list) else None
        if empty_items is not None:
            counts = empty_patch_cause_counts(empty_items)
            of = f" of {metrics_n * k}" if isinstance(metrics_n, int) else ""
            lines.append(
                f"- Empty model.patch (repo unchanged after {agent}): **{len(empty_items)}**{of} rollouts "
                f"(infra {counts['infra']} · cut_off {counts['cut_off']} · no_edit {counts['no_edit']})"
            )
        if isinstance(arm.get("cut_off_rollouts"), int):
            lines.append(f"- Cut-off replies (last reply hit max_tokens): **{arm['cut_off_rollouts']}** rollouts")
        if isinstance(arm.get("oom_killed_rollouts"), int):
            lines.append(
                f"- OOM-killed rollouts ({agent} exit 137 or the container ran out of memory): "
                f"**{arm['oom_killed_rollouts']}**"
            )
        macro = arm.get("macro") or {}
        micro = arm.get("micro") or {}
        lines.append(
            f"- Macro avg (best rollout per question) — F2P: **{_num(macro.get('f2p'))}** · P2P: **{_num(macro.get('p2p'))}** · partial: **{_num(macro.get('partial'))}**"
        )
        lines.append(
            "- Micro (best rollout per question) — F2P: "
            + _micro_count(micro.get("f2p"), micro.get("f2p_pass"), micro.get("f2p_total"))
            + " · P2P: "
            + _micro_count(micro.get("p2p"), micro.get("p2p_pass"), micro.get("p2p_total"))
            + " · partial: "
            + _micro_count(micro.get("partial"), micro.get("partial_pass"), micro.get("partial_total"))
        )
        micro_all = arm.get("micro_all")
        if isinstance(micro_all, dict):
            lines.append(
                f"- Micro (all {micro_all.get('rollouts')} scored rollouts) — F2P: "
                + _micro_count(micro_all.get("f2p"), micro_all.get("f2p_pass"), micro_all.get("f2p_total"))
                + " · P2P: "
                + _micro_count(micro_all.get("p2p"), micro_all.get("p2p_pass"), micro_all.get("p2p_total"))
            )
        p2p_excluded = arm.get("p2p_excluded_empty_patch")
        if isinstance(p2p_excluded, int) and not isinstance(p2p_excluded, bool) and p2p_excluded > 0:
            lines.append(
                "- P2P and partial averages are over questions whose best rollout submitted a patch "
                f"({p2p_excluded} excluded: best rollout left an empty model.patch)"
            )
        timing = arm.get("timing") or {}
        mem_peak = doc.get("container_mem_max_gb")
        if isinstance(mem_peak, (int, float)) and not isinstance(mem_peak, bool):
            lines.append(f"- Container memory — max peak: **{float(mem_peak):.2f} GB**")
        no_score = questions_without_score(doc)
        if no_score:
            lines.append(
                f"- Questions without a score: **{len(no_score)}** — "
                + " · ".join(f"{tid} ({why})" for tid, why in no_score).replace("|", "/")
            )
        else:
            lines.append("- Questions without a score: **0**")
        lines.append(f"- Median best-attempt duration: **{_dur_label(timing.get('median_best_attempt_s'))}**")
        expected = metrics_n * k if isinstance(metrics_n, int) else None
        wall_txt = _span_label(timing.get("wall_seconds"))
        if isinstance(scored, int) and isinstance(expected, int) and expected > 0 and scored >= expected:
            wall_txt = f"{wall_txt} (complete)"
        lines.append(
            f"- Timing — wall: **{wall_txt}** · concurrency: **{doc.get('concurrency')}** · rollouts/task: **{k}**"
        )
        progress = "-"
        if isinstance(scored, int) and isinstance(expected, int) and expected > 0:
            done = metrics_n - excluded if isinstance(metrics_n, int) else metrics_n
            progress = f"**{scored}/{expected} rollouts ({scored / expected * 100:.1f}%) · {done}/{metrics_n} tasks done**"
        lines.append(
            f"- Timing — mean/median per rollout: **{_span_label(timing.get('mean_rollout_s'))}/{_span_label(timing.get('median_rollout_s'))}** · progress: {progress}"
        )
        tokens = arm.get("tokens") or {}
        avg = tokens.get("avg_total_per_task")
        if avg is None and isinstance(tokens.get("total"), int) and isinstance(tokens.get("tasks_with_data"), int) and tokens.get("tasks_with_data"):
            avg = tokens["total"] / tokens["tasks_with_data"]
        token_sum = f"{_fmt_token(tokens.get('in'))}/{_fmt_token(tokens.get('out'))}/{_fmt_token(tokens.get('total'))}"
        lines.append(
            f"- Tokens — tasks with data: **{tokens.get('tasks_with_data')}** · total in/out/sum: **{token_sum}** · avg total/task: **{_fmt_token(avg)}**"
        )
        lines.append("")
        headers = ["#", "task", "c/n", "c_scored/n_scored", "pass_frac", "first", "best", "reward", "f2p", "p2p", "partial", "tok_in/out", "dur_s", "notes"]
        table_rows = []
        ordered = sorted(arm["tasks"], key=lambda row: (-row["pass_frac"], row["id"]))
        for i, row in enumerate(ordered, start=1):
            tok = "-" if row.get("tok_in") is None else f"{_fmt_token(row.get('tok_in'))}/{_fmt_token(row.get('tok_out'))}"
            c_s = row.get("c_scored")
            n_s = row.get("n_scored")
            scored_cn = f"{c_s}/{n_s}" if isinstance(c_s, int) and isinstance(n_s, int) else "-"
            table_rows.append(
                [
                    str(i),
                    str(row["id"]),
                    f"{row['c']}/{row['n']}",
                    scored_cn,
                    _num(row["pass_frac"]),
                    "yes" if row["first"] else "no",
                    "yes" if row["best"] else "no",
                    _num(row["reward"]),
                    _count(row.get("f2p"), row.get("f2p_pass"), row.get("f2p_total")),
                    _p2p_cell(row),
                    _num(row.get("partial")),
                    tok,
                    _dur_cell(row.get("dur_s")),
                    str(row.get("notes") or "-").replace("|", "/"),
                ]
            )
        lines.extend(_md_table(headers, table_rows))
        lines.append("")
        if empty_items is not None:
            lines.extend(_empty_patch_markdown(empty_items))
        unscored_tasks = arm.get("unscored_tasks") if isinstance(arm.get("unscored_tasks"), list) else []
        unscored_tasks = [row for row in unscored_tasks if isinstance(row, dict)]
        if not unscored_tasks:
            lines.append("unscored rollouts: 0")
            lines.append("")
        else:
            lines.append("## Unscored / no-response (not in Pass@k as a scored fail)")
            lines.append("")
            lines.append("Tasks with at least one attempt that returned no reward. High rates here can move padded Pass@k.")
            lines.append("")
            u_rows = []
            skipped = skipped_reasons(doc)
            for row in unscored_tasks:
                u_rows.append(
                    [
                        str(row.get("id") or ""),
                        f"{row.get('unscored')}/{row.get('n')}",
                        unscored_notes(row, skipped).replace("|", "/"),
                    ]
                )
            lines.extend(_md_table(["Task", "unscored/n", "notes"], u_rows))
            lines.append("")
    if isinstance(protocol, dict):
        from provenance import provenance_brief

        brief = provenance_brief(protocol)
        if brief:
            if lines[-1] != "":
                lines.append("")
            lines.extend(["### Provenance", ""])
            lines.extend(f"- {line}" for line in brief[:-1])
            lines.append(f"- {brief[-1].replace(PROVENANCE_FILE, f'[{PROVENANCE_FILE}]({PROVENANCE_FILE})')}")
    ac_line = anticheat_line(doc)
    if ac_line:
        if lines[-1] != "":
            lines.append("")
        lines.append(ac_line)
    return "\n".join(lines).rstrip("\n") + "\n"


# Written by tasks/leakscan in $WORKDIR: one `<task>\t<reason>` line per
# question the build keeps out of Harbor.
SKIPPED_TASKS = "skipped_tasks.txt"


def read_skipped_tasks(path: Path) -> dict[str, str]:
    """Questions a build kept out of Harbor, with the reason, by task id."""
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            tid, _, why = line.strip().partition("\t")
            if tid.strip():
                out[tid.strip()] = why.strip() or "skipped"
    return out


def container_mem_peak(jsonl: Path) -> float | None:
    """The largest ``peak_gb`` in a build's container_mem.jsonl, or None."""
    peak = None
    if not jsonl.is_file():
        return None
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            raw = row.get("peak_gb")
            if raw is None:
                continue
            gb = float(raw)
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            continue
        if gb <= 0:
            continue
        peak = gb if peak is None else max(peak, gb)
    return peak


def memory_sidecar(harness_dir: Path, workdir: Path | None = None) -> tuple[float | None, list[str]]:
    peak = container_mem_peak(harness_dir / "container_mem.jsonl")
    skipped_path = harness_dir / "skipped_questions.txt"
    skipped: list[str] = []
    if skipped_path.is_file():
        skipped = [ln.strip() for ln in skipped_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if workdir is not None:
        skipped.extend(f"{tid}: {why}" for tid, why in read_skipped_tasks(workdir / SKIPPED_TASKS).items())
    return peak, skipped


def copy_memory_sidecars(harness_dir: Path, out_dir: Path, skipped: list[str] | None = None) -> None:
    src = harness_dir / "container_mem.jsonl"
    if src.is_file():
        (out_dir / "container_mem.jsonl").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    if skipped:
        (out_dir / "skipped_questions.txt").write_text("".join(f"{item}\n" for item in skipped), encoding="utf-8")


def write_report(doc: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "artifact.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (out_dir / "summary.md").write_text(summary_markdown(doc), encoding="utf-8")
    from report_html import report_html

    (out_dir / "report.html").write_text(report_html(doc), encoding="utf-8")
    from provenance import provenance_document

    prov = provenance_document(doc)
    if prov:
        (out_dir / PROVENANCE_FILE).write_text(prov, encoding="utf-8")
    else:
        (out_dir / PROVENANCE_FILE).unlink(missing_ok=True)
    return path


def unscored_causes(arm: dict) -> dict[str, int]:
    """Unscored rollouts by cause: the `infra:` note, else the first note; trials that never appeared as `no trial`."""
    causes: dict[str, int] = {}
    for row in arm.get("tasks") or []:
        for attempt in row.get("rollouts") or []:
            if attempt_is_scored(attempt):
                continue
            notes = [part.strip() for part in str(attempt.get("notes") or "").split(";") if part.strip()]
            infra = [note[len("infra: "):] for note in notes if note.startswith("infra: ")]
            why = infra[0] if infra else (notes[0] if notes else "no score")
            causes[why] = causes.get(why, 0) + 1
        missing = int(row.get("unscored") or 0) - sum(
            1 for attempt in row.get("rollouts") or [] if not attempt_is_scored(attempt)
        )
        if missing > 0:
            causes["no trial"] = causes.get("no trial", 0) + missing
    return causes


def skipped_reasons(doc: dict) -> dict[str, str]:
    """doc["skipped_questions"] (`<task>: <reason>`) by task id."""
    out: dict[str, str] = {}
    for item in doc.get("skipped_questions") or []:
        tid, _, why = str(item).partition(": ")
        if tid.strip():
            out[tid.strip()] = why.strip() or "skipped"
    return out


def questions_without_score(doc: dict) -> list[tuple[str, str]]:
    """``(question, cause)`` for every question with no scored rollout.

    The cause is the skip reason (a leak-scan hit), else the rollouts' own
    causes, such as an `infra:` note or `no trial`.
    """
    arm = doc.get("icode") if isinstance(doc.get("icode"), dict) else {}
    skipped = skipped_reasons(doc)
    out = []
    for row in arm.get("tasks") or []:
        if not isinstance(row, dict) or row.get("n_scored"):
            continue
        tid = str(row.get("id") or "")
        if tid in skipped:
            out.append((tid, f"skipped: {skipped[tid]}"))
            continue
        n = row.get("n") or 0
        causes = sorted(unscored_causes({"tasks": [row]}).items(), key=lambda kv: (-kv[1], kv[0]))
        out.append((tid, "; ".join(f"{why} ({k}/{n})" for why, k in causes) or "no score"))
    return out


def unscored_notes(row: dict, skipped: dict[str, str]) -> str:
    """An Unscored table row's notes; a skipped question says why it never ran."""
    tid = str(row.get("id") or "")
    if tid in skipped:
        return f"skipped: {skipped[tid]}"
    notes = row.get("notes")
    return "-" if notes in (None, "") else str(notes)


def report_unscored(doc: dict, out: Path | None) -> int:
    """Write the unscored rollout count to `out` and warn when it is not 0."""
    arm = doc.get("icode") if isinstance(doc.get("icode"), dict) else {}
    lost = arm.get("unscored_rollouts")
    lost = lost if isinstance(lost, int) and not isinstance(lost, bool) else 0
    if out is not None:
        out.write_text(f"{lost}\n", encoding="utf-8")
    if lost:
        expected = int(doc.get("n_tasks") or 0) * int(doc.get("n_rollouts") or 0)
        why = ", ".join(f"{cause} {n}" for cause, n in sorted(unscored_causes(arm).items(), key=lambda kv: -kv[1]))
        print(f"WARNING: {lost} of {expected} rollouts have no score ({why or 'no reward.json'}); see Unscored in summary.md")
    return lost


def run_folder_name(utc: str, task_ids: list[str], build_number: str = "") -> str:
    number = build_number.strip()
    if number:
        return f"jenkins-{number}-{utc}"
    if len(task_ids) == 1:
        safe = task_ids[0].replace("/", "_").replace("\\", "_")
        return f"{utc}-{safe}"
    return f"{utc}-n{len(task_ids)}"


def demo_artifact() -> dict:
    def attempts(pattern: list[bool]) -> list[dict]:
        rows = []
        for i, ok in enumerate(pattern):
            rows.append(
                {
                    "resolved": ok,
                    "f2p": 1.0 if ok else 0.5,
                    "f2p_pass": 2 if ok else 1,
                    "f2p_total": 2,
                    "p2p": 1.0,
                    "p2p_pass": 4,
                    "p2p_total": 4,
                    "partial": 1.0 if ok else 5 / 6,
                    "tok_in": 1000 * (i + 1),
                    "tok_out": 10 * (i + 1),
                    "dur_s": 100.0 + i,
                    "notes": "",
                }
            )
        return rows

    icode = [
        ("alpha", attempts([True, True, False, True])),
        ("beta", attempts([False, True, False, False])),
        ("gamma", attempts([False, False, False, False])),
    ]
    return {
        "suite": "deepswe",
        "model": "openai/deepseek-flash",
        "api_base": CHAT_API_BASE,
        "run_id": "jenkins-25",
        "run_label": "deepswe-25",
        "run_dir": "output/deepswe/jenkins-25-demo",
        "n_tasks": 3,
        "n_rollouts": 4,
        "concurrency": 4,
        "cpus_each": 1,
        "icode": summarize_arm(icode, 4, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--harness-dir", default="")
    ap.add_argument("--baseline-dir", default="")
    ap.add_argument("--task-file", default="")
    ap.add_argument("--suite", default=os.environ.get("BENCHMARK", "deepswe"))
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"))
    ap.add_argument("--api-base", default=os.environ.get("DEEPSEEK_API_BASE", CHAT_API_BASE))
    ap.add_argument("--run-id", default=os.environ.get("BUILD_NUMBER", "local"))
    ap.add_argument("--n-rollouts", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=0)
    ap.add_argument("--cpus-each", type=float, default=0)
    ap.add_argument("--workdir", default=os.environ.get("WORKDIR", ""))
    ap.add_argument("--utc", default="")
    ap.add_argument("--run-folder", default="")
    ap.add_argument("--unscored-out", default="", help="file for the unscored rollout count (Jenkins reads it)")
    args = ap.parse_args()
    if args.demo:
        doc = demo_artifact()
        out = Path(args.out_dir or "/tmp/mac-k3d-report-demo")
        path = write_report(doc, out)
        print(f"wrote {path}")
        return 0
    n_rollouts = args.n_rollouts or int(os.environ.get("N_ROLLOUTS") or "1")
    concurrency = args.concurrency or int(os.environ.get("EVAL_SLOTS") or "1")
    cpus_each = cpu_count(args.cpus_each) or cpu_count(os.environ.get("EVAL_CPUS_EACH")) or 1
    ids = [ln.strip() for ln in Path(args.task_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    run_id = args.run_id
    if run_id not in ("local",) and not str(run_id).startswith(("jenkins-", "local-")):
        run_id = f"jenkins-{run_id}"
    workdir = Path(args.workdir) if args.workdir else None
    mem_peak, skipped = memory_sidecar(Path(args.harness_dir), workdir)
    protocol = build_eval_protocol(
        workdir=workdir,
        model=args.model,
        api_base=args.api_base,
        n_rollouts=n_rollouts,
        concurrency=concurrency,
        cpus_each=cpus_each,
    )
    doc = build_artifact(
        suite=args.suite,
        model=eval_model_label(args.model),
        api_base=args.api_base,
        task_ids=ids,
        harness_dir=Path(args.harness_dir),
        baseline_dir=Path(args.baseline_dir),
        n_rollouts=n_rollouts,
        concurrency=concurrency,
        cpus_each=cpus_each,
        run_id=run_id,
        eval_protocol=protocol,
    )
    utc = args.utc or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    folder = args.run_folder or run_folder_name(utc, ids, os.environ.get("BUILD_NUMBER", ""))
    doc["run_dir"] = f"output/{args.suite}/{folder}.tar.gz"
    doc["container_mem_max_gb"] = mem_peak
    doc["skipped_questions"] = skipped
    out = Path(args.out_dir) if args.out_dir else Path("eval-runs") / "output" / args.suite
    if out.name != folder:
        out = out / folder
    path = write_report(doc, out)
    copy_memory_sidecars(Path(args.harness_dir), out, skipped)
    print(f"wrote {path}")
    report_unscored(doc, Path(args.unscored_out) if args.unscored_out else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
