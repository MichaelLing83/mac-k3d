#!/usr/bin/env python3
"""Unit tests for check_report and score_results (no network)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LIB = Path(__file__).resolve().parent
STAGES = ROOT / "pipeline" / "stages"
sys.path.insert(0, str(LIB))

from check_report import validate  # noqa: E402
from openai_compat import missing_model_message, model_in_ids, models_base, parse_model_ids  # noqa: E402
from pier_result import hollow_job_reason  # noqa: E402
from score_results import (  # noqa: E402
    harbor_reward_resolved,
    pass_at_1,
    scale_eval_resolved,
    verifier_rates,
)


FIXTURE = LIB / "testdata" / "report-min.json"


def official_protocol_inputs() -> dict:
    """The provenance a comparison artifact must carry on every run."""
    return {
        "harbor": {"version": "0.22.0"},
        "benchmark": {
            "url": "https://github.com/datacurve-ai/deep-swe",
            "sha": "0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea",
            "task_count": 113,
            "tasks": {},
        },
        "grader_overlay": {"marker": "mac-k3d-lolbench-fix-rewards-v1", "sha256": "abc"},
        "images": {},
        "worker": {
            "node": "n",
            "nproc": 1,
            "memory_kb": 1,
            "docker_version": "27",
            "kernel": "7",
            "cpu_model": "cpu",
        },
        "requester": {"user": "unknown", "build_url": "unknown"},
        "pipeline": {"commit": "abcdef", "dirty": False},
        "isolation": valid_isolation(),
    }


def stamp_official(doc: dict) -> dict:
    doc["eval_protocol"] = {
        "icode": {"mode": "git"},
        "model_params": {
            "provider": "DeepSeek",
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        },
        "resources": {},
        **official_protocol_inputs(),
    }
    doc["anticheat"] = {"status": "ok", "version": "mac-k3d-anticheat-v1"}
    doc["icode_git"] = {
        "url": "https://gitcode.com/michaelling/jiuwenicode",
        "kind": "commit",
        "ref": "eea9d66dd00f137c3400b79c1c8574d8ae7debd1",
        "sha": "eea9d66dd00f137c3400b79c1c8574d8ae7debd1",
    }
    return doc


def valid_isolation() -> dict:
    return {
        "mode": "git",
        "sanitizer": "mac-k3d-icode-sanitize-v3",
        "sourceless": True,
        "removed": 9,
        "tree_sha256": "a" * 64,
        "runtime_sha256": "b" * 64,
        "manifest_matches_tree": True,
        "mount": {"count": 1, "target": "/opt/icode-host", "read_only": True},
        "leak_scan": {
            "scanner": "mac-k3d-leakscan-v1",
            "hit_tasks": [],
            "statuses": {"clean": 1},
            "report_sha256": "c" * 64,
        },
        "canary": {
            "version": "mac-k3d-canary-v1",
            "status": "pass",
            "node": "worker-1",
            "tasks": ["alpha"],
            "failed_tasks": [],
            "warn_tasks": [],
            "counts": {"tasks": 1, "pass": 1, "fail": 0},
            "summary_sha256": "d" * 64,
        },
    }


class OpenAICompatTests(unittest.TestCase):
    def test_parse_model_ids(self):
        ids = parse_model_ids(
            {"object": "list", "data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]}
        )
        self.assertEqual(ids, ["deepseek-flash", "deepseek-v4-pro"])
        self.assertEqual(parse_model_ids({}), [])
        self.assertEqual(parse_model_ids({"data": "nope"}), [])

    def test_product_name_is_not_an_id(self):
        ids = ["deepseek-flash", "deepseek-v4-pro"]
        self.assertTrue(model_in_ids("deepseek-flash", ids))
        self.assertFalse(model_in_ids("deepseek-v4.1-flash", ids))
        msg = missing_model_message("deepseek-v4.1-flash", ids)
        self.assertIn("is not returned by GET /models", msg)
        self.assertIn("deepseek-flash", msg)

    def test_models_list_strips_chat_v1(self):
        self.assertEqual(models_base("https://api.deepseek.com/v1"), "https://api.deepseek.com")
        self.assertEqual(models_base("https://api.deepseek.com"), "https://api.deepseek.com")


class PassAt1Tests(unittest.TestCase):
    def test_ratio(self):
        self.assertEqual(pass_at_1(1, 1), 1.0)
        self.assertEqual(pass_at_1(0, 1), 0.0)
        self.assertEqual(pass_at_1(1, 2), 0.5)
        self.assertEqual(pass_at_1(0, 0), 0.0)


class CheckReportTests(unittest.TestCase):
    def test_fixture_ok(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(validate(doc), [])

    def test_missing_keys(self):
        errors = validate({"harness": "icode"})
        self.assertTrue(any("missing model" in e for e in errors))
        self.assertTrue(any("missing macro" in e for e in errors))

    def test_lolbench_suite_ok(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        doc["suite"] = "lolbench"
        self.assertEqual(validate(doc), [])

    def test_unknown_suite_rejected(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        doc["suite"] = "humaneval"
        errors = validate(doc)
        self.assertTrue(any("suite should be" in e for e in errors))

    def test_swebenchpro_suite_ok(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        doc["suite"] = "swebenchpro"
        self.assertEqual(validate(doc), [])

    def test_f2p_list_rejected(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        doc["tasks"][0]["f2p"] = ["test_example.py::test_fix"]
        errors = validate(doc)
        self.assertTrue(any("tasks[0].f2p" in e for e in errors))

    def test_cli_fixture(self):
        proc = subprocess.run(
            [sys.executable, str(LIB / "check_report.py"), str(FIXTURE)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK report schema", proc.stdout)


class ScoreResultsTests(unittest.TestCase):
    def test_merges_meta_and_pass_at_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "example-task"
            tasks.mkdir(parents=True)
            harness = root / "harness"
            harness.mkdir()
            (harness / "meta.json").write_text(
                json.dumps(
                    {
                        "access_date_utc": "2026-09-14T00:00:00Z",
                        "duration_seconds": 2,
                        "llm_model_id": "deepseek-v4-pro",
                        "llm_model_served": "deepseek-v4-pro",
                        "token_usage": {"prompt": 3, "completion": 1, "total": 4},
                    }
                ),
                encoding="utf-8",
            )
            (harness / "example-task").mkdir()
            (harness / "example-task" / "result.json").write_text(
                json.dumps(
                    {
                        "resolved": True,
                        "FAIL_TO_PASS": ["t::fix"],
                        "PASS_TO_PASS": ["t::ok"],
                    }
                ),
                encoding="utf-8",
            )
            baseline = root / "baseline"
            (baseline / "example-task").mkdir(parents=True)
            (baseline / "example-task" / "agent.patch").write_text("diff --git a b\n", encoding="utf-8")
            (baseline / "summary.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "example-task",
                            "ok": True,
                            "patch_path": str(baseline / "example-task" / "agent.patch"),
                            "token_usage": {"prompt": 5, "completion": 2, "total": 7},
                            "duration_seconds": 1.5,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (baseline / "meta.json").write_text(
                json.dumps(
                    {
                        "access_date_utc": "2026-09-14T00:00:00Z",
                        "llm_model_id": "deepseek-v4-pro",
                        "llm_model_served": "deepseek-v4-flash",
                        "duration_seconds": 1.5,
                        "token_usage": {"prompt": 5, "completion": 2, "total": 7},
                    }
                ),
                encoding="utf-8",
            )
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(baseline),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "DEEPSEEK_MODEL": "deepseek-v4-pro", "BENCHMARK": "deepswe"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            self.assertEqual(doc["suite"], "deepswe")
            self.assertEqual(doc["pass_at_1"], 1.0)
            self.assertEqual(doc["model"], "deepseek-v4-pro")
            self.assertEqual(doc["model_served"], "deepseek-v4-flash")
            self.assertEqual(doc["tokens"]["in"], 3)
            self.assertEqual(doc["tokens"]["out"], 1)
            self.assertEqual(doc["tokens"]["total"], 4)
            self.assertEqual(doc["wall_seconds"], 3.5)
            self.assertEqual(doc["wall_minutes"], 0.058)
            self.assertIs(doc["tasks"][0]["first"], True)
            self.assertEqual(doc["tasks"][0]["reward"], 1.0)
            self.assertIsNone(doc["tasks"][0]["f2p"])
            self.assertNotIn("f2p_rate", doc["tasks"][0])
            self.assertNotIn("llm_name", doc)
            self.assertNotIn("notes", doc)
            self.assertEqual(doc["tasks"][0]["baseline"]["tok_in"], 5)
            self.assertEqual(doc["tasks"][0]["dur_min"], 0.033)

    def test_icode_git_copied_into_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "example-task"
            tasks.mkdir(parents=True)
            harness = root / "harness"
            harness.mkdir()
            (harness / "meta.json").write_text(
                json.dumps({"duration_seconds": 2, "token_usage": {"prompt": 1, "completion": 1, "total": 2}}),
                encoding="utf-8",
            )
            baseline = root / "baseline"
            baseline.mkdir()
            (baseline / "meta.json").write_text("{}", encoding="utf-8")
            work = root / "work"
            work.mkdir()
            (work / "icode_git.json").write_text(
                json.dumps(
                    {
                        "url": "https://github.com/org/icode.git",
                        "kind": "commit",
                        "ref": "abcdef1",
                        "sha": "abcdef1234567890abcdef1234567890abcdef12",
                        "subject": "fix harness for PR",
                    }
                ),
                encoding="utf-8",
            )
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(baseline),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "DEEPSEEK_MODEL": "deepseek-v4-pro",
                    "BENCHMARK": "deepswe",
                    "WORKDIR": str(work),
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            self.assertEqual(doc["icode_git"]["kind"], "commit")
            self.assertEqual(doc["icode_git"]["ref"], "abcdef1")
            self.assertTrue(doc["icode_git"]["sha"].startswith("abcdef12"))
            self.assertEqual(doc["icode_git"]["subject"], "fix harness for PR")

    def test_harbor_scores_newest_job_not_older_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "abs-module-cache-flags"
            tasks.mkdir(parents=True)
            harness = root / "harness"
            old = harness / "harbor_runs" / "2026-09-16__09-54-24" / "abs-module-cache-flags"
            new = harness / "harbor_runs" / "2026-09-16__11-22-34" / "abs-module-cache-flags"
            (old / "verifier").mkdir(parents=True)
            (new / "verifier").mkdir(parents=True)
            (old / "verifier" / "reward.json").write_text(
                '{"reward": 0, "f2p": 0.0, "f2p_passed": 0, "f2p_total": 20, "p2p": 1.0, "p2p_passed": 3, "p2p_total": 3}\n',
                encoding="utf-8",
            )
            (new / "verifier" / "reward.json").write_text(
                '{"reward": 1, "f2p": 1.0, "f2p_passed": 20, "f2p_total": 20, "p2p": 1.0, "p2p_passed": 3, "p2p_total": 3}\n',
                encoding="utf-8",
            )
            (old / "result.json").write_text("{}", encoding="utf-8")
            (new / "result.json").write_text("{}", encoding="utf-8")
            import time

            time.sleep(0.05)
            (new / "verifier" / "reward.json").write_text(
                '{"reward": 1, "f2p": 1.0, "f2p_passed": 20, "f2p_total": 20, "p2p": 1.0, "p2p_passed": 3, "p2p_total": 3}\n',
                encoding="utf-8",
            )
            (harness / "meta.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 958,
                        "llm_model_id": "deepseek-v4-pro",
                        "token_usage": {"prompt": 0, "completion": 0, "total": 0},
                    }
                ),
                encoding="utf-8",
            )
            (new / "artifacts").mkdir()
            (new / "artifacts" / "icode-usage.json").write_text(
                '{"input_tokens": 111, "output_tokens": 22, "total_tokens": 133}\n',
                encoding="utf-8",
            )
            baseline = root / "baseline"
            baseline.mkdir()
            (baseline / "meta.json").write_text(
                json.dumps({"duration_seconds": 10, "token_usage": {"prompt": 1, "completion": 1, "total": 2}}),
                encoding="utf-8",
            )
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(baseline),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "BENCHMARK": "deepswe", "DEEPSEEK_MODEL": "deepseek-v4-pro"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            self.assertEqual(doc["pass_at_1"], 1.0)
            self.assertEqual(doc["macro"]["f2p"], 1.0)
            self.assertEqual(doc["macro"]["reward"], 1.0)
            self.assertEqual(doc["tasks"][0]["f2p_pass"], 20)
            self.assertEqual(doc["tokens"]["in"], 111)
            self.assertEqual(doc["tokens"]["out"], 22)

    def test_two_rollouts_count_resolved_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "example-task"
            tasks.mkdir(parents=True)
            job = root / "harness" / "harbor_runs" / "jenkins-2" / "example-task"
            older = job / "attempt-a" / "verifier"
            newer = job / "attempt-b" / "verifier"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            (older / "reward.json").write_text(
                '{"reward": 1.0, "f2p": 1.0, "p2p": 1.0, "f2p_pass": 1, "f2p_total": 1, "p2p_pass": 1, "p2p_total": 1}\n',
                encoding="utf-8",
            )
            (newer / "reward.json").write_text(
                '{"reward": 0.0, "f2p": 0.0, "p2p": 1.0, "f2p_pass": 0, "f2p_total": 1, "p2p_pass": 1, "p2p_total": 1}\n',
                encoding="utf-8",
            )
            (job / "attempt-a" / "agent").mkdir()
            (job / "attempt-b" / "agent").mkdir()
            (job / "attempt-a" / "agent" / "icode-usage.json").write_text(
                '{"input_tokens": 10, "output_tokens": 1}\n', encoding="utf-8"
            )
            (job / "attempt-b" / "agent" / "icode-usage.json").write_text(
                '{"input_tokens": 20, "output_tokens": 2}\n', encoding="utf-8"
            )
            now = time.time()
            os.utime(older / "reward.json", (now - 20, now - 20))
            os.utime(newer / "reward.json", (now, now))
            harness = root / "harness"
            (harness / "meta.json").write_text(
                json.dumps({"duration_seconds": 4, "llm_model_id": "deepseek-flash"}),
                encoding="utf-8",
            )
            baseline = root / "baseline"
            baseline.mkdir()
            (baseline / "meta.json").write_text("{}", encoding="utf-8")
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(baseline),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "BENCHMARK": "deepswe",
                    "DEEPSEEK_MODEL": "deepseek-flash",
                    "N_ROLLOUTS": "2",
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            task = doc["tasks"][0]
            self.assertEqual(doc["n_rollouts"], 2)
            self.assertEqual(task["c"], 1)
            self.assertEqual(task["n"], 2)
            self.assertEqual(task["pass_frac"], 0.5)
            self.assertIs(task["first"], True)
            self.assertEqual(task["reward"], 0.5)
            self.assertEqual(doc["pass_at_1"], 0.5)
            self.assertEqual(task["f2p"], 0.5)
            self.assertEqual(task["p2p"], 1.0)
            self.assertEqual(task["f2p_pass"], 1)
            self.assertEqual(task["f2p_total"], 2)
            self.assertEqual(task["tok_in"], 30)
            self.assertEqual(task["tok_out"], 3)

    def test_current_run_ignores_leftover_and_fills_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tid = "instance_nodebb"
            tasks = root / "tasks" / tid
            tasks.mkdir(parents=True)
            harness = root / "harness"
            trial = harness / "harbor_runs" / "jenkins-16" / tid
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier" / "reward.json").write_text(
                json.dumps(
                    {
                        "reward": 1.0,
                        "resolved": True,
                        "f2p": 1.0,
                        "f2p_pass": 3,
                        "f2p_total": 3,
                        "p2p": 1.0,
                        "p2p_pass": 288,
                        "p2p_total": 288,
                    }
                ),
                encoding="utf-8",
            )
            (trial / "icode-usage.json").write_text(
                '{"input_tokens": 12743597, "output_tokens": 68171, "total_tokens": 12811768}\n',
                encoding="utf-8",
            )
            (harness / "meta.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 1785,
                        "llm_model_id": "deepseek-flash",
                        "token_usage": {"prompt": 15581690, "completion": 75133, "total": 15656823},
                    }
                ),
                encoding="utf-8",
            )
            baseline = root / "baseline"
            task_base = baseline / tid
            task_base.mkdir(parents=True)
            (task_base / "eval.json").write_text('{"resolved": false}\n', encoding="utf-8")
            (task_base / "response.txt").write_text("the tests fail\n", encoding="utf-8")
            (task_base / "agent.patch").write_text("diff --git a b\n", encoding="utf-8")
            (baseline / "summary.json").write_text(
                json.dumps(
                    [
                        {
                            "id": tid,
                            "ok": True,
                            "patch_path": str(task_base / "agent.patch"),
                            "token_usage": {"prompt": 1411, "completion": 33828, "total": 35239},
                            "duration_seconds": 108.497,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (baseline / "meta.json").write_text(
                json.dumps({"duration_seconds": 108.497, "token_usage": {"prompt": 1411, "completion": 33828}}),
                encoding="utf-8",
            )
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(baseline),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "BENCHMARK": "swebenchpro", "DEEPSEEK_MODEL": "deepseek-flash"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            self.assertEqual(doc["tasks"][0]["reward"], 1.0)
            self.assertEqual(doc["tasks"][0]["partial"], 1.0)
            self.assertEqual(doc["macro"]["partial"], 1.0)
            self.assertIsNone(doc["tasks"][0]["baseline"]["reward"])
            self.assertEqual(doc["tokens"]["in"], 12743597)
            self.assertEqual(doc["tokens"]["out"], 68171)
            self.assertEqual(doc["tasks"][0]["baseline"]["tok_in"], 1411)

    def test_clear_stale_baseline_grades(self):
        from baseline_deepseek import clear_stale_grades

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task"
            task.mkdir()
            (task / "eval.json").write_text('{"resolved": false}\n', encoding="utf-8")
            (task / "reward.json").write_text('{"reward": 0}\n', encoding="utf-8")
            (root / "scale_summary.json").write_text("{}\n", encoding="utf-8")
            (root / "scale_eval").mkdir()
            (root / "scale_eval" / "eval_results.json").write_text("{}\n", encoding="utf-8")
            (task / "agent.patch").write_text("diff\n", encoding="utf-8")
            clear_stale_grades(root, task)
            self.assertFalse((task / "eval.json").exists())
            self.assertFalse((task / "reward.json").exists())
            self.assertFalse((root / "scale_summary.json").exists())
            self.assertFalse((root / "scale_eval").exists())
            self.assertTrue((task / "agent.patch").is_file())

    def test_icode_usage_parses_harbor_json_line(self):
        from icode_usage import parse_icode_usage_text

        blob = (
            "warning: skip\n"
            '{"session_id": "cli-x", "usage": {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57}}\n'
        )
        got = parse_icode_usage_text(blob)
        self.assertEqual(got["prompt"], 50)
        self.assertEqual(got["completion"], 7)
        self.assertEqual(got["total"], 57)
        from icode_usage import usage_from_obj

        zero = usage_from_obj({"usage": {"prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_hit_tokens": 0}})
        self.assertEqual(zero["cache_hit"], 0)
        nested = usage_from_obj(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 4}}}
        )
        self.assertEqual(nested["cache_hit"], 4)

    def test_harbor_reward_json_sets_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "ruff_1"
            tasks.mkdir(parents=True)
            harness = root / "harness"
            (harness / "harbor_runs" / "jenkins-1" / "ruff_1").mkdir(parents=True)
            (harness / "harbor_runs" / "jenkins-1" / "ruff_1" / "reward.json").write_text(
                '{"reward": 1.0}\n', encoding="utf-8"
            )
            (harness / "meta.json").write_text(
                json.dumps({"duration_seconds": 9, "llm_model_id": "deepseek-v4-pro"}),
                encoding="utf-8",
            )
            baseline = root / "baseline"
            baseline.mkdir()
            (baseline / "meta.json").write_text("{}", encoding="utf-8")
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(baseline),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "BENCHMARK": "lolbench", "DEEPSEEK_MODEL": "deepseek-v4-pro"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            self.assertEqual(doc["suite"], "lolbench")
            self.assertEqual(doc["tasks"][0]["reward"], 1.0)
            self.assertIs(doc["tasks"][0]["first"], True)
            self.assertEqual(doc["pass_at_1"], 1.0)
            self.assertIsNone(doc["tasks"][0]["f2p"])
            self.assertNotIn("notes", doc)

            zero = root / "zero"
            zero.mkdir()
            (zero / "reward.json").write_text('{"reward": 0.0}\n', encoding="utf-8")
            self.assertIs(harbor_reward_resolved(zero), False)
            self.assertIs(harbor_reward_resolved(harness), True)

    def test_harbor_nested_job_dir_rates_not_resolved(self):
        """Layout from lolbench_one_task SUCCESS: reward 0.0, F2P ~0.368, P2P 1.0."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "ruff_1"
            tasks.mkdir(parents=True)
            job = (
                root
                / "harness"
                / "harbor_runs"
                / "jenkins-10"
                / "ruff_1"
                / "ruff_1_icode_union_10"
            )
            verifier = job / "ruff_1__g3wWhDM" / "verifier"
            verifier.mkdir(parents=True)
            (verifier / "reward.json").write_text('{"reward": 0.0}\n', encoding="utf-8")
            (job / "result.json").write_text(
                json.dumps(
                    {
                        "metrics": [
                            {
                                "f2p_pass_rate": 0.3684210526315789,
                                "p2p_pass_rate": 1.0,
                                "reward": 0.0,
                                "resolved": 0.0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            harness = root / "harness"
            (harness / "meta.json").write_text(
                json.dumps({"duration_seconds": 1576, "llm_model_id": "deepseek-v4-pro"}),
                encoding="utf-8",
            )
            baseline = root / "baseline"
            baseline.mkdir()
            (baseline / "meta.json").write_text("{}", encoding="utf-8")
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(baseline),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "BENCHMARK": "lolbench", "DEEPSEEK_MODEL": "deepseek-v4-pro"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            self.assertEqual(doc["suite"], "lolbench")
            self.assertEqual(doc["tasks"][0]["reward"], 0.0)
            self.assertEqual(doc["pass_at_1"], 0.0)
            self.assertAlmostEqual(doc["tasks"][0]["f2p"], 0.3684210526315789)
            self.assertEqual(doc["tasks"][0]["p2p"], 1.0)
            self.assertAlmostEqual(doc["macro"]["f2p"], 0.3684)
            self.assertEqual(doc["wall_seconds"], 1576)
            self.assertEqual(doc["wall_minutes"], round(1576 / 60.0, 3))
            self.assertIsNone(doc["tasks"][0]["tok_in"])
            self.assertNotIn("f2p_rate", doc["tasks"][0])
            self.assertNotIn("notes", doc)

    def test_lolbench_agent_report_counts_and_trial_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "ruff_1"
            tasks.mkdir(parents=True)
            trial = (
                root
                / "harness"
                / "harbor_runs"
                / "jenkins-15"
                / "ruff_1"
                / "ruff_1_icode_union_15"
                / "ruff_1__qbeDcAQ"
            )
            verifier = trial / "verifier"
            verifier.mkdir(parents=True)
            (verifier / "reward.json").write_text(
                json.dumps(
                    {
                        "reward": 0.0,
                        "resolved": 0.0,
                        "f2p_pass_rate": 0.6842105263157895,
                        "p2p_pass_rate": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            (verifier / "agent_report.json").write_text(
                json.dumps(
                    {
                        "f2p": {"passed": 13, "failed": 6, "total": 19},
                        "p2p": {"passed": 51, "failed": 0, "total": 51},
                        "resolved": False,
                    }
                ),
                encoding="utf-8",
            )
            (trial / "agent").mkdir()
            (trial / "agent" / "icode.txt").write_text(
                '{"usage": {"input_tokens": 10092649, "output_tokens": 73292, "total_tokens": 10165941}}\n',
                encoding="utf-8",
            )
            harness = root / "harness"
            (harness / "meta.json").write_text(
                json.dumps({"duration_seconds": 954, "token_usage": {"prompt": 1, "completion": 1}}),
                encoding="utf-8",
            )
            baseline = root / "baseline" / "ruff_1"
            baseline.mkdir(parents=True)
            (root / "baseline" / "summary.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "ruff_1",
                            "ok": True,
                            "token_usage": {"prompt": 663, "completion": 112, "total": 775},
                            "duration_seconds": 1.087,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "baseline" / "meta.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 1.087,
                        "token_usage": {"prompt": 663, "completion": 112, "total": 775},
                    }
                ),
                encoding="utf-8",
            )
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(root / "baseline"),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "BENCHMARK": "lolbench", "DEEPSEEK_MODEL": "deepseek-flash"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            task = doc["tasks"][0]
            self.assertEqual(task["f2p_pass"], 13)
            self.assertEqual(task["f2p_total"], 19)
            self.assertEqual(task["p2p_pass"], 51)
            self.assertEqual(task["p2p_total"], 51)
            self.assertEqual(task["partial"], 0.9143)
            self.assertEqual(doc["macro"]["partial"], 0.9143)
            self.assertEqual(task["tok_in"], 10092649)
            self.assertEqual(task["tok_out"], 73292)
            self.assertEqual(doc["tokens"]["in"], 10092649)
            self.assertEqual(doc["tokens"]["out"], 73292)
            self.assertEqual(task["baseline"]["tok_in"], 663)
            self.assertIsNone(task["baseline"]["reward"])

    def test_lolbench_reward_counts_without_agent_report(self):
        """Early-exit / patched report_to_reward writes counts on reward.json alone."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks" / "fastapi_1"
            tasks.mkdir(parents=True)
            trial = (
                root
                / "harness"
                / "harbor_runs"
                / "jenkins-19"
                / "fastapi_1"
                / "fastapi_1_icode_union_19_a01"
                / "fastapi_1__empty"
            )
            verifier = trial / "verifier"
            verifier.mkdir(parents=True)
            (verifier / "reward.json").write_text(
                json.dumps(
                    {
                        "reward": 0.0,
                        "resolved": 0.0,
                        "applied": 0.0,
                        "build_ok": 0.0,
                        "f2p_pass_rate": 0.0,
                        "p2p_pass_rate": 0.0,
                        "f2p_pass": 0,
                        "f2p_total": 11,
                        "p2p_pass": 0,
                        "p2p_total": 51,
                        "harness_ok": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            (trial / "agent").mkdir()
            (trial / "agent" / "icode.txt").write_text("{}\n", encoding="utf-8")
            harness = root / "harness"
            (harness / "meta.json").write_text(
                json.dumps({"duration_seconds": 10}),
                encoding="utf-8",
            )
            (root / "baseline").mkdir()
            (root / "baseline" / "summary.json").write_text("[]\n", encoding="utf-8")
            (root / "baseline" / "meta.json").write_text("{}\n", encoding="utf-8")
            out = root / "out.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "score_results.py"),
                    "--harness-dir",
                    str(harness),
                    "--baseline-dir",
                    str(root / "baseline"),
                    "--tasks-dir",
                    str(root / "tasks"),
                    "--n-tasks",
                    "1",
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "BENCHMARK": "lolbench", "DEEPSEEK_MODEL": "deepseek-flash"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(doc), [])
            task = doc["tasks"][0]
            self.assertEqual(task["f2p_pass"], 0)
            self.assertEqual(task["f2p_total"], 11)
            self.assertEqual(task["p2p_pass"], 0)
            self.assertEqual(task["p2p_total"], 51)
            from render_report import _attempt_from_trial

            row = _attempt_from_trial(trial, verifier / "reward.json")
            self.assertEqual(row["f2p_pass"], 0)
            self.assertEqual(row["f2p_total"], 11)
            self.assertEqual(row["p2p_pass"], 0)
            self.assertEqual(row["p2p_total"], 51)
            self.assertIsNotNone(row["partial"])

    def test_lolbench_fix_rewards_patcher(self):
        from lolbench_fix_rewards import MARKER, patch_harbor_tasks

        with tempfile.TemporaryDirectory() as tmp:
            harbor = Path(tmp) / "harbor_tasks"
            task = harbor / "fastapi_1" / "tests"
            task.mkdir(parents=True)
            (task / "report_to_reward.py").write_text(
                'x = {"instance_id": "fastapi_1"}\n',
                encoding="utf-8",
            )
            (task / "test.sh").write_text(
                "#!/usr/bin/env bash\nif [ ! -s \"$patch\" ]; then\n"
                "  python3 /tests/report_to_reward.py --missing-patch \"$patch\" a b\n"
                "  exit 0\nfi\n",
                encoding="utf-8",
            )
            self.assertEqual(patch_harbor_tasks(harbor), 1)
            self.assertEqual(patch_harbor_tasks(harbor), 0)
            report = (task / "report_to_reward.py").read_text(encoding="utf-8")
            self.assertIn(MARKER, report)
            self.assertIn("f2p_pass", report)
            self.assertIn("f2p_total", report)
            self.assertIn("p2p_pass", report)
            self.assertIn("p2p_total", report)
            self.assertIn("instance_id = 'fastapi_1'", report)
            compile(report, "report_to_reward.py", "exec")
            sh = (task / "test.sh").read_text(encoding="utf-8")
            self.assertIn(MARKER, sh)
            self.assertNotIn("--missing-patch", sh)
            self.assertIn('[ -s "$patch" ]', sh)
            # Patched script emits counts on early exit
            out_report = Path(tmp) / "agent_report.json"
            out_reward = Path(tmp) / "reward.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(task / "report_to_reward.py"),
                    "--suite",
                    "union",
                    "--missing-patch",
                    "/logs/artifacts/solution.patch",
                    str(out_report),
                    str(out_reward),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            reward = json.loads(out_reward.read_text(encoding="utf-8"))
            self.assertEqual(reward["f2p_pass"], 0)
            self.assertEqual(reward["f2p_total"], 0)
            self.assertEqual(reward["p2p_pass"], 0)
            self.assertEqual(reward["p2p_total"], 0)
            # Normal path with agent_report buckets
            out_report.write_text(
                json.dumps(
                    {
                        "applied": True,
                        "resolved": False,
                        "build": {"status": "ok"},
                        "f2p": {"passed": 8, "total": 11},
                        "p2p": {"passed": 51, "total": 51},
                    }
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(task / "report_to_reward.py"),
                    "--suite",
                    "union",
                    "--runner-rc",
                    "0",
                    str(out_report),
                    str(out_reward),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            reward = json.loads(out_reward.read_text(encoding="utf-8"))
            self.assertEqual(reward["f2p_pass"], 8)
            self.assertEqual(reward["f2p_total"], 11)
            self.assertEqual(reward["p2p_pass"], 51)
            self.assertEqual(reward["p2p_total"], 51)
            self.assertAlmostEqual(reward["f2p_pass_rate"], 8 / 11)

    def test_benchmark_step_lolbench_applies_fix_rewards(self):
        p2 = (STAGES / "tasks" / "benchmark.sh").read_text(encoding="utf-8")
        self.assertIn("lolbench_fix_rewards.py", p2)
        lolbench_block = p2.split("lolbench)", 1)[1].split("swebenchpro)", 1)[0]
        self.assertIn("lolbench_fix_rewards.py", lolbench_block)
        self.assertIn("$LOLBENCH_DIR/harbor_tasks", lolbench_block)

    def test_trial_files_includes_agent_report(self):
        from archive_run import _TRIAL_FILES

        self.assertIn("agent_report.json", _TRIAL_FILES)
        self.assertIn("reward.json", _TRIAL_FILES)

    def test_verifier_rates_nested_mean_and_list_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "nested"
            nested.mkdir()
            (nested / "result.json").write_text(
                json.dumps(
                    {
                        "stats": {
                            "evals": {
                                "icode": {
                                    "metrics": {
                                        "fail_to_pass": {"mean": 0.3684210526315789},
                                        "pass_to_pass": {"mean": 1.0},
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            got = verifier_rates(nested)
            self.assertAlmostEqual(got["f2p"], 0.3684210526315789)
            self.assertEqual(got["p2p"], 1.0)


class LocalEnvAndAgentTests(unittest.TestCase):
    def test_env_example_has_no_secret(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_API_KEY=", text)
        self.assertIn("GITCODE_TOKEN=", text)
        self.assertIn("GITHUB_TOKEN=", text)
        self.assertNotRegex(text, r"DEEPSEEK_API_KEY=sk-")
        self.assertNotRegex(text, r"GITCODE_TOKEN=\S")
        self.assertNotRegex(text, r"GITHUB_TOKEN=\S")

    def test_gitignore_covers_dotenv(self):
        proc = subprocess.run(
            ["git", "check-ignore", "-v", ".env"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(".env", proc.stdout)

    def test_common_loads_env_file_without_overwrite(self):
        common = ROOT / "pipeline" / "stages" / "_common.sh"
        with tempfile.TemporaryDirectory() as tmp:
            envf = Path(tmp) / "keys.env"
            envf.write_text(
                "DEEPSEEK_API_KEY=test-from-file\nDEEPSEEK_MODEL=deepseek-v4-pro\n",
                encoding="utf-8",
            )
            envf.chmod(0o600)
            script = f"""
unset DEEPSEEK_API_KEY
export MAC_K3D_ENV_FILE={envf}
# isolate workdir so tests do not touch the real eval-runs tree
export MAC_K3D_EVAL_WORKDIR={tmp}/eval-runs
source {common}
printf '%s' "$DEEPSEEK_API_KEY"
"""
            proc = subprocess.run(
                ["bash", "-c", script],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(proc.stdout.endswith("test-from-file"), proc.stdout)
            self.assertNotIn("test-from-file", proc.stderr)

            script_keep = f"""
export DEEPSEEK_API_KEY=already-set
export MAC_K3D_ENV_FILE={envf}
export MAC_K3D_EVAL_WORKDIR={tmp}/eval-runs2
source {common}
printf '%s' "$DEEPSEEK_API_KEY"
"""
            keep = subprocess.run(
                ["bash", "-c", script_keep],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(keep.returncode, 0, keep.stdout + keep.stderr)
            self.assertTrue(keep.stdout.endswith("already-set"), keep.stdout)

            envf.write_text(
                "DEEPSEEK_API_KEY=test-from-file\n"
                "GITCODE_TOKEN=gc-from-file\n"
                "MAC_K3D_GITHUB_PAT=gh-alias\n",
                encoding="utf-8",
            )
            envf.chmod(0o600)
            script_git = f"""
unset DEEPSEEK_API_KEY GITCODE_TOKEN GITHUB_TOKEN MAC_K3D_GITHUB_PAT MAC_K3D_GITCODE_PAT
export MAC_K3D_ENV_FILE={envf}
export MAC_K3D_EVAL_WORKDIR={tmp}/eval-runs3
source {common}
printf 'gc:%s gh:%s' "$GITCODE_TOKEN" "$GITHUB_TOKEN"
"""
            gitp = subprocess.run(
                ["bash", "-c", script_git],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(gitp.returncode, 0, gitp.stdout + gitp.stderr)
            self.assertTrue(gitp.stdout.endswith("gc:gc-from-file gh:gh-alias"), gitp.stdout)
            self.assertNotIn("gc-from-file", gitp.stderr)
            self.assertNotIn("gh-alias", gitp.stderr)

    def test_harbor_run_uses_import_path_not_bare_agent_icode(self):
        text = "\n".join(
            (STAGES / name).read_text(encoding="utf-8")
            for name in ("evaluate/harbor_cmd.sh", "evaluate/harbor_run.sh", "_common.sh")
        )
        self.assertIn("icode_harbor_agent:ICodeAgent", text)
        self.assertIn("harbor run", text)
        # One run covers every selected task x rollout; Harbor schedules them.
        self.assertIn('cmd+=(-k "$N_ROLLOUTS")', text)
        self.assertIn('cmd+=(-n "$EVAL_SLOTS")', text)
        # Declared cpus/memory stand unless an operator opts out on purpose.
        self.assertNotIn('cmd+=(--override-cpus "$EVAL_CPUS_EACH")', text)
        self.assertIn('if [ -n "${EVAL_OVERRIDE_CPUS:-}" ]; then', text)
        self.assertNotIn('TASK_ID="$tid"\n  break', text)
        self.assertIn("No such option", text)
        self.assertIn("selected_tasks", text)
        self.assertNotIn("pier run", text)
        self.assertNotIn("--agent icode", text)
        self.assertIn('cmd+=(--ae "ICODE_API_BASE=${ICODE_API_BASE}")', text)
        self.assertIn('cmd+=(--ae "ICODE_PROVIDER=${ICODE_PROVIDER}")', text)
        self.assertIn('cmd+=(--ae "ICODE_REASONING_EFFORT=${ICODE_REASONING_EFFORT}")', text)
        self.assertIn("ICODE_PROVIDER=DeepSeek", text)
        self.assertIn('ICODE_REASONING_EFFORT:-high', text)
        self.assertIn('ICODE_MAX_TOKENS="${ICODE_MAX_TOKENS:-65536}"', text)
        self.assertIn('ICODE_MAX_ITERATIONS="${ICODE_MAX_ITERATIONS:-500}"', text)
        self.assertIn('cmd+=(--ae "ICODE_MAX_TOKENS=${ICODE_MAX_TOKENS}")', text)
        self.assertIn('cmd+=(--ae "ICODE_MAX_ITERATIONS=${ICODE_MAX_ITERATIONS}")', text)
        self.assertIn('printf \'ICODE_MAX_TOKENS=%s\\n\' "${ICODE_MAX_TOKENS}"', text)
        self.assertIn('printf \'ICODE_MAX_ITERATIONS=%s\\n\' "${ICODE_MAX_ITERATIONS}"', text)
        self.assertIn('printf \'ICODE_MODEL=%s\\n\' "${ICODE_MODEL}"', text)
        self.assertFalse((LIB / "icode_pier_agent.py").exists())
        self.assertFalse((LIB / "pier-agent-icode").exists())

    def test_icode_nonzero_exit_does_not_fail_harbor_shell(self):
        agent = (ROOT / "pipeline" / "lib" / "icode_harbor_agent.py").read_text(encoding="utf-8")
        self.assertIn("set +o pipefail", agent)
        self.assertIn("icode-exit.txt", agent)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "icode.txt"
            status_path = Path(tmp) / "icode-exit.txt"
            script = f"""
set -o pipefail
set +o pipefail
(exit 1) | tee {log} >/dev/null
printf '%s\\n' "${{PIPESTATUS[0]}}" > {status_path}
exit 0
"""
            proc = subprocess.run(["bash", "-c", script], check=False, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(status_path.read_text(encoding="utf-8").strip(), "1")

    def test_long_task_lists_scroll(self):
        from report_html import report_html

        tasks = [{"id": f"t{i}", "c": 0 if i < 13 else 1, "n": 1, "best": i >= 13, "pass_frac": 0.0 if i < 13 else 1.0} for i in range(26)]
        page = report_html(
            {
                "suite": "deepswe",
                "n_tasks": 26,
                "n_rollouts": 1,
                "icode": {"tasks": tasks, "macro_pass@1": 0.5, "any_pass": 0.5},
            }
        )
        self.assertGreaterEqual(page.count('class="scroll"'), 2)
        self.assertIn("Lowest pass fraction", page)

    def test_shared_harbor_command_for_every_benchmark(self):
        agent = (ROOT / "pipeline" / "lib" / "icode_harbor_agent.py").read_text(encoding="utf-8")
        self.assertIn("set +o pipefail", agent)
        self.assertIn("icode-exit.txt", agent)
        self.assertIn('"DeepSeek" if "deepseek.com" in base else "OpenAI"', agent)
        self.assertIn("ICODE_REASONING_EFFORT", agent)
        self.assertIn("{CAPTURE} capture", agent)
        capture = (ROOT / "pipeline" / "lib" / "icode_capture.sh").read_text(encoding="utf-8")
        self.assertIn('commit -q --no-verify -m "icode solution"', capture)
        self.assertLess(capture.find("lolbench-submit \"$REPO\""), capture.find('commit -q --no-verify -m "icode solution"'))
        self.assertIn("https://api.deepseek.com/v1", agent)
        stage = (STAGES / "evaluate" / "harbor_cmd.sh").read_text(encoding="utf-8")
        # One `harbor run` per build names the agent once; the canary names its own.
        self.assertEqual(stage.count("icode_harbor_agent:ICodeAgent"), 1)
        self.assertIn("canary_harbor_agent:CanaryAgent", stage)
        self.assertIn('cmd+=(--ae "ICODE_PROVIDER=${ICODE_PROVIDER}")', stage)
        self.assertIn('cmd+=(--ae "ICODE_API_BASE=${ICODE_API_BASE}")', stage)
        self.assertIn('cmd+=(--ae "ICODE_REASONING_EFFORT=${ICODE_REASONING_EFFORT}")', stage)
        self.assertIn('cmd+=(--ae "ICODE_MAX_TOKENS=${ICODE_MAX_TOKENS}")', stage)
        self.assertIn('cmd+=(--ae "ICODE_MAX_ITERATIONS=${ICODE_MAX_ITERATIONS}")', stage)
        self.assertIn('env.setdefault("ICODE_MAX_TOKENS", MAX_TOKENS)', agent)
        self.assertIn('env.setdefault("ICODE_MAX_ITERATIONS", MAX_ITERATIONS)', agent)
        # Same defaults as _common.sh, for a Harbor run started without it.
        self.assertIn('MAX_TOKENS = "65536"', agent)
        self.assertIn('MAX_ITERATIONS = "500"', agent)
        self.assertIn('BENCHMARK:-deepswe}" = "lolbench"', stage)
        self.assertLess(stage.find("= \"lolbench\""), stage.find("cmd=(harbor run)"))

    def test_lolbench_uses_harbor_agent(self):
        text = "\n".join(
            (STAGES / name).read_text(encoding="utf-8")
            for name in ("evaluate/harbor_cmd.sh", "evaluate/harbor_run.sh", "tasks/images.sh")
        )
        self.assertIn("harbor run", text)
        self.assertIn("icode_harbor_agent:ICodeAgent", text)
        self.assertIn('python3 "$PIPELINE_LIB/network_allowlist.py" hosts', text)
        self.assertIn("ICODE_MODEL", text)
        self.assertIn("reward.json", text)
        self.assertIn('ensure_task_image "$tid" "$image"', text)
        common = (ROOT / "pipeline" / "stages" / "_common.sh").read_text(encoding="utf-8")
        image_fn = common[common.index("ensure_task_image() {"):]
        self.assertIn(
            'docker build --progress=plain "${platform[@]}" -t "$image" "$env_dir"', image_fn[: image_fn.index("\n}\n")]
        )
        self.assertIn("--mounts", text)
        self.assertIn("eval_progress.py", text)
        self.assertIn("heartbeat", text)
        self.assertNotIn("CMD+=(--force-build)", text)
        src = (ROOT / "pipeline" / "lib" / "icode_harbor_agent.py").read_text(encoding="utf-8")
        compile(src, "icode_harbor_agent.py", "exec")
        self.assertIn("/opt/icode-host", src)
        self.assertNotIn("gitcode.com", src)

    def test_env_checks_openai_models_when_key_set(self):
        p0 = (STAGES / "env" / "model_api.sh").read_text(encoding="utf-8")
        self.assertIn("openai_compat.py", p0)
        self.assertIn("--check-model", p0)
        self.assertIn("GET /models", p0)

    def test_env_installs_buildx_for_harbor_sidecar(self):
        env_phase = (STAGES / "env.sh").read_text(encoding="utf-8")
        compose = (STAGES / "env" / "compose.sh").read_text(encoding="utf-8")
        self.assertIn("env/compose", env_phase)
        self.assertIn("\nensure_docker_buildx\n", compose)
        self.assertIn("docker-buildx", compose)
        self.assertIn("docker buildx", compose)

    def test_env_installs_pinned_harbor_only(self):
        text = (STAGES / "env" / "harbor.sh").read_text(encoding="utf-8")
        self.assertIn('uv tool install --force "harbor==${HARBOR_VERSION}"', text)
        self.assertIn("pipeline/config/toolchain.env", text)
        self.assertNotIn("datacurve-pier", text)
        self.assertNotRegex(text, r"\bpier\b")

    def test_tasks_phase_installs_the_agent_for_every_benchmark(self):
        tasks = (STAGES / "tasks.sh").read_text(encoding="utf-8")
        evaluate = (STAGES / "evaluate.sh").read_text(encoding="utf-8")
        for step in ("tasks/icode", "tasks/agent", "tasks/isolation"):
            self.assertIn(f"  {step}\n", tasks)
        self.assertIn("evaluate/harbor_run", evaluate)
        agent = (STAGES / "tasks" / "agent.sh").read_text(encoding="utf-8")
        self.assertNotIn("lolbench)", agent)
        self.assertNotIn("Scale Docker eval", tasks + evaluate)

    def test_swebenchpro_uses_harbor(self):
        text = (STAGES / "evaluate" / "harbor_cmd.sh").read_text(encoding="utf-8")
        self.assertNotIn("swebenchpro_run.py", text)
        self.assertNotIn("pier run", text)
        self.assertIn("harbor run", text)
        self.assertIn("icode_harbor_agent:ICodeAgent", text)
        p2 = (STAGES / "tasks" / "benchmark.sh").read_text(encoding="utf-8")
        self.assertIn("swebenchpro_tasks.py", p2)
        self.assertIn("swebenchpro", p2)

    def test_hollow_pier_result(self):
        hollow = {
            "n_total_trials": 1,
            "stats": {
                "n_completed_trials": 1,
                "n_errored_trials": 1,
                "evals": {
                    "icode__deepseek-v4-pro__tasks": {
                        "n_trials": 0,
                        "n_errors": 1,
                    }
                },
            },
        }
        self.assertIsNotNone(hollow_job_reason(hollow))
        ran = {
            "stats": {
                "n_errored_trials": 1,
                "evals": {"x": {"n_trials": 1, "n_errors": 1}},
            }
        }
        self.assertIsNone(hollow_job_reason(ran))


class TaskSelectTests(unittest.TestCase):
    def test_write_selected_tasks_honors_task_and_lolbench(self):
        common = ROOT / "pipeline" / "stages" / "_common.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            swe = root / "deep-swe" / "tasks"
            (swe / "aaa-first").mkdir(parents=True)
            (swe / "zzz-last").mkdir(parents=True)
            lb = root / "lolbench" / "harbor_tasks"
            (lb / "ruff_1").mkdir(parents=True)
            (lb / "fastapi_1").mkdir(parents=True)
            pro = root / "swebenchpro" / "tasks"
            (pro / "django__forms-1234").mkdir(parents=True)
            script = f"""
export MAC_K3D_EVAL_WORKDIR={root}
export BENCHMARK=deepswe
export TASK=zzz-last
source {common}
write_selected_tasks
printf 'deepswe:%s\\n' "$(tr '\\n' ',' <"$MAC_K3D_EVAL_WORKDIR/selected_tasks.txt")"
export BENCHMARK=lolbench
export TASK=ruff_1
write_selected_tasks
printf 'lolbench:%s\\n' "$(tr '\\n' ',' <"$MAC_K3D_EVAL_WORKDIR/selected_tasks.txt")"
unset TASK
export N_TASKS=1
write_selected_tasks
printf 'lolbench-first:%s\\n' "$(tr '\\n' ',' <"$MAC_K3D_EVAL_WORKDIR/selected_tasks.txt")"
export BENCHMARK=swebenchpro
export TASK=django__forms-1234
write_selected_tasks
printf 'swebenchpro:%s\\n' "$(tr '\\n' ',' <"$MAC_K3D_EVAL_WORKDIR/selected_tasks.txt")"
"""
            proc = subprocess.run(
                ["bash", "-c", script],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("deepswe:zzz-last,", proc.stdout)
            self.assertIn("lolbench:ruff_1,", proc.stdout)
            self.assertIn("lolbench-first:fastapi_1,", proc.stdout)
            self.assertIn("swebenchpro:django__forms-1234,", proc.stdout)

    def test_write_selected_tasks_honors_comma_list(self):
        common = ROOT / "pipeline" / "stages" / "_common.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            swe = root / "deep-swe" / "tasks"
            (swe / "aaa-first").mkdir(parents=True)
            (swe / "zzz-last").mkdir(parents=True)
            script = f"""
export MAC_K3D_EVAL_WORKDIR={root}
export BENCHMARK=deepswe
unset TASK
export TASKS='zzz-last, aaa-first'
source {common}
write_selected_tasks
printf 'tasks:%s n:%s\\n' "$(tr '\\n' ',' <"$MAC_K3D_EVAL_WORKDIR/selected_tasks.txt")" "$N_TASKS"
unset TASKS
export TASK='zzz-last,aaa-first'
write_selected_tasks
printf 'task-csv:%s n:%s\\n' "$(tr '\\n' ',' <"$MAC_K3D_EVAL_WORKDIR/selected_tasks.txt")" "$N_TASKS"
unset TASK
export N_TASKS=2
write_selected_tasks
printf 'first2:%s\\n' "$(tr '\\n' ',' <"$MAC_K3D_EVAL_WORKDIR/selected_tasks.txt")"
"""
            proc = subprocess.run(
                ["bash", "-c", script],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("tasks:zzz-last,aaa-first, n:2", proc.stdout)
            self.assertIn("task-csv:zzz-last,aaa-first, n:2", proc.stdout)
            self.assertIn("first2:aaa-first,zzz-last,", proc.stdout)

    def test_offset_slices_use_byte_order_whatever_the_locale(self):
        """en_US.UTF-8 sorts sqlfmt-… before sql-formatter-…; a C-locale worker would cut a different slice."""
        common = ROOT / "pipeline" / "stages" / "_common.sh"
        suite = ["aaa-first", "sql-formatter-bigquery-pipe-formatting", "sqlfmt-create-table-ddl-formatting"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tid in reversed(suite):
                (root / "deep-swe" / "tasks" / tid).mkdir(parents=True)
            script = f"""
export MAC_K3D_EVAL_WORKDIR={root}
export BENCHMARK=deepswe
unset TASK TASKS
export N_TASKS=1 TASK_OFFSET=1
source {common}
write_selected_tasks
"""
            proc = subprocess.run(
                ["bash", "-c", script],
                env={**os.environ, "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual((root / "suite_tasks.txt").read_text(encoding="utf-8").split(), suite)
            self.assertEqual(
                (root / "selected_tasks.txt").read_text(encoding="utf-8").split(),
                ["sql-formatter-bigquery-pipe-formatting"],
            )

    def test_common_canonicalizes_relative_workdir(self):
        common = ROOT / "pipeline" / "stages" / "_common.sh"
        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
cd {tmp}
export MAC_K3D_EVAL_WORKDIR=rel-eval
source {common}
printf '%s' "$WORKDIR"
"""
            proc = subprocess.run(
                ["bash", "-c", script],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            workdir = proc.stdout.strip().splitlines()[-1]
            self.assertTrue(workdir.startswith("/"), workdir)
            self.assertTrue(workdir.endswith("rel-eval"), workdir)


class IcodeBuildToolTests(unittest.TestCase):
    def test_official_full_release_shapes(self):
        script = ROOT / "pipeline" / "tools" / "test_icode_build.sh"
        proc = subprocess.run(
            ["bash", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK test_icode_build.sh", proc.stdout)

    def test_icode_input_url_ref_validation(self):
        script = ROOT / "pipeline" / "tools" / "test_icode_input.sh"
        proc = subprocess.run(
            ["bash", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK test_icode_input.sh", proc.stdout)


class SwebenchproTests(unittest.TestCase):
    def test_dockerhub_image_matches_scale_helper(self):
        from swebenchpro_run import dockerhub_image

        meta = {
            "instance_id": "instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan",
            "repo": "NodeBB/NodeBB",
            "dockerhub_tag": "NodeBB_NodeBB",
            "dockerhub_username": "jefzda",
        }
        self.assertEqual(
            dockerhub_image(meta),
            "jefzda/sweap-images:nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5",
        )
        hub = {
            **meta,
            "dockerhub_tag": "nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5",
        }
        self.assertEqual(
            dockerhub_image(hub),
            "jefzda/sweap-images:nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5",
        )

    def test_row_from_obj_maps_uppercase_fail_to_pass(self):
        from swebenchpro_tasks import row_from_obj

        row = row_from_obj(
            {
                "instance_id": "instance_demo",
                "repo": "NodeBB/NodeBB",
                "FAIL_TO_PASS": ["test/a.js | new"],
                "PASS_TO_PASS": '["test/a.js | old"]',
                "selected_test_files_to_run": ["test/a.js"],
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["fail_to_pass"], '["test/a.js | new"]')
        self.assertEqual(row["pass_to_pass"], '["test/a.js | old"]')
        self.assertEqual(row["selected_test_files_to_run"], '["test/a.js"]')
        self.assertEqual(set(eval(row["fail_to_pass"])), {"test/a.js | new"})
        self.assertEqual(set(eval(row["pass_to_pass"])), {"test/a.js | old"})

    def test_suite_reward_matches_scale_rule(self):
        from swebenchpro_tasks import suite_reward

        got = suite_reward({"new", "old"}, ["new"], ["old"])
        self.assertEqual(got["reward"], 1.0)
        self.assertTrue(got["resolved"])
        self.assertEqual(got["f2p"], 1.0)
        self.assertEqual(got["p2p"], 1.0)
        missed = suite_reward({"old"}, ["new"], ["old"])
        self.assertEqual(missed["reward"], 0.0)
        self.assertFalse(missed["resolved"])
        self.assertEqual(missed["f2p_pass"], 0)
        self.assertEqual(missed["f2p_total"], 1)

    def test_scale_suite_rates_from_passed_names(self):
        from swebenchpro_run import scale_suite_rates

        rates = scale_suite_rates(
            {
                "fail_to_pass": '["new-test"]',
                "pass_to_pass": '["old-test"]',
            },
            {"new-test", "old-test"},
        )
        self.assertEqual(rates["f2p"], 1.0)
        self.assertEqual(rates["f2p_pass"], 1)
        self.assertEqual(rates["f2p_total"], 1)
        self.assertEqual(rates["p2p"], 1.0)
        self.assertEqual(rates["p2p_pass"], 1)
        self.assertEqual(rates["p2p_total"], 1)

    def test_parse_eval_results_bool_map(self):
        from swebenchpro_run import parse_eval_output

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "eval_results.json").write_text(
                json.dumps({"instance_demo": True, "instance_other": False}),
                encoding="utf-8",
            )
            got = parse_eval_output(out)
            self.assertEqual(got["instance_demo"], True)
            self.assertEqual(got["instance_other"], False)

    def test_write_patches_json_is_scale_list(self):
        from swebenchpro_run import write_patches_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "patches.json"
            write_patches_json(path, {"inst-1": "diff --git a b\n"})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(data, list)
            self.assertEqual(data[0]["instance_id"], "inst-1")
            self.assertIn("diff --git", data[0]["patch"])

    def test_materialize_jsonl_and_skip_docker(self):
        from swebenchpro_tasks import materialize

        fixture = LIB / "testdata" / "swebenchpro-mini.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "helper_code").mkdir()
            (src / "helper_code" / "sweap_eval.jsonl").write_text(
                fixture.read_text(encoding="utf-8"), encoding="utf-8"
            )
            out = Path(tmp) / "tasks"
            ids = materialize(src, out, n_tasks=1)
            self.assertEqual(ids, ["django__forms-1234"])
            self.assertTrue((out / "django__forms-1234" / "instruction.md").is_file())
            meta = json.loads((out / "django__forms-1234" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["dockerhub_tag"], "django_django")
            toml = (out / "django__forms-1234" / "task.toml").read_text(encoding="utf-8")
            self.assertIn("docker_image", toml)
            self.assertIn('environment_mode = "shared"', toml)
            self.assertNotIn('environment_mode = "separate"', toml)
            self.assertIn("jefzda/sweap-images:", toml)
            self.assertTrue((out / "django__forms-1234" / "tests" / "test.sh").is_file())
            self.assertTrue((out / "django__forms-1234" / "tests" / "grade.py").is_file())
            dockerfile = (out / "django__forms-1234" / "environment" / "Dockerfile").read_text(
                encoding="utf-8"
            )
            self.assertIn("ENTRYPOINT []", dockerfile)
            compose = (
                out / "django__forms-1234" / "environment" / "docker-compose.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn('entrypoint: ["sh", "-c", "sleep infinity"]', compose)
            verifier_compose = (
                out / "django__forms-1234" / "tests" / "docker-compose.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn('entrypoint: ["sh", "-c", "sleep infinity"]', verifier_compose)
            ids2 = materialize(src, out, task="flask__views-9")
            self.assertEqual(ids2, ["flask__views-9"])

            host = Path(tmp) / "icode-host"
            host.mkdir()
            (host / "icode").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            harness = Path(tmp) / "harness"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LIB / "swebenchpro_run.py"),
                    "--mode",
                    "harness",
                    "--tasks-dir",
                    str(out),
                    "--out-dir",
                    str(harness),
                    "--src-dir",
                    str(src),
                    "--n-tasks",
                    "1",
                    "--icode-host",
                    str(host),
                    "--skip-docker",
                    "--skip-eval",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            eval_doc = json.loads(
                (harness / "django__forms-1234" / "eval.json").read_text(encoding="utf-8")
            )
            self.assertEqual(eval_doc["resolved"], False)
            self.assertIs(scale_eval_resolved(harness / "django__forms-1234"), False)

    def test_unknown_benchmark_dies_in_the_benchmark_step(self):
        text = (STAGES / "tasks" / "benchmark.sh").read_text(encoding="utf-8")
        self.assertIn("use deepswe, lolbench, or swebenchpro", text)
        common = (ROOT / "pipeline" / "stages" / "_common.sh").read_text(encoding="utf-8")
        self.assertIn("swebenchpro) echo \"$SWEBENCHPRO_DIR/tasks\"", common)


class EvalSlotsDebugGuardTests(unittest.TestCase):
    def test_eval_slots_source_has_no_agent_debug_log(self):
        text = (LIB / "eval_slots.py").read_text(encoding="utf-8")
        self.assertNotIn("_debug_log", text)
        self.assertNotIn(".cursor/", text)


class SecretGuardTests(unittest.TestCase):
    def test_check_no_secrets_script_passes_on_tracked_tree(self):
        script = ROOT / "scripts" / "check_no_secrets.sh"
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK no tracked secret files", proc.stdout)

    def test_check_docs_script_passes_on_tracked_tree(self):
        script = ROOT / "scripts" / "check_docs.sh"
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK docs layout", proc.stdout)

    def test_gitignore_covers_runtime_env_and_eval_workdirs(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("**/.pier-env", gi)
        self.assertIn("**/.harbor-env", gi)
        self.assertIn("eval-runs-*/", gi)
        self.assertIn(".cursor/debug-*.log", gi)


class EvalReportTests(unittest.TestCase):
    def test_parallel_degree_reads_the_plan_evaluate_wrote(self):
        """EVAL_SLOTS and EVAL_CPUS_EACH come from the applied plan, not a guess."""
        script = ROOT / "pipeline" / "lib" / "parallel_degree.sh"
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "eval_resources.json").write_text(
                json.dumps(
                    {
                        "declared": {"peak": {"cpus": 2, "memory_mb": 8192}},
                        "applied": {"slots": 3, "cpus_each": 2},
                    }
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{script}" && eval_parallel_degree && echo "$EVAL_SLOTS $EVAL_CPUS_EACH"',
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "N_ROLLOUTS": "4", "CPU_LOCK_QTY": "8", "WORKDIR": str(work)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "3 2")

    def test_parallel_degree_prints_a_float_cpu_count_as_a_whole_number(self):
        """A plan written before the fix holds ``cpus_each: 2.0``; P8 must still get ``2``."""
        script = ROOT / "pipeline" / "lib" / "parallel_degree.sh"
        for cpus_each, want in ((2.0, "3 2"), (0.5, "3 0.5")):
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                (work / "eval_resources.json").write_text(
                    json.dumps(
                        {
                            "declared": {"peak": {"cpus": cpus_each}},
                            "applied": {"slots": 3, "cpus_each": cpus_each},
                        }
                    ),
                    encoding="utf-8",
                )
                proc = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f'source "{script}" && eval_parallel_degree && echo "$EVAL_SLOTS $EVAL_CPUS_EACH"',
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "N_ROLLOUTS": "1", "CPU_LOCK_QTY": "16", "WORKDIR": str(work)},
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), want)

    def test_report_renders_when_the_task_declares_float_cpus(self):
        """Build #51: ``render_report.py: error: argument --cpus-each: invalid int value: '2.0'``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "eval-runs"
            trial = work / "harness" / "harbor_runs" / "jenkins-51" / "alpha" / "alpha__trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier" / "reward.json").write_text('{"reward": 1}\n', encoding="utf-8")
            (trial / "result.json").write_text("{}\n", encoding="utf-8")
            (work / "selected_tasks.txt").write_text("alpha\n", encoding="utf-8")
            (work / "eval_resources.json").write_text(
                json.dumps(
                    {
                        "declared": {"peak": {"cpus": 2.0, "memory_mb": 8192}},
                        "applied": {"slots": 1, "cpus_each": 2.0, "cpu_lock_qty": 16},
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            for key in ("BUILD_NUMBER", "ICODE_API_BASE", "OFFICIAL", "EVAL_CPUS_EACH", "EVAL_SLOTS"):
                env.pop(key, None)
            env.update(
                {
                    "MAC_K3D_EVAL_WORKDIR": str(work),
                    "MAC_K3D_OUTPUT_ROOT": str(root / "backup"),
                    "HOME": str(root / "home"),
                    "BENCHMARK": "deepswe",
                    "N_TASKS": "1",
                    "N_ROLLOUTS": "1",
                    "CPU_LOCK_QTY": "16",
                    "HARNESS": "icode",
                    "LLM": "deepseek",
                    "DEEPSEEK_MODEL": "deepseek-flash",
                }
            )
            proc = subprocess.run(
                ["bash", str(STAGES / "report.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            artifacts = list((work / "output" / "deepswe").glob("*/artifact.json"))
            self.assertEqual(len(artifacts), 1)
            written = json.loads(artifacts[0].read_text(encoding="utf-8"))
            self.assertEqual(written["cpus_each"], 2)

    def test_render_report_accepts_a_float_cpus_each(self):
        for value, want in (("2.0", 2), ("0.5", 0.5)):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                harness = root / "harness"
                trial = harness / "harbor_runs" / "jenkins-1" / "alpha" / "alpha__trial"
                (trial / "verifier").mkdir(parents=True)
                (trial / "verifier" / "reward.json").write_text('{"reward": 1}\n', encoding="utf-8")
                (trial / "result.json").write_text("{}\n", encoding="utf-8")
                tasks = root / "selected_tasks.txt"
                tasks.write_text("alpha\n", encoding="utf-8")
                out = root / "out"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "pipeline" / "lib" / "render_report.py"),
                        "--harness-dir", str(harness),
                        "--baseline-dir", str(root / "baseline"),
                        "--task-file", str(tasks),
                        "--suite", "deepswe",
                        "--run-id", "local",
                        "--cpus-each", value,
                        "--run-folder", "run",
                        "--out-dir", str(out),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "HOME": str(root)},
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                written = json.loads((out / "run" / "artifact.json").read_text(encoding="utf-8"))
                self.assertEqual(written["cpus_each"], want)

    def test_parallel_degree_does_not_guess_when_evaluate_never_ran(self):
        """A report-only replay of a tree with no plan reports 1x1, not a probe of this host."""
        script = ROOT / "pipeline" / "lib" / "parallel_degree.sh"
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{script}" && eval_parallel_degree && echo "$EVAL_SLOTS $EVAL_CPUS_EACH"',
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "N_ROLLOUTS": "4", "CPU_LOCK_QTY": "8", "WORKDIR": tmp},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "1 1")
        self.assertNotIn("task_resources.py", script.read_text(encoding="utf-8"))

    def test_parallel_degree_rejects_bad_integers(self):
        script = ROOT / "pipeline" / "lib" / "parallel_degree.sh"
        for env in ({"N_ROLLOUTS": "x", "CPU_LOCK_QTY": "4"}, {"N_ROLLOUTS": "4", "CPU_LOCK_QTY": "0"}):
            proc = subprocess.run(
                ["bash", "-c", f'source "{script}" && eval_parallel_degree'],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, **env},
            )
            self.assertNotEqual(proc.returncode, 0, f"{env} should be rejected")

    def test_eval_protocol_records_what_actually_ran(self):
        from render_report import build_eval_protocol

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "icode_bin_path.txt").write_text(
                "/opt/drops/icode-linux-x86_64-full-v0.1.44\n",
                encoding="utf-8",
            )
            (work / "eval_protocol_inputs.json").write_text(
                json.dumps(
                    {
                        "model": "deepseek-flash",
                        "api_base": "https://api.deepseek.com/v1",
                        "provider": "DeepSeek",
                        "reasoning_effort": "high",
                        "max_tokens": 65536,
                        "max_iterations": 500,
                        "cpu_lock_qty": 8,
                        "concurrency": 4,
                        "cpus_each": 2,
                    }
                ),
                encoding="utf-8",
            )
            protocol = build_eval_protocol(
                workdir=work,
                model="deepseek-flash",
                api_base="https://api.deepseek.com/v1",
                n_rollouts=4,
                concurrency=4,
                cpus_each=2,
            )
            self.assertNotIn("version", protocol["icode"])
            self.assertEqual(protocol["icode"]["bin_path"], "/opt/drops/icode-linux-x86_64-full-v0.1.44")
            self.assertEqual(protocol["model_params"]["provider"], "DeepSeek")
            self.assertEqual(protocol["model_params"]["reasoning_effort"], "high")
            self.assertEqual(protocol["model_params"]["thinking"]["type"], "enabled")
            self.assertEqual(protocol["model_params"]["max_tokens"], 65536)
            self.assertEqual(protocol["model_params"]["max_iterations"], 500)
            self.assertEqual(protocol["resources"]["concurrency"], 4)
            # Declared, not CPU_LOCK_QTY / slots.
            self.assertEqual(protocol["resources"]["cpus_each"], 2)

    def test_git_mode_ignores_a_leftover_release_record(self):
        from render_report import build_eval_protocol, icode_under_test, summary_markdown
        from report_html import report_html

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "icode_git.json").write_text(
                json.dumps(
                    {
                        "url": "https://gitcode.com/michaelling/jiuwenicode",
                        "kind": "pr",
                        "ref": "2",
                        "sha": "eea9d66dd00f137c3400b79c1c8574d8ae7debd1",
                        "subject": "pin",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (work / "icode_release.json").write_text(
                json.dumps({"filename": "ICODE_RELEASE_FILE", "sha256": "abc123"}) + "\n",
                encoding="utf-8",
            )
            old = os.environ.get("ICODE_MODE")
            os.environ["ICODE_MODE"] = "git"
            try:
                protocol = build_eval_protocol(
                    workdir=work,
                    model="deepseek-flash",
                    api_base="https://api.deepseek.com/v1",
                    n_rollouts=1,
                    concurrency=1,
                    cpus_each=1,
                )
            finally:
                if old is None:
                    os.environ.pop("ICODE_MODE", None)
                else:
                    os.environ["ICODE_MODE"] = old
            self.assertNotIn("version", protocol["icode"])
            self.assertNotIn("release", protocol["icode"])
            self.assertEqual(protocol["icode"]["git"]["sha"], "eea9d66dd00f137c3400b79c1c8574d8ae7debd1")
            doc = {"suite": "deepswe", "eval_protocol": protocol, "icode": {"tasks": []}}
            text = summary_markdown(doc)
            page = report_html(doc)
            self.assertIn("iCode under test", text)
            self.assertIn("git pr 2 -> eea9d66dd00f137c3400b79c1c8574d8ae7debd1", text)
            self.assertNotIn("iCode version", text)
            self.assertNotIn("iCode release", text)
            self.assertIn("iCode under test", page)
            self.assertNotIn("iCode version", page)
            self.assertEqual(
                icode_under_test(protocol),
                "git pr 2 -> eea9d66dd00f137c3400b79c1c8574d8ae7debd1 (pin)",
            )

    def test_eval_protocol_keeps_a_float_cpu_count_whole(self):
        from render_report import build_eval_protocol

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "eval_protocol_inputs.json").write_text(
                json.dumps({"cpus_each": 2.0}), encoding="utf-8"
            )
            protocol = build_eval_protocol(
                workdir=work,
                model="deepseek-flash",
                api_base="https://api.deepseek.com/v1",
                n_rollouts=1,
                concurrency=1,
                cpus_each=1,
            )
            self.assertEqual(protocol["resources"]["cpus_each"], 2)
            self.assertIsInstance(protocol["resources"]["cpus_each"], int)

    def test_docker_stats_peak_ignores_sidecars(self):
        from eval_slots import max_icode_mem_gb

        sample = (
            "k3d-server\t2.0GiB / 16GiB\n"
            "q1_icode_1_a01\t400MiB / 16GiB\n"
            "abs-module-cache-flags__BB2vSAk__env-main-1\t800MiB / 16GiB\n"
            "fastapi-deprecation-response-hea__FzVfjPP-main-1\t350MiB / 16GiB\n"
            "fastapi-deprecation-response-hea__FzVfjPP-main-1\t400KiB / 16GiB\n"
            "abs-module-cache-flags__BB2vSAk__env-harbor-docker-egress-control-sidecar-1\t4GiB / 16GiB\n"
        )
        self.assertAlmostEqual(max_icode_mem_gb(sample), 800 / 1024)
        trial_only = (
            "k3d-server\t2.0GiB / 16GiB\n"
            "fastapi-deprecation-response-hea__FzVfjPP-main-1\t350MiB / 16GiB\n"
            "abs-module-cache-flags__BB2vSAk__env-harbor-docker-egress-control-sidecar-1\t4GiB / 16GiB\n"
        )
        self.assertAlmostEqual(max_icode_mem_gb(trial_only), 350 / 1024)
        # A 400 KiB reading is startup noise, not a peak.
        self.assertIsNone(
            max_icode_mem_gb("fastapi-deprecation-response-hea__FzVfjPP-main-1\t400KiB / 16GiB\n")
        )


    def test_pass_at_ladder_and_demo_report(self):
        from eval_metrics import pass_at_ladder, summarize_arm
        from render_report import demo_artifact, summary_markdown
        from report_html import CANDIDATE, report_html

        flags = [[True, False], [False, True]]
        ladder = pass_at_ladder(flags, 2)
        self.assertEqual(set(ladder), {"pass@1", "pass@2"})
        self.assertAlmostEqual(ladder["pass@1"], 0.5)
        self.assertAlmostEqual(ladder["pass@2"], 1.0)
        from eval_metrics import mean_ci

        mean, half = mean_ci([0.5] + [0.0] * 19)
        self.assertAlmostEqual(mean, 0.025)
        self.assertAlmostEqual(half, 0.049)
        arm = summarize_arm(
            [
                (
                    "q",
                    [
                        {"resolved": True, "f2p": 1.0, "f2p_pass": 1, "f2p_total": 1, "p2p": 1.0, "p2p_pass": 1, "p2p_total": 1, "partial": 1.0, "tok_in": 10, "tok_out": 1, "dur_s": 3},
                        {"resolved": False, "f2p": 0.0, "f2p_pass": 0, "f2p_total": 1, "p2p": 1.0, "p2p_pass": 1, "p2p_total": 1, "partial": 0.5, "tok_in": 20, "tok_out": 2, "dur_s": 4},
                    ],
                )
            ],
            2,
            2,
        )
        self.assertEqual(arm["tasks"][0]["c"], 1)
        self.assertEqual(arm["tasks"][0]["n"], 2)
        self.assertAlmostEqual(arm["tasks"][0]["pass_frac"], 0.5)
        self.assertTrue(arm["tasks"][0]["first"])
        self.assertEqual(arm["tasks"][0]["reward"], 1.0)
        self.assertEqual(arm["pass@1"], 1.0)
        self.assertEqual(arm["pass@2"], 1.0)
        self.assertNotIn("n_tasks", arm)
        self.assertNotIn("n_rollouts", arm)
        self.assertNotIn("concurrency", arm)
        self.assertNotIn("pass_at", arm)
        self.assertNotIn("first_wilson_low", arm)
        self.assertNotIn("first_wilson_high", arm)
        self.assertNotIn("any_pass_hits", arm)
        self.assertNotIn("best_attempt_pass", arm)
        self.assertIn("any_pass", arm)
        self.assertIn("total", arm["tokens"])
        self.assertIn("partial_pass", arm["micro"])
        self.assertIn("rollouts", arm["tasks"][0])
        self.assertEqual(len(arm["tasks"][0]["rollouts"]), 2)
        doc = demo_artifact()
        self.assertEqual({f"pass@{i}" for i in range(1, 5)} <= set(doc["icode"]), True)
        self.assertNotIn("llm", doc)
        self.assertNotIn("n_tasks", doc["icode"])
        text = summary_markdown(doc)
        self.assertIn("Run: `deepswe-25`", text)
        self.assertIn("Pass@1..k (padded, missing=fail)", text)
        self.assertIn("Pass@1..k (scored-only, missing omitted)", text)
        self.assertIn("Pass@1 **33.3%**", text)
        self.assertIn("Pass@2 **66.7%**", text)
        self.assertIn("macro Pass@1 (padded, mean of c/n)", text)
        self.assertIn("macro Pass@1 (scored-only, mean of c_scored/n_scored)", text)
        self.assertIn("c_scored/n_scored", text)
        self.assertIn("unscored rollouts: 0", text)
        self.assertIn("first-rollout", text)
        self.assertIn("avg total/task", text)
        self.assertIn("best_attempt_hits", doc["icode"])
        self.assertIn("infra_excluded", doc["icode"])
        self.assertIn("avg_total_per_task", doc["icode"]["tokens"])
        self.assertNotIn("LLM only", text)
        page = report_html(doc)
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn("icode + deepseek-flash", page)
        self.assertNotIn("deepseek-v4.1-flash", page)
        self.assertIn("macro_pass@1_ci", doc["icode"])
        self.assertIn("macro_pass@1_scored_ci", doc["icode"])
        self.assertNotIn("macro_pass@1_sd", doc["icode"])
        self.assertNotIn("macro_pass@1_se", doc["icode"])
        self.assertIn("(CI)", text)
        self.assertIn("33.3%", page)
        self.assertIn("Pass@1..k (padded, missing=fail)", page)
        self.assertIn("Pass@1..k (scored-only, missing omitted)", page)
        self.assertIn("(CI)", page)
        self.assertIn("Lowest pass fraction", page)
        self.assertNotIn("Highest pass fraction", page)
        self.assertIn("Solved", page)
        self.assertIn("Unsolved", page)
        self.assertIn('class="donut"', page)
        self.assertIn("% of 3", page)
        self.assertIn('class="pair"', page)
        self.assertIn("Latency (best-attempt duration)", page)
        self.assertIn("Tokens &amp; wall clock", page)
        self.assertNotIn("~$", text)
        self.assertNotIn("cost \u03a3", text)
        self.assertNotIn("cost", doc["icode"]["tokens"])
        self.assertIn("avg_total_per_task", doc["icode"]["tokens"])
        self.assertNotIn("min-height: 360px", page)
        self.assertNotIn('class="outcome-body"', page)
        self.assertNotIn('class="scroll"', page)
        self.assertNotIn("LLM only", page)
        self.assertNotIn("<script", page)
        blocks = []
        current = []
        for line in text.splitlines():
            if line.startswith("|"):
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        self.assertGreaterEqual(len(blocks), 1)
        for block in blocks:
            self.assertEqual(len(set(len(line) for line in block)), 1)



    def test_question_memory_record_covers_every_benchmark(self):
        """One harbor run has no active question, so the memory trace is per build."""
        import tempfile
        from pathlib import Path

        import task_resources
        from render_report import copy_memory_sidecars, memory_sidecar

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            samples = iter([0.4, 1.2, None, 0.9])
            task_resources_measure = task_resources.sample_peak.__globals__
            import eval_slots

            original = eval_slots.measure_container_gb
            eval_slots.measure_container_gb = lambda: next(samples, None)
            try:
                for _ in range(4):
                    task_resources.sample_peak(harness, slots=2)
            finally:
                eval_slots.measure_container_gb = original
            del task_resources_measure
            rows = [
                json.loads(line)
                for line in (harness / "container_mem.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            # The None reading records nothing; the rest keep the running peak.
            self.assertEqual([row["peak_gb"] for row in rows], [0.4, 1.2, 0.9])
            self.assertEqual([row["slots"] for row in rows], [2, 2, 2])
            self.assertAlmostEqual(
                float((harness / "container_mem_peak_gb").read_text(encoding="utf-8")), 1.2
            )
            peak, skipped = memory_sidecar(harness)
            self.assertAlmostEqual(peak, 1.2)
            self.assertEqual(skipped, [])
            out = Path(tmp) / "out"
            out.mkdir()
            copy_memory_sidecars(harness, out)
            self.assertTrue((out / "container_mem.jsonl").is_file())
            self.assertFalse((out / "container_mem_history.jsonl").exists())

        run_all = (ROOT / "pipeline" / "stages" / "run_all.sh").read_text(encoding="utf-8")
        common = (ROOT / "pipeline" / "stages" / "_common.sh").read_text(encoding="utf-8")
        self.assertIn("MAC_K3D_PHASE", run_all)
        for name in ("deepswe", "lolbench", "swebenchpro"):
            self.assertIn(name, common)
        p5 = "\n".join(
            (STAGES / "evaluate" / name).read_text(encoding="utf-8")
            for name in ("slots.sh", "harbor_cmd.sh", "harbor_run.sh")
        )
        self.assertIn('"$HARNESS_DIR/container_mem.jsonl"', p5)
        self.assertNotIn("container_mem_history.jsonl", p5)
        self.assertIn('task_resources.py" sample', p5)
        self.assertNotIn("eval_slots.py", p5)
        self.assertIn("--override-memory-mb", p5)
        self.assertIn("out of memory", p5)
        self.assertNotIn("[[:space:]]137", p5)
        oom_re = re.compile(
            r"out of memory|Cannot allocate memory|oom-kill|oom_kill|"
            r"exit(?:ed)?\s+(?:with\s+)?(?:status|code)\s*137(?:[^0-9]|$)|"
            r"ExitCode[=:\s]*137(?:[^0-9]|$)",
            re.I,
        )
        self.assertIsNone(oom_re.search("\u2502 137    \u2502     1 \u2502"))
        self.assertIsNone(oom_re.search("============================= 137 passed in 0.93s =============================="))
        self.assertIsNotNone(oom_re.search("harbor run exited with code 137"))
        self.assertIsNotNone(oom_re.search("ExitCode: 137"))
        self.assertIsNotNone(oom_re.search("process out of memory"))


    def test_compact_rollouts_only_when_every_attempt_is_perfect(self):
        from eval_metrics import summarize_arm

        perfect = {
            "resolved": True,
            "f2p": 1.0,
            "p2p": 1.0,
            "f2p_pass": 1,
            "f2p_total": 1,
            "p2p_pass": 1,
            "p2p_total": 1,
            "partial": 1.0,
            "tok_in": 7_500_000,
            "tok_out": 82_100,
            "dur_s": 382.2,
        }
        arm = summarize_arm([("ok", [dict(perfect), dict(perfect)])], 2, 1)
        self.assertNotIn("rollouts", arm["tasks"][0])
        self.assertEqual(arm["tasks"][0]["reward"], 1.0)
        miss = dict(perfect)
        miss["f2p"] = 0.5
        miss["resolved"] = False
        kept = summarize_arm([("miss", [dict(perfect), miss])], 2, 1)
        self.assertEqual(len(kept["tasks"][0]["rollouts"]), 2)
        from render_report import summary_markdown

        doc = {
            "suite": "deepswe",
            "model": "deepseek-flash",
            "api_base": "https://api.deepseek.com",
            "run_id": "local-test",
            "n_tasks": 1,
            "n_rollouts": 2,
            "concurrency": 1,
            "cpus_each": 1,
            "icode": arm,
            "llm": arm,
        }
        text = summary_markdown(doc)
        self.assertIn("15M", text)
        self.assertIn("164.2k", text)
        self.assertIn("| 382 ", text)
        self.assertNotIn("-s", text)

    def test_duration_uses_agent_execution_then_trial_then_usage(self):
        from render_report import _attempt_from_trial

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp)
            (trial / "verifier").mkdir()
            (trial / "verifier" / "reward.json").write_text('{"reward": 1}\n', encoding="utf-8")
            (trial / "usage.json").write_text('{"duration_seconds": 1.5, "usage": {"prompt": 3, "completion": 1}}\n', encoding="utf-8")
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 9,
                        "started_at": "2026-09-23T02:27:19.693803Z",
                        "finished_at": "2026-09-23T02:30:32.293097Z",
                        "agent_execution": {
                            "started_at": "2026-09-23T02:27:19.693803Z",
                            "finished_at": "2026-09-23T02:33:41.933783Z",
                        },
                    }
                ),
                encoding="utf-8",
            )
            row = _attempt_from_trial(trial, trial / "verifier" / "reward.json")
            self.assertAlmostEqual(row["dur_s"], 382.23998, places=2)
            result = json.loads((trial / "result.json").read_text(encoding="utf-8"))
            del result["agent_execution"]
            (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
            row = _attempt_from_trial(trial, trial / "verifier" / "reward.json")
            self.assertAlmostEqual(row["dur_s"], 192.599294, places=2)
            (trial / "result.json").write_text('{"duration_seconds": 9}\n', encoding="utf-8")
            row = _attempt_from_trial(trial, trial / "verifier" / "reward.json")
            self.assertAlmostEqual(row["dur_s"], 1.5)

    def test_a_suite_that_did_not_compile_stays_unsolved_and_is_noted(self):
        from render_report import _attempt_from_trial

        reward = json.dumps(
            {
                "reward": 0,
                "f2p": 0.0,
                "f2p_pass": 0,
                "f2p_total": 5,
                "p2p": 0.0,
                "p2p_pass": 0,
                "p2p_total": 4,
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp)
            verifier = trial / "verifier"
            verifier.mkdir()
            (verifier / "reward.json").write_text(reward, encoding="utf-8")
            (verifier / "test-stdout.txt").write_text(
                "FAIL\tgithub.com/open-policy-agent/opa/v1/rego [build failed]\n",
                encoding="utf-8",
            )
            row = _attempt_from_trial(trial, verifier / "reward.json")
            self.assertFalse(row["resolved"])
            self.assertEqual(row["p2p"], 0.0)
            self.assertEqual(row["p2p_pass"], 0)
            self.assertEqual(row["p2p_total"], 4)
            self.assertEqual(row["f2p_pass"], 0)
            self.assertEqual(row["f2p_total"], 5)
            self.assertEqual(row["notes"], "suite did not compile (tests did not run)")

            (verifier / "test-stdout.txt").write_text(
                "FAIL\tTestEvalPartialFormattedOutput\n",
                encoding="utf-8",
            )
            ordinary = _attempt_from_trial(trial, verifier / "reward.json")
            self.assertFalse(ordinary["resolved"])
            self.assertEqual(ordinary["p2p_pass"], 0)
            self.assertEqual(ordinary["p2p_total"], 4)
            self.assertNotIn("suite did not compile", ordinary["notes"])

            (verifier / "test-stdout.txt").write_text("ok\n", encoding="utf-8")
            (verifier / "run.log").write_text(
                '{"Action":"fail","FailedBuild":"github.com/open-policy-agent/opa/v1/topdown"}\n',
                encoding="utf-8",
            )
            from_log = _attempt_from_trial(trial, verifier / "reward.json")
            self.assertEqual(from_log["notes"], "suite did not compile (tests did not run)")
            self.assertEqual(from_log["p2p_total"], 4)

    def test_wall_seconds_spans_attempt_timestamps(self):
        from eval_metrics import summarize_arm

        arm = summarize_arm(
            [
                (
                    "q",
                    [
                        {
                            "resolved": True,
                            "f2p": 0.5,
                            "p2p": 1.0,
                            "dur_s": 10,
                            "started_at": "2026-09-23T02:27:19Z",
                            "finished_at": "2026-09-23T02:27:29Z",
                        },
                        {
                            "resolved": False,
                            "f2p": 0.0,
                            "p2p": 1.0,
                            "dur_s": 20,
                            "started_at": "2026-09-23T02:27:19Z",
                            "finished_at": "2026-09-23T02:28:19Z",
                        },
                    ],
                )
            ],
            2,
            1,
        )
        self.assertAlmostEqual(arm["timing"]["wall_seconds"], 60.0)

    def test_padded_vs_scored_ladder_two_of_four_missing(self):
        from eval_metrics import pass_at_ladder, pass_at_ladder_scored, summarize_arm
        from render_report import summary_markdown
        from report_html import report_html

        scored = [
            {"resolved": True, "f2p": 1.0, "f2p_pass": 1, "f2p_total": 1, "p2p": 1.0, "p2p_pass": 1, "p2p_total": 1},
            {"resolved": False, "f2p": 0.0, "f2p_pass": 0, "f2p_total": 1, "p2p": 1.0, "p2p_pass": 1, "p2p_total": 1},
        ]
        clean = [
            {"resolved": False, "f2p": 0.0, "f2p_pass": 0, "f2p_total": 1, "p2p": 1.0, "p2p_pass": 1, "p2p_total": 1}
            for _ in range(4)
        ]
        padded = pass_at_ladder([[True, False], [False, False, False, False]], 4)
        self.assertAlmostEqual(padded["pass@1"], 0.5)
        self.assertAlmostEqual(padded["pass@4"], 0.5)
        scored_ladder = pass_at_ladder_scored([[True, False], [False, False, False, False], []], 4)
        self.assertAlmostEqual(scored_ladder["pass@1_scored"], 0.5)
        self.assertAlmostEqual(scored_ladder["pass@4_scored"], 0.5)
        self.assertEqual(set(scored_ladder), {f"pass@{i}_scored" for i in range(1, 5)})

        arm = summarize_arm(
            [
                ("gap", scored),
                ("clean", clean),
                ("empty", []),
            ],
            4,
            1,
        )
        gap = next(row for row in arm["tasks"] if row["id"] == "gap")
        clean_row = next(row for row in arm["tasks"] if row["id"] == "clean")
        empty = next(row for row in arm["tasks"] if row["id"] == "empty")
        self.assertEqual(gap["c"], 1)
        self.assertEqual(gap["n"], 4)
        self.assertEqual(gap["c_scored"], 1)
        self.assertEqual(gap["n_scored"], 2)
        self.assertAlmostEqual(gap["pass_frac"], 0.25)
        self.assertAlmostEqual(gap["pass_frac_scored"], 0.5)
        self.assertEqual(gap["unscored"], 2)
        self.assertAlmostEqual(gap["unscored_frac"], 0.5)
        self.assertEqual(clean_row["unscored"], 0)
        self.assertEqual(empty["unscored"], 4)
        self.assertEqual(empty["n_scored"], 0)
        self.assertAlmostEqual(arm["pass@1"], 1 / 3)
        self.assertAlmostEqual(arm["macro_pass@1"], (0.25 + 0.0 + 0.0) / 3)
        self.assertAlmostEqual(arm["pass@1_scored"], 0.5)
        self.assertAlmostEqual(arm["macro_pass@1_scored"], (0.5 + 0.0) / 2)
        self.assertEqual(arm["unscored_rollouts"], 6)
        ids = [row["id"] for row in arm["unscored_tasks"]]
        self.assertEqual(ids, ["empty", "gap"])
        self.assertNotIn("clean", ids)
        self.assertEqual(arm["pass_methods"]["padded"], "missing attempt counts as not resolved; n is N_ROLLOUTS")
        self.assertEqual(
            arm["pass_methods"]["scored"],
            "only attempts with reward.json; missing omitted from the denominator",
        )

        doc = {
            "suite": "deepswe",
            "model": "openai/deepseek-flash",
            "api_base": "https://api.deepseek.com/v1",
            "run_id": "local-test",
            "n_tasks": 3,
            "n_rollouts": 4,
            "concurrency": 1,
            "cpus_each": 1,
            "icode": arm,
        }
        stamp_official(doc)
        self.assertEqual(validate(doc), [])
        text = summary_markdown(doc)
        self.assertIn("Pass@1..k (padded, missing=fail): Pass@1 **33.3%**", text)
        self.assertIn("Pass@1..k (scored-only, missing omitted): Pass@1 **50.0%**", text)
        self.assertIn("macro Pass@1 (padded, mean of c/n): **8.3%**", text)
        self.assertIn("c_scored/n_scored", text)
        self.assertIn("## Unscored / no-response", text)
        self.assertIn("| empty", text)
        self.assertIn("| gap", text)
        self.assertNotIn("| clean", text.split("## Unscored")[-1])
        page = report_html(doc)
        self.assertIn("Unscored / no-response (not in Pass@k as a scored fail)", page)
        self.assertIn("empty", page)
        self.assertIn("2/4", page)
        self.assertIn("Pass@1..k (padded, missing=fail)", page)
        self.assertIn("Pass@1..k (scored-only, missing omitted)", page)

    def test_unscored_list_omits_clean_tasks(self):
        from eval_metrics import summarize_arm
        from report_html import report_html

        ok = {
            "resolved": True,
            "f2p": 1.0,
            "f2p_pass": 1,
            "f2p_total": 1,
            "p2p": 1.0,
            "p2p_pass": 1,
            "p2p_total": 1,
        }
        miss = {"notes": "missing reward.json", "has_reward": False}
        arm = summarize_arm(
            [
                ("ok", [dict(ok), dict(ok)]),
                ("gap", [dict(ok), miss]),
            ],
            2,
            1,
        )
        self.assertEqual([row["id"] for row in arm["unscored_tasks"]], ["gap"])
        self.assertEqual(arm["unscored_tasks"][0]["unscored"], 1)
        page = report_html(
            {
                "suite": "deepswe",
                "n_tasks": 2,
                "n_rollouts": 2,
                "icode": arm,
            }
        )
        self.assertIn("gap", page)
        tail = page.split("Unscored / no-response")[-1]
        self.assertNotIn(">ok<", tail.replace(" ", ""))

    def test_heartbeat_line_format(self):
        from eval_progress import ETA_FORMULA, heartbeat_text, progress_doc

        doc = progress_doc(
            done=12,
            needed=452,
            inflight=4,
            slots=4,
            mean_rollout_s=18 * 60,
            started_at="2026-09-24T00:00:00Z",
        )
        self.assertAlmostEqual(doc["eta_s"], (452 - 12) * 18 * 60 / 4)
        self.assertEqual(doc["eta_note"], ETA_FORMULA)
        text = heartbeat_text(doc, elapsed_s=3600)
        self.assertIn("harbor heartbeat 3600s 12/452 (2.7%)", text)
        self.assertIn("inflight=4 slots=4", text)
        self.assertNotIn("ETA", text)
        self.assertIn("PROGRESS", text)
        self.assertIn("12/452 rollouts", text)
        sample = progress_doc(
            done=12,
            needed=52,
            inflight=4,
            slots=4,
            mean_rollout_s=20 * 60,
            started_at="2026-09-24T00:00:00Z",
        )
        self.assertNotIn("ETA", heartbeat_text(sample, elapsed_s=3600))
        early = progress_doc(
            done=0,
            needed=452,
            inflight=4,
            slots=4,
            mean_rollout_s=None,
            started_at="2026-09-24T00:00:00Z",
        )
        self.assertIsNone(early["eta_s"])
        self.assertNotIn("ETA", heartbeat_text(early, elapsed_s=60))
        p5 = "\n".join(
            (STAGES / "evaluate" / name).read_text(encoding="utf-8") for name in ("slots.sh", "harbor_run.sh")
        )
        self.assertIn("progress.json", p5)
        self.assertIn("eval_progress.py", p5)
        self.assertNotIn("ETA ≈ remaining", p5)
        self.assertIn('rm -rf "$HARNESS_DIR/harbor_runs/jenkins-${BUILD_NUMBER:-local}"', p5)
        # The scheduler that owned the per-unit wait is Harbor's job now.
        self.assertNotIn('wait "$pid"', p5)
        self.assertIn('kill "$heartbeat_pid"', p5)

    def test_attempt_index_keeps_a_missing_slot(self):
        from render_report import harness_task_attempts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "harness" / "harbor_runs" / "jenkins-9" / "alpha"
            late = root / "alpha_icode_9_a02" / "trial"
            (late / "verifier").mkdir(parents=True)
            (late / "verifier" / "reward.json").write_text('{"reward": 1}\n', encoding="utf-8")
            (late / "result.json").write_text("{}\n", encoding="utf-8")
            early = root / "alpha_icode_9_a01" / "trial"
            early.mkdir(parents=True)
            (early / "result.json").write_text("{}\n", encoding="utf-8")
            now = time.time()
            os.utime(late / "verifier" / "reward.json", (now - 50, now - 50))
            os.utime(early / "result.json", (now, now))
            rows = harness_task_attempts(Path(tmp) / "harness", "alpha", 4)
        self.assertEqual(len(rows), 4)
        self.assertFalse(rows[0]["has_reward"])
        self.assertTrue(rows[1]["resolved"])
        self.assertFalse(rows[2]["has_reward"])
        self.assertFalse(rows[3]["has_reward"])

    @staticmethod
    def _trial(at: Path, task: str, reward: float | None = None, rates: dict | None = None) -> Path:
        """A trial as Harbor 0.22 writes it: task_name <org>/<id>, task_id {"path": ...}."""
        at.mkdir(parents=True, exist_ok=True)
        result = {"task_name": f"datacurve/{task}", "task_id": {"path": f"/eval-runs/deep-swe/tasks/{task}"}}
        (at / "result.json").write_text(json.dumps(result), encoding="utf-8")
        if reward is not None:
            (at / "verifier").mkdir(exist_ok=True)
            (at / "verifier" / "reward.json").write_text(
                json.dumps({"reward": reward, **(rates or {})}), encoding="utf-8"
            )
        return at

    @staticmethod
    def _artifact(harness: Path, task_ids: list[str]) -> dict:
        from render_report import build_artifact

        return build_artifact(
            suite="deepswe",
            model="deepseek-flash",
            api_base="https://example.test/v1",
            task_ids=task_ids,
            harness_dir=harness,
            baseline_dir=harness.parent / "baseline",
            n_rollouts=1,
            concurrency=1,
            cpus_each=1,
            run_id="local-test",
        )

    @staticmethod
    def _score_temp(root: Path, harness: Path, task_ids: list[str]) -> dict:
        for tid in task_ids:
            (root / "tasks" / tid).mkdir(parents=True, exist_ok=True)
        (root / "selected.txt").write_text("".join(f"{tid}\n" for tid in task_ids), encoding="utf-8")
        out = root / "score-temp.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(LIB / "score_results.py"),
                "--harness-dir",
                str(harness),
                "--baseline-dir",
                str(root / "baseline"),
                "--tasks-dir",
                str(root / "tasks"),
                "--task-file",
                str(root / "selected.txt"),
                "--n-tasks",
                str(len(task_ids)),
                "--out",
                str(out),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "BENCHMARK": "deepswe", "N_ROLLOUTS": "1"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(out.read_text(encoding="utf-8"))

    def test_each_id_harbor_records_matches_the_selected_task(self):
        from score_results import harbor_task_trials, trial_task_ids

        recorded = {
            "prefixed task_name": ({"task_name": "datacurve/alpha"}, None),
            "dict task_id": ({"task_id": {"path": "/eval-runs/deep-swe/tasks/alpha"}}, None),
            "config.json only": ({}, {"task": {"path": "/eval-runs/deep-swe/tasks/alpha"}}),
        }
        for label, (result, config) in recorded.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                harness = Path(tmp) / "harness"
                trial = harness / "harbor_runs" / "jenkins-51" / "icode_deepswe_51" / "alpha__x"
                (trial / "verifier").mkdir(parents=True)
                (trial / "verifier" / "reward.json").write_text('{"reward": 1}', encoding="utf-8")
                (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
                if config is not None:
                    (trial / "config.json").write_text(json.dumps(config), encoding="utf-8")
                self.assertIn("alpha", trial_task_ids(trial))
                self.assertEqual(harbor_task_trials(harness, "alpha"), [trial])

    def test_a_task_id_does_not_match_a_longer_one(self):
        from score_results import harbor_task_trials

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            self._trial(harness / "harbor_runs" / "jenkins-51" / "job" / "alphabet__x", "alphabet", 1.0)
            self.assertEqual(harbor_task_trials(harness, "alpha"), [])
            self.assertEqual(len(harbor_task_trials(harness, "alphabet")), 1)

    def test_the_report_scores_a_trial_named_org_slash_id(self):
        """The 2026-10-06 local run: Harbor scored 1, the report said 0% with the trial infra-excluded."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            job = harness / "harbor_runs" / "jenkins-local" / "icode_deepswe_local"
            self._trial(job / "abs-module-cache-flags__4TUicFK", "abs-module-cache-flags", 1.0)
            for pointer in (False, True):
                with self.subTest(harbor_jobs_dir=pointer):
                    if pointer:
                        (harness / "harbor_jobs_dir.txt").write_text(f"{job.parent}\n", encoding="utf-8")
                    arm = self._artifact(harness, ["abs-module-cache-flags"])["icode"]
                    self.assertEqual(arm["macro_pass@1"], 1.0)
                    self.assertEqual(arm["infra_excluded"], 0)

    def test_a_reused_jenkins_workspace_reports_only_this_build(self):
        """Builds up to PF.1 left a jobs dir named <tid> that used to win over this build's trial."""
        from score_results import harbor_task_trials

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            runs = harness / "harbor_runs"
            self._trial(
                runs / "jenkins-40" / "alpha" / "job" / "alpha_icode_40_a01" / "trial",
                "alpha",
                0.0,
                {"f2p": 0.25, "p2p": 0.5},
            )
            current = self._trial(
                runs / "jenkins-52" / "icode_deepswe_52" / "alpha__x", "alpha", 1.0, {"f2p": 1.0, "p2p": 1.0}
            )
            (harness / "harbor_jobs_dir.txt").write_text(f"{runs / 'jenkins-52'}\n", encoding="utf-8")

            self.assertEqual(
                [p.resolve() for p in harbor_task_trials(harness, "alpha")],
                [current.resolve()],
            )
            arm = self._artifact(harness, ["alpha"])["icode"]
            self.assertEqual(arm["macro_pass@1"], 1.0)
            self.assertEqual(arm["infra_excluded"], 0)
            row = self._score_temp(root, harness, ["alpha"])["tasks"][0]
            self.assertEqual((row["reward"], row["f2p"], row["p2p"]), (1.0, 1.0, 1.0))

            # This build lost its trial: unscored, never build #40's result.
            shutil.rmtree(current)
            self.assertEqual(harbor_task_trials(harness, "alpha"), [])
            row = self._score_temp(root, harness, ["alpha"])["tasks"][0]
            self.assertEqual((row["reward"], row["f2p"], row["p2p"]), (None, None, None))

    def test_a_copied_harness_still_finds_its_build(self):
        from score_results import current_jobs_dir

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "copy" / "harness"
            (harness / "harbor_runs" / "jenkins-52").mkdir(parents=True)
            pointer = harness / "harbor_jobs_dir.txt"
            pointer.write_text("/home/toby/jenkins-agent/workspace/x/eval-runs/harness/harbor_runs/jenkins-52\n")
            self.assertEqual(current_jobs_dir(harness), (harness / "harbor_runs" / "jenkins-52").resolve())
            pointer.write_text(f"{Path(tmp) / 'elsewhere'}\n")
            self.assertIsNone(current_jobs_dir(harness))
            pointer.write_text(f"{harness / 'harbor_runs'}\n")
            self.assertIsNone(current_jobs_dir(harness))

    def test_trials_are_found_in_both_the_old_and_the_one_run_layout(self):
        """Builds up to PF.1 gave every task its own jobs dir; one harbor run does not."""
        from score_results import harbor_task_trials, trial_reward_files

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            old = harness / "harbor_runs" / "jenkins-50" / "alpha" / "job"
            self._trial(old / "alpha_icode_50_a01" / "trial", "alpha", 1.0)
            self._trial(old / "alpha_icode_50_a02" / "trial", "alpha", 0.0)
            found = harbor_task_trials(harness, "alpha")
            self.assertEqual([p.parent.name for p in found], ["alpha_icode_50_a01", "alpha_icode_50_a02"])
            self.assertEqual(len(trial_reward_files(found)), 2)

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            job = harness / "harbor_runs" / "jenkins-51" / "icode_deepswe_51"
            self._trial(job / "alpha__a01" / "trial", "alpha", 1.0)
            self._trial(job / "beta__a01" / "trial", "beta", 0.0)
            self._trial(job / "alpha__a02" / "trial", "alpha", 0.0)
            alpha = harbor_task_trials(harness, "alpha")
            self.assertEqual(len(alpha), 2)
            self.assertEqual({p.parent.name for p in alpha}, {"alpha__a01", "alpha__a02"})
            self.assertEqual(len(harbor_task_trials(harness, "beta")), 1)
            self.assertEqual(harbor_task_trials(harness, "gamma"), [])

    def test_a_merged_harness_unions_rollouts_in_plan_order(self):
        """Pass@1 is the first rollout, even when a later rollout finished in an earlier build."""
        from score_results import harbor_task_trials

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            runs = harness / "harbor_runs"
            second = self._trial(runs / "jenkins-51" / "job" / "alpha__a01" / "trial", "alpha", 0.0)
            first = self._trial(runs / "jenkins-52" / "job" / "alpha__a01" / "trial", "alpha", 1.0)
            (runs / "jenkins-51" / "rollout.txt").write_text("2\n", encoding="utf-8")
            (runs / "jenkins-52" / "rollout.txt").write_text("1\n", encoding="utf-8")
            self.assertEqual(harbor_task_trials(harness, "alpha"), [first, second])

    def test_a_trial_without_a_verifier_dir_still_yields_its_reward(self):
        from score_results import trial_reward_files

        with tempfile.TemporaryDirectory() as tmp:
            flat = Path(tmp) / "flat"
            flat.mkdir()
            (flat / "reward.json").write_text('{"reward": 1}', encoding="utf-8")
            nested = Path(tmp) / "nested" / "deep" / "down"
            nested.mkdir(parents=True)
            (nested / "reward.json").write_text('{"reward": 0}', encoding="utf-8")
            empty = Path(tmp) / "empty"
            empty.mkdir()
            got = trial_reward_files([flat, Path(tmp) / "nested", empty])
            self.assertEqual(got, [flat / "reward.json", nested / "reward.json"])

    def test_archive_backs_up_outside_the_workspace(self):
        from render_report import run_folder_name

        p8 = "\n".join(
            (STAGES / name).read_text(encoding="utf-8") for name in ("report/render.sh", "archive/backup.sh")
        )
        self.assertNotIn("score_results.py", p8)
        self.assertIn("MAC_K3D_OUTPUT_ROOT", p8)
        self.assertEqual(run_folder_name("20260923T023527Z", ["abs-module-cache-flags"]), "20260923T023527Z-abs-module-cache-flags")
        self.assertEqual(run_folder_name("20260923T023527Z", ["a", "b"]), "20260923T023527Z-n2")
        self.assertEqual(run_folder_name("20260923T023527Z", ["a", "b"], "23"), "jenkins-23-20260923T023527Z")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("\n/output/\n", gitignore)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "eval-runs"
            trial = work / "harness" / "harbor_runs" / "jenkins-1" / "alpha" / "alpha__trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier" / "reward.json").write_text('{"reward": 1}\n', encoding="utf-8")
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "agent_execution": {
                            "started_at": "2026-09-23T02:27:19.693803Z",
                            "finished_at": "2026-09-23T02:33:41.933783Z",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (trial / "agent.patch").write_text("diff\n", encoding="utf-8")
            missed = work / "harness" / "harbor_runs" / "jenkins-1" / "alpha" / "alpha__missed"
            missed.mkdir()
            (missed / "result.json").write_text("{}\n", encoding="utf-8")
            (trial / ".harbor-env").write_text("DEEPSEEK_API_KEY=secret\n", encoding="utf-8")
            (work / "selected_tasks.txt").write_text("alpha\n", encoding="utf-8")
            attempt = work / "baseline" / "alpha" / "attempt-01"
            (attempt / "verifier").mkdir(parents=True)
            (attempt / "verifier" / "reward.json").write_text('{"reward": 0}\n', encoding="utf-8")
            (attempt / ".harbor-env").write_text("DEEPSEEK_API_KEY=secret\n", encoding="utf-8")
            (attempt / "agent.patch").write_text("patch\n", encoding="utf-8")
            (work / "harness" / "container_mem.jsonl").write_text(
                json.dumps({"question": "alpha", "peak_gb": 1.5, "slots": 1, "memory_mb": 0}) + "\n",
                encoding="utf-8",
            )
            (work / "harness" / "skipped_questions.txt").write_text("beta\n", encoding="utf-8")
            (work / "eval_protocol_inputs.json").write_text(
                json.dumps(official_protocol_inputs()) + "\n", encoding="utf-8"
            )
            (work / "icode_git.json").write_text(
                json.dumps(
                    {
                        "url": "https://gitcode.com/michaelling/jiuwenicode",
                        "kind": "commit",
                        "ref": "eea9d66dd00f137c3400b79c1c8574d8ae7debd1",
                        "sha": "eea9d66dd00f137c3400b79c1c8574d8ae7debd1",
                        "subject": "pin",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            anticheat = work / "harness" / "anticheat"
            anticheat.mkdir(parents=True)
            (anticheat / "summary.json").write_text(
                json.dumps({"status": "ok", "version": "mac-k3d-anticheat-v1", "counts": {"clean": 1}}) + "\n",
                encoding="utf-8",
            )
            backup = root / "backup"
            env = os.environ.copy()
            env.pop("BUILD_NUMBER", None)
            env.pop("ICODE_API_BASE", None)
            env.pop("OFFICIAL", None)
            env.update(
                {
                    "MAC_K3D_EVAL_WORKDIR": str(work),
                    "MAC_K3D_OUTPUT_ROOT": str(backup),
                    "HOME": str(root / "home"),
                    "BENCHMARK": "deepswe",
                    "N_TASKS": "1",
                    "N_ROLLOUTS": "1",
                    "CPU_LOCK_QTY": "1",
                    "HARNESS": "icode",
                    "LLM": "deepseek",
                    "DEEPSEEK_MODEL": "deepseek-flash",
                }
            )
            proc = subprocess.run(
                ["bash", "-c", 'bash "$1/report.sh" && bash "$1/archive.sh"', "_", str(STAGES)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            runs = list((backup / "deepswe").iterdir())
            self.assertEqual(len(runs), 1)
            archive = runs[0]
            self.assertTrue(archive.name.endswith("-alpha.tar.gz"), archive.name)
            folder = archive.name[: -len(".tar.gz")]
            with tarfile.open(archive, "r:gz") as tar:
                names = tar.getnames()
                self.assertIn(f"{folder}/artifact.json", names)
                self.assertIn(f"{folder}/summary.md", names)
                self.assertIn(f"{folder}/report.html", names)
                self.assertIn(f"{folder}/container_mem.jsonl", names)
                self.assertIn(f"{folder}/skipped_questions.txt", names)
                self.assertIn(f"{folder}/cost-token-report.md", names)
                self.assertIn(f"{folder}/icode/alpha/attempt-01/result.json", names)
                self.assertIn(f"{folder}/icode/alpha/attempt-02/result.json", names)
                self.assertIn(f"{folder}/icode/alpha/attempt-01/agent.patch", names)
                self.assertFalse(any(".harbor-env" in name or name.endswith(".pdf") for name in names))
                self.assertFalse(any("eval-icode-deepseek-" in name for name in names))
                written = json.loads(tar.extractfile(f"{folder}/artifact.json").read().decode("utf-8"))
                html_report = tar.extractfile(f"{folder}/report.html").read().decode("utf-8")
                skip_txt = tar.extractfile(f"{folder}/skipped_questions.txt").read().decode("utf-8")
            self.assertTrue(written["run_dir"].endswith(f"{folder}.tar.gz"))
            self.assertNotIn("llm", written)
            self.assertEqual(written["model"], "openai/deepseek-flash")
            self.assertEqual(written["api_base"], "https://api.deepseek.com/v1")
            self.assertAlmostEqual(written["container_mem_max_gb"], 1.5)
            self.assertEqual(written["skipped_questions"], ["beta"])
            self.assertEqual(skip_txt.strip(), "beta")
            self.assertTrue(html_report.startswith("<!DOCTYPE html>"))
            self.assertIn("icode + deepseek-flash", html_report)
            self.assertNotIn("deepseek-v4.1-flash", html_report)
            self.assertAlmostEqual(written["icode"]["tasks"][0]["dur_s"], 382.23998, places=2)
            self.assertEqual(validate(written), [])
            workspace_run = work / "output" / "deepswe" / folder
            self.assertTrue((workspace_run / "artifact.json").is_file())
            self.assertTrue((workspace_run / "summary.md").is_file())
            self.assertTrue((workspace_run / "report.html").is_file())
            self.assertTrue((workspace_run / "container_mem.jsonl").is_file())
            self.assertTrue((workspace_run / "skipped_questions.txt").is_file())
            self.assertFalse((workspace_run / "report.pdf").exists())
            last = (work / "last_output.txt").read_text(encoding="utf-8").strip()
            self.assertTrue(last.endswith(f"{folder}/**"), last)
            self.assertFalse(any((work / "reports").glob("eval-*.json")))


class CostTokenReportTests(unittest.TestCase):
    def test_render_and_build_locator(self):
        from cost_token_report import estimate_cost, main, render_report, resolve_run_dir

        doc = {
            "suite": "deepswe",
            "model": "openai/deepseek-flash",
            "run_id": "jenkins-99",
            "run_label": "deepswe-99",
            "n_tasks": 2,
            "n_rollouts": 4,
            "icode": {
                "tokens": {
                    "tasks_with_data": 2,
                    "in": 2_000_000,
                    "out": 1_000_000,
                    "total": 3_000_000,
                    "avg_total_per_task": 1_500_000,
                },
                "timing": {"wall_seconds": 3661},
                "tasks": [
                    {"id": "heavy", "tok_in": 1_500_000, "tok_out": 800_000},
                    {"id": "light", "tok_in": 500_000, "tok_out": 200_000},
                ],
            },
        }
        md = render_report(doc, folder_label="output/deepswe/jenkins-99-demo")
        self.assertIn("deepswe-99", md)
        self.assertIn("2,000,000", md)
        self.assertIn("1.50M", md)
        self.assertIn("heavy", md)
        self.assertIn("~$0.90", md)  # off-peak 0% hit: 2*0.15 + 1*0.60 = 0.90
        off = estimate_cost(
            input_tokens=2_000_000,
            output_tokens=1_000_000,
            band={"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60},
            hit_share=0.0,
        )
        self.assertAlmostEqual(off["total"], 0.90, places=4)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run = repo / "output" / "deepswe" / "jenkins-99-20260101T000000Z"
            run.mkdir(parents=True)
            (run / "artifact.json").write_text(json.dumps(doc), encoding="utf-8")
            run_dir, tar_path, loaded = resolve_run_dir(
                repo=repo,
                run_dir=None,
                suite="deepswe",
                build="99",
                run=None,
                tar=None,
            )
            self.assertIsNone(tar_path)
            self.assertEqual(run_dir, run)
            self.assertEqual(loaded["run_label"], "deepswe-99")
            out = repo / "report.md"
            rc = main(
                [
                    "--repo",
                    str(repo),
                    "--suite",
                    "deepswe",
                    "--build",
                    "99",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("**~$0.90**", text)
            self.assertIn("~$1.80", text)  # peak 0% hit


class ProvenanceTests(unittest.TestCase):
    def _stub_bin(self, directory: Path, name: str, body: str) -> None:
        path = directory / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _run_env_harbor(self, version_line: str) -> subprocess.CompletedProcess[str]:
        tmp = Path(self._p1_tmp)
        bindir = tmp / "bin"
        bindir.mkdir()
        work = tmp / "work"
        uv_log = tmp / "uv.log"
        self._stub_bin(
            bindir,
            "uv",
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$UV_LOG\"\nexit 0\n",
        )
        self._stub_bin(
            bindir,
            "harbor",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            f"  printf '%s\\n' '{version_line}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
        )
        env = os.environ.copy()
        env["HOME"] = str(tmp)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        env["MAC_K3D_EVAL_WORKDIR"] = str(work)
        env["UV_LOG"] = str(uv_log)
        env["HARBOR_VERSION"] = "0.22.0"
        env["BENCHMARK"] = "deepswe"
        env.pop("OFFICIAL", None)
        return subprocess.run(
            ["bash", str(STAGES / "env" / "harbor.sh")],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_env_harbor_reinstalls_and_rejects_a_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._p1_tmp = tmp
            proc = self._run_env_harbor("harbor 9.9.9")
            log = Path(tmp) / "uv.log"
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("wanted 0.22.0", proc.stderr)
            self.assertIn("tool install --force harbor==0.22.0", log.read_text(encoding="utf-8"))
            self.assertFalse((Path(tmp) / "work" / "harbor_version.txt").is_file())

    def test_env_harbor_keeps_a_matching_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._p1_tmp = tmp
            proc = self._run_env_harbor("0.22.0")
            log = Path(tmp) / "uv.log"
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertFalse(log.exists(), "a matching harbor must not be reinstalled")
            recorded = (Path(tmp) / "work" / "harbor_version.txt").read_text(encoding="utf-8").strip()
            self.assertEqual(recorded, "0.22.0")

    def test_pipeline_facts_reads_build_json(self):
        from provenance import pipeline_facts, provenance_markdown

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pipeline").mkdir()
            (root / "pipeline" / "BUILD.json").write_text(
                json.dumps(
                    {
                        "version": "0.5.2",
                        "commit": "84c66ededd24aa",
                        "dirty": False,
                        "pipeline_hash": "00000000deadbeef",
                        "binary": "/home/u/.local/bin/mac-k3d",
                    }
                ),
                encoding="utf-8",
            )
            got = pipeline_facts(root)
            self.assertEqual(
                got,
                {
                    "source": "binary",
                    "version": "0.5.2",
                    "commit": "84c66ededd24aa",
                    "dirty": False,
                    "pipeline_hash": "00000000deadbeef",
                },
            )
            self.assertNotIn("url", got)
            lines = provenance_markdown({"pipeline": got})
            self.assertIn(
                "- Pipeline: `84c66ededd24aa` dirty `false` from `binary` hash `00000000deadbeef`", lines
            )

    def test_pipeline_facts_without_a_known_commit_leaves_it_empty(self):
        from provenance import pipeline_facts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pipeline").mkdir()
            (root / "pipeline" / "BUILD.json").write_text(
                json.dumps({"version": "0.5.2", "commit": "unknown", "dirty": None, "pipeline_hash": "ab"}),
                encoding="utf-8",
            )
            got = pipeline_facts(root)
            self.assertEqual(got["commit"], "")
            self.assertIsNone(got["dirty"])
            # Neither BUILD.json nor git: say so instead of guessing.
            (root / "pipeline" / "BUILD.json").unlink()
            self.assertEqual(pipeline_facts(root), {"source": "unknown", "commit": "", "dirty": None})

    def test_pipeline_facts_falls_back_to_a_git_checkout(self):
        from provenance import pipeline_facts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
            subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
            (root / "a.txt").write_text("a\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True, env=env)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "a"], check=True, env=env)
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(pipeline_facts(root), {"source": "checkout", "commit": head, "dirty": False})
            (root / "a.txt").write_text("b\n", encoding="utf-8")
            self.assertTrue(pipeline_facts(root)["dirty"])

    def test_every_run_refuses_a_dirty_or_mixed_pipeline(self):
        from check_report import official_provenance_errors

        dirty = official_provenance_errors({"eval_protocol": {"pipeline": {"commit": "abc", "dirty": True}}})
        clean = official_provenance_errors({"eval_protocol": {"pipeline": {"commit": "abc", "dirty": False}}})
        mixed = official_provenance_errors(
            {"pipeline_status": "mixed", "eval_protocol": {"pipeline": {"commit": "abc", "dirty": False}}}
        )
        self.assertTrue(any("clean pipeline commit" in e for e in dirty))
        self.assertFalse(any("clean pipeline commit" in e or "pipeline_status" in e for e in clean))
        self.assertTrue(any("pipeline_status mixed" in e for e in mixed))

    def test_task_count_helper(self):
        from provenance import assert_task_count, count_task_dirs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            (root / "notes.txt").write_text("x", encoding="utf-8")
            self.assertEqual(count_task_dirs(root), 3)
            self.assertEqual(assert_task_count(root, 3), 3)
            with self.assertRaises(SystemExit) as caught:
                assert_task_count(root, 4)
            self.assertEqual(caught.exception.code, 1)

    def test_pin_benchmark_sha_checks_out_and_refuses_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "origin"
            dest = root / "bench"
            origin.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.email", "t@t"], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.name", "t"], check=True)
            (origin / "f").write_text("a\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(origin), "add", "f"], check=True)
            subprocess.run(["git", "-C", str(origin), "commit", "-q", "-m", "base"], check=True)
            first = subprocess.check_output(["git", "-C", str(origin), "rev-parse", "HEAD"], text=True).strip()
            (origin / "f").write_text("b\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(origin), "commit", "-q", "-am", "next"], check=True)
            second = subprocess.check_output(["git", "-C", str(origin), "rev-parse", "HEAD"], text=True).strip()
            env = os.environ.copy()
            env["MAC_K3D_EVAL_WORKDIR"] = str(root / "eval")
            env.pop("OFFICIAL", None)
            common = ROOT / "pipeline" / "stages" / "_common.sh"

            def pin(sha: str) -> subprocess.CompletedProcess[str]:
                script = f'source "{common}" && pin_benchmark_sha "{dest}" "{origin}" "{sha}" && git -C "{dest}" rev-parse HEAD'
                return subprocess.run(
                    ["bash", "-c", script],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

            first_pin = pin(first)
            self.assertEqual(first_pin.returncode, 0, first_pin.stdout + first_pin.stderr)
            self.assertEqual(first_pin.stdout.strip().splitlines()[-1], first)
            (dest / "f").write_text("local\n", encoding="utf-8")
            stay = pin(first)
            self.assertEqual(stay.returncode, 0, stay.stdout + stay.stderr)
            self.assertEqual((dest / "f").read_text(encoding="utf-8"), "local\n")
            move = pin(second)
            self.assertEqual(move.returncode, 0, move.stdout + move.stderr)
            self.assertEqual(move.stdout.strip().splitlines()[-1], second)
            self.assertEqual((dest / "f").read_text(encoding="utf-8"), "b\n")

    def test_build_eval_protocol_copies_provenance(self):
        from render_report import build_artifact, build_eval_protocol, demo_artifact, summary_markdown
        from report_html import report_html

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            harness = work / "harness"
            baseline = work / "baseline"
            harness.mkdir()
            baseline.mkdir()
            (work / "icode_release.json").write_text(
                json.dumps({"filename": "icode-full.tar.gz", "sha256": "abc123"}) + "\n",
                encoding="utf-8",
            )
            (work / "eval_protocol_inputs.json").write_text(
                json.dumps(
                    {
                        "model": "deepseek-flash",
                        "api_base": "https://example.test/v1",
                        "provider": "DeepSeek",
                        "reasoning_effort": "high",
                        "cpu_lock_qty": 4,
                        "concurrency": 1,
                        "cpus_each": 1,
                        "harbor": {"version": "0.22.0"},
                        "benchmark": {
                            "url": "https://github.com/datacurve-ai/deep-swe",
                            "sha": "0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea",
                            "task_count": 113,
                            "tasks": {
                                "alpha": {
                                    "task_toml_sha256": "tomlhash",
                                    "tests_list_sha256": "testshash",
                                }
                            },
                        },
                        "grader_overlay": {
                            "marker": "mac-k3d-lolbench-fix-rewards-v1",
                            "sha256": "overlayhash",
                            "applied": False,
                        },
                        "images": {
                            "alpha": {
                                "image": "example:alpha",
                                "id": "sha256:image",
                                "repo_digests": ["example@sha256:image"],
                            }
                        },
                        "worker": {
                            "node": "worker-a",
                            "nproc": 16,
                            "memory_kb": 14000000,
                            "docker_version": "27.0.0",
                            "kernel": "7.0.0",
                            "cpu_model": "Test CPU",
                        },
                        "requester": {"user": "unknown", "build_url": "unknown"},
                        "pipeline": {"commit": "abcdef", "dirty": False},
                        "isolation": valid_isolation(),
                    }
                ),
                encoding="utf-8",
            )
            protocol = build_eval_protocol(
                workdir=work,
                model="deepseek-flash",
                api_base="https://example.test/v1",
                n_rollouts=1,
                concurrency=1,
                cpus_each=1,
            )
            self.assertEqual(protocol["harbor"]["version"], "0.22.0")
            self.assertEqual(protocol["benchmark"]["sha"], "0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea")
            self.assertEqual(protocol["benchmark"]["tasks"]["alpha"]["task_toml_sha256"], "tomlhash")
            self.assertEqual(protocol["grader_overlay"]["marker"], "mac-k3d-lolbench-fix-rewards-v1")
            self.assertEqual(protocol["images"]["alpha"]["id"], "sha256:image")
            self.assertEqual(protocol["worker"]["nproc"], 16)
            self.assertEqual(protocol["requester"]["user"], "unknown")
            self.assertEqual(protocol["pipeline"]["dirty"], False)
            self.assertEqual(protocol["isolation"]["runtime_sha256"], "b" * 64)
            self.assertEqual(protocol["icode"]["release"]["sha256"], "abc123")
            artifact = build_artifact(
                suite="deepswe",
                model="deepseek-flash",
                api_base="https://example.test/v1",
                task_ids=[],
                harness_dir=harness,
                baseline_dir=baseline,
                n_rollouts=1,
                concurrency=1,
                cpus_each=1,
                run_id="local-prov",
                eval_protocol=protocol,
            )
            self.assertEqual(artifact["icode_release"]["filename"], "icode-full.tar.gz")
            doc = demo_artifact()
            doc["eval_protocol"] = protocol
            text = summary_markdown(doc)
            page = report_html(doc)
            self.assertIn("- iCode under test:", text)
            self.assertNotIn("iCode version", text)
            self.assertIn("iCode under test:", page)
            self.assertNotIn("iCode version", page)
            self.assertIn("Provenance", text)
            self.assertIn("0.22.0", text)
            self.assertIn("tomlhash", text)
            self.assertIn(f"runtime sha256 {'b' * 64}", text)
            self.assertIn("Agent mount: /opt/icode-host read-only true (1 mount)", text)
            self.assertIn("Leak scan: mac-k3d-leakscan-v1 · hit tasks none · clean 1", text)
            self.assertIn("Provenance", page)
            self.assertIn("sha256:image", page)
            self.assertIn(f"runtime sha256 {'b' * 64}", page)
            # Provenance closes both files, after the results and the Unscored section.
            results_end = max(text.find("unscored rollouts: 0"), text.find("## Unscored"))
            self.assertGreater(text.index("### Provenance"), results_end)
            self.assertTrue(text.rstrip().endswith("| alpha | sha256:image | tomlhash | testshash |"), text[-300:])
            self.assertGreater(page.index("<h2>Provenance</h2>"), page.index("<h2>Pass fraction</h2>"))
            doc["anticheat"] = {"status": "ok", "version": "v1", "counts": {"clean": 4}}
            last = summary_markdown(doc).rstrip().splitlines()[-1]
            self.assertTrue(last.startswith("- Anti-cheat `v1`: clean **4**"), last)
            self.assertIn("(metrics in this summary are official; see anticheat/report.md)", last)

    def test_official_missing_harbor_version_fails_check(self):
        from render_report import demo_artifact

        doc = demo_artifact()
        doc["eval_protocol"] = {
            "icode": {"mode": "git"},
            "model_params": {
                "provider": "DeepSeek",
                "reasoning_effort": "high",
                "thinking": {"type": "enabled"},
            },
            "resources": {},
            "benchmark": {
                "url": "https://github.com/datacurve-ai/deep-swe",
                "sha": "0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea",
                "task_count": 113,
                "tasks": {},
            },
            "grader_overlay": {"marker": "mac-k3d-lolbench-fix-rewards-v1", "sha256": "abc"},
            "images": {},
            "worker": {
                "node": "n",
                "nproc": 1,
                "memory_kb": 1,
                "docker_version": "27",
                "kernel": "7",
                "cpu_model": "cpu",
            },
            "requester": {"user": "unknown", "build_url": "unknown"},
            "pipeline": {"commit": "abcdef", "dirty": False},
            "isolation": valid_isolation(),
        }
        doc["anticheat"] = {"status": "ok", "version": "mac-k3d-anticheat-v1"}
        doc["icode_git"] = {
            "url": "https://gitcode.com/michaelling/jiuwenicode",
            "kind": "commit",
            "ref": "eea9d66dd00f137c3400b79c1c8574d8ae7debd1",
            "sha": "eea9d66dd00f137c3400b79c1c8574d8ae7debd1",
        }
        errors = validate(doc)
        self.assertTrue(any("eval_protocol.harbor.version" in err for err in errors), errors)
        doc["eval_protocol"]["harbor"] = {"version": "0.22.0"}
        self.assertEqual(validate(doc), [])

    def test_official_isolation_rules(self):
        from check_report import official_isolation_errors

        self.assertEqual(official_isolation_errors(valid_isolation()), [])
        self.assertEqual(official_isolation_errors(None), ["missing eval_protocol.isolation"])
        cases = [
            ("mode", "binary", "mode must be git or release"),
            ("sanitizer", "", "missing eval_protocol.isolation.sanitizer"),
            ("sourceless", False, "sourceless must be true"),
            ("tree_sha256", "short", "tree_sha256 must be a sha256"),
            ("runtime_sha256", "", "runtime_sha256 must be a sha256"),
            ("manifest_matches_tree", False, "manifest_matches_tree must be true"),
        ]
        for key, value, expected in cases:
            iso = valid_isolation()
            iso[key] = value
            errors = official_isolation_errors(iso)
            self.assertTrue(any(expected in err for err in errors), (key, errors))
        for mount in (
            {"count": 1, "target": "/opt/icode-host", "read_only": False},
            {"count": 2, "target": "/opt/icode-host", "read_only": True},
            {"count": 1, "target": "/elsewhere", "read_only": True},
        ):
            iso = valid_isolation()
            iso["mount"] = mount
            self.assertTrue(any("one read-only mount" in err for err in official_isolation_errors(iso)), mount)
        iso = valid_isolation()
        iso["leak_scan"]["hit_tasks"] = ["cpython_5"]
        # A leak hit with no trial was skipped, so nothing leaked.
        skipped = {"icode": {"tasks": [{"id": "cpython_5", "n_scored": 0, "rollouts": []}]}}
        self.assertEqual(official_isolation_errors(iso, skipped), [])
        ran = {"icode": {"tasks": [{"id": "cpython_5", "n_scored": 0, "rollouts": [{"trial": "cpython_5__a"}]}]}}
        self.assertIn(
            "eval_protocol.isolation.leak_scan: hit questions have trials (cpython_5)",
            official_isolation_errors(iso, ran),
        )
        scored = {"icode": {"tasks": [{"id": "cpython_5", "n_scored": 4}]}}
        self.assertTrue(any("hit questions have trials" in err for err in official_isolation_errors(iso, scored)))
        iso = valid_isolation()
        del iso["leak_scan"]
        self.assertTrue(any("missing eval_protocol.isolation.leak_scan" in err for err in official_isolation_errors(iso)))

    def test_official_isolation_requires_passing_canary(self):
        from check_report import official_isolation_errors

        self.assertEqual(official_isolation_errors(valid_isolation()), [])
        iso = valid_isolation()
        del iso["canary"]
        self.assertTrue(any("missing eval_protocol.isolation.canary" in err for err in official_isolation_errors(iso)))
        iso = valid_isolation()
        iso["canary"]["status"] = "fail"
        iso["canary"]["failed_tasks"] = ["alpha"]
        self.assertTrue(any("canary.status must be pass" in err for err in official_isolation_errors(iso)))

    def test_release_mode_checks_the_binary_sha256_and_skips_the_git_runtime(self):
        from check_report import official_isolation_errors, official_provenance_errors
        from render_report import icode_under_test

        iso = valid_isolation()
        iso["mode"] = "release"
        iso["sanitizer"] = ""
        iso["sourceless"] = None
        iso["tree_sha256"] = ""
        iso["runtime_sha256"] = ""
        iso["manifest_matches_tree"] = None
        self.assertEqual(official_isolation_errors(iso), [])
        digest = "e" * 64
        doc = {
            "eval_protocol": {
                "isolation": iso,
                "icode": {"mode": "release", "release": {"filename": "icode-drop", "sha256": digest}},
            }
        }
        errors = official_provenance_errors(doc)
        self.assertFalse(any("icode.release.sha256" in err or "missing icode_git" in err for err in errors), errors)
        bare = {"eval_protocol": {"isolation": iso, "icode": {"mode": "release"}}}
        self.assertTrue(any("release.sha256" in err for err in official_provenance_errors(bare)))
        self.assertEqual(
            icode_under_test(doc["eval_protocol"]),
            f"release icode-drop sha256 {digest}",
        )

    def test_a_merged_report_carries_each_shards_hashes_in_its_rows(self):
        from check_report import official_isolation_errors

        iso = valid_isolation()
        del iso["leak_scan"]["report_sha256"]
        del iso["canary"]["summary_sha256"]
        rows = [
            {"shard": 1, "leak_scan_sha256": "a", "canary": {"status": "pass", "summary_sha256": "c"}},
            {"shard": 2, "leak_scan_sha256": "b", "canary": {"status": "pass", "summary_sha256": "d"}},
            {"shard": 3, "result": "FAILURE", "not_run": ["gamma"]},
        ]
        doc = {"eval_protocol": {"shards": rows}}
        self.assertEqual(official_isolation_errors(iso, doc), [])
        del rows[1]["leak_scan_sha256"]
        self.assertIn("missing eval_protocol.isolation.leak_scan", official_isolation_errors(iso, doc))
        self.assertIn("missing eval_protocol.isolation.leak_scan", official_isolation_errors(iso))

    def test_inputs_keep_image_ids_only_for_this_runs_tasks(self):
        """deepswe_some_task #5: the reused workspace's image table listed every earlier build's tasks."""
        from unittest import mock

        import provenance

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks"
            for tid in ("alpha", "beta"):
                (tasks / tid).mkdir(parents=True)
            selected = root / "selected_tasks.txt"
            selected.write_text("alpha\nbeta\n", encoding="utf-8")
            out = root / "eval_protocol_inputs.json"
            out.write_text(
                json.dumps(
                    {
                        "images": {
                            "alpha": {"image": "alpha:latest", "id": "sha256:alpha"},
                            "old": {"image": "old:latest", "id": "sha256:old"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            found = {"beta": "sha256:beta"}

            def inspect(task_dir: Path) -> dict:
                return {"image": f"{task_dir.name}:latest", "id": found.get(task_dir.name, ""), "repo_digests": []}

            with mock.patch.object(provenance, "inspect_task", side_effect=inspect):
                doc = provenance.write_protocol_inputs(
                    out=out,
                    repo_dir=root / "repo",
                    tasks_dir=tasks,
                    selected_file=selected,
                    overlay=root / "overlay.patch",
                    applied=False,
                    workdir=root,
                    pipeline_root=root,
                )
        self.assertEqual(sorted(doc["images"]), ["alpha", "beta"])
        # A task inspected again without an id keeps the id an earlier build recorded.
        self.assertEqual(doc["images"]["alpha"]["id"], "sha256:alpha")
        self.assertEqual(doc["images"]["beta"]["id"], "sha256:beta")

    def test_requester_is_the_jenkins_user_or_the_local_login(self):
        from unittest import mock

        from provenance import requester_facts

        url = "http://jenkins:8080/job/deepswe_one_task/70/"
        cases = [
            ({"BUILD_USER": "Toby", "BUILD_URL": url}, {"user": "Toby", "build_url": url}),
            ({"BUILD_URL": url, "USER": "jenkins"}, {"user": "unknown", "build_url": url}),
            ({"USER": "Toby"}, {"user": "local Toby", "build_url": "unknown"}),
        ]
        for env, want in cases:
            clean = {k: v for k, v in os.environ.items() if k not in ("BUILD_USER", "BUILD_USER_ID", "BUILD_URL", "USER", "LOGNAME")}
            with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
                self.assertEqual(requester_facts(), want, env)

    def test_env_clears_the_files_a_shard_archives_but_keeps_overrides(self):
        text = (STAGES / "env.sh").read_text(encoding="utf-8")
        start = text.index('rm -f "$WORKDIR/eval_protocol_inputs.json"')
        self.assertLess(start, text.index("run_steps"))
        lines = text[start:].splitlines()
        end = next(i for i, line in enumerate(lines) if not line.rstrip().endswith("\\"))
        command = "\n".join(lines[: end + 1])
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "eval-runs"
            harness = work / "harness"
            anticheat = harness / "anticheat"
            anticheat.mkdir(parents=True)
            per_build = [
                work / "eval_protocol_inputs.json",
                work / "eval_resources.json",
                work / "egress_probe.json",
                work / "selected_tasks.txt",
                work / "selected_tasks_offset.txt",
                work / "suite_tasks.txt",
                work / "icode_git.json",
                work / "icode_release.json",
                work / "icode_bin_path.txt",
                work / "icode_host_root.txt",
                anticheat / "summary.json",
                anticheat / "report.md",
                anticheat / "anticheat.jsonl",
            ]
            for path in per_build:
                path.write_text("from build 58\n", encoding="utf-8")
            kept = [harness / "anticheat_overrides.json", harness / "harbor_runs" / "jenkins-58" / "result.json"]
            for path in kept:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            proc = subprocess.run(
                ["bash", "-c", command],
                env={**os.environ, "WORKDIR": str(work), "HARNESS_DIR": str(harness)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual([p.name for p in per_build if p.exists()], [])
            self.assertTrue(all(p.exists() for p in kept))


class AgentIsolationTests(unittest.TestCase):
    SENTINEL_KEY = "sk-sentinel-deepseek-0000"
    SENTINEL_TOKEN = "gc-sentinel-token-0000"

    def _snapshot(self, root: Path) -> dict:
        out = {}
        for path in sorted(root.rglob("*")):
            st = path.lstat()
            data = b""
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()
            out[path.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size, data)
        return out

    def _write(self, path: Path, text: str = "VALUE = 1\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_sanitizer_strips_deliverables_and_is_idempotent(self):
        from icode_sanitize import MANIFEST_NAME, SANITIZER_VERSION, sanitize_tree

        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "icode-src"
            sandbox = tree / ".venv" / "sandbox-cpython"
            stdlib = sandbox / "lib" / "python3.13"
            venv_site = tree / ".venv" / "lib" / "python3.13" / "site-packages"
            (sandbox / "bin").mkdir(parents=True)
            (sandbox / "bin" / "python3").symlink_to(sys.executable)
            for rel in (
                "tomllib/__init__.py",
                "tomllib/_parser.py",
                "zoneinfo/__init__.py",
                "zoneinfo/_common.py",
                "test/test_tomllib.py",
                "idlelib/idle_test/test_editor.py",
                "idlelib/editor.py",
                "typing.py",
                "site-packages/pip/_vendor/tomli/__init__.py",
                "site-packages/pip/__init__.py",
            ):
                self._write(stdlib / rel)
            for rel in (
                "setuptools/_vendor/tomli/_parser.py",
                "setuptools/_vendor/tomli-2.4.0.dist-info/METADATA",
                "setuptools/_vendor/tomli_w/__init__.py",
                "tomli-2.0.1.dist-info/METADATA",
                "backports/zoneinfo/__init__.py",
                "httpx/__init__.py",
            ):
                self._write(venv_site / rel)
            self._write(tree / "openjiuwen_icode" / "__init__.py")

            doc = sanitize_tree(tree)
            self.assertIsNotNone(doc)
            for gone in (
                stdlib / "tomllib" / "__init__.py",
                stdlib / "tomllib" / "_parser.py",
                stdlib / "test",
                stdlib / "idlelib" / "idle_test",
                stdlib / "zoneinfo" / "__init__.py",
                stdlib / "zoneinfo" / "_common.py",
                stdlib / "site-packages" / "pip" / "_vendor" / "tomli",
                venv_site / "setuptools" / "_vendor" / "tomli",
                venv_site / "setuptools" / "_vendor" / "tomli-2.4.0.dist-info",
                venv_site / "tomli-2.0.1.dist-info",
                venv_site / "backports" / "zoneinfo",
            ):
                self.assertFalse(gone.exists(), gone)
            self.assertTrue((stdlib / "zoneinfo" / "__init__.pyc").is_file())
            self.assertTrue((stdlib / "zoneinfo" / "_common.pyc").is_file())
            self.assertEqual(sorted(p.name for p in (stdlib / "tomllib").iterdir()), ["__init__.pyc"])
            probe = (
                "import sys; sys.path.insert(0, sys.argv[1]); import tomllib; print(tomllib.__file__)\n"
                "try:\n    tomllib.loads('a = 1')\nexcept tomllib.TOMLDecodeError:\n    print('refused')"
            )
            stub = subprocess.run(
                [sys.executable, "-I", "-c", probe, str(stdlib)], capture_output=True, text=True, check=False
            )
            self.assertEqual(stub.returncode, 0, stub.stderr)
            self.assertEqual(stub.stdout.split(), [str(stdlib / "tomllib" / "__init__.pyc"), "refused"])
            for kept in (
                stdlib / "idlelib" / "editor.py",
                stdlib / "typing.py",
                stdlib / "site-packages" / "pip" / "__init__.py",
                venv_site / "httpx" / "__init__.py",
                venv_site / "setuptools" / "_vendor" / "tomli_w" / "__init__.py",
                tree / "openjiuwen_icode" / "__init__.py",
            ):
                self.assertTrue(kept.is_file(), kept)
            manifest = json.loads((tree / ".venv" / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["sanitizer"], SANITIZER_VERSION)
            self.assertFalse(manifest["sourceless"])
            self.assertIn(".venv/sandbox-cpython/lib/python3.13/tomllib", manifest["removed"])
            self.assertEqual(manifest["stubbed"], [".venv/sandbox-cpython/lib/python3.13/tomllib"])
            self.assertIn(".venv/lib/python3.13/site-packages/setuptools/_vendor/tomli", manifest["removed"])
            self.assertEqual(len(manifest["tree_sha256"]), 64)

            before = self._snapshot(tree)
            again = sanitize_tree(tree)
            self.assertEqual(again, doc)
            self.assertEqual(self._snapshot(tree), before)

    def _fake_clone(self, parent: Path, home: str) -> Path:
        """A git-mode tree with the host-specific files a real uv sync leaves in .venv."""
        tree = parent / "icode-src"
        tree.mkdir(parents=True)
        tree = tree.resolve()
        venv = tree / ".venv"
        sandbox = venv / "sandbox-cpython"
        stdlib = sandbox / "lib" / "python3.13"
        site = venv / "lib" / "python3.13" / "site-packages"
        (sandbox / "bin").mkdir(parents=True)
        (sandbox / "bin" / "python3").symlink_to(sys.executable)
        self._write(stdlib / "typing.py", "def cast(kind, value):\n    return value\n")
        self._write(stdlib / "zoneinfo" / "__init__.py", "ZONE = 'UTC'\n")
        self._write(site / "pkg" / "__init__.py", "VALUE = 1\n")
        self._write(site / "pkg" / "__pycache__" / "__init__.cpython-313.pyc", f"cache {home}")
        self._write(site / "pkg-1.0.dist-info" / "RECORD", f"../../../bin/pkg,sha256={home},{len(str(tree))}\n")
        self._write(site / "pkg-1.0.dist-info" / "uv_cache.json", json.dumps({"timestamp": home}))
        self._write(site / "pkg-1.0.dist-info" / "direct_url.json", json.dumps({"url": f"file://{tree}"}))
        self._write(venv / "bin" / "icode", f"#!{tree}/.venv/bin/python\nimport sys\n")
        self._write(venv / "bin" / "activate", f"VIRTUAL_ENV='{tree}/.venv'\n")
        self._write(venv / "pyvenv.cfg", f"home = {home}\n")
        (venv / "bin" / "python").symlink_to(f"{home}/bin/python3.13")
        return tree

    def test_startup_probe_imports_the_run_path(self):
        """--help skips the agent stack; build 47/48 crashed there on a removed stdlib module."""
        cases = (
            ("import missing_stdlib_module_for_probe\n", 1, "cannot import its run path"),
            ("VALUE = 1\n", 0, "OK sanitized iCode runtime starts"),
            (None, 0, "OK sanitized iCode runtime starts"),
        )
        for factory, rc, message in cases:
            with tempfile.TemporaryDirectory() as tmp:
                tree = Path(tmp) / "icode-src"
                (tree / ".venv" / "sandbox-cpython" / "bin").mkdir(parents=True)
                (tree / ".venv" / "sandbox-cpython" / "bin" / "python3").symlink_to(sys.executable)
                self._write(tree / ".venv" / "bin" / "icode", "import sys\nsys.exit(0)\n")
                (tree / ".venv" / "bin" / "icode").chmod(0o755)
                self._write(tree / "openjiuwen_icode" / "__init__.py", "")
                if factory is not None:
                    self._write(tree / "openjiuwen_icode" / "agent" / "__init__.py", "")
                    self._write(tree / "openjiuwen_icode" / "agent" / "factory.py", factory)
                    self._write(tree / "openjiuwen_icode" / "host" / "__init__.py", "")
                    self._write(tree / "openjiuwen_icode" / "host" / "bootstrap.py", "VALUE = 1\n")
                proc = subprocess.run(
                    ["bash", "-c", 'source "$1"; icode_probe_sandbox "$2"', "_", str(LIB / "icode_input.sh"), str(tree)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, rc, (factory, proc.stderr))
                self.assertIn(message, proc.stderr, factory)

    def test_runtime_sha256_is_the_same_for_every_workspace(self):
        import shutil

        from icode_sanitize import runtime_sha256, sanitize_tree

        with tempfile.TemporaryDirectory() as tmp:
            a = self._fake_clone(Path(tmp) / "lolbench_one_task", "/home/a/uv")
            b = self._fake_clone(Path(tmp) / "deepswe_one_task_longer_path", "/home/bb/uv")
            for path in b.rglob("*"):
                if not path.is_symlink():
                    os.utime(path, (1_000_000_000, 1_000_000_000))
            doc_a = sanitize_tree(a, sourceless=True)
            doc_b = sanitize_tree(b, sourceless=True)
            self.assertEqual(doc_a["runtime_sha256"], doc_b["runtime_sha256"])
            self.assertNotEqual(doc_a["tree_sha256"], doc_b["tree_sha256"])
            moved = Path(tmp) / "moved" / "icode-src"
            shutil.copytree(a, moved, symlinks=True)
            self.assertEqual(runtime_sha256(moved), doc_a["runtime_sha256"])
            pyc = b / ".venv" / "sandbox-cpython" / "lib" / "python3.13" / "typing.pyc"
            pyc.write_bytes(pyc.read_bytes() + b"\0")
            self.assertNotEqual(runtime_sha256(b), doc_a["runtime_sha256"])

    def test_stale_manifest_requires_refresh(self):
        from icode_sanitize import EXIT_STALE, MANIFEST_NAME, SANITIZER_VERSION, StaleManifestError, sanitize_tree

        outside = ".venv/lib/python3.13/site-packages/setuptools/_vendor/tomli"
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._fake_clone(Path(tmp) / "job", "/home/a/uv")
            (tree / ".venv" / MANIFEST_NAME).write_text(
                json.dumps({"sanitizer": "mac-k3d-icode-sanitize-v1", "removed": [outside], "sourceless_removed": 625}),
                encoding="utf-8",
            )
            with self.assertRaises(StaleManifestError):
                sanitize_tree(tree, sourceless=True)
            cli = subprocess.run(
                [sys.executable, str(LIB / "icode_sanitize.py"), "--tree", str(tree), "--sourceless"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli.returncode, EXIT_STALE, cli.stderr)
            doc = sanitize_tree(tree, sourceless=True, refresh=True)
            self.assertEqual(doc["sanitizer"], SANITIZER_VERSION)
            self.assertIn(outside, doc["removed"])
            self.assertEqual(doc["sourceless_removed"], 1)
            self.assertEqual(sanitize_tree(tree, sourceless=True), doc)

    def test_isolation_record_hashes_the_tree_now(self):
        from agent_mounts import build_mounts
        from icode_sanitize import SANITIZER_VERSION, sanitize_tree
        from provenance import isolation_record, record_leakscan

        with tempfile.TemporaryDirectory() as tmp:
            tree = self._fake_clone(Path(tmp) / "job", "/home/a/uv")
            doc = sanitize_tree(tree, sourceless=True)
            record = isolation_record(tree, build_mounts(tree))
            self.assertEqual(record["mode"], "git")
            self.assertEqual(record["sanitizer"], SANITIZER_VERSION)
            self.assertTrue(record["sourceless"])
            self.assertEqual(record["tree_sha256"], doc["tree_sha256"])
            self.assertEqual(record["runtime_sha256"], doc["runtime_sha256"])
            self.assertTrue(record["manifest_matches_tree"])
            self.assertEqual(record["mount"], {"count": 1, "target": "/opt/icode-host", "read_only": True})
            self._write(tree / ".venv" / "lib" / "python3.13" / "site-packages" / "pkg" / "late.py", "LATE = 1\n")
            self.assertFalse(isolation_record(tree, build_mounts(tree))["manifest_matches_tree"])

            release = Path(tmp) / "icode-bin"
            release.mkdir()
            rel = isolation_record(release, build_mounts(release))
            self.assertEqual(rel["mode"], "release")
            self.assertIsNone(rel["sourceless"])
            self.assertIsNone(rel["manifest_matches_tree"])

            inputs = Path(tmp) / "inputs.json"
            inputs.write_text(json.dumps({"isolation": record}), encoding="utf-8")
            report = Path(tmp) / "leak.json"
            report.write_text(
                json.dumps(
                    {
                        "scanner": "mac-k3d-leakscan-v1",
                        "hit_tasks": ["b", "a"],
                        "tasks": {"a": {"status": "hit"}, "b": {"status": "hit"}, "c": {"status": "clean"}},
                    }
                ),
                encoding="utf-8",
            )
            merged = record_leakscan(inputs, report)["isolation"]
            self.assertEqual(merged["runtime_sha256"], record["runtime_sha256"])
            self.assertEqual(merged["leak_scan"]["hit_tasks"], ["a", "b"])
            self.assertEqual(merged["leak_scan"]["statuses"], {"clean": 1, "hit": 2})
            self.assertEqual(len(merged["leak_scan"]["report_sha256"]), 64)

    def test_record_canary_merges_summary_into_isolation(self):
        from provenance import isolation_lines, isolation_view, record_canary

        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "inputs.json"
            inputs.write_text(json.dumps({"isolation": {"mode": "git", "leak_scan": {"scanner": "s"}}}), encoding="utf-8")
            summary = Path(tmp) / "summary.json"
            summary.write_text(
                json.dumps(
                    {
                        "schema": "mac-k3d-canary-v1",
                        "config_version": "mac-k3d-canary-v1",
                        "node": "worker-1",
                        "status": "pass",
                        "counts": {"tasks": 2, "pass": 2, "fail": 0},
                        "failed_tasks": [],
                        "warn_tasks": ["cpython_5"],
                        "tasks": {"fastapi_1": {}, "cpython_5": {}},
                    }
                ),
                encoding="utf-8",
            )
            merged = record_canary(inputs, summary)["isolation"]
            self.assertEqual(merged["leak_scan"], {"scanner": "s"})
            canary = merged["canary"]
            self.assertEqual(canary["status"], "pass")
            self.assertEqual(canary["tasks"], ["cpython_5", "fastapi_1"])
            self.assertEqual(canary["warn_tasks"], ["cpython_5"])
            self.assertEqual(canary["counts"], {"tasks": 2, "pass": 2, "fail": 0})
            self.assertEqual(len(canary["summary_sha256"]), 64)
            lines = isolation_lines(isolation_view(merged))
            self.assertIn(
                "Canary: mac-k3d-canary-v1 · pass · tasks cpython_5, fastapi_1 · failed none · warnings cpython_5",
                lines,
            )

    def test_sanitizer_skips_tree_without_sandbox(self):
        from icode_sanitize import sanitize_tree

        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "icode-bin"
            self._write(tree / "icode", "#!/bin/sh\n")
            self.assertIsNone(sanitize_tree(tree))
            self.assertEqual(sorted(p.name for p in tree.iterdir()), ["icode"])

    def test_sourceless_stdlib_still_imports(self):
        import shutil
        import sysconfig

        from icode_sanitize import sanitize_tree

        exe = Path(os.path.realpath(sys.executable))
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        source = Path(sysconfig.get_paths()["stdlib"])
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "icode-src"
            sandbox = tree / ".venv" / "sandbox-cpython"
            stdlib = sandbox / "lib" / version
            (sandbox / "bin").mkdir(parents=True)
            py = sandbox / "bin" / "python3"
            shutil.copy2(exe, py)
            ignore = shutil.ignore_patterns(
                "test", "site-packages", "dist-packages", "__pycache__", "idlelib", "tkinter", "turtledemo", "ensurepip"
            )
            shutil.copytree(source, stdlib, ignore=ignore, symlinks=True)
            probe = subprocess.run(
                [str(py), "-I", "-c", "import sys, json, typing; print(sys.prefix)"],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != sandbox.resolve():
                self.skipTest(f"host python is not relocatable: {probe.stdout.strip()} {probe.stderr[-200:]}")

            doc = sanitize_tree(tree, sourceless=True)
            self.assertTrue(doc["sourceless"])
            self.assertGreater(doc["sourceless_removed"], 100)
            self.assertFalse((stdlib / "typing.py").exists())
            self.assertFalse((stdlib / "json" / "__init__.py").exists())
            self.assertTrue((stdlib / "typing.pyc").is_file())
            self.assertTrue((stdlib / "json" / "__init__.pyc").is_file())
            run = subprocess.run(
                [str(py), "-I", "-c", "import json, typing; print(json.dumps(typing.get_args(typing.Union[int, str])[0].__name__))"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), '"int"')

    def test_leakscan_reports_copied_gold_and_ignores_unrelated(self):
        from anticheat_leakscan import scan

        gold = [f"    value_{i} = compute_distinctive_thing({i}, alpha, beta)" for i in range(40)]
        test_lines = [f"    assert check_distinctive_case_{i}(fixture) is True" for i in range(40)]
        patch = (
            "diff --git a/pkg/mod.py b/pkg/mod.py\nnew file mode 100644\n--- /dev/null\n+++ b/pkg/mod.py\n"
            "@@ -0,0 +1,40 @@\n"
            + "".join(f"+{line}\n" for line in gold)
            + "diff --git a/tests/test_mod.py b/tests/test_mod.py\n--- /dev/null\n+++ b/tests/test_mod.py\n"
            "@@ -0,0 +1,40 @@\n"
            + "".join(f"+{line}\n" for line in test_lines)
        )
        small = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n" + "".join(f"+{line}\n" for line in gold[:5])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks"
            self._write(tasks / "big" / "solution" / "solution.patch", patch)
            self._write(tasks / "small" / "solution" / "solution.patch", small)
            (tasks / "nogold" / "tests").mkdir(parents=True)
            leaky = root / "leaky"
            self._write(leaky / "lib" / "python3.13" / "mod.py", "\n".join(gold) + "\n")
            self._write(leaky / ".git" / "objects" / "copy", "\n".join(gold) + "\n")
            unrelated = root / "unrelated"
            self._write(unrelated / "lib" / "other.py", "\n".join(f"    other_{i} = something_unrelated({i})" for i in range(60)))
            tests_only = root / "tests-only"
            self._write(tests_only / "lib" / "copied_tests.py", "\n".join(test_lines) + "\n")

            ids = ["big", "small", "nogold"]
            report = scan(leaky, tasks, ids)
            self.assertEqual(report["hit_tasks"], ["big"])
            big = report["tasks"]["big"]
            self.assertEqual(big["status"], "hit")
            self.assertEqual(big["gold_lines"], 40)
            self.assertEqual(big["hits"], [{"path": "lib/python3.13/mod.py", "matched": 40, "share": 1.0}])
            self.assertEqual(report["tasks"]["small"]["status"], "too_small")
            self.assertEqual(report["tasks"]["nogold"]["status"], "no_gold")
            self.assertEqual(scan(unrelated, tasks, ids)["tasks"]["big"]["status"], "clean")
            self.assertEqual(scan(tests_only, tasks, ids)["tasks"]["big"]["status"], "clean")

            script = LIB / "anticheat_leakscan.py"
            out = root / "leak.json"
            hit = subprocess.run(
                [sys.executable, str(script), "--tree", str(leaky), "--tasks-dir", str(tasks), "--all", "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(hit.returncode, 2, hit.stdout + hit.stderr)
            self.assertIn("LEAK task=big file=lib/python3.13/mod.py matched=40/40", hit.stdout)
            self.assertNotIn("compute_distinctive_thing", hit.stdout)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["hit_tasks"], ["big"])
            clean = subprocess.run(
                [sys.executable, str(script), "--tree", str(unrelated), "--tasks-dir", str(tasks), "--all", "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

    def test_an_official_leak_hit_skips_only_that_question(self):
        gold = [f"    value_{i} = compute_distinctive_thing({i}, alpha, beta)" for i in range(40)]
        patch = (
            "diff --git a/pkg/mod.py b/pkg/mod.py\nnew file mode 100644\n--- /dev/null\n+++ b/pkg/mod.py\n"
            "@@ -0,0 +1,40 @@\n" + "".join(f"+{line}\n" for line in gold)
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            tasks = work / "deep-swe" / "tasks"
            self._write(tasks / "big" / "solution" / "solution.patch", patch)
            (tasks / "fine" / "tests").mkdir(parents=True)
            tree = root / "icode"
            self._write(tree / "lib" / "mod.py", "\n".join(gold) + "\n")
            self._write(work / "icode_host_root.txt", f"{tree}\n")
            self._write(work / "eval_protocol_inputs.json", "{}\n")
            skipped = work / "skipped_tasks.txt"

            def leakscan(task_list: str, official: str) -> subprocess.CompletedProcess:
                env = {**os.environ, "MAC_K3D_EVAL_WORKDIR": str(work), "BENCHMARK": "deepswe", "TASKS": task_list}
                env.update({"TASK": "", "TASK_OFFSET": "0", "OFFICIAL": official})
                return subprocess.run(
                    ["bash", str(STAGES / "tasks" / "leakscan.sh")], capture_output=True, text=True, check=False, env=env
                )

            proc = leakscan("big,fine", "1")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("WARNING: leak scan found task gold for 1 of 2 questions (big;", proc.stdout)
            self.assertIn("skipping those questions", proc.stdout)
            self.assertEqual(skipped.read_text(encoding="utf-8"), "big\tleak scan hit\n")
            recorded = json.loads((work / "eval_protocol_inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["isolation"]["leak_scan"]["hit_tasks"], ["big"])

            proc = leakscan("big", "1")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("leak scan found task gold for every selected question (big;", proc.stdout + proc.stderr)

            # The same skip happens with OFFICIAL unset: every run skips a hit.
            skipped.unlink()
            proc = leakscan("big,fine", "0")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("skipping those questions", proc.stdout)
            self.assertEqual(skipped.read_text(encoding="utf-8"), "big\tleak scan hit\n")

    def test_the_evaluate_phase_leaves_skipped_questions_out_of_harbor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            for tid in ("alpha", "beta", "gamma"):
                (work / "deep-swe" / "tasks" / tid).mkdir(parents=True)
            self._write(work / "selected_tasks.txt", "alpha\nbeta\ngamma\n")
            self._write(work / "selected_tasks_offset.txt", "0\n")
            self._write(work / "skipped_tasks.txt", "beta\tleak scan hit\n")
            self._write(work / "agent_mounts.json", "[]\n")
            self._write(work / "eval_resources.json", "{}\n")
            self._write(work / "icode_host_root.txt", f"{root / 'icode'}\n")
            bin_dir = root / "bin"
            self._write(bin_dir / "harbor", "#!/bin/sh\nexit 0\n")
            (bin_dir / "harbor").chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                "MAC_K3D_EVAL_WORKDIR": str(work),
                "BENCHMARK": "deepswe",
                "TASKS": "alpha,beta,gamma",
                "TASK": "",
                "TASK_OFFSET": "0",
                "N_ROLLOUTS": "4",
                "CPU_LOCK_QTY": "8",
                "DEEPSEEK_API_KEY": "placeholder-not-a-token",
            }
            proc = subprocess.run(
                [
                    "bash", "-c",
                    'source "$1"; source "$2"; load_eval_state; echo "ids=${TASK_IDS[*]} needed=$NEEDED"',
                    "_", str(STAGES / "_common.sh"), str(STAGES / "evaluate" / "harbor_cmd.sh"),
                ],
                capture_output=True, text=True, check=False, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("ids=alpha gamma needed=8", proc.stdout)
            self.assertIn("evaluate: skipping 1 questions from", proc.stderr)
            self.assertIn("skipped_tasks.txt: beta", proc.stderr)
            # The report still counts it: it stays selected.
            self.assertEqual((work / "selected_tasks.txt").read_text(encoding="utf-8"), "alpha\nbeta\ngamma\n")

    def test_agent_mounts_read_only_and_allowlist(self):
        from agent_mounts import ICODE_TARGET, build_mounts, check_mounts

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            icode = work / "icode-src"
            icode.mkdir()
            bench = work / "deep-swe"
            (bench / "tasks").mkdir(parents=True)
            forbid = [bench, bench / "tasks"]
            mounts = build_mounts(icode)
            self.assertEqual(
                mounts,
                [{"type": "bind", "source": str(icode), "target": ICODE_TARGET, "read_only": True}],
            )
            self.assertEqual(check_mounts(mounts, icode, forbid), [])
            second = mounts + [{"type": "bind", "source": str(bench), "target": "/bench", "read_only": True}]
            self.assertTrue(any("exactly one mount" in err for err in check_mounts(second, icode, forbid)))
            writable = [dict(mounts[0], read_only=False)]
            self.assertTrue(any("read_only" in err for err in check_mounts(writable, icode, forbid)))
            self.assertTrue(any("overlaps" in err for err in check_mounts(build_mounts(work), work, forbid)))
            inside = bench / "icode"
            inside.mkdir()
            self.assertTrue(any("overlaps" in err for err in check_mounts(build_mounts(inside), inside, forbid)))

    def _run_eval_dry(
        self, tmp: Path, host_root: Path | None = None, extra_env: dict | None = None
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        work = tmp / "eval"
        task = work / "deep-swe" / "tasks" / "alpha"
        self._write(task / "task.toml", 'docker_image = "example/alpha:1"\n')
        self._write(
            task / "solution" / "solution.patch",
            "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n+print('hello from the alpha gold patch line')\n",
        )
        icode = work / "icode-bin"
        self._write(icode / "icode", "#!/bin/sh\necho icode\n")
        (icode / "icode").chmod(0o755)
        (work / "icode_bin_path.txt").write_text(f"{icode / 'icode'}\n", encoding="utf-8")
        (work / "icode_host_root.txt").write_text(f"{host_root or icode}\n", encoding="utf-8")
        bindir = tmp / "bin"
        bindir.mkdir()
        marker = tmp / "harbor-ran"
        for name, body in (
            ("harbor", f"#!/bin/sh\n[ \"$1\" = \"--version\" ] && {{ echo 0.22.0; exit 0; }}\ntouch '{marker}'\nexit 0\n"),
            ("docker", "#!/bin/sh\nexit 1\n"),
        ):
            (bindir / name).write_text(body, encoding="utf-8")
            (bindir / name).chmod(0o755)
        env_file = tmp / "empty.env"
        env_file.write_text("", encoding="utf-8")
        env_file.chmod(0o600)
        env = os.environ.copy()
        for key in (
            "OFFICIAL",
            "TASK",
            "TASKS",
            "ICODE_SOURCELESS_STDLIB",
            "DEEPSWE_DIR",
            "LOLBENCH_DIR",
            "SWEBENCHPRO_DIR",
            "GITHUB_TOKEN",
            "MAC_K3D_GITHUB_PAT",
            "MAC_K3D_GITCODE_PAT",
            "MAC_K3D_EVAL_OUTPUT",
            "CANARY",
            "CANARY_ALLOW_HOST",
        ):
            env.pop(key, None)
        env.update(
            {
                "HOME": str(tmp),
                "PATH": str(bindir) + os.pathsep + env.get("PATH", ""),
                "MAC_K3D_EVAL_WORKDIR": str(work),
                "MAC_K3D_ENV_FILE": str(env_file),
                "DEEPSEEK_API_KEY": self.SENTINEL_KEY,
                "GITCODE_TOKEN": self.SENTINEL_TOKEN,
                "BENCHMARK": "deepswe",
                "N_TASKS": "1",
                "N_ROLLOUTS": "1",
                "CPU_LOCK_QTY": "1",
                "BUILD_NUMBER": "dry",
                "MAC_K3D_HARBOR_DRY_RUN": "1",
            }
        )
        env.update(extra_env or {})
        stages = ROOT / "pipeline" / "stages"
        script = (
            "set -e\n"
            'for step in tasks/icode_sandbox tasks/isolation tasks/leakscan; do bash "$1/$step.sh"; done\n'
            'bash "$1/evaluate.sh"\n'
        )
        proc = subprocess.run(
            ["bash", "-c", script, "_", str(stages)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertFalse(marker.exists(), "dry run must not call harbor run")
        return proc, work

    def test_eval_dry_run_has_no_gitcode_token_and_read_only_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, work = self._run_eval_dry(Path(tmp))
            output = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, output)
            line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("harbor dry-run")), "")
            self.assertIn("harbor run", line)
            self.assertIn('"read_only": true', line)
            self.assertIn('"target": "/opt/icode-host"', line)
            # The key reaches Harbor only through --env-file, never on its command line.
            self.assertNotIn("DEEPSEEK_API_KEY", line)
            self.assertIn(f"--env-file {work / '.harbor-env'}", line)
            tokens = line.split()
            hosts = [tokens[i + 1] for i, tok in enumerate(tokens) if tok == "--allow-agent-host"]
            self.assertEqual(hosts, ["api.deepseek.com", "api.deepseek.ai"])
            self.assertNotIn("GITCODE_TOKEN", output)
            self.assertNotIn(self.SENTINEL_TOKEN, output)
            self.assertNotIn(self.SENTINEL_KEY, output)
            self.assertFalse((work / ".harbor-env").exists(), "the step removes its env file on exit")
            leak = json.loads((work / "anticheat_leakscan.json").read_text(encoding="utf-8"))
            self.assertEqual(leak["hit_tasks"], [])
            self.assertEqual(leak["tasks"]["alpha"]["status"], "too_small")
            isolation = json.loads((work / "eval_protocol_inputs.json").read_text(encoding="utf-8"))["isolation"]
            self.assertEqual(isolation["mode"], "release")
            self.assertEqual(isolation["mount"], {"count": 1, "target": "/opt/icode-host", "read_only": True})
            self.assertEqual(isolation["leak_scan"]["scanner"], "mac-k3d-leakscan-v1")
            self.assertEqual(isolation["leak_scan"]["hit_tasks"], [])
            self.assertEqual(isolation["leak_scan"]["statuses"], {"too_small": 1})
            self.assertEqual(isolation["network_allowlist"]["version"], "mac-k3d-network-allowlist-v1")
            self.assertEqual(isolation["network_allowlist"]["agent_hosts"], ["api.deepseek.com", "api.deepseek.ai"])
        stage = (ROOT / "pipeline" / "stages" / "evaluate" / "harbor_cmd.sh").read_text(encoding="utf-8")
        self.assertIn("unset GITCODE_TOKEN MAC_K3D_GITCODE_PAT GITHUB_TOKEN MAC_K3D_GITHUB_PAT", stage)

    def test_harbor_env_lives_only_while_the_step_runs(self):
        script = (
            "set -euo pipefail\n"
            'source "$1"\n'
            'HARBOR_ENV="$2"\n'
            "write_harbor_env\n"
            'ls -l "$HARBOR_ENV" | cut -c1-10\n'
            'grep -c "^DEEPSEEK_API_KEY=" "$HARBOR_ENV"\n'
            '[ "$3" = ok ] || false\n'
        )
        harbor_cmd = ROOT / "pipeline" / "stages" / "evaluate" / "harbor_cmd.sh"
        for how, rc in (("ok", 0), ("fail", 1)):
            with tempfile.TemporaryDirectory() as tmp:
                env_file = Path(tmp) / ".harbor-env"
                env = dict(os.environ, DEEPSEEK_API_KEY=self.SENTINEL_KEY)
                for key in ("DEEPSEEK_MODEL", "ICODE_MODEL", "ICODE_API_BASE", "ICODE_PROVIDER",
                            "ICODE_REASONING_EFFORT", "ICODE_MAX_TOKENS", "ICODE_MAX_ITERATIONS"):
                    env[key] = "x"
                proc = subprocess.run(
                    ["bash", "-c", script, "_", str(harbor_cmd), str(env_file), how],
                    capture_output=True, text=True, check=False, env=env,
                )
                self.assertEqual(proc.returncode, rc, proc.stderr)
                self.assertEqual(proc.stdout.split(), ["-rw-------", "1"], proc.stderr)
                self.assertFalse(env_file.exists(), f"{how}: .harbor-env must not outlive the step")
        env_sh = (STAGES / "env.sh").read_text(encoding="utf-8")
        self.assertIn('rm -f "$WORKDIR/.harbor-env"', env_sh)

    def test_lolbench_image_of_another_arch_is_rebuilt(self):
        cases = (
            ("arm64", "x86_64", True, 0, "local img:1 is arm64, this worker is amd64; docker build"),
            ("amd64", "x86_64", True, 0, "using local image img:1"),
            ("arm64", "aarch64", True, 0, "using local image img:1"),
            ("arm64", "x86_64", False, 1, "Dockerfile is missing"),
        )
        for image_arch, host_arch, dockerfile, rc, message in cases:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                bindir = tmp_path / "bin"
                bindir.mkdir()
                log = tmp_path / "docker.log"
                (bindir / "docker").write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' \"$*\" >>'{log}'\n"
                    'case "$1" in\n'
                    f"  info) echo {host_arch} ;;\n"
                    f"  image) case \"$*\" in *--format*) echo {image_arch} ;; esac ;;\n"
                    "esac\nexit 0\n",
                    encoding="utf-8",
                )
                (bindir / "docker").chmod(0o755)
                env_dir = tmp_path / "environment"
                env_dir.mkdir()
                if dockerfile:
                    (env_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
                env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                           MAC_K3D_EVAL_WORKDIR=str(tmp_path / "work"), BENCHMARK="lolbench")
                proc = subprocess.run(
                    ["bash", "-c", 'source "$1"; ensure_task_image cpython_5 img:1 "$2"', "_",
                     str(ROOT / "pipeline" / "stages" / "_common.sh"), str(env_dir)],
                    capture_output=True, text=True, check=False, env=env,
                )
                self.assertEqual(proc.returncode, rc, (image_arch, host_arch, proc.stderr))
                self.assertIn(message, proc.stdout + proc.stderr)
                built = any(line.startswith("build ") for line in log.read_text(encoding="utf-8").splitlines())
                self.assertEqual(built, rc == 0 and "docker build" in message, (image_arch, host_arch))

    def test_task_containers_keep_amd64_only_environments(self):
        """DeepSWE and SWE-bench Pro run their amd64-only prebuilt images; LoLBench is native
        unless a Dockerfile downloads an x86-64-only build."""
        cases = (
            ("aarch64", "lolbench", "RUN echo hi\n", "linux/arm64", False),
            ("aarch64", "lolbench", "RUN curl -o d.zip https://x/deno-x86_64-unknown-linux-gnu.zip\n", "linux/amd64", True),
            ("aarch64", "lolbench", "# deno-x86_64-unknown-linux-gnu\nRUN echo hi\n", "linux/arm64", False),
            ("aarch64", "lolbench", 'RUN echo "deb [ arch=amd64,arm64 ] https://repo" >x\n', "linux/arm64", False),
            ("aarch64", "deepswe", "RUN echo hi\n", "linux/amd64", True),
            ("aarch64", "swebenchpro", "RUN echo hi\n", "linux/amd64", True),
            ("x86_64", "lolbench", "RUN curl https://x/tool-linux-amd64.tar.gz\n", "linux/amd64", False),
            ("x86_64", "deepswe", "RUN echo hi\n", "linux/amd64", False),
            ("x86_64", "swebenchpro", "RUN echo hi\n", "linux/amd64", False),
        )
        for host_arch, bench, dockerfile, want, emulated in cases:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                bindir = tmp_path / "bin"
                bindir.mkdir()
                (bindir / "docker").write_text(f"#!/bin/sh\necho {host_arch}\n", encoding="utf-8")
                (bindir / "docker").chmod(0o755)
                work = tmp_path / "work"
                tasks = work / {"swebenchpro": "swebenchpro/tasks", "lolbench": "lolbench/harbor_tasks"}.get(
                    bench, "deep-swe/tasks"
                )
                (tasks / "t1" / "environment").mkdir(parents=True)
                (tasks / "t1" / "environment" / "Dockerfile").write_text(dockerfile, encoding="utf-8")
                (work / "selected_tasks.txt").write_text("t1\n", encoding="utf-8")
                env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                           MAC_K3D_EVAL_WORKDIR=str(work), BENCHMARK=bench)
                for key in ("SWEBENCHPRO_DOCKER_PLATFORM", "DEEPSWE_DOCKER_PLATFORM", "DEEPSWE_DIR",
                            "SWEBENCHPRO_DIR", "LOLBENCH_DIR"):
                    env.pop(key, None)
                proc = subprocess.run(
                    ["bash", "-c", 'source "$1"; write_container_platform', "_",
                     str(ROOT / "pipeline" / "stages" / "_common.sh")],
                    capture_output=True, text=True, check=False, env=env,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                doc = json.loads((work / "container_platform.json").read_text(encoding="utf-8"))
                case = (host_arch, bench, dockerfile)
                self.assertEqual(doc["container_platform"], want, case)
                self.assertEqual(doc["emulated"], emulated, case)
                self.assertIn("emulated" if emulated else "native", proc.stdout, case)

    def test_worker_line_names_the_container_platform(self):
        from provenance import container_platform_facts, provenance_markdown

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.assertEqual(container_platform_facts(work), {})
            (work / "container_platform.json").write_text(
                json.dumps({"container_platform": "linux/amd64", "docker_platform": "linux/arm64", "emulated": True}),
                encoding="utf-8",
            )
            facts = container_platform_facts(work)
        self.assertEqual(facts, {"container_platform": "linux/amd64", "emulated": True})
        protocol = {"worker": {"node": "mac-mini", "os": "Darwin", **facts}}
        line = next(ln for ln in provenance_markdown(protocol) if ln.startswith("- Worker:"))
        self.assertTrue(line.endswith("· os `Darwin` · containers `linux/amd64 emulated`"), line)
        old = next(ln for ln in provenance_markdown({"worker": {"node": "lab"}}) if ln.startswith("- Worker:"))
        self.assertTrue(old.endswith("cpu `-`"), old)

    def test_vpn_mtu_below_docker_bridge_warns(self):
        cases = (
            ("1280", "1500", True),
            ("1280", "", True),
            ("1500", "1500", False),
            ("1280", "1280", False),
            ("", "1500", False),
        )
        for tunnel_mtu, bridge_mtu, warns in cases:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                bindir = tmp_path / "bin"
                bindir.mkdir()
                if os.uname().sysname == "Darwin":
                    # warn_docker_mtu uses route and ifconfig on Darwin, ip on Linux.
                    # The ifconfig sed keeps the digits after " mtu ", so the stub
                    # line has that space, as real macOS ifconfig does.
                    iface = "   interface: surfshark_wg" if tunnel_mtu else ""
                    (bindir / "route").write_text(
                        "#!/bin/sh\n"
                        'case "$*" in\n'
                        "  '-n get 1.1.1.1')\n"
                        f"    [ -n '{iface}' ] || exit 2\n"
                        f"    printf '%s\\n' '{iface}' ;;\n"
                        "esac\n",
                        encoding="utf-8",
                    )
                    (bindir / "ifconfig").write_text(
                        "#!/bin/sh\n"
                        'case "$1" in\n'
                        "  surfshark_wg)\n"
                        f"    printf '%s\\n' 'surfshark_wg: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu {tunnel_mtu}' ;;\n"
                        "esac\n",
                        encoding="utf-8",
                    )
                    stubs = ("route", "ifconfig", "docker")
                else:
                    route = "1.1.1.1 dev surfshark_wg table 300000 src 10.14.0.2" if tunnel_mtu else ""
                    (bindir / "ip").write_text(
                        "#!/bin/sh\n"
                        'case "$*" in\n'
                        f"  'route get 1.1.1.1') [ -n '{route}' ] || exit 2; echo '{route}' ;;\n"
                        f"  *link*) echo '7: surfshark_wg: <POINTOPOINT,UP> mtu {tunnel_mtu} qdisc noqueue' ;;\n"
                        "esac\n",
                        encoding="utf-8",
                    )
                    stubs = ("ip", "docker")
                (bindir / "docker").write_text(f"#!/bin/sh\necho '{bridge_mtu}'\n", encoding="utf-8")
                for name in stubs:
                    (bindir / name).chmod(0o755)
                env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                           MAC_K3D_EVAL_WORKDIR=str(tmp_path / "work"))
                proc = subprocess.run(
                    ["bash", "-c", 'source "$1"; warn_docker_mtu', "_",
                     str(ROOT / "pipeline" / "stages" / "_common.sh")],
                    capture_output=True, text=True, check=False, env=env,
                )
                self.assertEqual(proc.returncode, 0, (tunnel_mtu, bridge_mtu, proc.stderr))
                self.assertEqual("MTU 1280 but Docker's bridge uses 1500" in proc.stderr, warns,
                                 (tunnel_mtu, bridge_mtu, proc.stderr))
                self.assertEqual("daemon.json" in proc.stderr, warns)
        for stage in ("env/host.sh", "evaluate/canary.sh", "evaluate/harbor_run.sh"):
            self.assertIn("\nwarn_docker_mtu\n", (STAGES / stage).read_text(encoding="utf-8"))

    @staticmethod
    def _dry_line(stdout: str, prefix: str) -> list[str]:
        line = next((ln for ln in stdout.splitlines() if ln.startswith(prefix)), "")
        return line.split(": ", 1)[1].split() if ": " in line else []

    @staticmethod
    def _without(tokens: list[str], flags: dict[str, int]) -> list[str]:
        out, skip = [], 0
        for tok in tokens:
            if skip:
                skip -= 1
                continue
            if tok in flags:
                skip = flags[tok]
                continue
            out.append(tok)
        return out

    def test_canary_runs_with_icode_flags_mounts_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, work = self._run_eval_dry(Path(tmp), extra_env={"CANARY": "only"})
            output = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, output)
            icode = self._dry_line(proc.stdout, "harbor dry-run")
            canary = self._dry_line(proc.stdout, "canary dry-run")
            self.assertTrue(icode and canary, output)
            self.assertIn("canary_harbor_agent:CanaryAgent", canary)
            self.assertIn("--disable-verification", canary)
            self.assertFalse(any("DEEPSEEK_API_KEY" in tok for tok in canary), canary)
            self.assertNotIn(self.SENTINEL_KEY, output)
            spec_arg = canary[canary.index("--ak") + 1]
            self.assertTrue(spec_arg.startswith("spec="), spec_arg)
            spec = json.loads(Path(spec_arg[len("spec="):]).read_text(encoding="utf-8"))
            self.assertEqual(spec["task"], "alpha")
            self.assertIn({"kind": "model", "host": "api.deepseek.com"}, spec["hosts"])
            self.assertIn("jenkins-dry", spec_arg)
            # The canary runs one trial, so it carries no -k/-r; everything else
            # about the two commands has to match.
            differ = {"-a": 1, "--job-name": 1, "--jobs-dir": 1, "-k": 1, "-r": 1}
            self.assertEqual(
                self._without(canary, {**differ, "--ak": 1, "--disable-verification": 0}),
                self._without(icode, differ),
                "the canary must get exactly iCode's flags, mounts and agent env",
            )
            self.assertTrue((work / "canary" / "jenkins-dry" / "alpha" / "canary_spec.json").is_file())

    def test_canary_runs_on_every_scored_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, _ = self._run_eval_dry(Path(tmp))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("canary dry-run", proc.stdout)

    def test_canary_allow_host_opens_one_host_for_the_canary_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, _ = self._run_eval_dry(Path(tmp), extra_env={"CANARY": "only", "CANARY_ALLOW_HOST": "github.com"})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            icode = self._dry_line(proc.stdout, "harbor dry-run")
            canary = self._dry_line(proc.stdout, "canary dry-run")
            self.assertIn("github.com", canary)
            self.assertNotIn("github.com", icode)
            spec_arg = canary[canary.index("--ak") + 1]
            spec = json.loads(Path(spec_arg[len("spec="):]).read_text(encoding="utf-8"))
            self.assertEqual(spec["allow_host"], "github.com")

    def test_a_scored_run_refuses_canary_off_and_an_opened_host(self):
        cases = (
            ({"CANARY": "off"}, "CANARY must be on, or only"),
            ({"CANARY_ALLOW_HOST": "github.com"}, "a scored run cannot open a host"),
            ({"CANARY": "sometimes"}, "CANARY must be on, or only"),
        )
        for extra, message in cases:
            with tempfile.TemporaryDirectory() as tmp:
                proc, _ = self._run_eval_dry(Path(tmp), extra_env=extra)
                self.assertNotEqual(proc.returncode, 0, (extra, proc.stdout))
                self.assertIn(message, proc.stderr, extra)
                self.assertNotIn("harbor dry-run", proc.stdout)

    def test_isolation_refuses_icode_root_that_contains_benchmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "eval"
            proc, _ = self._run_eval_dry(Path(tmp), host_root=work)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("overlaps benchmark path", proc.stderr)
            self.assertNotIn("harbor dry-run", proc.stdout)


SUBNETTED = (
    "Docker compose command failed for environment {task}. Stdout: Network {task}__env_default Creating "
    "failed to create network: Error response from daemon: all predefined address pools have been fully subnetted"
)


class UnscoredRolloutTests(unittest.TestCase):
    """deepswe_some_task #6: 5 questions x 4 rollouts, 8 trials Harbor never started."""

    @staticmethod
    def _scored(job: Path, task: str, n: int, f2p_passed: int) -> Path:
        trial = job / f"{task}__s{n}AbCd"
        (trial / "verifier").mkdir(parents=True)
        (trial / "agent").mkdir()
        result = {"task_name": f"datacurve/{task}", "task_id": {"path": f"/eval-runs/deep-swe/tasks/{task}"}}
        (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
        reward = {"reward": 1 if f2p_passed == 4 else 0, "f2p": f2p_passed / 4, "f2p_total": 4,
                  "f2p_passed": f2p_passed, "p2p": 1.0, "p2p_total": 2, "p2p_passed": 2}
        (trial / "verifier" / "reward.json").write_text(json.dumps(reward), encoding="utf-8")
        art = trial / "artifacts" / "logs" / "artifacts"
        art.mkdir(parents=True)
        (art / "model.patch").write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
        return trial

    @staticmethod
    def _never_ran(job: Path, task: str, n: int, message: str) -> Path:
        trial = job / f"{task}__n{n}AbCd"
        (trial / "agent").mkdir(parents=True)
        (trial / "verifier").mkdir()
        result = {
            "task_name": f"datacurve/{task}",
            "task_id": {"path": f"/eval-runs/deep-swe/tasks/{task}"},
            "exception_info": {"exception_type": "RuntimeError", "exception_message": message,
                               "exception_traceback": "Traceback ...", "occurred_at": "2026-10-07T21:00:00Z"},
        }
        (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
        (trial / "trial.log").write_text("starting environment\n", encoding="utf-8")
        return trial

    def _build6(self, root: Path) -> tuple[Path, list[str]]:
        harness = root / "harness"
        job = harness / "harbor_runs" / "jenkins-6" / "icode_deepswe_6"
        layout = {"q1": (4, 0), "q2": (4, 0), "q3": (2, 2), "q4": (1, 3), "q5": (1, 3)}
        for task, (scored, lost) in layout.items():
            for n in range(scored):
                self._scored(job, task, n, 4 if n == 0 else 2)
            for n in range(lost):
                self._never_ran(job, task, n, SUBNETTED.format(task=task))
        return harness, list(layout)

    def test_build_6_counts_only_the_rollouts_that_ran(self):
        from anticheat_verdict import run_live
        from render_report import build_artifact, report_unscored, summary_markdown
        from report_html import report_html

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness, ids = self._build6(root)
            task_file = root / "selected_tasks.txt"
            task_file.write_text("\n".join(ids) + "\n", encoding="utf-8")
            args = type("A", (), {"harness_dir": str(harness), "tasks_dir": "", "task_file": str(task_file),
                                  "benchmark": "deepswe"})
            run_live(args)
            doc = build_artifact(
                suite="deepswe", model="m", api_base="b", task_ids=ids, harness_dir=harness,
                baseline_dir=root / "baseline", n_rollouts=4, concurrency=4, cpus_each=1, run_id="jenkins-6",
            )
            arm = doc["icode"]
            self.assertEqual(doc["anticheat"]["counts"], {"clean": 12, "flagged": 0, "rejected": 0, "not_run": 8})
            self.assertEqual((arm["scored_rollouts"], arm["unscored_rollouts"]), (12, 8))
            self.assertEqual(arm["scored_rollouts"] + arm["unscored_rollouts"], 20)
            self.assertEqual(arm["micro_all"]["rollouts"], 12)
            md = summary_markdown(doc)
            self.assertIn("clean **12** · flagged **0** · rejected **0** · not run **8**", md)
            self.assertIn("scored rollouts: **12** · unscored rollouts: **8** · questions with no trial: **0**", md)
            self.assertIn("progress: **12/20 rollouts (60.0%)", md)
            self.assertIn("missing reward.json; infra: Docker network pool exhausted", json.dumps(arm["tasks"]))
            self.assertIn("12 clean, 0 flagged, 0 rejected, 8 not run.", report_html(doc))
            out = root / "unscored_rollouts.txt"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(report_unscored(doc, out), 8)
            self.assertEqual(out.read_text(encoding="utf-8"), "8\n")
            self.assertEqual(
                printed.getvalue().strip(),
                "WARNING: 8 of 20 rollouts have no score (Docker network pool exhausted 8); see Unscored in summary.md",
            )

    def test_questions_without_a_score_are_named_with_their_cause(self):
        """A leak-skipped question and one whose image never pulled, beside a scored one."""
        from render_report import build_artifact, questions_without_score, summary_markdown
        from report_html import report_html

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            job = harness / "harbor_runs" / "jenkins-8" / "icode_deepswe_8"
            for n in range(2):
                self._scored(job, "etree", n, 4)
                self._never_ran(job, "ytt", n, "Error response from daemon: pull access denied for ytt")
            doc = build_artifact(
                suite="deepswe", model="m", api_base="b", task_ids=["etree", "httpx", "ytt"], harness_dir=harness,
                baseline_dir=root / "baseline", n_rollouts=2, concurrency=2, cpus_each=1, run_id="jenkins-8",
            )
            doc["skipped_questions"] = ["httpx: leak scan hit"]
            self.assertEqual(
                questions_without_score(doc),
                [("httpx", "skipped: leak scan hit"), ("ytt", "image pull failed (2/2)")],
            )
            md = summary_markdown(doc)
            self.assertIn(
                "- Questions without a score: **2** — httpx (skipped: leak scan hit) · ytt (image pull failed (2/2))", md
            )
            self.assertNotIn("Skipped questions", md)
            unscored = md[md.index("## Unscored"):]
            self.assertRegex(unscored, r"\| httpx +\| 2/2 +\| skipped: leak scan hit +\|")
            self.assertRegex(unscored, r"\| ytt +\| 2/2 +\| missing reward.json \(2/2\); infra: image pull failed \(2/2\) +\|")
            page = report_html(doc)
            self.assertIn("Questions without a score: 2 (httpx (skipped: leak scan hit) · ytt (image pull failed (2/2)))", page)
            self.assertIn("<td>skipped: leak scan hit</td>", page)
            # A question that simply never got a trial (its shard stopped) says so.
            doc["skipped_questions"] = []
            self.assertIn("- Questions without a score: **2** — httpx (no trial (2/2)) · ytt", summary_markdown(doc))

    def test_a_fully_scored_run_writes_0_and_warns_nothing(self):
        from render_report import report_unscored

        doc = {"n_tasks": 1, "n_rollouts": 2, "icode": {"unscored_rollouts": 0, "tasks": []}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "unscored_rollouts.txt"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(report_unscored(doc, out), 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "0\n")
            self.assertEqual(printed.getvalue(), "")

    def test_a_missing_trial_counts_as_unscored_too(self):
        from render_report import report_unscored

        arm = {"unscored_rollouts": 3, "tasks": [{"id": "a", "unscored": 3, "rollouts": [{"has_reward": False, "notes": "missing reward.json"}]}]}
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            report_unscored({"n_tasks": 1, "n_rollouts": 4, "icode": arm}, None)
        self.assertIn("3 of 4 rollouts have no score (no trial 2, missing reward.json 1)", printed.getvalue())

    def test_harbors_exception_names_the_infra_cause(self):
        from render_report import _attempt_from_trial

        cases = {
            SUBNETTED.format(task="t"): "Docker network pool exhausted",
            "failed to register layer: unable to prepare extraction snapshot: target snapshot sha256:ab already exists":
                "Docker image store damaged",
            "failed to extract layer: missing parent snapshot sha256:cd": "Docker image store damaged",
            "Error response from daemon: pull access denied for x, repository does not exist": "image pull failed",
            "toomanyrequests: You have reached your pull rate limit": "image pull failed",
            "Docker compose command failed for environment t. Stdout: service main exited 1": "docker compose failed",
            "the agent did not finish in 3600 s": "AgentTimeoutError",
        }
        for message, cause in cases.items():
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as tmp:
                trial = self._never_ran(Path(tmp), "t", 0, message)
                if cause == "AgentTimeoutError":
                    doc = json.loads((trial / "result.json").read_text(encoding="utf-8"))
                    doc["exception_info"]["exception_type"] = "AgentTimeoutError"
                    (trial / "result.json").write_text(json.dumps(doc), encoding="utf-8")
                row = _attempt_from_trial(trial, None)
                self.assertEqual(row["notes"], f"missing reward.json; infra: {cause}")
                self.assertTrue(row["infra_failure"])

    def test_a_scored_trial_with_an_exception_keeps_its_score(self):
        from render_report import _attempt_from_trial

        with tempfile.TemporaryDirectory() as tmp:
            trial = self._scored(Path(tmp), "t", 0, 4)
            doc = json.loads((trial / "result.json").read_text(encoding="utf-8"))
            doc["exception_info"] = {"exception_type": "RuntimeError", "exception_message": "late cleanup error"}
            (trial / "result.json").write_text(json.dumps(doc), encoding="utf-8")
            row = _attempt_from_trial(trial, trial / "verifier" / "reward.json")
            self.assertTrue(row["has_reward"])
            self.assertNotIn("infra", row["notes"])
            self.assertNotIn("infra_failure", row)

    def test_micro_over_all_scored_rollouts_sits_next_to_the_best_rollout_lines(self):
        from eval_metrics import summarize_arm
        from render_report import summary_markdown
        from report_html import report_html

        def attempt(passed: int, scored: bool = True) -> dict:
            return {"resolved": passed == 4, "has_reward": scored, "f2p": passed / 4, "f2p_pass": passed, "f2p_total": 4,
                    "p2p": 1.0, "p2p_pass": 2, "p2p_total": 2, "partial": None,
                    "notes": "" if scored else "missing reward.json"}

        arm = summarize_arm([("a", [attempt(4), attempt(1), attempt(0, scored=False)])], 3, 1)
        self.assertEqual(arm["scored_rollouts"], 2)
        self.assertEqual(arm["micro"]["f2p_pass"], 4)
        self.assertEqual(
            {k: arm["micro_all"][k] for k in ("rollouts", "f2p_pass", "f2p_total", "p2p_pass", "p2p_total")},
            {"rollouts": 2, "f2p_pass": 5, "f2p_total": 8, "p2p_pass": 4, "p2p_total": 4},
        )
        doc = {"suite": "deepswe", "n_tasks": 1, "n_rollouts": 3, "icode": arm}
        md = summary_markdown(doc)
        self.assertIn("- Macro avg (best rollout per question) — F2P: **1.0000**", md)
        self.assertIn("- Micro (best rollout per question) — F2P: **1.0000** (4/4)", md)
        self.assertIn("- Micro (all 2 scored rollouts) — F2P: **0.6250** (5/8) · P2P: **1.0000** (4/4)", md)
        self.assertLess(md.index("Micro (best rollout"), md.index("Micro (all 2 scored"))
        page = report_html(doc)
        for label in ("Macro F2P (best rollout per question)", "Micro F2P (best rollout per question)",
                      "Micro F2P (all scored rollouts)"):
            self.assertIn(label, page)


if __name__ == "__main__":
    raise SystemExit(unittest.main())

