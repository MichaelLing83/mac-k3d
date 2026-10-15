#!/usr/bin/env python3
"""Anti-cheat detection: similarity gate, transcript scan, verdicts, harness labels, official metrics.

Synthetic fixtures live in testdata/anticheat/. The real-data regression on
LoLBench build 21 runs only when that run and its transcripts are on this machine.
"""

from __future__ import annotations

import gzip
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from anticheat_similarity import generated_globs, load_config, similarity  # noqa: E402
from anticheat_transcript import mask, scan  # noqa: E402
from anticheat_verdict import (  # noqa: E402
    Context,
    adjust_attempt,
    apply_override,
    decide,
    load_overrides,
    rescore,
)
from harness_labels import labels_for  # noqa: E402
from harness_labels import load as load_labels  # noqa: E402

FIX = LIB / "testdata" / "anticheat"
CONFIG = load_config()
GLOBS = generated_globs(CONFIG, "deepswe")
GOLD = (FIX / "gold.patch").read_text(encoding="utf-8")
NO_SCAN = {"status": "scanned", "counts": {}, "hits": []}
BUILD21 = ROOT / "output" / "lolbench" / "jenkins-21-20260929T010830Z"
BUILD21_HARBOR = Path.home() / "jenkins-agent/workspace/lolbench_one_task/eval-runs/harness/harbor_runs/jenkins-21"
LOLBENCH_TASKS = ROOT / "eval-runs" / "lolbench" / "harbor_tasks"


def sim_of(name: str) -> dict:
    return {"status": "ok", **similarity((FIX / name).read_text(encoding="utf-8"), GOLD, GLOBS)}


def scan_of(name: str, deliverables: set[str] | None = None) -> dict:
    return scan([FIX / name], deliverables or set())


class SimilarityTests(unittest.TestCase):
    def test_gold_scores_one(self):
        sim = sim_of("gold.patch")
        self.assertEqual((sim["line_j"], sim["path_j"]), (1.0, 1.0))
        self.assertEqual(sim["n_gold_added"], 41, "test and lockfile lines are not counted")

    def test_near_copy_is_rejected(self):
        sim = sim_of("near_copy.patch")
        self.assertGreaterEqual(sim["line_j"], 0.8)
        self.assertEqual(decide(sim, NO_SCAN, [], CONFIG)[0], "rejected")

    def test_independent_implementation_is_clean(self):
        sim = sim_of("independent.patch")
        self.assertAlmostEqual(sim["line_j"], 0.1, delta=0.05)
        self.assertEqual(decide(sim, NO_SCAN, [], CONFIG)[0], "clean")

    def test_overlap_only_in_generated_and_test_files_is_clean(self):
        sim = sim_of("generated_only.patch")
        self.assertEqual(sim["line_j"], 0.0)
        self.assertEqual(decide(sim, NO_SCAN, [], CONFIG)[0], "clean")

    def test_top_files_name_the_overlap(self):
        self.assertEqual(sim_of("near_copy.patch")["top_files"][0]["path"], "widgets/registry.py")


class TranscriptTests(unittest.TestCase):
    def test_copy_from_mounted_stdlib_is_high(self):
        doc = scan_of("mount_copy.jsonl", {"tomllib"})
        self.assertEqual(doc["counts"], {"mount_read_deliverable": 1})
        self.assertEqual(doc["max_severity"], "high")

    def test_mount_read_without_deliverable_is_medium(self):
        self.assertEqual(scan_of("mount_copy.jsonl", {"zoneinfo"})["counts"], {"mount_read": 1})

    def test_pip_install_is_network_hit(self):
        self.assertEqual(scan_of("pip_install.jsonl")["counts"], {"network_attempt": 1})

    def test_web_search_call_is_retrieval_hit(self):
        doc = scan_of("web_search.jsonl")
        self.assertEqual(doc["counts"], {"retrieval_tool": 1})
        self.assertEqual(doc["hits"][0]["tool"], "web_search")

    def test_fetch_webpage_that_returned_content_is_high(self):
        doc = scan_of("fetch_ok.jsonl")
        self.assertEqual(doc["counts"], {"retrieval_success": 1, "retrieval_tool": 1})

    def test_pytest_traceback_under_bundled_packages_is_fidelity_only(self):
        doc = scan_of("pytest_traceback.jsonl")
        self.assertEqual(doc["counts"], {"bundled_package": 1})
        self.assertEqual(doc["hits"][0]["severity"], "info")
        self.assertEqual(decide({"status": "empty_patch"}, doc, [], CONFIG)[0], "clean")

    def test_grader_listing_is_a_read_but_own_patch_is_not(self):
        self.assertEqual(scan_of("gold_read.jsonl")["counts"], {"gold_probe": 1, "gold_read": 1})
        self.assertEqual(scan_of("own_patch.jsonl")["counts"], {"gold_probe": 1})

    def test_written_file_content_is_not_scanned(self):
        self.assertEqual(scan_of("write_content.jsonl")["counts"], {})

    def test_gzipped_transcript_scans_the_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            gz = Path(tmp) / "events.jsonl.gz"
            with gzip.open(gz, "wt", encoding="utf-8") as out:
                out.write((FIX / "mount_copy.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(scan([gz], {"tomllib"})["counts"], {"mount_read_deliverable": 1})

    def test_excerpts_mask_secrets_and_stay_short(self):
        key = "sk-" + "a1b2c3d4" * 4
        self.assertNotIn(key, mask(f"curl -H 'Authorization: Bearer {key}' api"))
        self.assertEqual(mask("export API_KEY=hunter2hunter2"), "export API_KEY=***")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            long_cmd = "pip install " + "x" * 500 + f" --token={key}"
            path.write_text(
                json.dumps({"type": "ToolCallStart", "tool_name": "bash", "tool_args": {"command": long_cmd}}) + "\n",
                encoding="utf-8",
            )
            hit = scan([path], set())["hits"][0]
            self.assertLessEqual(len(hit["excerpt"]), 200)
            self.assertNotIn(key, json.dumps(scan([path], set())))


class VerdictTests(unittest.TestCase):
    def test_flag_plus_deliverable_read_is_rejected(self):
        sim = {"status": "ok", "line_j": 0.61, "path_j": 0.4, "n_added": 800}
        verdict, reasons = decide(sim, scan_of("mount_copy.jsonl", {"tomllib"}), [], CONFIG)
        self.assertEqual(verdict, "rejected")
        self.assertTrue(any("mount_read_deliverable" in r for r in reasons))

    def test_memorization_case_is_flagged_not_rejected(self):
        sim = {"status": "ok", "line_j": 0.66, "path_j": 0.44, "n_added": 720}
        self.assertEqual(decide(sim, NO_SCAN, [], CONFIG)[0], "flagged")

    def test_high_transcript_hit_alone_is_flagged(self):
        self.assertEqual(decide({"status": "empty_patch"}, scan_of("fetch_ok.jsonl"), [], CONFIG)[0], "flagged")

    def test_medium_hits_alone_stay_clean(self):
        self.assertEqual(decide({"status": "ok", "line_j": 0.1, "path_j": 0.2, "n_added": 50}, scan_of("pip_install.jsonl"), [], CONFIG)[0], "clean")

    def test_small_identical_fix_is_flagged_for_review(self):
        sim = {"status": "ok", "line_j": 1.0, "path_j": 1.0, "n_added": 3}
        self.assertEqual(decide(sim, NO_SCAN, [], CONFIG)[0], "flagged")

    def test_override_replaces_verdict_and_needs_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "anticheat_overrides.json"
            row = {"task": "t", "attempt": 2, "decision": "clean", "who": "toby", "when": "2026-10-02", "reason": "repo idiom"}
            path.write_text(json.dumps([row]), encoding="utf-8")
            doc = apply_override({"task": "t", "attempt": 2, "verdict": "flagged", "override": None}, load_overrides(path))
            self.assertEqual(doc["verdict"], "clean")
            self.assertEqual(doc["override"]["who"], "toby")
            path.write_text(json.dumps([{**row, "who": ""}]), encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_overrides(path)

    def test_a_reviewer_cannot_decide_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "anticheat_overrides.json"
            row = {"task": "t", "attempt": 1, "decision": "not_run", "who": "toby", "when": "2026-10-08", "reason": "x"}
            path.write_text(json.dumps([row]), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                load_overrides(path)
            self.assertIn("('clean', 'flagged', 'rejected')", str(ctx.exception))


class HarnessLabelTests(unittest.TestCase):
    def test_kombu_is_tuned_and_hinted_unknown_task_is_neither(self):
        labels = load_labels()
        self.assertTrue(labels["available"])
        self.assertEqual(
            labels_for("kombu-single-active-consumer-priority", labels),
            {"harness_tuned_on": True, "harness_hint": True},
        )
        self.assertEqual(labels_for("no-such-task", labels), {"harness_tuned_on": False, "harness_hint": False})

    def test_counts_match_the_report(self):
        import yaml

        doc = yaml.safe_load(Path(load_labels()["file"]).read_text(encoding="utf-8"))
        self.assertEqual(len(doc["deepswe"]["tuned_on"]), 83)
        self.assertEqual(len(doc["deepswe"]["hinted"]), 14)
        self.assertEqual(len(doc["lolbench"]["hinted"]), 13)
        self.assertEqual(doc["icode_sha"], "eea9d66dd00f137c3400b79c1c8574d8ae7debd1")


def _row(resolved: bool, verdict: str = "clean") -> dict:
    return {
        "resolved": resolved,
        "has_reward": True,
        "f2p": 1.0 if resolved else 0.5,
        "f2p_pass": 2 if resolved else 1,
        "f2p_total": 2,
        "p2p": 1.0,
        "p2p_pass": 4,
        "p2p_total": 4,
        "partial": 1.0,
        "notes": "",
        "anticheat": verdict,
    }


class MetricsTests(unittest.TestCase):
    def test_rejected_rollout_is_unresolved_with_zeroed_tests(self):
        out = adjust_attempt(_row(True, "rejected"))
        self.assertFalse(out["resolved"])
        self.assertEqual((out["f2p"], out["p2p"], out["f2p_pass"], out["p2p_pass"]), (0.0, 0.0, 0, 0))
        self.assertEqual((out["f2p_total"], out["p2p_total"]), (2, 4))
        self.assertIn("anticheat rejected", out["notes"])
        self.assertIs(adjust_attempt(_row(True, "flagged"))["resolved"], True)

    def test_rejected_rollouts_lower_pass_at_1_by_expected_amount(self):
        from anticheat_verdict import arm_metrics

        rows = [
            ("alpha", [_row(True), _row(True, "rejected")]),
            ("beta", [_row(True, "rejected"), _row(False)]),
        ]
        metrics = arm_metrics(rows, 2)
        self.assertAlmostEqual(metrics["raw"], 0.75)
        self.assertAlmostEqual(metrics["official"], 0.25)


def _trial(harness: Path, tid: str, attempt: int, patch: str, transcript: Path, reward: float = 1.0) -> Path:
    trial = harness / "harbor_runs" / "jenkins-9" / tid / f"{tid}_icode_9_a{attempt:02d}" / f"{tid}__t{attempt}"
    (trial / "verifier").mkdir(parents=True)
    (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": reward}), encoding="utf-8")
    (trial / "result.json").write_text("{}", encoding="utf-8")
    art = trial / "artifacts" / "logs" / "artifacts"
    art.mkdir(parents=True)
    (art / "model.patch").write_text(patch, encoding="utf-8")
    sessions = trial / "agent" / "icode-project" / "sessions" / "cli-1"
    sessions.mkdir(parents=True)
    shutil.copy(transcript, sessions / "events.jsonl")
    (trial / "trial.log").write_text("trial ok\n", encoding="utf-8")
    return trial


class LivePipelineTests(unittest.TestCase):
    """anticheat writes verdicts into trials; report scores rejected rollouts as unresolved; archive keeps transcripts."""

    def test_verdicts_flow_into_artifact_and_backup(self):
        from anticheat_verdict import run_live
        from archive_run import backup_run
        from render_report import build_artifact, summary_markdown

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            tasks = root / "tasks"
            (tasks / "alpha" / "solution").mkdir(parents=True)
            shutil.copy(FIX / "gold.patch", tasks / "alpha" / "solution" / "solution.patch")
            _trial(harness, "alpha", 1, (FIX / "near_copy.patch").read_text(encoding="utf-8"), FIX / "pip_install.jsonl")
            _trial(harness, "alpha", 2, (FIX / "independent.patch").read_text(encoding="utf-8"), FIX / "web_search.jsonl")
            task_file = root / "tasks.txt"
            task_file.write_text("alpha\n", encoding="utf-8")
            args = type("A", (), {"harness_dir": str(harness), "tasks_dir": str(tasks), "task_file": str(task_file), "benchmark": "deepswe"})
            run_live(args)
            verdicts = sorted(
                json.loads(p.read_text(encoding="utf-8"))["verdict"] for p in harness.rglob("agent/anticheat.json")
            )
            self.assertEqual(verdicts, ["clean", "rejected"])
            self.assertTrue((harness / "anticheat" / "report.md").is_file())

            doc = build_artifact(
                suite="deepswe",
                model="m",
                api_base="b",
                task_ids=["alpha"],
                harness_dir=harness,
                baseline_dir=root / "baseline",
                n_rollouts=2,
                concurrency=1,
                cpus_each=1,
                run_id="jenkins-9",
            )
            self.assertAlmostEqual(doc["icode_raw"]["macro_pass@1"], 1.0)
            self.assertAlmostEqual(doc["icode"]["macro_pass@1"], 0.5)
            self.assertEqual(doc["anticheat"]["status"], "ok")
            self.assertEqual(doc["anticheat"]["counts"]["rejected"], 1)
            self.assertIn("raw **100.0%** → official **50.0%**", summary_markdown(doc))

            secret = "sk-" + "z9y8x7w6" * 4
            events = next(harness.rglob("alpha_icode_9_a01/*/agent/icode-project/sessions/cli-1/events.jsonl"))
            events.write_text(events.read_text(encoding="utf-8") + json.dumps({"note": secret}) + "\n", encoding="utf-8")
            report_dir = root / "report"
            report_dir.mkdir()
            dest = backup_run(
                backup_root=root / "backup",
                suite="deepswe",
                task_ids=["alpha"],
                report_dir=report_dir,
                run_folder="jenkins-9-x",
                harness_dir=harness,
            )
            attempt = dest / "icode" / "alpha" / "attempt-01"
            archived = attempt / "agent" / "icode-project" / "sessions" / "cli-1" / "events.jsonl.gz"
            self.assertTrue(archived.is_file())
            text = gzip.open(archived, "rt", encoding="utf-8").read()
            self.assertIn("pip install requests", text)
            self.assertNotIn(secret, text)
            for rel in ("agent/anticheat.json", "trial.log"):
                self.assertTrue((attempt / rel).is_file(), rel)
            self.assertTrue((dest / "anticheat" / "summary.json").is_file())

    def test_a_shard_gzips_its_transcripts_in_place_and_the_backup_keeps_them(self):
        from anticheat_verdict import run_live
        from archive_run import backup_run, compress_transcripts
        from archive_run import main as archive_main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            tasks = root / "tasks"
            (tasks / "alpha" / "solution").mkdir(parents=True)
            shutil.copy(FIX / "gold.patch", tasks / "alpha" / "solution" / "solution.patch")
            _trial(harness, "alpha", 1, (FIX / "near_copy.patch").read_text(encoding="utf-8"), FIX / "pip_install.jsonl")
            _trial(harness, "alpha", 2, (FIX / "independent.patch").read_text(encoding="utf-8"), FIX / "web_search.jsonl")
            secret = "sk-" + "q1w2e3r4" * 4
            first = next(harness.rglob("alpha_icode_9_a01/*/agent/icode-project/sessions/cli-1/events.jsonl"))
            first.write_text(first.read_text(encoding="utf-8") + json.dumps({"note": secret}) + "\n", encoding="utf-8")
            jobs = harness / "harbor_runs" / "jenkins-9"

            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(archive_main(["compress", "--jobs-dir", str(jobs)]), 0)
            self.assertIn(f"compress: 2 transcripts gzipped and masked under {jobs}", out.getvalue())
            self.assertEqual(list(harness.rglob("events.jsonl")), [])
            packed = sorted(harness.rglob("events.jsonl.gz"))
            self.assertEqual(len(packed), 2)
            self.assertEqual(list(harness.rglob("*.tmp")), [])
            text = gzip.open(first.with_name("events.jsonl.gz"), "rt", encoding="utf-8").read()
            self.assertIn("pip install requests", text)
            self.assertNotIn(secret, text)
            # Packing twice changes nothing.
            self.assertEqual(compress_transcripts(jobs), (0, []))

            # Anti-cheat reads the gzipped transcripts the same way.
            task_file = root / "tasks.txt"
            task_file.write_text("alpha\n", encoding="utf-8")
            args = type("A", (), {"harness_dir": str(harness), "tasks_dir": str(tasks), "task_file": str(task_file), "benchmark": "deepswe"})
            run_live(args)
            verdicts = sorted(
                json.loads(p.read_text(encoding="utf-8"))["verdict"] for p in harness.rglob("agent/anticheat.json")
            )
            self.assertEqual(verdicts, ["clean", "rejected"])

            report_dir = root / "report"
            report_dir.mkdir()
            dest = backup_run(
                backup_root=root / "backup",
                suite="deepswe",
                task_ids=["alpha"],
                report_dir=report_dir,
                run_folder="jenkins-9-x",
                harness_dir=harness,
            )
            archived = dest / "icode" / "alpha" / "attempt-01" / "agent" / "icode-project" / "sessions" / "cli-1"
            self.assertEqual(sorted(p.name for p in archived.iterdir()), ["events.jsonl.gz"])
            self.assertEqual(
                (archived / "events.jsonl.gz").read_bytes(), first.with_name("events.jsonl.gz").read_bytes()
            )

        backup = (ROOT / "pipeline" / "stages" / "archive" / "backup.sh").read_text(encoding="utf-8")
        self.assertIn('compress --jobs-dir "$HARNESS_DIR/harbor_runs/jenkins-${BUILD_NUMBER:-local}"', backup)
        self.assertLess(backup.index(" compress --jobs-dir"), backup.index("--report-dir"))

    def test_a_rollout_harbor_never_started_is_not_run_not_clean(self):
        from anticheat_verdict import run_live

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            _trial(harness, "alpha", 1, (FIX / "independent.patch").read_text(encoding="utf-8"), FIX / "web_search.jsonl")
            _trial(harness, "alpha", 2, "", FIX / "pip_install.jsonl", reward=0.0)
            lost = harness / "harbor_runs" / "jenkins-9" / "alpha" / "alpha_icode_9_a03" / "alpha__t3"
            (lost / "agent").mkdir(parents=True)
            (lost / "result.json").write_text(
                json.dumps({"exception_info": {
                    "exception_type": "RuntimeError",
                    "exception_message": "Docker compose command failed for environment alpha. Stdout: failed to create "
                                         "network: all predefined address pools have been fully subnetted",
                }}),
                encoding="utf-8",
            )
            task_file = root / "tasks.txt"
            task_file.write_text("alpha\n", encoding="utf-8")
            args = type("A", (), {"harness_dir": str(harness), "tasks_dir": "", "task_file": str(task_file), "benchmark": "deepswe"})
            run_live(args)
            doc = json.loads((lost / "agent" / "anticheat.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["verdict"], "not_run")
            self.assertEqual(
                doc["reasons"],
                ["no patch, transcript or reward.json; Harbor RuntimeError: Docker network pool exhausted"],
            )
            summ = json.loads((harness / "anticheat" / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summ["counts"], {"clean": 2, "flagged": 0, "rejected": 0, "not_run": 1})
            report = (harness / "anticheat" / "report.md").read_text(encoding="utf-8")
            self.assertIn("· not run **1** of 3 attempts", report)
            self.assertIn("| alpha | 03 | no patch, transcript or reward.json; Harbor RuntimeError: Docker network pool exhausted |",
                          report)

    def test_missing_verdicts_leave_official_equal_to_raw(self):
        from render_report import build_artifact

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            _trial(harness, "alpha", 1, "", FIX / "pip_install.jsonl")
            doc = build_artifact(
                suite="deepswe",
                model="m",
                api_base="b",
                task_ids=["alpha"],
                harness_dir=harness,
                baseline_dir=Path(tmp),
                n_rollouts=1,
                concurrency=1,
                cpus_each=1,
                run_id="jenkins-9",
            )
            self.assertEqual(doc["anticheat"]["status"], "not_run")
            self.assertEqual(doc["icode"]["macro_pass@1"], doc["icode_raw"]["macro_pass@1"])


class WiringTests(unittest.TestCase):
    def test_anticheat_phase_runs_before_scoring(self):
        stages = ROOT / "pipeline" / "stages"
        run_all = (stages / "run_all.sh").read_text(encoding="utf-8")
        phases = run_all[run_all.index("PHASES=(") :].split(")", 1)[0].split()
        self.assertLess(phases.index("anticheat"), phases.index("score"))
        anticheat = (stages / "anticheat.sh").read_text(encoding="utf-8")
        self.assertLess(anticheat.index("anticheat/receipts"), anticheat.index("anticheat/verdict"))
        verdict = (stages / "anticheat" / "verdict.sh").read_text(encoding="utf-8")
        self.assertIn('rm -rf "$HARNESS_DIR/anticheat"', verdict)
        self.assertIn("anticheat_verdict.py", verdict)
        score = (stages / "score" / "score.sh").read_text(encoding="utf-8")
        self.assertIn("score_results.py", score)
        self.assertNotIn("anticheat_verdict.py", score)

    def test_config_ships_inside_pipeline(self):
        for rel in ("config/anticheat-v1.json", "config/harness/icode-pr2-eea9d66.yaml"):
            self.assertTrue((ROOT / "pipeline" / rel).is_file(), rel)

    def test_context_reads_gold_and_deliverables(self):
        ctx = Context("lolbench", LOLBENCH_TASKS)
        if not (LOLBENCH_TASKS / "cpython_5").is_dir():
            self.skipTest("LoLBench tasks not checked out")
        self.assertTrue({"tomllib", "tomli"} <= ctx.task("cpython_5")["deliverables"])


@unittest.skipUnless(
    (BUILD21 / "artifact.json").is_file() and BUILD21_HARBOR.is_dir() and LOLBENCH_TASKS.is_dir(),
    "build 21 run or its transcripts are not on this machine",
)
class Build21RegressionTests(unittest.TestCase):
    def test_cpython_5_passes_are_rejected_and_official_is_zero(self):
        doc, report = rescore(BUILD21, BUILD21_HARBOR, LOLBENCH_TASKS)
        rejected = {(a["task"], a["attempt"]) for a in doc["attempts"] if a["verdict"] == "rejected"}
        self.assertEqual(rejected, {("cpython_5", 1), ("cpython_5", 3)})
        self.assertAlmostEqual(doc["anticheat"]["macro_pass@1_raw"], 0.025)
        self.assertEqual(doc["anticheat"]["macro_pass@1_official"], 0.0)
        flagged = {(a["task"], a["attempt"]) for a in doc["attempts"] if a["verdict"] == "flagged"}
        self.assertIn(("cpython_5", 2), flagged, "the memorization case is flagged for review, not rejected")
        self.assertIn("cp /opt/icode-host/.venv/sandbox-cpython/lib/python3.13/tomllib", report)


if __name__ == "__main__":
    unittest.main()
