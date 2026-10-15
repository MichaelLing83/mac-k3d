#!/usr/bin/env python3
"""question_log.py: one row per question per run in docs/testing/question-log.md."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = LIB.parent.parent
sys.path.insert(0, str(LIB))

from question_log import mask, read_runs, split_row  # noqa: E402

COVERAGE = """# Question coverage on this worker

## DeepSWE

1 run, 2 not run, 3 task dirs.

| question | status | builds | peak_gb | slots | memory_mb |
|----------|--------|--------|---------|-------|-----------|
| `abs-module-cache-flags` | run | 21, 22 | | | |
| `ipython-session-bundle-replay` | not run |  | | | |
| `zz-other` | not run |  | | | |

## LoLBench

0 run, 1 not run, 1 harbor tasks.

| question | status | builds | peak_gb | slots | memory_mb |
|----------|--------|--------|---------|-------|-----------|
| `ruff_1` | not run |  | | | |
"""

CONSOLE_53 = """Started by user admin
Running on mac-iZt4ndd2dff7gqjta7mppaZ in /home/toby/jenkins/workspace/deepswe_one_task
PROGRESS 0% env: checking this worker (benchmark=deepswe n=1 harness=icode llm=deepseek)
== env/host
== tasks/select
OK selected ipython-session-bundle-replay
== evaluate/harbor_run
harbor: 0/1 trials scored (exit 1)
\u2502 ValueError: network_mode='allowlist' is not supported by DockerEnvironment \u2502
ERROR: harbor run exited 1 before any trial finished: ValueError: network_mode='allowlist' is not supported (see /w/harbor.log)
Finished: FAILURE
"""


def task(tid: str, c: int, n: int, *, n_scored: int | None = None, reward: float = 0.0, notes: str = "-") -> dict:
    n_scored = n if n_scored is None else n_scored
    return {"id": tid, "c": c, "n": n, "c_scored": c, "n_scored": n_scored, "unscored": n - n_scored,
            "reward": reward, "notes": notes}


def artifact(run_id: str, tasks: list[dict], **extra) -> dict:
    doc = {
        "run_id": run_id,
        "suite": "deepswe",
        "icode": {"tasks": tasks},
        "skipped_questions": [],
        "anticheat": {"rejected": [], "flagged": []},
        "eval_protocol": {
            "worker": {"node": "mac-iZt4", "docker_version": "29.6.2"},
            "isolation": {"egress_probe": {"substituted": True, "image": "alpine:3.20@sha256:d9e8"}},
        },
    }
    doc.update(extra)
    return doc


class QuestionLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "docs" / "testing").mkdir(parents=True)
        self.coverage = self.repo / "docs" / "testing" / "question-coverage.md"
        self.coverage.write_text(COVERAGE, encoding="utf-8")
        self.log = self.repo / "docs" / "testing" / "question-log.md"

    def tearDown(self):
        self._tmp.cleanup()

    def record(self, *args: str, art: dict | None = None, console: str | None = None) -> subprocess.CompletedProcess:
        extra = list(args)
        if art is not None:
            path = self.repo / "artifact.json"
            path.write_text(json.dumps(art), encoding="utf-8")
            extra += ["--artifact", str(path)]
        if console is not None:
            path = self.repo / "console.txt"
            path.write_text(console, encoding="utf-8")
            extra += ["--console", str(path)]
        proc = subprocess.run(
            [sys.executable, str(LIB / "question_log.py"), "record", "--repo", str(self.repo), *extra],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def runs(self) -> dict[tuple[str, str], list[str]]:
        rows = read_runs(self.log.read_text(encoding="utf-8"))
        return {(r[1], r[4]): r for r in rows}

    def open_rows(self) -> list[list[str]]:
        text = self.log.read_text(encoding="utf-8")
        block = text.split("<!-- question-log:open:begin -->", 1)[1].split("<!-- question-log:open:end -->", 1)[0]
        return [split_row(ln) for ln in block.splitlines() if ln.startswith("|")][2:]

    def test_results_from_a_finished_jenkins_build(self):
        art = artifact(
            "jenkins-54",
            [
                task("abs-module-cache-flags", 1, 1, reward=1.0),
                task("ipython-session-bundle-replay", 0, 2, reward=0.25),
                task("zz-other", 0, 1, n_scored=0, notes="missing reward.json"),
            ],
            skipped_questions=["question=too-big out of memory (needs 64 GB)"],
            anticheat={"rejected": [{"task": "ipython-session-bundle-replay", "reasons": ["gold_read"]}],
                       "flagged": []},
        )
        self.record("--run", "deepswe_one_task #54", "--build", "54", "--result", "SUCCESS",
                    "--date", "1791190000000", art=art)
        runs = self.runs()
        run = "deepswe_one_task #54"
        self.assertEqual(runs[(run, "abs-module-cache-flags")][5], "pass 1/1")
        self.assertEqual(runs[(run, "abs-module-cache-flags")][6], "done")
        self.assertEqual(runs[(run, "abs-module-cache-flags")][2], "mac-iZt4 (docker 29.6.2) · egress probe substituted")
        fail = runs[(run, "ipython-session-bundle-replay")]
        self.assertEqual(fail[5], "fail 0/2 (reward 0.25)")
        self.assertIn("anti-cheat rejected: gold_read", fail[7])
        self.assertEqual(runs[(run, "zz-other")][5], "unscored 0/1")
        self.assertEqual(runs[(run, "zz-other")][7], "missing reward.json")
        skipped = runs[(run, "too-big")]
        self.assertEqual((skipped[5], skipped[7]), ("skipped", "out of memory (needs 64 GB)"))
        self.assertEqual(sorted(r[0] for r in self.open_rows()), ["ipython-session-bundle-replay", "too-big", "zz-other"])

        cov = self.coverage.read_text(encoding="utf-8")
        self.assertIn("| `abs-module-cache-flags` | run | 21, 22, 54 | | | |", cov)
        self.assertIn("| `ipython-session-bundle-replay` | run | 54 | | | |", cov)
        self.assertIn("| `zz-other` | run | 54 | | | |", cov)
        self.assertNotIn("too-big", cov, "a skip stays not run")
        self.assertIn("3 run, 0 not run, 3 task dirs.", cov)
        self.assertIn("0 run, 1 not run, 1 harbor tasks.", cov)

    def test_build_53_console_without_an_artifact(self):
        proc = self.record("--run", "deepswe_one_task #53", "--build", "53", "--result", "FAILURE",
                           "--worker", "mac-iZt4ndd2dff7gqjta7mppaZ", "--benchmark", "deepswe",
                           "--date", "1791190000000", console=CONSOLE_53)
        row = self.runs()[("deepswe_one_task #53", "ipython-session-bundle-replay")]
        self.assertEqual(row[2:7], ["mac-iZt4ndd2dff7gqjta7mppaZ", "deepswe", "ipython-session-bundle-replay",
                                    "failed", "evaluate/harbor_run"])
        self.assertIn("harbor run exited 1 before any trial finished: ValueError", row[7])
        self.assertNotIn("\u2502", row[7])
        self.assertEqual([r[0] for r in self.open_rows()], ["ipython-session-bundle-replay"])
        self.assertIn("not run", self.coverage.read_text(encoding="utf-8").split("ipython-session-bundle-replay")[1][:20])
        self.assertIn("1 row(s) for deepswe_one_task #53", proc.stdout)

    def test_an_older_runs_artifact_is_ignored(self):
        stale = artifact("jenkins-52", [task("abs-module-cache-flags", 1, 1)])
        proc = self.record("--run", "deepswe_one_task #53", "--build", "53", "--result", "FAILURE",
                           art=stale, console=CONSOLE_53)
        self.assertIn("artifact.json is from run jenkins-52, not build 53; ignored", proc.stdout)
        runs = self.runs()
        self.assertNotIn(("deepswe_one_task #53", "abs-module-cache-flags"), runs)
        self.assertEqual(runs[("deepswe_one_task #53", "ipython-session-bundle-replay")][5], "failed")

        local = artifact("local-20261006T085615Z", [task("abs-module-cache-flags", 1, 1)])
        proc = self.record("--run", "deepswe_one_task #55", "--build", "55", art=local)
        self.assertIn("not build 55; ignored", proc.stdout)

    def test_a_merged_some_task_report_belongs_to_that_dispatcher_build(self):
        art = artifact(
            "deepswe_some_task-9",
            [task("ytt-jsonpath-query-api", 2, 4, reward=0.5), task("dasel-html-document-format", 0, 4)],
        )
        proc = self.record("--run", "deepswe_some_task #9", "--build", "9", "--result", "SUCCESS",
                           "--worker", "mac-Michael-Ubuntu", "--benchmark", "deepswe",
                           "--date", "1791360000000", art=art)
        runs = self.runs()
        self.assertEqual(runs[("deepswe_some_task #9", "ytt-jsonpath-query-api")][5], "fail 2/4 (reward 0.5)")
        self.assertEqual(runs[("deepswe_some_task #9", "dasel-html-document-format")][5], "fail 0/4 (reward 0)")
        self.assertIn("2 row(s) for deepswe_some_task #9", proc.stdout)
        cov = self.coverage.read_text(encoding="utf-8")
        self.assertIn("| `ytt-jsonpath-query-api` | run | 9 |", cov)

        other_job = artifact("lolbench_some_task-9", [task("abs-module-cache-flags", 1, 1)])
        proc = self.record("--run", "deepswe_some_task #9", "--build", "9", art=other_job)
        self.assertIn("artifact.json is from run lolbench_some_task-9, not build 9; ignored", proc.stdout)
        earlier = artifact("deepswe_some_task-8", [task("abs-module-cache-flags", 1, 1)])
        proc = self.record("--run", "deepswe_some_task #9", "--build", "9", art=earlier)
        self.assertIn("artifact.json is from run deepswe_some_task-8, not build 9; ignored", proc.stdout)
        longer = artifact("jenkins-19", [task("abs-module-cache-flags", 1, 1)])
        proc = self.record("--run", "deepswe_one_task #9", "--build", "9", art=longer)
        self.assertIn("artifact.json is from run jenkins-19, not build 9; ignored", proc.stdout)

    def test_fix_survives_a_rerecord_and_a_later_pass_closes_the_problem(self):
        self.record("--run", "deepswe_one_task #53", "--build", "53", "--result", "FAILURE",
                    "--date", "1791190000000", console=CONSOLE_53)
        text = self.log.read_text(encoding="utf-8")
        old = next(ln for ln in text.splitlines() if "| deepswe_one_task #53 |" in ln)
        cells = split_row(old)
        cells[8] = "probe fallback alpine:3.20"
        self.log.write_text(text.replace(old, "| " + " | ".join(cells) + " |"), encoding="utf-8")

        self.record("--run", "deepswe_one_task #53", "--build", "53", "--result", "FAILURE",
                    "--date", "1791190000000", console=CONSOLE_53)
        rows = read_runs(self.log.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][8], "probe fallback alpine:3.20")
        self.assertEqual(self.open_rows()[0][7], "probe fallback alpine:3.20")

        art = artifact("jenkins-54", [task("ipython-session-bundle-replay", 1, 1, reward=1.0)])
        self.record("--run", "deepswe_one_task #54", "--build", "54", "--result", "SUCCESS",
                    "--date", "1791199000000", art=art)
        rows = read_runs(self.log.read_text(encoding="utf-8"))
        self.assertEqual([r[1] for r in rows], ["deepswe_one_task #54", "deepswe_one_task #53"])
        self.assertEqual(rows[1][8], "probe fallback alpine:3.20")
        self.assertEqual(self.open_rows(), [["none", "", "", "", "", "", "", ""]])

    def test_canary_only_build_does_not_reopen_or_close_a_question(self):
        self.record("--run", "deepswe_one_task #53", "--build", "53", "--result", "FAILURE",
                    "--date", "1791190000000", console=CONSOLE_53)
        canary = "OK selected ipython-session-bundle-replay\n== evaluate/canary\ncanary: pass (1 tasks)\nFinished: SUCCESS\n"
        self.record("--run", "deepswe_one_task #54", "--build", "54", "--date", "1791199000000", console=canary)
        self.assertEqual(self.runs()[("deepswe_one_task #54", "ipython-session-bundle-replay")][5], "canary pass")
        self.assertEqual([r[2] for r in self.open_rows()], ["deepswe_one_task #53 (2026-10-05 08:46)"])

    def test_a_local_run_is_labelled_by_its_stamp(self):
        art = artifact("local-20261006T085615Z", [task("abs-module-cache-flags", 1, 1, reward=1.0)])
        self.record("--result", "exit 0", art=art)
        rows = read_runs(self.log.read_text(encoding="utf-8"))
        self.assertEqual(rows[0][:2], ["2026-10-06 08:56", "local 20261006T085615Z"])
        self.assertIn("| `abs-module-cache-flags` | run | 21, 22, local | | | |",
                      self.coverage.read_text(encoding="utf-8"))
        self.record("--result", "exit 0", art=art)
        self.assertEqual(self.coverage.read_text(encoding="utf-8").count("21, 22, local |"), 1)

    def test_a_run_that_stopped_before_selecting_gets_one_run_row(self):
        console = "== env/egress\nERROR: Harbor cannot enforce network isolation on this worker\n"
        self.record("--result", "exit 1", "--date", "1791190000", console=console)
        rows = read_runs(self.log.read_text(encoding="utf-8"))
        self.assertEqual(rows[0][4:7], ["(run)", "failed", "env/egress"])
        self.assertEqual(self.open_rows(), [["none", "", "", "", "", "", "", ""]])

    def test_secrets_never_reach_the_doc(self):
        model_key = "sk-" + "abcdef01" * 2
        jenkins_token = "11" + "aaaabbbbccccdddd" * 2
        console = CONSOLE_53.replace(
            "ERROR: harbor run",
            f"ERROR: DEEPSEEK_API_KEY={model_key} token={jenkins_token} "
            "https://admin:hunter22@ctl:17070/x harbor run",
        )
        self.record("--run", "deepswe_one_task #53", "--build", "53", "--result", "FAILURE", console=console)
        text = self.log.read_text(encoding="utf-8")
        for secret in (model_key, jenkins_token, "hunter22"):
            self.assertNotIn(secret, text)
        self.assertIn("DEEPSEEK_API_KEY=***", text)

    def test_mask_cuts_long_text_and_pipes_stay_in_one_cell(self):
        self.assertEqual(len(mask("x" * 500)), 200)
        masked = mask("Authorization: Bearer abcdefghijklmnop")
        self.assertTrue(masked.startswith("Authorization: ***"), masked)
        self.assertNotIn("abcdefghijklmnop", masked)
        art = artifact("jenkins-54", [task("abs-module-cache-flags", 0, 1, notes="a | b")])
        self.record("--run", "deepswe_one_task #54", "--build", "54", "--result", "SUCCESS", art=art)
        self.assertEqual(self.runs()[("deepswe_one_task #54", "abs-module-cache-flags")][7], "a \\| b")


class QuestionLogDocTests(unittest.TestCase):
    def test_repo_log_parses_and_links_coverage(self):
        log = ROOT / "docs" / "testing" / "question-log.md"
        text = log.read_text(encoding="utf-8")
        self.assertIn("<!-- question-log:open:begin -->", text)
        self.assertIn("## Runs", text)
        for row in read_runs(text):
            self.assertEqual(len(row), 9, row)
        coverage = (ROOT / "docs" / "testing" / "question-coverage.md").read_text(encoding="utf-8")
        self.assertIn("question-log.md", coverage)


if __name__ == "__main__":
    unittest.main()
