#!/usr/bin/env python3
"""Per-question record of every eval run, kept in the repo's docs.

  record --repo DIR [--run LABEL] [--build N] [--artifact FILE] [--console FILE]
         [--result R] [--worker NODE] [--benchmark B] [--date ISO]

Writes one row per question into docs/testing/question-log.md and flips the
question to `run` in docs/testing/question-coverage.md. `mac-k3d eval record`
calls it for a Jenkins build (artifact.json + consoleText from the controller)
and at the end of every local run.

- artifact.json is used only when its `run_id` belongs to this run
  (`jenkins-<build>`, or `local-…` for a local run); Jenkins may archive an older run's
  output from a reused workspace.
- Without a matching artifact (the build stopped before `report`) the console
  gives the selected questions, the last `== phase/step` and the first error.
- Rows are keyed by run and question. Re-recording a run rewrites every cell
  except `fix`, which is the reader's.
- Text is masked for keys and tokens and cut to 200 characters; the console is
  never copied wholesale.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_REL = Path("docs/testing/question-log.md")
COVERAGE_REL = Path("docs/testing/question-coverage.md")
COLUMNS = ["date (UTC)", "run", "worker", "benchmark", "question", "result", "stage", "problem", "fix"]
OPEN_COLUMNS = ["question", "benchmark", "last run", "worker", "result", "stage", "problem", "fix"]
OPEN_BEGIN = "<!-- question-log:open:begin -->"
OPEN_END = "<!-- question-log:open:end -->"
RUNS_HEADER = "## Runs"
RUN_ROW = "(run)"
# A run that ended without scoring by design (CANARY=only) says nothing about the question.
NEUTRAL_RESULTS = {"canary pass", "no score"}
MAX_TEXT = 200
COVERAGE_SECTIONS = {"deepswe": "## DeepSWE", "lolbench": "## LoLBench", "swebenchpro": "## SWE-bench Pro"}

HEADER = f"""# Question run log

One row per question per eval run, newest first. `mac-k3d eval record --job <job> --build <n>` adds a Jenkins build. On `some_task` or `full_suite_task` that build is the dispatcher: the merged report's `run_id` is `<job>-<n>`, and every question in it is recorded. A `one_task` report is `jenkins-<n>`. Every `mac-k3d eval --local` run adds itself when it ends, passed or failed. Coverage per question (which ones ever ran) stays in [question-coverage.md](question-coverage.md).

Only **fix** is yours: write what you changed or found. Re-recording a run rewrites the other cells and keeps **fix**. Problem text is masked for keys and tokens and cut to {MAX_TEXT} characters; open the build's console or `summary.md` for the rest.

Results: `pass c/n` (every rollout resolved), `fail c/n` (scored, not all resolved), `unscored` (no verifier result), `skipped`, `failed` (the run stopped before scoring this question), `canary pass` / `no score` (a `CANARY=only` run; Open problems skips it). A `(run)` row is a run that stopped before it selected any question.

## Open problems

The latest result of each question that is not `pass`, with the newest fix note written for it.

{OPEN_BEGIN}
{OPEN_END}

{RUNS_HEADER}

"""

SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"), "***"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{10,}"), "***"),
    (re.compile(r"\b11[0-9a-f]{32}\b"), "***"),
    (re.compile(r"(https?://)[^/\s:@]+:[^@\s/]+@"), r"\1***@"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 ***"),
    (
        re.compile(r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|token|secret|password|passwd|pat|authorization))(\s*[=:]\s*)\S+"),
        r"\1\2***",
    ),
]
BOX = re.compile(r"[│╭╮╰╯─━┃]+|\x1b\[[0-9;]*[A-Za-z]")
ERROR_LINE = re.compile(r"\b\w+(?:Error|Exception)\b(?::|$)")
STEP_LINE = re.compile(r"(?:^|\s)== ([a-z_]+/[a-z_0-9]+)\s*$")
SELECTED_LINE = re.compile(r"(?:^|\s)OK selected (.+)$")
RUNNING_ON = re.compile(r"Running on (\S+)")
FINISHED = re.compile(r"^Finished: ([A-Z_]+)\s*$")
BENCHMARK_LINE = re.compile(r"env: checking this worker \(benchmark=([a-z0-9_]+)")


def mask(text: str) -> str:
    out = BOX.sub(" ", str(text))
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    return out if len(out) <= MAX_TEXT else out[: MAX_TEXT - 1] + "…"


def cell(text: object) -> str:
    return mask(str(text if text is not None else "")).replace("|", "\\|")


def split_row(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip())
    return [p.strip() for p in parts[1:-1]]


def join_row(cells: list[str]) -> str:
    return "|" + "|".join(f" {c} " if c else " " for c in cells) + "|"


def utc_date(value: str = "") -> str:
    if value:
        raw = value.strip()
        if raw.isdigit():
            stamp = datetime.fromtimestamp(int(raw) / (1000 if len(raw) > 11 else 1), tz=timezone.utc)
            return stamp.strftime("%Y-%m-%d %H:%M")
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M"):
            try:
                stamp = datetime.strptime(raw, fmt)
            except ValueError:
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- inputs


def console_facts(text: str) -> dict:
    facts: dict = {"questions": [], "stage": "", "errors": [], "exception": "", "worker": "", "result": "", "benchmark": ""}
    for raw in text.splitlines():
        line = BOX.sub(" ", raw).rstrip()
        if not facts["worker"] and (m := RUNNING_ON.search(line)):
            facts["worker"] = m.group(1)
        if m := STEP_LINE.search(line):
            facts["stage"] = m.group(1)
        if m := SELECTED_LINE.search(line):
            facts["questions"] = m.group(1).split()
        if not facts["benchmark"] and (m := BENCHMARK_LINE.search(line)):
            facts["benchmark"] = m.group(1)
        if "ERROR:" in line:
            facts["errors"].append(line.split("ERROR:", 1)[1].strip())
        if ERROR_LINE.search(line) and "ERROR:" not in line:
            facts["exception"] = line.strip()
        if "canary: pass" in line:
            facts["canary_pass"] = True
        if m := FINISHED.match(line.strip()):
            facts["result"] = m.group(1)
    return facts


def job_of_run(run: str) -> str:
    """`deepswe_some_task #9` -> `deepswe_some_task`. Empty when the label has no job."""
    head = str(run or "").split("#", 1)[0].strip()
    return head


def artifact_matches_build(run_id: str, build: str, job: str = "") -> bool:
    """A one_task report is `jenkins-<build>`. A some/full merge is `<job>-<build>`."""
    number = str(build).strip()
    if not number:
        return False
    if run_id in {number, f"jenkins-{number}"}:
        return True
    return bool(job) and run_id == f"{job}-{number}"


def load_artifact(path: Path | None, build: str, job: str = "") -> tuple[dict | None, str]:
    """(artifact or None, why it was not used)."""
    if path is None or not path.is_file():
        return None, ""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"artifact.json unreadable ({exc})"
    if not isinstance(doc, dict):
        return None, "artifact.json is not an object"
    run_id = str(doc.get("run_id") or "")
    if build:
        if not artifact_matches_build(run_id, build, job):
            return None, f"artifact.json is from run {run_id or '?'}, not build {build}; ignored"
    elif not run_id.startswith("local"):
        return None, f"artifact.json is from Jenkins build {run_id or '?'}, not a local run; ignored"
    return doc, ""


def _num(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def task_result(task: dict) -> str:
    c, n = _num(task.get("c")), _num(task.get("n"))
    n_scored, unscored = _num(task.get("n_scored")), _num(task.get("unscored"))
    if n_scored == 0:
        return f"unscored 0/{n}" if n else "unscored"
    if n and c == n:
        return f"pass {c}/{n}"
    out = f"fail {c}/{n}"
    reward = task.get("reward")
    if isinstance(reward, (int, float)) and not isinstance(reward, bool):
        out += f" (reward {reward:g})"
    if unscored:
        out += f", {unscored} unscored"
    return out


def anticheat_notes(artifact: dict) -> dict[str, list[str]]:
    ac = artifact.get("anticheat") if isinstance(artifact.get("anticheat"), dict) else {}
    notes: dict[str, list[str]] = {}
    for verdict in ("rejected", "flagged"):
        for item in ac.get(verdict) or []:
            if not isinstance(item, dict) or not item.get("task"):
                continue
            reasons = ", ".join(str(r) for r in item.get("reasons") or []) or "no reason recorded"
            notes.setdefault(str(item["task"]), []).append(f"anti-cheat {verdict}: {reasons}")
    return notes


def egress_note(artifact: dict) -> str:
    protocol = artifact.get("eval_protocol") if isinstance(artifact.get("eval_protocol"), dict) else {}
    isolation = protocol.get("isolation") if isinstance(protocol.get("isolation"), dict) else {}
    egress = isolation.get("egress_probe") if isinstance(isolation.get("egress_probe"), dict) else {}
    return "egress probe substituted" if egress.get("substituted") is True else ""


def artifact_worker(artifact: dict) -> str:
    protocol = artifact.get("eval_protocol") if isinstance(artifact.get("eval_protocol"), dict) else {}
    worker = protocol.get("worker") if isinstance(protocol.get("worker"), dict) else {}
    node = str(worker.get("node") or "")
    docker = str(worker.get("docker_version") or "")
    return f"{node} (docker {docker})" if node and docker else node


def build_failed(result: str) -> bool:
    low = result.strip().lower()
    return bool(low) and low not in {"success", "exit 0", "0"}


def run_error(facts: dict) -> str:
    errors = facts.get("errors") or []
    first = errors[0] if errors else ""
    exc = facts.get("exception") or ""
    name = exc.split(":", 1)[0].split()[-1] + ":" if ":" in exc and exc.split(":", 1)[0].split() else exc
    if first and exc and name not in first:
        return f"{first} | {exc}"
    return first or exc


def rows_for_run(args: argparse.Namespace) -> tuple[list[dict], list[str]]:
    notes: list[str] = []
    console = ""
    if args.console and Path(args.console).is_file():
        console = Path(args.console).read_text(encoding="utf-8", errors="replace")
    facts = console_facts(console)
    result = args.result or facts["result"]
    artifact, why = load_artifact(
        Path(args.artifact) if args.artifact else None, args.build, job_of_run(args.run)
    )
    if why:
        notes.append(why)
    error = run_error(facts) if build_failed(result) or not artifact else ""
    stage = facts["stage"] or "-"

    stamp = str((artifact or {}).get("run_id") or "").removeprefix("local-") if not args.build else ""
    date = utc_date(args.date or stamp)
    base = {
        "date": date,
        "run": args.run or f"local {stamp or date}",
        "worker": args.worker or facts["worker"],
        "benchmark": args.benchmark or facts["benchmark"],
    }
    rows: list[dict] = []
    if artifact:
        base["benchmark"] = str(artifact.get("suite") or base["benchmark"])
        worker = artifact_worker(artifact) or base["worker"]
        extra = egress_note(artifact)
        base["worker"] = f"{worker} · {extra}" if extra else worker
        ac = anticheat_notes(artifact)
        icode = artifact.get("icode") if isinstance(artifact.get("icode"), dict) else {}
        for task in icode.get("tasks") or []:
            if not isinstance(task, dict) or not task.get("id"):
                continue
            tid = str(task["id"])
            problem = [str(task.get("notes") or "").strip()]
            problem += ac.get(tid, [])
            if error:
                problem.append(f"build {result.lower()} at {stage}: {error}")
            rows.append(
                {
                    **base,
                    "question": tid,
                    "result": task_result(task),
                    "stage": stage if error else "done",
                    "problem": "; ".join(p for p in problem if p and p != "-"),
                    "covered": True,
                }
            )
        for item in artifact.get("skipped_questions") or []:
            text = str(item).strip()
            if not text:
                continue
            head, _, rest = text.partition(" ")
            tid = head.removeprefix("question=")
            rows.append({**base, "question": tid, "result": "skipped", "stage": "evaluate/slots",
                         "problem": rest.strip(), "covered": False})
    if not rows:
        questions = facts["questions"] or [RUN_ROW]
        if build_failed(result) or not result:
            verdict = "failed"
        else:
            verdict = "canary pass" if facts.get("canary_pass") else "no score"
            error = ""
        for tid in questions:
            rows.append({**base, "question": tid, "result": verdict, "stage": stage,
                         "problem": error or "; ".join(notes), "covered": False})
    return rows, notes


# ---------------------------------------------------------------- docs


def read_runs(text: str) -> list[list[str]]:
    if RUNS_HEADER not in text:
        return []
    rows = []
    for line in text.split(RUNS_HEADER, 1)[1].splitlines():
        if not line.startswith("|"):
            continue
        cells = split_row(line)
        if len(cells) != len(COLUMNS) or cells[0] == COLUMNS[0] or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(cells)
    return rows


def open_problems(rows: list[list[str]]) -> list[list[str]]:
    latest: dict[tuple[str, str], list[str]] = {}
    fixes: dict[tuple[str, str], str] = {}
    for cells in rows:  # newest first
        date, run, worker, bench, question, result, stage, problem, fix = cells
        if question == RUN_ROW:
            continue
        key = (bench, question)
        if fix and key not in fixes:
            fixes[key] = fix
        if result not in NEUTRAL_RESULTS:
            latest.setdefault(key, cells)
    out = []
    for key, cells in latest.items():
        date, run, worker, bench, question, result, stage, problem, fix = cells
        if result.startswith("pass"):
            continue
        out.append([question, bench, f"{run} ({date})", worker, result, stage, problem, fixes.get(key, "")])
    return sorted(out, key=lambda c: (c[1], c[0]))


def render_log(existing: str, rows: list[list[str]]) -> str:
    text = existing if OPEN_BEGIN in existing and RUNS_HEADER in existing else HEADER
    head, _, _ = text.partition(OPEN_BEGIN)
    _, _, tail = text.partition(OPEN_END)
    before_runs = tail.split(RUNS_HEADER, 1)[0]
    problems = open_problems(rows)
    open_lines = [join_row(OPEN_COLUMNS), join_row(["---"] * len(OPEN_COLUMNS))]
    open_lines += [join_row(c) for c in problems] if problems else [join_row(["none"] + [""] * (len(OPEN_COLUMNS) - 1))]
    runs_lines = [join_row(COLUMNS), join_row(["---"] * len(COLUMNS))] + [join_row(c) for c in rows]
    return (
        head + OPEN_BEGIN + "\n" + "\n".join(open_lines) + "\n" + OPEN_END
        + before_runs + RUNS_HEADER + "\n\n" + "\n".join(runs_lines) + "\n"
    )


def merge_rows(old: list[list[str]], new: list[dict]) -> list[list[str]]:
    run = new[0]["run"] if new else ""
    fixes = {(c[1], c[4]): c[8] for c in old if c[1] == run}
    kept = [c for c in old if c[1] != run]
    added = []
    for row in new:
        key = (row["run"], row["question"])
        added.append([
            cell(row["date"]), cell(row["run"]), cell(row["worker"] or "-"), cell(row["benchmark"] or "-"),
            cell(row["question"]), cell(row["result"]), cell(row["stage"] or "-"), cell(row["problem"] or "-"),
            fixes.get((cell(key[0]), cell(key[1])), ""),
        ])
    merged = added + kept
    order = sorted(range(len(merged)), key=lambda i: (merged[i][0], -i), reverse=True)
    return [merged[i] for i in order]


def coverage_label(build: str) -> str:
    return build if build else "local"


def update_coverage(text: str, benchmark: str, questions: list[str], label: str) -> tuple[str, list[str]]:
    header = COVERAGE_SECTIONS.get(benchmark)
    if not header or header not in text or not questions:
        return text, []
    start = text.index(header)
    nxt = text.find("\n## ", start + len(header))
    end = len(text) if nxt == -1 else nxt
    section = text[start:end].split("\n")
    changed: list[str] = []
    wanted = set(questions)
    seen: set[str] = set()
    last_row = -1
    for i, line in enumerate(section):
        if not line.startswith("| `"):
            continue
        last_row = i
        cells = split_row(line)
        qid = cells[0].strip("`")
        if qid not in wanted or len(cells) < 3:
            continue
        seen.add(qid)
        builds = [b.strip() for b in cells[2].split(",") if b.strip()]
        if label not in builds:
            builds.append(label)
        new_cells = [cells[0], "run", ", ".join(builds)] + cells[3:]
        if new_cells != cells:
            section[i] = join_row(new_cells)
            changed.append(qid)
    for qid in questions:
        if qid in seen or last_row == -1:
            continue
        last_row += 1
        section.insert(last_row, f"| `{qid}` | run | {label} | | | |")
        changed.append(qid)
    rows = [split_row(line) for line in section if line.startswith("| `")]
    ran = sum(1 for c in rows if len(c) > 1 and c[1] == "run")
    for i, line in enumerate(section):
        m = re.match(r"^(\d+) run, (\d+) not run, (.*)$", line)
        if m:
            section[i] = f"{ran} run, {len(rows) - ran} not run, {m.group(3)}"
            break
    return text[:start] + "\n".join(section) + text[end:], changed


def record(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    log_path = repo / LOG_REL
    rows, notes = rows_for_run(args)
    for note in notes:
        print(f"question-log: {note}")
    existing = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    merged = merge_rows(read_runs(existing), rows)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(render_log(existing, merged), encoding="utf-8")
    print(f"question-log: {len(rows)} row(s) for {rows[0]['run']} -> {LOG_REL}")
    for row in rows:
        problem = f" · {mask(row['problem'])}" if row["problem"] else ""
        print(f"  {row['question']}: {row['result']} ({row['stage'] or '-'}){problem}")

    covered = [row["question"] for row in rows if row.get("covered")]
    coverage_path = repo / COVERAGE_REL
    if covered and coverage_path.is_file():
        bench = rows[0]["benchmark"]
        text, changed = update_coverage(coverage_path.read_text(encoding="utf-8"), bench, covered,
                                        coverage_label(args.build))
        if changed:
            coverage_path.write_text(text, encoding="utf-8")
            print(f"question-log: {COVERAGE_REL} marks {', '.join(changed)} as run ({coverage_label(args.build)})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--repo", required=True, help="repo checkout whose docs/testing/ is updated")
    rec.add_argument("--run", default="", help="e.g. 'deepswe_one_task #54'; default 'local <run stamp>'")
    rec.add_argument("--build", default="", help="Jenkins build number; empty for a local run")
    rec.add_argument("--artifact", default="", help="the run's artifact.json, when there is one")
    rec.add_argument("--console", default="", help="console text of the run")
    rec.add_argument("--result", default="", help="Jenkins result (SUCCESS, FAILURE, …) or 'exit N'")
    rec.add_argument("--worker", default="", help="node the run used")
    rec.add_argument("--benchmark", default="", help="used when there is no artifact")
    rec.add_argument("--date", default="", help="start time: epoch ms, ISO, or YYYYmmddTHHMMSSZ")
    args = parser.parse_args(argv)
    try:
        return record(args)
    except OSError as exc:
        print(f"ERROR: question-log: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
