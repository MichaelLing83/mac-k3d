#!/usr/bin/env python3
"""Unit tests for env_check (the first-time oracle + nop check in tasks/images)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

import env_check  # noqa: E402
from provenance import env_check_record, inspect_image  # noqa: E402

F2P, P2P = 37, 451


def grade(fp: int, pp: int, *, f2p: int = F2P, p2p: int = P2P) -> dict:
    """reward.json as DeepSWE's tests/grader.py writes it."""
    total = f2p + p2p
    return {
        "reward": 1 if (f2p > 0 and fp == f2p and pp == p2p) else 0,
        "f2p_total": f2p,
        "f2p_passed": fp,
        "p2p_total": p2p,
        "p2p_passed": pp,
        "f2p": fp / f2p if f2p else 0.0,
        "p2p": pp / p2p if p2p else 1.0,
        "partial": (fp + pp) / total if total else 0.0,
    }


APPLY_FAILED = {**grade(0, 0), "reward": 0, "apply_failed": 1}
ORACLE_PASS = grade(F2P, P2P)
NOP_PASS = grade(0, P2P)


def jobs(root: Path, name: str, *, reward: dict | None = None, text: str | None = None,
         exception: str = "") -> Path:
    """A Harbor jobs dir with one trial: <jobs>/<job>/<trial>/{result.json,verifier/}."""
    trial = root / name / "job" / f"{name}__abc"
    (trial / "verifier").mkdir(parents=True)
    if reward is not None:
        (trial / "verifier" / "reward.json").write_text(json.dumps(reward), encoding="utf-8")
    if text is not None:
        (trial / "verifier" / "reward.txt").write_text(text, encoding="utf-8")
    info = {"exception_type": exception} if exception else None
    (trial / "result.json").write_text(json.dumps({"exception_info": info}), encoding="utf-8")
    return root / name


def task_dir(root: Path) -> Path:
    task = root / "alpha"
    for sub in ("tests", "solution", "environment"):
        (task / sub).mkdir(parents=True)
    (task / "task.toml").write_text(
        '[environment]\ndocker_image = "public.ecr.aws/d3j8x8q7/swe-bench-202605:alpha-v1.1"\n',
        encoding="utf-8",
    )
    (task / "tests" / "test.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (task / "solution" / "solve.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (task / "environment" / "Dockerfile").write_text("FROM public.ecr.aws/x8v8d7g8/mars-base:latest\n",
                                                      encoding="utf-8")
    return task


def run(*argv: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return env_check.main(list(argv))


class PassRuleTests(unittest.TestCase):
    def verdict(self, oracle: dict, nop: dict | None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            o = jobs(root, "oracle", **oracle)
            n = jobs(root, "nop", **nop) if nop is not None else None
            return env_check.judge(o, n, source="built", platform="linux/arm64")

    def test_oracle_solves_and_nop_leaves_the_question_unsolved(self):
        got = self.verdict({"reward": ORACLE_PASS}, {"reward": NOP_PASS})
        self.assertTrue(got["pass"], got)
        self.assertTrue(got["stored"])
        self.assertEqual(got["reason"], "")
        self.assertEqual((got["oracle"]["f2p_passed"], got["oracle"]["p2p_passed"]), (F2P, P2P))
        self.assertEqual((got["nop"]["f2p_passed"], got["nop"]["p2p_passed"]), (0, P2P))

    def test_nop_may_pass_some_fail_to_pass_tests(self):
        got = self.verdict({"reward": ORACLE_PASS}, {"reward": grade(F2P - 1, P2P)})
        self.assertTrue(got["pass"], got)

    def test_each_failure_names_its_cause_and_only_grader_answers_are_stored(self):
        cases = (
            ({"reward": grade(35, P2P)}, None, "oracle f2p 35/37", True),
            ({"reward": grade(F2P, 450)}, None, "oracle p2p 450/451", True),
            ({"reward": APPLY_FAILED}, None, "oracle patch did not apply", True),
            ({"reward": grade(0, P2P, f2p=0)}, None, "oracle f2p 0/0 (no fail-to-pass tests)", True),
            ({"reward": {"reward": 1}}, None, "oracle reward.json has no F2P/P2P counts", True),
            ({"text": "-1\n"}, None, "oracle verifier crashed (reward -1.0)", False),
            ({"exception": "AgentTimeoutError"}, None, "oracle left no reward.json (AgentTimeoutError)", False),
            ({}, None, "oracle left no reward.json", False),
            ({"reward": ORACLE_PASS}, {"reward": grade(F2P, P2P)}, "nop f2p 37/37 (passes without the fix)", True),
            ({"reward": ORACLE_PASS}, {"reward": grade(0, 400)}, "nop p2p 400/451", True),
            ({"reward": ORACLE_PASS}, {"reward": APPLY_FAILED}, "nop patch did not apply", True),
            ({"reward": ORACLE_PASS}, {"text": "-1"}, "nop verifier crashed (reward -1.0)", False),
            ({"reward": ORACLE_PASS}, {"exception": "EnvironmentStartTimeoutError"},
             "nop left no reward.json (EnvironmentStartTimeoutError)", False),
            ({"reward": ORACLE_PASS}, None, "nop not run", False),
        )
        for oracle, nop, reason, stored in cases:
            with self.subTest(reason=reason):
                got = self.verdict(oracle, nop)
                self.assertFalse(got["pass"], got)
                self.assertEqual(got["reason"], f"environment check failed: {reason} on built linux/arm64")
                self.assertEqual(got["stored"], stored, got)

    def test_a_failing_oracle_does_not_blame_the_nop(self):
        got = self.verdict({"reward": grade(35, P2P)}, {"reward": grade(F2P, P2P)})
        self.assertEqual(got["reason"], "environment check failed: oracle f2p 35/37 on built linux/arm64")

    def test_no_image_is_a_fail_that_is_not_stored(self):
        got = env_check.no_image("native build failed", source="built", platform="linux/arm64")
        self.assertEqual((got["pass"], got["stored"]), (False, False))
        self.assertEqual(got["reason"], "environment check failed: native build failed on built linux/arm64")

    def test_oracle_only_judges_the_first_half(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = jobs(root, "good", reward=ORACLE_PASS)
            bad = jobs(root, "bad", reward=APPLY_FAILED)
            key = root / "key.json"
            key.write_text("{}", encoding="utf-8")
            self.assertEqual(run("judge", "--oracle", str(good), "--oracle-only", "--key", str(key)), 0)
            self.assertEqual(run("judge", "--oracle", str(bad), "--oracle-only", "--key", str(key)), 1)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.task = task_dir(self.root)
        self.base = dict(benchmark="deepswe", source="built", platform="linux/arm64", image_id="sha256:aaa",
                         harbor_version="0.22.0", base_image="mars-base@sha256:111")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in ("JOB_NAME", "BUILD_NUMBER"):
            os.environ.pop(name, None)

    def tearDown(self):
        self._tmp.cleanup()

    def key(self, **over) -> dict:
        return env_check.make_key(self.task, **{**self.base, **over})

    def test_every_input_of_the_image_is_in_the_key(self):
        before = self.key()
        edits = {
            "task.toml": lambda: (self.task / "task.toml").write_text(
                '[environment]\ndocker_image = "x:alpha-v1.2"\n', encoding="utf-8"),
            "a test file": lambda: (self.task / "tests" / "test.sh").write_text("#!/bin/sh\nexit 1\n",
                                                                                  encoding="utf-8"),
            "a new test file": lambda: (self.task / "tests" / "extra.py").write_text("", encoding="utf-8"),
            "the solution": lambda: (self.task / "solution" / "solve.sh").write_text("#!/bin/sh\ntrue\n",
                                                                                       encoding="utf-8"),
            "the Dockerfile": lambda: (self.task / "environment" / "Dockerfile").write_text(
                "FROM public.ecr.aws/x8v8d7g8/mars-base:latest\nRUN true\n", encoding="utf-8"),
        }
        for name, edit in edits.items():
            with self.subTest(changed=name):
                edit()
                after = self.key()
                self.assertNotEqual(after, before)
                before = after
        for part, value in (("image_id", "sha256:bbb"), ("harbor_version", "0.23.0"),
                            ("base_image", "mars-base@sha256:222"), ("platform", "linux/amd64")):
            with self.subTest(changed=part):
                self.assertNotEqual(self.key(**{part: value}), before)

    def test_a_published_image_ignores_the_dockerfile(self):
        before = self.key(source="published", base_image="")
        (self.task / "environment" / "Dockerfile").write_text("FROM other\n", encoding="utf-8")
        self.assertEqual(self.key(source="published", base_image=""), before)
        self.assertNotIn("dockerfile_sha256", before)
        self.assertNotIn("base_image", before)

    def test_cached_exits_0_for_a_stored_pass_2_for_a_stored_fail_1_for_a_miss(self):
        store = self.root / "store"
        build = self.root / "env_check.json"
        key = self.root / "key.json"
        out = self.root / "hit.json"
        key.write_text(json.dumps(self.key()), encoding="utf-8")
        cached = ("cached", "--store", str(store), "--task", "alpha", "--key", str(key), "--out", str(out))
        self.assertEqual(run(*cached), 1)

        def store_verdict(verdict: dict) -> None:
            path = self.root / "verdict.json"
            path.write_text(json.dumps(verdict), encoding="utf-8")
            run("record", "--store", str(store), "--task", "alpha", "--key", str(key),
                "--verdict", str(path), "--build-record", str(build))

        with tempfile.TemporaryDirectory() as tmp:
            o = jobs(Path(tmp), "oracle", reward=ORACLE_PASS)
            n = jobs(Path(tmp), "nop", reward=NOP_PASS)
            store_verdict(env_check.judge(o, n, source="built", platform="linux/arm64"))
        self.assertEqual(run(*cached), 0)
        hit = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(hit["verdict"]["pass"])
        self.assertEqual(hit["checked_in"], "local#0")

        with tempfile.TemporaryDirectory() as tmp:
            o = jobs(Path(tmp), "oracle", reward=grade(35, P2P))
            store_verdict(env_check.judge(o, None, source="built", platform="linux/arm64"))
        self.assertEqual(run(*cached), 2)

        key.write_text(json.dumps(self.key(image_id="sha256:rebuilt")), encoding="utf-8")
        self.assertEqual(run(*cached), 1)

    def test_a_result_that_is_not_stored_leaves_the_cache_alone(self):
        store = self.root / "store"
        key = self.key()
        verdict = env_check.no_image("native build failed", source="built", platform="linux/arm64")
        env_check.record(store=store, task="alpha", key=key, verdict=verdict,
                         build_record=self.root / "env_check.json", image="", reused=False)
        self.assertFalse(env_check.store_path(store, "alpha").exists())
        self.assertIsNone(env_check.cached(store, "alpha", key))

    def test_published_and_built_results_are_kept_side_by_side(self):
        store = self.root / "store"
        build = self.root / "env_check.json"
        built = self.key()
        published = self.key(source="published", platform="linux/amd64", base_image="")
        with tempfile.TemporaryDirectory() as tmp:
            o = jobs(Path(tmp), "oracle", reward=ORACLE_PASS)
            n = jobs(Path(tmp), "nop", reward=NOP_PASS)
            for key in (built, published):
                verdict = env_check.judge(o, n, source=key["source"], platform=key["platform"])
                env_check.record(store=store, task="alpha", key=key, verdict=verdict, build_record=build,
                                 image="img", reused=False)
        doc = json.loads(env_check.store_path(store, "alpha").read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc["results"]), ["built linux/arm64", "published linux/amd64"])
        self.assertIsNotNone(env_check.cached(store, "alpha", built))
        self.assertIsNotNone(env_check.cached(store, "alpha", published))


class BuildRecordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = self.root / "store"
        self.build = self.root / "env_check.json"
        self.selected = self.root / "selected_tasks.txt"
        self.skipped = self.root / "skipped_tasks.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, tid: str, source: str, platform: str, oracle: dict, nop: dict | None = None) -> dict:
        key = {"source": source, "platform": platform, "image_id": f"sha256:{tid}"}
        with tempfile.TemporaryDirectory() as tmp:
            o = jobs(Path(tmp), "oracle", reward=oracle)
            n = jobs(Path(tmp), "nop", reward=nop) if nop is not None else None
            verdict = env_check.judge(o, n, source=source, platform=platform)
        return env_check.record(store=self.store, task=tid, key=key, verdict=verdict, build_record=self.build,
                                image=f"img:{tid}", reused=False)

    def finish(self) -> dict:
        return env_check.finish(build_record=self.build, selected=self.selected, skipped=self.skipped,
                                benchmark="deepswe", host_platform="linux/arm64")

    def test_a_fallback_keeps_the_native_attempt_and_settles_one_platform(self):
        self.selected.write_text("alpha\nbeta\n", encoding="utf-8")
        self.check("alpha", "built", "linux/arm64", grade(35, P2P))
        entry = self.check("alpha", "published", "linux/amd64", ORACLE_PASS, NOP_PASS)
        self.check("beta", "published", "linux/amd64", ORACLE_PASS, NOP_PASS)
        self.assertEqual(entry["attempts"], [{
            "source": "built", "platform": "linux/arm64", "pass": False, "reused": False,
            "reason": "environment check failed: oracle f2p 35/37 on built linux/arm64",
        }])
        got = self.finish()
        self.assertEqual((got["passed"], got["out"], got["platform"]), (["alpha", "beta"], [], "linux/amd64"))
        self.assertEqual(env_check.platform(self.build, self.selected), "linux/amd64")
        self.selected.write_text("alpha\n", encoding="utf-8")
        self.assertEqual(env_check.platform(self.build, self.selected), "")

    def test_a_failed_question_is_kept_out_with_its_reason(self):
        self.selected.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        self.skipped.write_text("gamma\tleak scan: solution in repo\n", encoding="utf-8")
        self.check("alpha", "built", "linux/arm64", ORACLE_PASS, NOP_PASS)
        self.check("beta", "built", "linux/arm64", ORACLE_PASS, grade(F2P, P2P))
        got = self.finish()
        self.assertEqual((got["passed"], got["out"], got["platform"]), (["alpha"], ["beta", "gamma"], "linux/arm64"))
        self.assertEqual(self.skipped.read_text(encoding="utf-8").splitlines(), [
            "gamma\tleak scan: solution in repo",
            "beta\tenvironment check failed: nop f2p 37/37 (passes without the fix) on built linux/arm64",
        ])

    def test_passing_questions_must_share_a_platform(self):
        self.selected.write_text("alpha\nbeta\n", encoding="utf-8")
        self.check("alpha", "built", "linux/arm64", ORACLE_PASS, NOP_PASS)
        self.check("beta", "published", "linux/amd64", ORACLE_PASS, NOP_PASS)
        with self.assertRaises(SystemExit) as caught:
            self.finish()
        self.assertIn("disagree on the platform", str(caught.exception))

    def test_finish_exits_3_when_every_question_is_out(self):
        self.selected.write_text("alpha\n", encoding="utf-8")
        self.check("alpha", "built", "linux/arm64", APPLY_FAILED)
        code = run("finish", "--build-record", str(self.build), "--selected", str(self.selected),
                   "--skipped", str(self.skipped), "--host-platform", "linux/arm64")
        self.assertEqual(code, 3)
        self.assertIn("alpha\tenvironment check failed: oracle patch did not apply", self.skipped.read_text())

    def test_a_reused_result_keeps_where_it_was_checked(self):
        key = {"source": "built", "platform": "linux/arm64", "image_id": "sha256:a"}
        verdict = {"pass": True, "stored": True, "oracle": None, "nop": None, "reason": ""}
        entry = env_check.record(store=self.store, task="alpha", key=key, verdict=verdict,
                                 build_record=self.build, image="img", reused=True,
                                 checked_in="deepswe_one_task#12", checked_at="2026-10-01T00:00:00Z")
        self.assertEqual((entry["checked_in"], entry["reused"]), ("deepswe_one_task#12", True))
        self.assertFalse(env_check.store_path(self.store, "alpha").exists())


class ProvenanceTests(unittest.TestCase):
    def test_eval_protocol_copies_the_selected_checks_and_their_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp) / "env_check.json"
            entry = {
                "source": "built", "platform": "linux/arm64", "image": "img:alpha", "image_id": "sha256:a",
                "pass": True, "reason": "", "checked_in": "local#0", "checked_at": "now", "reused": False,
                "dockerfile_sha256": "dfhash", "base_image": "mars-base@sha256:111",
                "oracle": {**grade(F2P, P2P), "apply_failed": False, "crash": False},
                "nop": {**grade(0, P2P), "apply_failed": False, "crash": False},
                "attempts": [{"source": "published", "platform": "linux/amd64", "pass": False,
                              "reason": "pull failed", "reused": False, "extra": 1}],
            }
            build.write_text(json.dumps({"tasks": {"alpha": entry, "other": entry}}), encoding="utf-8")
            got = env_check_record(build, ["alpha", "missing"])
            self.assertEqual(env_check_record(Path(tmp) / "absent.json", ["alpha"]), {})
        self.assertEqual(list(got), ["alpha"])
        row = got["alpha"]
        self.assertEqual(row["oracle"], {"f2p_passed": F2P, "f2p_total": F2P, "p2p_passed": P2P,
                                         "p2p_total": P2P, "reward": 1})
        self.assertEqual(row["nop"]["f2p_passed"], 0)
        self.assertEqual((row["base_image"], row["dockerfile_sha256"]), ("mars-base@sha256:111", "dfhash"))
        self.assertNotIn("extra", row["attempts"][0])

    def test_image_inspect_records_the_image_platform(self):
        outputs = (
            ('sha256:a\n["repo@sha256:d"]\nlinux/arm64\n', {"platform": "linux/arm64"}),
            ("sha256:a\n[]\n/\n", {}),
            ("sha256:a\n[]\n", {}),
        )
        for text, want in outputs:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                bindir = Path(tmp)
                (bindir / "out.txt").write_text(text, encoding="utf-8")
                (bindir / "docker").write_text(f'#!/bin/sh\ncat "{bindir}/out.txt"\n', encoding="utf-8")
                (bindir / "docker").chmod(0o755)
                with mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}):
                    got = inspect_image("img:alpha")
                self.assertEqual(got.get("id"), "sha256:a")
                self.assertEqual({k: got[k] for k in ("platform",) if k in got}, want)


class ShellScenarioTests(unittest.TestCase):
    def test_tasks_images_with_fake_docker_and_harbor(self):
        """amd64 published, arm64 native, reuse, fallback, kept out, all out, prewarm."""
        env = dict(os.environ)
        for name in ("DEEPSWE_DIR", "DEEPSWE_DOCKER_PLATFORM", "MAC_K3D_SHARE", "MAC_K3D_EVAL_WORKDIR"):
            env.pop(name, None)
        proc = subprocess.run(
            ["bash", str(ROOT / "pipeline" / "tools" / "test_env_check.sh")],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-4000:])
        self.assertIn("OK test_env_check.sh", proc.stdout)


if __name__ == "__main__":
    unittest.main()
