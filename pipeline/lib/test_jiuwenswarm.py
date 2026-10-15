#!/usr/bin/env python3
"""JiuwenSwarm harness: the result line, usage helpers, run document, harness table, canary verdict.

Stdlib only. test_model_key.py covers the agent's key handling and run command
with a fake Harbor; test_harness_store.sh covers the build and the store.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parent
ROOT = LIB.parent
sys.path.insert(0, str(LIB))

import harness  # noqa: E402
import icode_usage  # noqa: E402
import jiuwenswarm_trial  # noqa: E402
import jiuwenswarm_usage  # noqa: E402
from canary_verdict import load_config, load_facts, verdict  # noqa: E402
from test_canary import TESTDATA  # noqa: E402

RESULT_OK = {
    "schema_version": "0.1",
    "type": "result",
    "status": "completed",
    "exit_code": 0,
    "output": 'Done. {"not": "usage"}',
    "usage": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500, "cache_tokens": 800, "model_calls": 4},
}
RESULT_FAILED = {
    "schema_version": "0.1",
    "type": "result",
    "status": "failed",
    "exit_code": 1,
    "output": None,
    "error": {"code": "RUNTIME_ERROR", "message": "[181001] model call failed, reason: APIConnectionError: Connection error."},
    "usage": {"model_calls": 0},
}


def event(seq: int, kind: str, payload: dict) -> dict:
    return {"schema_version": "0.1", "type": "event", "sequence": seq, "request_id": "r", "event_type": kind, "payload": payload}


def write_trial(root: Path, result: dict, exit_code: str = "0") -> Path:
    agent = root / "trial" / "agent"
    agent.mkdir(parents=True)
    lines = [
        event(1, "chat.tool_call", {"tool": "bash", "arguments": {"command": "pytest -q"}}),
        event(2, "chat.usage_summary", {"usage": {"input_tokens": 1, "output_tokens": 1}}),
        result,
    ]
    (agent / jiuwenswarm_usage.LOG).write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    (agent / jiuwenswarm_usage.EXIT).write_text(exit_code + "\n", encoding="utf-8")
    return agent.parent


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_last_result_line_wins_over_events(self):
        trial = write_trial(self.root, RESULT_OK)
        got = jiuwenswarm_usage.read_result(trial / "agent" / jiuwenswarm_usage.LOG)
        self.assertEqual((got["status"], got["exit_code"]), ("completed", 0))
        self.assertEqual(jiuwenswarm_usage.result_error(got), "")

    def test_a_failed_result_names_its_error(self):
        trial = write_trial(self.root, RESULT_FAILED, "1")
        got = jiuwenswarm_usage.read_result(trial / "agent" / jiuwenswarm_usage.LOG)
        self.assertTrue(jiuwenswarm_usage.result_error(got).startswith("RUNTIME_ERROR: [181001] model call failed"))

    def test_no_result_line(self):
        path = self.root / "j.jsonl"
        path.write_text(json.dumps(event(1, "chat.delta", {"text": "hi"})) + "\n{broken\n", encoding="utf-8")
        self.assertIsNone(jiuwenswarm_usage.read_result(path))
        self.assertIsNone(jiuwenswarm_usage.read_result(self.root / "missing.jsonl"))

    def test_shared_helpers_read_the_jiuwenswarm_files(self):
        trial = write_trial(self.root, RESULT_OK)
        usage = icode_usage.find_icode_usage(trial)
        self.assertEqual(
            {k: usage[k] for k in ("prompt", "completion", "total", "cache_hit", "model_calls")},
            {"prompt": 1200, "completion": 300, "total": 1500, "cache_hit": 800, "model_calls": 4},
        )
        self.assertEqual(icode_usage.icode_exit_code(trial), 0)
        self.assertIn('"type": "result"', icode_usage.icode_log_text(trial))

    def test_icode_files_win_when_both_exist(self):
        trial = write_trial(self.root, RESULT_OK, "1")
        (trial / "agent" / "icode-exit.txt").write_text("0\n", encoding="utf-8")
        (trial / "agent" / "icode.txt").write_text("icode log\n", encoding="utf-8")
        self.assertEqual(icode_usage.icode_exit_code(trial), 0)
        self.assertEqual(icode_usage.icode_log_text(trial), "icode log\n")

    def test_provider_error_marker_reads_the_result_error_object(self):
        trial = write_trial(self.root, RESULT_FAILED, "1")
        self.assertEqual(icode_usage.provider_error_marker(icode_usage.icode_log_text(trial)), "connection error")
        ok = write_trial(self.root / "ok", RESULT_OK)
        self.assertIsNone(icode_usage.provider_error_marker(icode_usage.icode_log_text(ok)))


class RunDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task = self.root / "task.md"
        self.task.write_text("Fix the bug.\n", encoding="utf-8")
        self.out = self.root / "run.json"

    def tearDown(self):
        self.tmp.cleanup()

    def rundoc(self, **env: str) -> int:
        clean = {k: v for k, v in os.environ.items() if not k.startswith("JIUWENSWARM_")}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return jiuwenswarm_trial.rundoc(str(self.task), "/work/repo", str(self.out))

    def test_defaults(self):
        self.assertEqual(self.rundoc(), 0)
        doc = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(doc, {
            "schema_version": "0.1",
            "type": "run",
            "input": "Fix the bug.\n",
            "mode": "agent.code.normal",
            "workspace": {"cwd": "/work/repo", "project_dir": "/work/repo", "trusted_dirs": ["/work/repo"]},
            "max_turns": 500,
        })

    def test_env_sets_mode_turns_and_timeout(self):
        self.assertEqual(self.rundoc(JIUWENSWARM_AGENT_MODE="agent.code.plan", JIUWENSWARM_MAX_TURNS="40",
                                     JIUWENSWARM_TIMEOUT_SECONDS="3000"), 0)
        doc = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((doc["mode"], doc["max_turns"], doc["timeout_seconds"]), ("agent.code.plan", 40, 3000))
        self.assertNotIn("permissions", doc)

    def test_bad_values_stop_before_the_document(self):
        for env in ({"JIUWENSWARM_AGENT_MODE": "agent.fast"}, {"JIUWENSWARM_MAX_TURNS": "0"},
                    {"JIUWENSWARM_MAX_TURNS": "1001"}, {"JIUWENSWARM_TIMEOUT_SECONDS": "-5"},
                    {"JIUWENSWARM_MAX_TURNS": "lots"}):
            with self.subTest(env=env):
                self.out.unlink(missing_ok=True)
                self.assertEqual(self.rundoc(**env), 2)
                self.assertFalse(self.out.exists())

    def test_seed_refuses_without_the_data_dir_or_endpoint(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(jiuwenswarm_trial.seed(), 2)
        with mock.patch.dict(os.environ, {"JIUWENSWARM_DATA_DIR": str(self.root / "d"), "API_BASE": "x"}, clear=True):
            self.assertEqual(jiuwenswarm_trial.seed(), 2)

    def test_env_lines_refuse_what_dotenv_cannot_carry(self):
        self.assertEqual(jiuwenswarm_trial._env_line("MODEL_NAME", "deepseek-flash"), 'MODEL_NAME="deepseek-flash"\n')
        for bad in ('a"b', "a\nb", "a\\b"):
            with self.assertRaises(ValueError):
                jiuwenswarm_trial._env_line("API_BASE", bad)


class HarnessTableTests(unittest.TestCase):
    def shell(self, function: str, name: str) -> str:
        script = (
            f'die() {{ echo "$*" >&2; exit 1; }}; '
            f'eval "$(sed -n "/^{function}() {{/,/^}}/p" "$1")"; HARNESS="$2" {function}'
        )
        proc = subprocess.run(["bash", "-c", script, "x", str(ROOT / "stages" / "_common.sh"), name],
                              capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    def test_the_shell_table_matches(self):
        for name, row in harness.HARNESSES.items():
            with self.subTest(harness=name):
                self.assertEqual(self.shell("harness_display", name), row.display)
                self.assertEqual(self.shell("harness_mount_target", name), row.mount)

    def test_supported_benchmarks_match_harness_check_supported(self):
        text = (LIB / "harness_lib.sh").read_text(encoding="utf-8")
        body = re.search(r"^harness_check_supported\(\) \{\n(.*?)^\}", text, re.S | re.M).group(1)
        self.assertIn('"$HARNESS" = jiuwenswarm', body)
        self.assertIn('!= deepswe', body)
        self.assertEqual(harness.get("jiuwenswarm").benchmarks, ("deepswe",))
        self.assertTrue(harness.supports(harness.get("icode"), "lolbench"))
        self.assertFalse(harness.supports(harness.get("jiuwenswarm"), "lolbench"))

    def test_agent_paths_exist(self):
        for row in harness.HARNESSES.values():
            for path in (row.agent, row.canary):
                module, _, cls = path.partition(":")
                self.assertTrue((LIB / f"{module}.py").is_file(), path)
                self.assertRegex((LIB / f"{module}.py").read_text(encoding="utf-8"), rf"(?m)^class {cls}\(")

    def test_lookup(self):
        self.assertEqual(harness.get(None).name, "icode")
        self.assertEqual(harness.current({"HARNESS": "jiuwenswarm"}).mount, "/opt/jiuwenswarm-host")
        self.assertEqual(harness.from_record({"name": "nope"}).name, "icode")
        self.assertEqual(harness.from_record(None).name, "icode")
        with self.assertRaises(ValueError):
            harness.get("claude")

    def test_cli(self):
        def run(*args: str, env_harness: str = "") -> subprocess.CompletedProcess[str]:
            env = {**os.environ, "HARNESS": env_harness}
            return subprocess.run([sys.executable, str(LIB / "harness.py"), *args], capture_output=True, text=True, env=env)

        self.assertEqual(run("get", "mount", env_harness="jiuwenswarm").stdout.strip(), "/opt/jiuwenswarm-host")
        self.assertEqual(run("get", "icode", "display").stdout.strip(), "iCode")
        self.assertEqual(run("get", "jiuwenswarm", "benchmarks").stdout.strip(), "deepswe")
        self.assertEqual(run("get", "icode", "benchmarks").stdout.strip(), "")
        self.assertEqual(run("get", "nope", "display").returncode, 2)
        self.assertEqual(run("get", "icode", "colour").returncode, 2)


class CanaryVerdictTests(unittest.TestCase):
    """The pass fixture, as a JiuwenSwarm canary would have recorded it."""

    def setUp(self):
        self.config = load_config()
        self.facts = load_facts(TESTDATA / "pass")
        self.facts["host"] = {
            **(self.facts.get("host") or {}),
            "harness": "jiuwenswarm",
            "mount": "/opt/jiuwenswarm-host",
            "runtime_probes": ["python", "runner", "seed"],
        }
        self.facts["runtime"] = {
            name: {"name": name, "exit": 0, "error": None} for name in ("python", "runner", "seed")
        }
        probe = json.loads(json.dumps(self.facts["probe"]))
        probe["mount"]["path"] = "/opt/jiuwenswarm-host"
        probe["env_names"] = sorted({*probe["env_names"], "API_KEY"} - {"DEEPSEEK_API_KEY"})
        self.facts["probe"] = probe

    def checks(self) -> dict:
        return {k: v["status"] for k, v in verdict(self.facts, self.config)["checks"].items()}

    def test_a_clean_jiuwenswarm_canary_passes(self):
        got = self.checks()
        self.assertEqual(got["runtime"], "pass")
        self.assertEqual(got["env_names"], "pass")
        self.assertEqual(got["icode_home"], "skip")
        self.assertEqual({k for k, v in got.items() if v == "fail"}, set())

    def test_a_runtime_that_does_not_start_fails(self):
        self.facts["runtime"]["python"] = {
            "exit": 1, "error": "version `GLIBC_2.28' not found (required by python3.11)",
        }
        del self.facts["runtime"]["seed"]
        detail = verdict(self.facts, self.config)["checks"]["runtime"]
        self.assertEqual(detail["status"], "fail")
        self.assertIn("python: exit 1 (version `GLIBC_2.28' not found", detail["detail"])
        self.assertIn("seed: not recorded", detail["detail"])

    def test_api_key_is_allowed_for_jiuwenswarm_only(self):
        self.facts["host"]["harness"] = "icode"
        self.assertEqual(self.checks()["env_names"], "fail")

    def test_python_env_into_the_jiuwenswarm_mount_fails(self):
        self.facts["probe"]["python_env"] = {"PYTHONPATH": "/opt/jiuwenswarm-host/venv/lib", "VIRTUAL_ENV": None}
        self.assertEqual(self.checks()["python_env"], "fail")

    def test_task_mcp_servers_still_fail(self):
        self.facts["host"]["task_mcp_servers"] = 2
        self.assertEqual(self.checks()["icode_home"], "fail")


class ProbeRuntimeTests(unittest.TestCase):
    def test_runtime_records_the_exit_and_last_error_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "CANARY_LOG_DIR": tmp}
            probe = str(LIB / "canary_probe.sh")
            subprocess.run(["bash", probe, "runtime", "python", "true"], env=env, check=True)
            subprocess.run(["bash", probe, "runtime", "seed", "bash", "-c", "echo one >&2; echo last line >&2; exit 4"],
                           env=env, check=True)
            bad = subprocess.run(["bash", probe, "runtime", "Bad-Name", "true"], env=env, capture_output=True)
            facts = load_facts(Path(tmp))["runtime"]
        self.assertEqual(facts["python"]["exit"], 0)
        self.assertEqual((facts["seed"]["exit"], facts["seed"]["error"]), (4, "last line"))
        self.assertEqual(bad.returncode, 2)


JIUWEN_PROTOCOL_HARNESS = {
    "name": "jiuwenswarm",
    "display": "JiuwenSwarm",
    "mount": "/opt/jiuwenswarm-host",
    "mode": "git",
    "source": "store",
    "bundle_sha256": "b" * 64,
    "tree_sha256": "c" * 64,
    "built_on": "linux/amd64",
    "settings": {"agent_mode": "agent.code.normal", "max_turns": 500, "timeout_seconds": None},
}


def jiuwen_isolation() -> dict:
    return {
        "mode": "git",
        "harness": "jiuwenswarm",
        "sanitizer": "",
        "sourceless": None,
        "tree_sha256": "c" * 64,
        "bundle_sha256": "b" * 64,
        "mount": {"count": 1, "target": "/opt/jiuwenswarm-host", "read_only": True},
        "leak_scan": {"scanner": "mac-k3d-leakscan-v1", "hit_tasks": [], "report_sha256": "d" * 64},
        "canary": {"version": "mac-k3d-canary-v1", "status": "pass", "summary_sha256": "e" * 64},
    }


class ReportTests(unittest.TestCase):
    def test_official_isolation_takes_the_stored_tree_digest_instead_of_the_sanitizer(self):
        from check_report import official_isolation_errors

        doc = {"eval_protocol": {"harness": JIUWEN_PROTOCOL_HARNESS}}
        self.assertEqual(official_isolation_errors(jiuwen_isolation(), doc), [])
        unhashed = {**jiuwen_isolation(), "bundle_sha256": ""}
        self.assertTrue(any("bundle_sha256" in e for e in official_isolation_errors(unhashed, doc)))
        at_icode = {**jiuwen_isolation(), "mount": {"count": 1, "target": "/opt/icode-host", "read_only": True}}
        self.assertTrue(any("/opt/jiuwenswarm-host" in e for e in official_isolation_errors(at_icode, doc)))
        # A JiuwenSwarm isolation record under an iCode protocol is not passed off as iCode.
        errors = official_isolation_errors(jiuwen_isolation(), {"eval_protocol": {}})
        self.assertTrue(any("eval_protocol.harness is icode" in e for e in errors), errors)

    def test_the_report_validator_takes_either_harness_name(self):
        from check_report import validate

        self.assertFalse(any("harness should" in e for e in validate({"harness": "jiuwenswarm"})))
        self.assertTrue(any("harness should" in e for e in validate({"harness": "claude"})))

    def test_the_summary_names_the_harness_and_its_bundle(self):
        from render_report import harness_display, summary_markdown

        protocol = {"harness": JIUWEN_PROTOCOL_HARNESS, "icode": {"mode": "git", "source": "gitcode"}}
        doc = {"suite": "deepswe", "n_tasks": 1, "n_rollouts": 1, "icode": {"tasks": []}, "eval_protocol": protocol}
        text = summary_markdown(doc)
        self.assertIn("Harness: JiuwenSwarm (git) · bundle sha256 `" + "b" * 64 + "` from store", text)
        self.assertIn("- JiuwenSwarm mode: `git`", text)
        self.assertNotIn("iCode mode", text)
        self.assertEqual(harness_display({}), "iCode")

    def test_an_oom_kill_note_names_jiuwenswarm(self):
        import render_report

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "alpha__1"
            (trial / "agent").mkdir(parents=True)
            (trial / "agent" / jiuwenswarm_usage.EXIT).write_text("137\n", encoding="utf-8")
            (trial / "result.json").write_text(json.dumps({"task_name": "alpha"}), encoding="utf-8")
            row = render_report._attempt_from_trial(trial, None)
        self.assertEqual(row["icode_exit"], 137)
        self.assertIn("JiuwenSwarm killed (exit 137", row["notes"])

    def test_shards_on_different_bundles_are_a_mixed_measurement(self):
        from aggregate_runs import shard_mismatches, shard_row

        def protocol(sha):
            return {"harness": {**JIUWEN_PROTOCOL_HARNESS, "bundle_sha256": sha}, "model_params": {"model": "m"}}

        same = [shard_row(1, ["1"], protocol("b" * 64)), shard_row(2, ["2"], protocol("b" * 64))]
        self.assertEqual(shard_mismatches(same), {})
        mixed = [shard_row(1, ["1"], protocol("b" * 64)), shard_row(2, ["2"], protocol("f" * 64))]
        self.assertEqual(shard_mismatches(mixed), {"harness_bundle_sha256": ["b" * 64, "f" * 64]})
        # An arm64 shard builds its own bundle from the same pin; that is not a mismatch.
        arm = protocol("f" * 64)
        arm["harness"]["built_on"], arm["harness"]["bundle_platform"] = "linux/arm64", "linux/arm64"
        self.assertEqual(shard_mismatches([same[0], shard_row(2, ["2"], arm)]), {})

    def test_an_official_report_needs_one_harness_bundle_per_platform(self):
        from check_report import official_provenance_errors

        doc = {"eval_protocol": {"harness": JIUWEN_PROTOCOL_HARNESS, "isolation": jiuwen_isolation(),
                                 "model_params_mismatch": {"harness_bundle_sha256": ["b" * 64, "f" * 64]}}}
        errors = official_provenance_errors(doc)
        self.assertIn("eval_protocol.model_params_mismatch.harness_bundle_sha256: "
                      "the shards ran different harness bundles on one platform", errors)
        doc["eval_protocol"]["model_params_mismatch"] = {"max_tokens": [1, 2]}
        self.assertFalse(any("harness" in e for e in official_provenance_errors(doc)))

    def test_the_protocol_inputs_name_jiuwenswarm_settings(self):
        import provenance

        env = {"HARNESS": "jiuwenswarm", "JIUWENSWARM_MAX_TURNS": "120", "JIUWENSWARM_TIMEOUT_SECONDS": "900"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env):
            work = Path(tmp)
            (work / "harness.json").write_text(json.dumps({"name": "jiuwenswarm", "mode": "release",
                                                           "bundle_sha256": "b" * 64}), encoding="utf-8")
            record = provenance.harness_protocol(work)
            inputs = provenance.model_inputs("jiuwenswarm")
        self.assertEqual((record["name"], record["mount"], record["mode"]), ("jiuwenswarm", "/opt/jiuwenswarm-host", "release"))
        self.assertEqual(record["settings"], {"agent_mode": "agent.code.normal", "max_turns": 120, "timeout_seconds": 900})
        self.assertEqual((inputs["provider"], inputs["reasoning_effort"], inputs["max_iterations"]), ("OpenAI", "default", 120))
        self.assertIn("timeout_seconds 900", provenance.harness_line(record))


if __name__ == "__main__":
    unittest.main()
