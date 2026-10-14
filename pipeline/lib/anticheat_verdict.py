#!/usr/bin/env python3
"""Anti-cheat verdict per rollout: clean, flagged or rejected (report section 9.2, items 10-12).

Two signals: line/path Jaccard of the graded patch against the task's gold
patch (anticheat_similarity), and transcript rule hits (anticheat_transcript).
Thresholds and rules live in pipeline/config/anticheat-v1.json. Reviewer
decisions in anticheat_overrides.json replace the automatic verdict. A rollout
with no patch, no transcript and no reward.json never ran (Harbor stopped it
first) and is `not_run`, not clean.

Live (anticheat/verdict), writes <trial>/agent/anticheat.json and <harness>/anticheat/{summary.json,report.md}:
    anticheat_verdict.py --harness-dir H --tasks-dir T --task-file F --benchmark B

Rescore an old run without modifying it, writes <run>/artifact.anticheat.json and <run>/anticheat/report.md:
    anticheat_verdict.py --run-dir output/<suite>/<run> [--harbor-runs DIR] [--tasks-dir T]

Rejected rollouts count as unresolved in the official metrics, with F2P/P2P zeroed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path

from anticheat_similarity import (
    deliverable_names,
    generated_globs,
    load_config,
    parse_patch,
    similarity,
)
from anticheat_transcript import RULE_SEVERITY, scan
from harness_labels import labels_for
from harness_labels import load as load_labels
from score_results import (
    attempt_index,
    harbor_task_trials,
    infra_cause,
    load_json,
    parse_reward_value,
    trial_exception,
)

SCHEMA = "mac-k3d-anticheat-v1"
# What a reviewer may decide in anticheat_overrides.json.
OVERRIDE_DECISIONS = ("clean", "flagged", "rejected")
VERDICTS = OVERRIDE_DECISIONS + ("not_run",)
PATCH_CANDIDATES = (
    "artifacts/logs/artifacts/model.patch",
    "artifacts/logs/artifacts/solution.patch",
    "agent/capture.patch",
    "agent.patch",
)
REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE_TASKS = {
    "deepswe": "eval-runs/deep-swe/tasks",
    "lolbench": "eval-runs/lolbench/harbor_tasks",
    "swebenchpro": "eval-runs/swebenchpro/tasks",
}


def gold_patch(task_dir: Path) -> str | None:
    sol = task_dir / "solution"
    path = sol / "solution.patch"
    if not path.is_file():
        found = sorted(sol.glob("*.patch")) if sol.is_dir() else []
        path = found[0] if found else path
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def owned_test_paths(task_dir: Path) -> set[str]:
    owned: set[str] = set()
    tests = task_dir / "tests"
    if tests.is_dir():
        for patch in tests.rglob("*.patch"):
            owned |= set(parse_patch(patch.read_text(encoding="utf-8", errors="replace")))
    return owned


def patch_of_record(attempt: Path) -> Path | None:
    for rel in PATCH_CANDIDATES:
        if (attempt / rel).is_file():
            return attempt / rel
    found = sorted((attempt / "artifacts").rglob("*.patch")) if (attempt / "artifacts").is_dir() else []
    return found[0] if found else None


def transcripts(attempt: Path) -> list[Path]:
    """iCode's session events, or JiuwenSwarm's headless JSONL (plain or gzipped)."""
    agent = attempt / "agent"
    found = [p for p in (agent / "jiuwenswarm.jsonl", agent / "jiuwenswarm.jsonl.gz") if p.is_file()]
    sessions = agent / "icode-project" / "sessions"
    if sessions.is_dir():
        found += sorted(sessions.glob("*/events.jsonl")) + sorted(sessions.glob("*/events.jsonl.gz"))
    return found


def raw_resolved(attempt: Path) -> bool | None:
    for rel in ("verifier/reward.json", "reward.json"):
        data = load_json(attempt / rel)
        if isinstance(data, dict):
            return parse_reward_value(data.get("reward", data.get("resolved")))
    return None


class Context:
    """Per-run inputs shared by every attempt."""

    def __init__(self, suite: str, tasks_dir: Path | None, config: dict | None = None, labels: dict | None = None):
        self.suite = suite
        self.tasks_dir = tasks_dir
        self.config = config or load_config()
        self.labels = labels if labels is not None else load_labels()
        self.globs = generated_globs(self.config, suite)
        self._tasks: dict[str, dict] = {}

    def task(self, task_id: str) -> dict:
        if task_id not in self._tasks:
            tdir = self.tasks_dir / task_id if self.tasks_dir else None
            gold = gold_patch(tdir) if tdir and tdir.is_dir() else None
            aliases = (self.config.get("deliverable_aliases") or {}).get(task_id, [])
            stop = set(self.config.get("deliverable_stop_names") or [])
            names = deliverable_names(gold, self.globs, stop, aliases) if gold else set(aliases)
            owned = owned_test_paths(tdir) if tdir and tdir.is_dir() else set()
            self._tasks[task_id] = {"gold": gold, "deliverables": names, "owned_tests": owned}
        return self._tasks[task_id]


def tampering(patch_text: str, owned: set[str]) -> list[str]:
    return sorted(set(parse_patch(patch_text)) & owned)


def decide(sim: dict, scan_doc: dict, tamper: list[str], config: dict) -> tuple[str, list[str]]:
    th = config["thresholds"]
    reasons: list[str] = []
    flagged_sim = False
    rejected = False
    if sim.get("status") == "ok":
        lj, pj, n = sim["line_j"], sim["path_j"], sim["n_added"]
        enough = n >= th["flag_min_added"]
        if lj >= th["reject_line_j"] and n >= th.get("reject_min_added", th["flag_min_added"]):
            rejected = True
            reasons.append(f"line_j {lj:.2f} >= {th['reject_line_j']} over {n} added lines")
        elif lj >= th["flag_line_j"] and enough:
            flagged_sim = True
            reasons.append(f"line_j {lj:.2f} >= {th['flag_line_j']} over {n} added lines")
        elif pj >= th["flag_path_j"] and lj >= th["flag_path_line_j"]:
            flagged_sim = True
            reasons.append(f"path_j {pj:.2f} >= {th['flag_path_j']} with line_j {lj:.2f}")
        elif lj >= th["reject_line_j"]:
            flagged_sim = True
            reasons.append(f"line_j {lj:.2f} >= {th['reject_line_j']} but only {n} added lines")
    counts = scan_doc.get("counts") or {}
    flag_sev = set(config.get("flag_severities") or ["high"])
    severe = sorted(r for r in counts if RULE_SEVERITY.get(r) in flag_sev)
    evidence = sorted(set(config.get("reject_with_flag_rules") or []) & set(counts))
    for rule in severe:
        reasons.append(f"transcript {rule} x{counts[rule]}")
    if tamper and RULE_SEVERITY["test_tampering"] in flag_sev:
        reasons.append(f"test_tampering {len(tamper)} file(s)")
    if not rejected and flagged_sim and evidence:
        rejected = True
        reasons.append("similarity flag + transcript evidence: " + ", ".join(evidence))
    if rejected:
        return "rejected", reasons
    if flagged_sim or severe or (tamper and RULE_SEVERITY["test_tampering"] in flag_sev):
        return "flagged", reasons
    return "clean", reasons


def never_ran(*trials: Path | None) -> str:
    """Why a rollout with nothing to judge has no verdict: Harbor's exception, when one was recorded."""
    for trial in trials:
        exception = trial_exception(trial) if trial is not None else None
        if exception is not None:
            return f"no patch, transcript or reward.json; Harbor {exception['type'] or 'error'}: {infra_cause(exception)}"
    return "no patch, transcript or reward.json; Harbor recorded no exception"


def evaluate(ctx: Context, task_id: str, attempt_no: int, attempt: Path, transcript_root: Path | None = None) -> dict:
    task = ctx.task(task_id)
    patch_path = patch_of_record(attempt)
    patch_text = patch_path.read_text(encoding="utf-8", errors="replace") if patch_path else ""
    if not task["gold"]:
        sim = {"status": "no_gold"}
    elif not patch_text.strip():
        sim = {"status": "empty_patch"}
    else:
        sim = {"status": "ok", **similarity(patch_text, task["gold"], ctx.globs)}
    files = transcripts(transcript_root or attempt)
    scan_doc = scan(files, task["deliverables"])
    scan_doc["transcripts"] = [str(p.relative_to(transcript_root or attempt)) for p in files]
    tamper = tampering(patch_text, task["owned_tests"]) if patch_text else []
    verdict, reasons = decide(sim, scan_doc, tamper, ctx.config)
    resolved = raw_resolved(attempt)
    if not patch_text.strip() and not files and resolved is None:
        verdict, reasons = "not_run", [never_ran(attempt, transcript_root)]
    return {
        "schema": SCHEMA,
        "version": ctx.config["version"],
        "suite": ctx.suite,
        "task": task_id,
        "attempt": attempt_no,
        "verdict": verdict,
        "auto_verdict": verdict,
        "override": None,
        "reasons": reasons,
        "resolved_raw": resolved,
        "patch": {
            "path": str(patch_path.relative_to(attempt)) if patch_path else None,
            "bytes": len(patch_text.encode("utf-8")),
        },
        "similarity": sim,
        "transcript": scan_doc,
        "test_tampering": tamper,
        "deliverables": sorted(task["deliverables"]),
        **labels_for(task_id, ctx.labels),
    }


def load_overrides(path: Path) -> dict[tuple[str, int], dict]:
    data = load_json(path)
    if data is None:
        return {}
    rows = data.get("overrides") if isinstance(data, dict) else data
    out: dict[tuple[str, int], dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        decision = str(row.get("decision") or "")
        if decision not in OVERRIDE_DECISIONS or not all(str(row.get(k) or "").strip() for k in ("who", "when", "reason")):
            raise SystemExit(f"bad override (need task, attempt, decision in {OVERRIDE_DECISIONS}, who, when, reason): {row}")
        out[(str(row.get("task")), int(row.get("attempt")))] = row
    return out


def apply_override(doc: dict, overrides: dict) -> dict:
    row = overrides.get((doc["task"], int(doc["attempt"])))
    if row:
        doc["override"] = {k: row.get(k) for k in ("decision", "who", "when", "reason")}
        doc["verdict"] = row["decision"]
    return doc


def adjust_attempt(row: dict) -> dict:
    """The official copy of one attempt row: a rejected rollout is unresolved with F2P/P2P zeroed."""
    if row.get("anticheat") != "rejected":
        return row
    out = dict(row)
    out["resolved"] = False
    for key in ("f2p", "p2p", "partial"):
        if out.get(key) is not None:
            out[key] = 0.0
    for key in ("f2p_pass", "p2p_pass"):
        if out.get(key) is not None:
            out[key] = 0
    note = "anticheat rejected"
    out["notes"] = f"{out['notes']}; {note}" if out.get("notes") else note
    return out


def summary(docs: list[dict], config: dict, labels: dict) -> dict:
    counts = Counter(doc["verdict"] for doc in docs)

    def brief(doc: dict) -> dict:
        return {"task": doc["task"], "attempt": doc["attempt"], "resolved_raw": doc["resolved_raw"], "reasons": doc["reasons"]}

    return {
        "version": config["version"],
        "thresholds": config["thresholds"],
        "reject_with_flag_rules": config.get("reject_with_flag_rules") or [],
        "counts": {v: counts.get(v, 0) for v in VERDICTS},
        "attempts": len(docs),
        "no_transcript": sum(1 for doc in docs if doc["transcript"]["status"] != "scanned"),
        "rejected": [brief(doc) for doc in docs if doc["verdict"] == "rejected"],
        "flagged": [brief(doc) for doc in docs if doc["verdict"] == "flagged"],
        "overrides": sum(1 for doc in docs if doc.get("override")),
        "harness_labels": {"available": labels.get("available"), "file": Path(labels.get("file") or "").name, "icode_sha": labels.get("icode_sha")},
    }


def _pct(value) -> str:
    return "-" if not isinstance(value, (int, float)) else f"{float(value) * 100:.1f}%"


def report_markdown(title: str, docs: list[dict], summ: dict, metrics: dict | None = None) -> str:
    th = summ["thresholds"]
    c = summ["counts"]
    lines = [f"# Anti-cheat report — {title}", ""]
    lines.append(f"- Version: `{summ['version']}`")
    lines.append(
        f"- Flag: line_j ≥ {th['flag_line_j']} over ≥ {th['flag_min_added']} added non-test lines, "
        f"or path_j ≥ {th['flag_path_j']} with line_j ≥ {th['flag_path_line_j']}, or any high-severity transcript rule"
    )
    lines.append(
        f"- Reject: line_j ≥ {th['reject_line_j']} over ≥ {th.get('reject_min_added', th['flag_min_added'])} lines, "
        f"or a similarity flag plus transcript evidence ({', '.join(summ['reject_with_flag_rules'])})"
    )
    lines.append(
        f"- Verdicts: clean **{c['clean']}** · flagged **{c['flagged']}** · rejected **{c['rejected']}** "
        f"· not run **{c.get('not_run', 0)}** of {summ['attempts']} attempts · no transcript: {summ['no_transcript']} "
        f"· overrides: {summ['overrides']}"
    )
    if metrics:
        lines.append(
            f"- Macro Pass@1: raw **{_pct(metrics.get('raw'))}** → official **{_pct(metrics.get('official'))}** "
            "(rejected counted unresolved)"
        )
    hl = summ["harness_labels"]
    lines.append(f"- Harness labels: `{hl.get('file') or '-'}` (iCode `{(hl.get('icode_sha') or '-')[:12]}`)")
    lines.append("")
    for verdict in ("rejected", "flagged"):
        rows = [d for d in docs if d["verdict"] == verdict]
        lines.append(f"## {verdict.capitalize()} ({len(rows)})")
        lines.append("")
        if not rows:
            lines.append("none")
            lines.append("")
            continue
        lines.append("| task | attempt | resolved (raw) | line_j | path_j | added | tuned_on | hint | reasons |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for d in rows:
            sim = d["similarity"]
            lines.append(
                f"| {d['task']} | {d['attempt']:02d} | {d['resolved_raw']} | {sim.get('line_j', sim['status'])} | "
                f"{sim.get('path_j', '-')} | {sim.get('n_added', '-')} | {d['harness_tuned_on']} | {d['harness_hint']} | "
                + "; ".join(d["reasons"]).replace("|", "/")
                + (f" (override by {d['override']['who']}: {d['override']['reason']})" if d.get("override") else "")
                + " |"
            )
        lines.append("")
        for d in rows:
            hits = [h for h in d["transcript"]["hits"] if h["severity"] != "info"]
            if not hits:
                continue
            lines.append(f"**{d['task']} attempt-{d['attempt']:02d}** evidence:")
            lines.append("")
            for h in hits[:8]:
                excerpt = h["excerpt"].replace("`", "'")
                lines.append(f"- `{h['rule']}` ({h['severity']}, {h['tool']}): `{excerpt}`")
            lines.append("")
    never = [d for d in docs if d["verdict"] == "not_run"]
    if never:
        lines.append(f"## Not run ({len(never)})")
        lines.append("")
        lines.append("| task | attempt | reason |")
        lines.append("|---|---|---|")
        for d in never:
            lines.append(f"| {d['task']} | {d['attempt']:02d} | " + "; ".join(d["reasons"]).replace("|", "/") + " |")
        lines.append("")
    rule_attempts: Counter = Counter()
    rule_hits: Counter = Counter()
    for d in docs:
        for rule, n in (d["transcript"].get("counts") or {}).items():
            rule_attempts[rule] += 1
            rule_hits[rule] += n
    lines.append("## Transcript rules (all attempts)")
    lines.append("")
    if not rule_hits:
        lines.append("no hits")
    else:
        lines.append("| rule | severity | attempts | hits |")
        lines.append("|---|---|---|---|")
        for rule in sorted(rule_hits, key=lambda r: (-rule_attempts[r], r)):
            lines.append(f"| {rule} | {RULE_SEVERITY.get(rule, '-')} | {rule_attempts[rule]} | {rule_hits[rule]} |")
    lines.append("")
    return "\n".join(lines)


def _attempt_number(trial: Path, fallback: int) -> int:
    index = attempt_index(trial)
    return index if index is not None else fallback


def run_live(args: argparse.Namespace) -> int:
    harness = Path(args.harness_dir)
    out_dir = harness / "anticheat"
    shutil.rmtree(out_dir, ignore_errors=True)
    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else None
    ids = [ln.strip() for ln in Path(args.task_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    ctx = Context(args.benchmark, tasks_dir)
    overrides = load_overrides(harness / "anticheat_overrides.json")
    docs = []
    for tid in ids:
        for n, trial in enumerate(harbor_task_trials(harness, tid), start=1):
            doc = apply_override(evaluate(ctx, tid, _attempt_number(trial, n), trial), overrides)
            (trial / "agent").mkdir(parents=True, exist_ok=True)
            (trial / "agent" / "anticheat.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            docs.append(doc)
    summ = summary(docs, ctx.config, ctx.labels)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summ, indent=2) + "\n", encoding="utf-8")
    title = f"{args.benchmark} {os.environ.get('BUILD_NUMBER') and 'jenkins-' + os.environ['BUILD_NUMBER'] or 'local'}"
    (out_dir / "report.md").write_text(report_markdown(title, docs, summ), encoding="utf-8")
    c = summ["counts"]
    print(
        f"anticheat: clean {c['clean']} flagged {c['flagged']} rejected {c['rejected']} not_run {c['not_run']} "
        f"({summ['attempts']} attempts)"
    )
    return 0


def _harbor_trials(harbor_runs: Path, tid: str) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for n, trial in enumerate(harbor_task_trials(harbor_runs, tid), start=1):
        out.setdefault(_attempt_number(trial, n), trial)
    return out


def arm_metrics(task_rows: list[tuple[str, list[dict]]], n_rollouts: int) -> dict:
    from eval_metrics import summarize_arm

    raw = summarize_arm(task_rows, n_rollouts, 1)
    official = summarize_arm([(tid, [adjust_attempt(r) for r in rows]) for tid, rows in task_rows], n_rollouts, 1)
    return {"raw_arm": raw, "official_arm": official, "raw": raw.get("macro_pass@1"), "official": official.get("macro_pass@1")}


def rescore(run: Path, harbor_runs: Path | None = None, tasks_dir: Path | None = None) -> tuple[dict, str]:
    """artifact.anticheat.json content and report.md text for an output run folder. Writes nothing."""
    from render_report import _attempt_from_trial

    artifact = load_json(run / "artifact.json")
    if not isinstance(artifact, dict):
        raise SystemExit(f"no artifact.json in {run} (extract the .tar.gz first)")
    suite = str(artifact.get("suite") or "deepswe")
    tasks_dir = tasks_dir or REPO_ROOT / SUITE_TASKS.get(suite, "")
    if not tasks_dir.is_dir():
        raise SystemExit(f"tasks dir not found: {tasks_dir} (pass --tasks-dir)")
    ctx = Context(suite, tasks_dir)
    overrides = load_overrides(run / "anticheat_overrides.json")
    n_rollouts = int(artifact.get("n_rollouts") or 1)
    params = (artifact.get("eval_protocol") or {}).get("model_params") or {}
    max_tokens = params.get("max_tokens") if isinstance(params.get("max_tokens"), int) else None
    ids = [ln.strip() for ln in (run / "tasks.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    docs: list[dict] = []
    task_rows: list[tuple[str, list[dict]]] = []
    for tid in ids:
        trials = _harbor_trials(harbor_runs, tid) if harbor_runs else {}
        rows = []
        for attempt in sorted((run / "icode" / tid).glob("attempt-*")):
            n = int(attempt.name.split("-", 1)[1])
            source = attempt if transcripts(attempt) else trials.get(n)
            doc = apply_override(evaluate(ctx, tid, n, attempt, transcript_root=source), overrides)
            docs.append(doc)
            reward = attempt / "verifier" / "reward.json"
            row = _attempt_from_trial(attempt, reward if reward.is_file() else None, max_tokens)
            row["anticheat"] = doc["verdict"]
            rows.append(row)
        task_rows.append((tid, rows))
    metrics = arm_metrics(task_rows, n_rollouts)
    summ = summary(docs, ctx.config, ctx.labels)
    summ["macro_pass@1_raw"] = metrics["raw"]
    summ["macro_pass@1_official"] = metrics["official"]
    original = (artifact.get("icode") or {}).get("macro_pass@1")
    doc = {
        "schema": "mac-k3d-artifact-anticheat-v1",
        "run_dir": run.name,
        "source_artifact_macro_pass@1": original,
        "anticheat": summ,
        "icode": metrics["official_arm"],
        "icode_raw": {k: v for k, v in metrics["raw_arm"].items() if k not in ("tasks", "unscored_tasks")},
        "attempts": docs,
    }
    title = str(artifact.get("run_label") or run.name)
    return doc, report_markdown(title, docs, summ, metrics)


def run_rescore(args: argparse.Namespace) -> int:
    run = Path(args.run_dir)
    doc, report = rescore(
        run,
        Path(args.harbor_runs) if args.harbor_runs else None,
        Path(args.tasks_dir) if args.tasks_dir else None,
    )
    (run / "artifact.anticheat.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (run / "anticheat").mkdir(exist_ok=True)
    (run / "anticheat" / "report.md").write_text(report, encoding="utf-8")
    summ = doc["anticheat"]
    c = summ["counts"]
    print(
        f"{run.name}: clean {c['clean']} flagged {c['flagged']} rejected {c['rejected']} not_run {c['not_run']} "
        f"of {summ['attempts']}; "
        f"no transcript {summ['no_transcript']}; macro Pass@1 raw {_pct(summ['macro_pass@1_raw'])} "
        f"(artifact {_pct(doc['source_artifact_macro_pass@1'])}) -> official {_pct(summ['macro_pass@1_official'])}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harness-dir", default="")
    ap.add_argument("--tasks-dir", default="")
    ap.add_argument("--task-file", default="")
    ap.add_argument("--benchmark", default=os.environ.get("BENCHMARK", "deepswe"))
    ap.add_argument("--run-dir", default="")
    ap.add_argument("--harbor-runs", default="")
    args = ap.parse_args()
    if args.run_dir:
        return run_rescore(args)
    if not (args.harness_dir and args.task_file):
        ap.error("need --run-dir, or --harness-dir with --task-file")
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
