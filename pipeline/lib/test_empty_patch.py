#!/usr/bin/env python3
"""Empty patches and cut-off replies: why iCode left the repo unchanged, and how it is reported."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from eval_metrics import attempt_is_scored, joined_notes, summarize_arm, task_row  # noqa: E402
from icode_usage import provider_error_marker, usage_from_obj  # noqa: E402
from render_report import _attempt_from_trial, _p2p_cell, build_artifact, summary_markdown  # noqa: E402
from report_html import report_html  # noqa: E402

# DeepSWE runs the base repo's tests for an empty patch, so they pass.
YTT_REWARD = {"reward": 0, "f2p": 0.0, "f2p_total": 103, "f2p_passed": 0, "p2p": 1.0, "p2p_total": 1, "p2p_passed": 1}
SOLVED_REWARD = {"reward": 1, "f2p": 1.0, "f2p_total": 24, "f2p_passed": 24, "p2p": 1.0, "p2p_total": 2, "p2p_passed": 2}
API_401 = (
    "implement turn caught tool/runtime error; continuing (1/10): "
    "Error code: 401 - {'error': {'message': 'Authentication Fails'}}"
)
NAME_TOO_LONG = (
    "implement turn caught tool/runtime error; continuing (2/10): [Errno 36] File name too long: "
    "'/app/{\"file_path\": \"/app/src/a.ts\", \"content\": \"// retry on 401 or rate limit\"}'"
)


def final_line(*, model_calls=13, last_out=8192, result="") -> str:
    usage = {
        "input_tokens": 313669,
        "output_tokens": 44348,
        "total_tokens": 358017,
        "model_calls": model_calls,
        "last_input_tokens": 34002,
        "last_output_tokens": last_out,
    }
    return json.dumps({"session_id": "cli-1", "result": result, "duration": 170.5, "usage": usage, "ok": False})


def write_trial(
    trial: Path,
    *,
    task: str = "ytt-jsonpath-query-api",
    patch_bytes: int = 0,
    reward: dict | None = None,
    log: list[str] | None = None,
    exit_code: int | None = 1,
    **final,
) -> Path:
    (trial / "verifier").mkdir(parents=True)
    (trial / "verifier" / "reward.json").write_text(json.dumps(reward or YTT_REWARD), encoding="utf-8")
    (trial / "result.json").write_text(
        json.dumps({"task_name": f"datacurve/{task}", "task_id": {"path": f"/eval-runs/deep-swe/tasks/{task}"}}),
        encoding="utf-8",
    )
    agent = trial / "agent"
    agent.mkdir()
    (agent / "capture.json").write_text(json.dumps({"patch": {"bytes": patch_bytes}}), encoding="utf-8")
    lines = list(log or []) + [final_line(**final)]
    (agent / "icode.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if exit_code is not None:
        (agent / "icode-exit.txt").write_text(f"{exit_code}\n", encoding="utf-8")
    return trial


def attempt(tmp: str, max_tokens: int | None = None, **kwargs) -> dict:
    trial = write_trial(Path(tmp) / "ytt-jsonpath-query-api__zGkW2ar", **kwargs)
    return _attempt_from_trial(trial, trial / "verifier" / "reward.json", max_tokens)


class UsageTests(unittest.TestCase):
    def test_usage_carries_model_calls_and_the_last_reply(self):
        got = usage_from_obj(json.loads(final_line()))
        self.assertEqual(got["model_calls"], 13)
        self.assertEqual(got["last_output_tokens"], 8192)
        self.assertEqual(got["completion"], 44348)

    def test_an_icode_without_those_fields_gives_none(self):
        got = usage_from_obj({"usage": {"input_tokens": 10, "output_tokens": 2}})
        self.assertIsNone(got["model_calls"])
        self.assertIsNone(got["last_output_tokens"])


class ProviderErrorTests(unittest.TestCase):
    def test_an_api_error_in_an_icode_warning_counts(self):
        self.assertEqual(provider_error_marker(API_401), "authentication")

    def test_the_json_error_payload_counts(self):
        text = json.dumps({"error": "Error code: 402 - Insufficient Balance", "code": "error"})
        self.assertEqual(provider_error_marker(text), "insufficient balance")

    def test_a_connection_error_counts(self):
        text = "implement turn caught tool/runtime error; continuing (3/10): Connection error."
        self.assertEqual(provider_error_marker(text), "connection error")

    def test_the_models_final_result_never_counts(self):
        self.assertIsNone(provider_error_marker(final_line(result="The client retries on 401 and on rate limit.")))

    def test_a_filesystem_error_quoting_the_models_file_does_not_count(self):
        self.assertIsNone(provider_error_marker(NAME_TOO_LONG))

    def test_a_marker_inside_a_longer_number_does_not_count(self):
        text = "implement turn caught tool/runtime error; continuing (1/10): bad offset 14012"
        self.assertIsNone(provider_error_marker(text))


class CauseTests(unittest.TestCase):
    def test_a_cut_off_reply_that_left_no_patch_is_cut_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, log=[NAME_TOO_LONG])
        self.assertEqual(row["empty_patch_cause"], "cut_off")
        self.assertTrue(row["cut_off_reply"])
        self.assertIsNone(row["p2p"])
        self.assertEqual((row["grader_p2p_pass"], row["grader_p2p_total"]), (1, 1))
        self.assertEqual((row["model_calls"], row["last_output_tokens"], row["icode_exit"]), (13, 8192, 1))
        self.assertEqual(row["trial"], "ytt-jsonpath-query-api__zGkW2ar")
        self.assertIn("empty model.patch", row["notes"])
        self.assertIn("last reply cut off at max_tokens", row["notes"])
        # iCode's own failure, so it is a scored fail.
        self.assertTrue(attempt_is_scored(row))

    def test_a_model_api_error_is_infra_and_unscored(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, log=[API_401], last_out=40)
        self.assertEqual(row["empty_patch_cause"], "infra")
        self.assertTrue(row["infra_failure"])
        self.assertIn("infra: model API error (authentication)", row["notes"])
        self.assertFalse(attempt_is_scored(row))

    def test_no_model_call_is_infra(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, model_calls=0, last_out=0)
        self.assertEqual(row["empty_patch_cause"], "infra")
        self.assertIn("infra: no model call completed", row["notes"])

    def test_api_words_in_the_final_result_are_not_infra(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, last_out=500, result="Handled 401 and rate limit responses.")
        self.assertEqual(row["empty_patch_cause"], "no_edit")
        self.assertNotIn("infra_failure", row)

    def test_a_short_last_reply_is_no_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, last_out=500)
        self.assertEqual(row["empty_patch_cause"], "no_edit")
        self.assertNotIn("cut_off_reply", row)

    def test_the_runs_own_max_tokens_decides_a_cut_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, max_tokens=65536)
        # 8192 is far under 65536: the reply ended on its own.
        self.assertNotIn("cut_off_reply", row)
        self.assertEqual(row["empty_patch_cause"], "no_edit")

    def test_a_real_patch_with_a_cut_off_reply_is_flagged_without_a_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, max_tokens=65536, patch_bytes=900, last_out=65536, reward=SOLVED_REWARD)
        self.assertTrue(row["cut_off_reply"])
        self.assertNotIn("empty_patch", row)
        self.assertNotIn("empty_patch_cause", row)
        self.assertEqual(row["p2p"], 1.0)

    def test_no_exit_file_records_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, exit_code=None)
        self.assertIsNone(row["icode_exit"])


class OomTests(unittest.TestCase):
    """iCode killed by the kernel (exit 137): the grader's result stands, the note says why."""

    def test_exit_137_is_tagged_and_keeps_its_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, exit_code=137, max_tokens=65536, patch_bytes=900, last_out=500, reward=SOLVED_REWARD)
        self.assertTrue(row["oom_killed"])
        self.assertIn("iCode killed (exit 137, likely out of memory)", row["notes"])
        self.assertTrue(row["resolved"])
        self.assertTrue(attempt_is_scored(row))

    def test_exit_1_is_not_oom(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = attempt(tmp, exit_code=1, max_tokens=65536, last_out=500)
        self.assertNotIn("oom_killed", row)
        self.assertNotIn("exit 137", row["notes"])

    def test_harbor_out_of_memory_messages_name_the_cause(self):
        from score_results import OOM_CAUSE, infra_cause

        for message in (
            "Container main OOMKilled",
            "fork: Cannot allocate memory",
            "Command failed (exit 137)",
            "process exited with exit code 137",
        ):
            with self.subTest(message=message):
                self.assertEqual(infra_cause({"type": "RuntimeError", "message": message}), OOM_CAUSE)
        self.assertEqual(
            infra_cause({"type": "RuntimeError", "message": "Docker compose command failed: service main exited 1"}),
            "docker compose failed",
        )

    def test_the_summary_counts_oom_killed_rollouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            job = harness / "harbor_runs" / "jenkins-9" / "icode_deepswe_9"
            write_trial(job / "etree-xml-diff-patch__a", task="etree-xml-diff-patch", exit_code=137,
                        patch_bytes=900, reward=SOLVED_REWARD, last_out=500)
            write_trial(job / "etree-xml-diff-patch__b", task="etree-xml-diff-patch", exit_code=0,
                        patch_bytes=900, reward=SOLVED_REWARD, last_out=500)
            doc = build_artifact(
                suite="deepswe", model="m", api_base="b", task_ids=["etree-xml-diff-patch"], harness_dir=harness,
                baseline_dir=root / "baseline", n_rollouts=2, concurrency=2, cpus_each=1, run_id="jenkins-9",
            )
        self.assertEqual(doc["icode"]["oom_killed_rollouts"], 1)
        md = summary_markdown(doc)
        self.assertIn("- OOM-killed rollouts (iCode exit 137 or the container ran out of memory): **1**", md)
        self.assertIn("iCode killed (exit 137, likely out of memory) (1/2)", md)
        self.assertIn("OOM-killed rollouts 1.", report_html(doc))

    def test_harbor_run_names_the_oom_trials(self):
        import os
        import subprocess

        stage = LIB.parent / "stages" / "evaluate" / "harbor_run.sh"
        text = stage.read_text(encoding="utf-8")
        helper = text[text.index("oom_trials() {"):text.index("run_harbor() {")]
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs"
            write_trial(jobs / "icode_deepswe_9" / "etree__killed", exit_code=137)
            write_trial(jobs / "icode_deepswe_9" / "etree__fine", exit_code=1)
            proc = subprocess.run(
                ["bash", "-c", f'{helper}\noom_trials', "_"],
                capture_output=True, text=True, check=False,
                env={**os.environ, "JOBS_DIR": str(jobs), "PIPELINE_LIB": str(LIB)},
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "etree__killed\n")
        self.assertIn('oom="$(oom_trials)"', text)
        self.assertIn("trials ran out of memory ($(harness_display) exit 137 or the container OOM-killed)", text)
        # A scored Harbor retry must not be re-queued from leftover log text.
        guard = text[text.index('oom="$(oom_trials)"'):text.index("if [ -n \"$oom\" ]")]
        self.assertIn('[ "${rewards:-0}" -eq 0 ]', guard)
        # A previous build's list lives in this shared workspace. No OOM this run removes it.
        no_oom = text[text.index("else\n    # This workspace is shared.") : text.index("for tid in")]
        self.assertIn('rm -f "$HARNESS_DIR/oom_trials.txt"', no_oom)
        self.assertLess(no_oom.index('rm -f "$HARNESS_DIR/oom_trials.txt"'), no_oom.index("elif"))


class NotesAndCellTests(unittest.TestCase):
    def test_notes_are_joined_once_with_how_many_rollouts_had_them(self):
        rows = [
            {"notes": "empty model.patch"},
            {"notes": "empty model.patch; anticheat rejected"},
            {"notes": ""},
            {"notes": "empty model.patch"},
        ]
        self.assertEqual(joined_notes(rows, 4), "empty model.patch (3/4); anticheat rejected (1/4)")
        self.assertEqual(joined_notes([{"notes": "empty model.patch"}], 1), "empty model.patch")
        self.assertEqual(joined_notes([{"notes": ""}], 1), "-")

    def test_the_p2p_cell_shows_the_grader_but_says_excluded(self):
        row = task_row(
            "ytt",
            [{"f2p": 0.0, "f2p_pass": 0, "f2p_total": 103, "p2p": None, "empty_patch": True,
              "grader_p2p_pass": 1, "grader_p2p_total": 1, "notes": "empty model.patch", "has_reward": True}],
            1,
        )
        self.assertEqual(_p2p_cell(row), "excluded (grader 1/1)")
        self.assertEqual(_p2p_cell({"p2p": 1.0, "p2p_pass": 2, "p2p_total": 2}), "1.0000 (2/2)")


def make_harness(root: Path, *, empty_log: list[str] | None = None) -> Path:
    """One question solved, one that left an empty patch."""
    job = root / "harness" / "harbor_runs" / "jenkins-1" / "icode_deepswe"
    write_trial(job / "igel__a", task="igel", patch_bytes=900, reward=SOLVED_REWARD, last_out=523, model_calls=74, exit_code=0)
    write_trial(job / "ytt__b", task="ytt", log=empty_log)
    return root / "harness"


def artifact(harness: Path, **kwargs) -> dict:
    return build_artifact(
        suite="deepswe",
        model="deepseek-flash",
        api_base="https://api.deepseek.com/v1",
        task_ids=["igel", "ytt"],
        harness_dir=harness,
        baseline_dir=harness / "baseline",
        n_rollouts=1,
        concurrency=1,
        cpus_each=2,
        run_id="jenkins-1",
        **kwargs,
    )


class SectionTests(unittest.TestCase):
    def test_empty_patches_get_their_own_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = artifact(make_harness(Path(tmp)))
        arm = doc["icode"]
        self.assertEqual(arm["empty_patch_rollouts"], 1)
        self.assertEqual(arm["cut_off_rollouts"], 1)
        item = arm["empty_patches"][0]
        self.assertEqual((item["id"], item["rollout"], item["trial"], item["cause"]), ("ytt", 1, "ytt__b", "cut_off"))
        self.assertEqual((item["f2p_pass"], item["f2p_total"]), (0, 103))
        self.assertEqual((item["grader_p2p_pass"], item["grader_p2p_total"]), (1, 1))
        self.assertNotIn("empty_patches", doc["icode_raw"])
        tasks = {row["id"]: row for row in arm["tasks"]}
        # An all-perfect question still keeps no rollouts list.
        self.assertNotIn("rollouts", tasks["igel"])
        self.assertIn("rollouts", tasks["ytt"])
        # The P2P averages cover the question that submitted a patch only.
        self.assertEqual(arm["p2p_excluded_empty_patch"], 1)
        self.assertEqual(arm["micro"]["p2p_total"], 2)
        self.assertEqual(arm["micro"]["partial_total"], 26)

        md = summary_markdown(doc)
        self.assertIn("- Empty model.patch (repo unchanged after iCode): **1** of 2 rollouts (infra 0 · cut_off 1 · no_edit 0)", md)
        self.assertIn("- Cut-off replies (last reply hit max_tokens): **1** rollouts", md)
        self.assertIn("(1 excluded: best rollout left an empty model.patch)", md)
        self.assertIn("excluded (grader 1/1)", md)
        self.assertLess(md.index("| # | task"), md.index("## Empty patch: iCode left the repo unchanged"))
        self.assertLess(md.index("## Empty patch"), md.index("unscored rollouts: 0"))

        page = report_html(doc)
        self.assertIn("<h2>Empty patch: iCode left the repo unchanged</h2>", page)
        self.assertIn("Empty model.patch 1 of 2 rollouts (infra 0, cut_off 1, no_edit 0).", page)
        self.assertIn("<td>ytt__b</td>", page)

    def test_no_empty_patch_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "harness" / "harbor_runs" / "jenkins-1" / "icode_deepswe"
            write_trial(job / "igel__a", task="igel", patch_bytes=900, reward=SOLVED_REWARD, last_out=523)
            doc = build_artifact(
                suite="deepswe", model="m", api_base="b", task_ids=["igel"], harness_dir=root / "harness",
                baseline_dir=root, n_rollouts=1, concurrency=1, cpus_each=2, run_id="jenkins-1",
            )
        self.assertEqual(doc["icode"]["empty_patches"], [])
        self.assertIn("empty-patch rollouts: 0", summary_markdown(doc))
        page = report_html(doc)
        self.assertIn("<h2>Empty patch: iCode left the repo unchanged</h2>", page)
        self.assertIn("<p>None</p>", page[page.index("<h2>Empty patch"):])

    def test_an_infra_rollout_is_listed_and_unscored(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = artifact(make_harness(Path(tmp), empty_log=[API_401]))
        arm = doc["icode"]
        self.assertEqual(arm["empty_patches"][0]["cause"], "infra")
        self.assertEqual(arm["unscored_rollouts"], 1)
        self.assertEqual(arm["unscored_tasks"][0]["id"], "ytt")
        self.assertIn("infra 1", summary_markdown(doc))

    def test_the_run_max_tokens_reaches_every_rollout(self):
        protocol = {"model_params": {"max_tokens": 65536, "max_iterations": 500}}
        with tempfile.TemporaryDirectory() as tmp:
            doc = artifact(make_harness(Path(tmp)), eval_protocol=protocol)
        self.assertEqual(doc["icode"]["cut_off_rollouts"], 0)
        self.assertEqual(doc["icode"]["empty_patches"][0]["cause"], "no_edit")
        self.assertIn("max_tokens `65536` · max_iterations `500`", summary_markdown(doc))
        self.assertIn("max_tokens 65536. max_iterations 500.", report_html(doc))

    def test_summarize_arm_counts_cut_off_rollouts_on_real_patches_too(self):
        rows = [{"resolved": True, "f2p": 1.0, "p2p": 1.0, "cut_off_reply": True, "has_reward": True}]
        arm = summarize_arm([("a", rows)], 1, 1)
        self.assertEqual(arm["cut_off_rollouts"], 1)
        self.assertEqual(arm["empty_patch_rollouts"], 0)


if __name__ == "__main__":
    unittest.main()
