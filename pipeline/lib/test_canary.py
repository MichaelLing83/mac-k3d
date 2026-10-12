#!/usr/bin/env python3
"""Isolation canary tests (report P0.6): fixtures, spec, summary and the probe script."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from canary_verdict import (  # noqa: E402
    CHECKS,
    FACT_FILES,
    build_spec,
    content_fingerprint,
    gold_content_hashes,
    load_config,
    load_facts,
    main,
    render_spec,
    summarize,
    verdict,
)

TESTDATA = LIB / "testdata" / "canary"
PROBE = LIB / "canary_probe.sh"
CAPTURE = LIB / "icode_capture.sh"

GOLD_LINE = "return the secret gold answer forty two"
GOLD_PATCH = f"""diff --git a/src/pkg/new_mod.py b/src/pkg/new_mod.py
new file mode 100644
--- /dev/null
+++ b/src/pkg/new_mod.py
@@ -0,0 +1,2 @@
+def answer():
+    # {GOLD_LINE}
diff --git a/tests/test_new_mod.py b/tests/test_new_mod.py
new file mode 100644
--- /dev/null
+++ b/tests/test_new_mod.py
@@ -0,0 +1 @@
+assert True
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""


def facts_for(name: str) -> dict:
    """pass/ facts with the fixture's canary.json on top (leak fixtures hold only canary.json)."""
    facts = load_facts(TESTDATA / "pass")
    if name != "pass":
        facts.update({k: v for k, v in load_facts(TESTDATA / name).items() if v is not None})
    return facts


def statuses(result: dict) -> dict:
    return {name: check["status"] for name, check in result["checks"].items()}


def write_task(root: Path, toml: str = 'docker_image = "x"\n') -> Path:
    task = root / "alpha"
    (task / "solution").mkdir(parents=True)
    (task / "task.toml").write_text(toml, encoding="utf-8")
    (task / "solution" / "solution.patch").write_text(GOLD_PATCH, encoding="utf-8")
    return task


class VerdictFixtureTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_pass_fixture_passes_with_tool_list_skipped(self):
        result = verdict(facts_for("pass"), self.config)
        got = statuses(result)
        self.assertEqual(result["status"], "pass", result["checks"])
        self.assertEqual(got["tool_list"], "skip")
        self.assertEqual({k for k, v in got.items() if v != "pass"}, {"tool_list"})
        self.assertEqual(set(got), set(CHECKS))

    def test_each_leak_fixture_fails_only_its_check(self):
        for fixture, check in (
            ("network_leak", "network"),
            ("env_leak", "env_names"),
            ("history_leak", "history"),
        ):
            result = verdict(facts_for(fixture), self.config)
            got = statuses(result)
            self.assertEqual(result["status"], "fail", fixture)
            self.assertEqual({k for k, v in got.items() if v == "fail"}, {check}, (fixture, result["checks"]))

    def test_leak_details_name_the_leak_but_no_values(self):
        net = verdict(facts_for("network_leak"), self.config)["checks"]["network"]["detail"]
        self.assertIn("github.com", net)
        env = verdict(facts_for("env_leak"), self.config)["checks"]["env_names"]["detail"]
        self.assertIn("GITCODE_TOKEN", env)
        self.assertIn("HF_TOKEN", env)
        self.assertNotIn("DEEPSEEK_API_KEY", env)
        hist = verdict(facts_for("history_leak"), self.config)["checks"]["history"]["detail"]
        self.assertIn("56 commits reachable from refs but not from HEAD", hist)
        self.assertIn("remotes origin", hist)
        self.assertIn("2 unreachable objects", hist)

    def test_icode_limit_names_are_not_secret_like(self):
        from canary_verdict import check_env

        facts = facts_for("pass")
        names = list(facts["probe"]["env_names"]) + ["ICODE_MAX_TOKENS", "ICODE_MAX_ITERATIONS"]
        got = check_env({"env_names": names}, self.config)
        self.assertEqual(got["status"], "pass", got)

    def test_reachable_declared_host_warns_but_task_passes(self):
        facts = facts_for("pass")
        facts["probe"]["network"].append(
            {"kind": "declared", "host": "openrouter.ai", "curl_exit": 0, "http_code": "200", "error": None}
        )
        result = verdict(facts, self.config)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["checks"]["declared_hosts"]["status"], "warn")
        self.assertIn("openrouter.ai", result["checks"]["declared_hosts"]["detail"])

    def test_unreachable_model_host_fails(self):
        facts = facts_for("pass")
        for entry in facts["probe"]["network"]:
            if entry["kind"] == "model":
                entry.update({"curl_exit": 7, "http_code": "000", "error": "curl: (7) Failed to connect"})
        result = verdict(facts, self.config)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"]["model_api"]["status"], "fail")
        self.assertEqual(result["checks"]["network"]["status"], "pass")

    def test_missing_curl_fails_network(self):
        facts = facts_for("pass")
        facts["probe"]["curl"] = False
        self.assertEqual(verdict(facts, self.config)["checks"]["network"]["status"], "fail")

    def test_missing_probe_fails_every_check(self):
        facts = facts_for("pass")
        facts["probe"] = None
        result = verdict(facts, self.config)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(all(result["checks"][c]["status"] == "fail" for c in CHECKS if c != "tool_list"))

    def test_missing_probe_names_why_the_trial_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "cpython_5__abc"
            (trial / "agent").mkdir(parents=True)
            message = (
                "Command failed (exit 127): set -euo pipefail; icode --help GITCODE_TOKEN=gc-secret-value\n"
                "stdout: total 672\n"
                "/home/agent/.local/bin/icode: 16: exec: /opt/icode-host/.venv/sandbox-cpython/bin/python3.13: not found\n"
                "stderr: None"
            )
            (trial / "result.json").write_text(
                json.dumps({"exception_info": {"exception_type": "NonZeroAgentExitCodeError", "exception_message": message}}),
                encoding="utf-8",
            )
            detail = verdict(load_facts(trial / "agent"), self.config)["checks"]["network"]["detail"]
        self.assertIn("the trial stopped before the probe: NonZeroAgentExitCodeError: exit 127", detail)
        self.assertIn("python3.13: not found", detail)
        self.assertNotIn("gc-secret-value", detail)

    def test_allow_host_negative_test_fails_either_way_and_says_which(self):
        facts = facts_for("pass")
        facts["host"]["allow_host"] = "pypi.org"
        network = verdict(facts, self.config)["checks"]["network"]
        self.assertEqual(network["status"], "fail")
        self.assertIn("negative test inconclusive: CANARY_ALLOW_HOST=pypi.org was opened but did not answer", network["detail"])
        self.assertIn("(pypi.org unreachable (curl exit", network["detail"])
        for entry in facts["probe"]["network"]:
            if entry["host"] == "pypi.org":
                entry.update({"curl_exit": 0, "http_code": "200", "error": None})
        network = verdict(facts, self.config)["checks"]["network"]
        self.assertEqual(network, {"status": "fail", "detail": "pypi.org reached (HTTP 200)"})

    def test_pythonpath_into_mount_fails(self):
        facts = facts_for("pass")
        facts["probe"]["python_env"]["PYTHONPATH"] = "/opt/icode-host/lib/python3.12/site-packages"
        result = verdict(facts, self.config)
        self.assertEqual(result["checks"]["python_env"]["status"], "fail")
        self.assertIn("PYTHONPATH", result["checks"]["python_env"]["detail"])

    def test_writable_mount_fails(self):
        facts = facts_for("pass")
        facts["probe"]["mount"].update({"options": "rw,relatime", "read_only": False, "touch_exit": 0})
        detail = verdict(facts, self.config)["checks"]["mount"]
        self.assertEqual(detail["status"], "fail")
        self.assertIn("not ro", detail["detail"])
        facts = facts_for("pass")
        facts["root"] = None
        self.assertEqual(verdict(facts, self.config)["checks"]["mount"]["status"], "fail")

    def test_mcp_config_and_research_subagent_fail(self):
        facts = facts_for("pass")
        facts["home_post"]["mcp_json"] = True
        self.assertEqual(verdict(facts, self.config)["checks"]["icode_home"]["status"], "fail")
        facts = facts_for("pass")
        facts["home_pre"]["research_subagent"] = True
        self.assertEqual(verdict(facts, self.config)["checks"]["icode_home"]["status"], "fail")
        facts = facts_for("pass")
        facts["host"]["task_mcp_servers"] = 1
        self.assertEqual(verdict(facts, self.config)["checks"]["icode_home"]["status"], "fail")

    def test_filesystem_hit_and_timeout_fail(self):
        facts = facts_for("pass")
        facts["probe"]["filesystem"]["hits"] = ["/tmp/solution.patch"]
        self.assertEqual(verdict(facts, self.config)["checks"]["filesystem"]["status"], "fail")
        facts = facts_for("pass")
        facts["probe"]["filesystem"]["timed_out"] = True
        self.assertEqual(verdict(facts, self.config)["checks"]["filesystem"]["status"], "fail")
        config = dict(self.config, filesystem_ignore=["/usr/share/*"])
        facts = facts_for("pass")
        facts["probe"]["filesystem"]["hits"] = ["/usr/share/doc/solution.patch"]
        self.assertEqual(verdict(facts, config)["checks"]["filesystem"]["status"], "pass")


class SpecTests(unittest.TestCase):
    def test_spec_lists_hosts_and_gold_file_tails_without_gold_content(self):
        toml = (
            'docker_image = "x"\n[agent]\nallowed_hosts = '
            '["api.deepseek.com", "openrouter.ai", "github.com", "*.example.com", "api.openai.com"]\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            spec = build_spec(write_task(Path(tmp), toml), "lolbench", load_config())
        kinds = {}
        for entry in spec["hosts"]:
            kinds.setdefault(entry["kind"], []).append(entry["host"])
        self.assertEqual(kinds["model"], ["api.deepseek.com"])
        self.assertEqual(kinds["declared"], ["openrouter.ai", "api.openai.com"])
        self.assertIn("github.com", kinds["source"])
        self.assertIn("gitcode.com", kinds["source"])
        self.assertIn("*/pkg/new_mod.py", spec["paths"])
        self.assertIn("*/tests/private", spec["paths"])
        self.assertNotIn("*/tests/test_new_mod.py", spec["paths"])
        self.assertIn("solution.patch", spec["names"])
        self.assertEqual(spec["task"], "alpha")
        text = render_spec(spec)
        self.assertIn("host declared openrouter.ai\n", text)
        self.assertIn("path */pkg/new_mod.py\n", text)
        self.assertIn("prune /logs/agent\n", text)
        self.assertIn("timeout curl 10\n", text)
        self.assertNotIn(GOLD_LINE, text)
        self.assertNotIn(GOLD_LINE, json.dumps(spec))
        self.assertNotIn("README.md", text)
        self.assertTrue(spec["hashes"])
        self.assertIn(f"hash {spec['hashes'][0]}\n", text)
        for digest in spec["hashes"]:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_a_copied_gold_file_hashes_and_a_same_name_does_not(self):
        body = "func Diff() int {\n    return 1\n"
        patch = (
            "diff --git a/pkg/diff/diff.go b/pkg/diff/diff.go\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/pkg/diff/diff.go\n"
            "@@ -0,0 +1,2 @@\n"
            "+func Diff() int {\n"
            "+    return 1\n"
        )
        globs = []
        digest = content_fingerprint(body)
        self.assertEqual(digest, content_fingerprint("func Diff() int {\n    return 1\n"))
        self.assertIn(digest, gold_content_hashes(patch, globs))
        self.assertNotIn(content_fingerprint("package diff\nfunc somethingElse() {}\n"), gold_content_hashes(patch, globs))
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "etree-xml-diff-patch"
            (task / "solution").mkdir(parents=True)
            (task / "task.toml").write_text('docker_image = "x"\n', encoding="utf-8")
            (task / "solution" / "solution.patch").write_text(patch, encoding="utf-8")
            spec = build_spec(task, "deepswe", load_config())
            rendered = render_spec(spec)
        self.assertIn(digest, spec["hashes"])
        self.assertIn(f"hash {digest}\n", rendered)
        self.assertNotIn("func Diff()", rendered)
        self.assertNotIn("func Diff()", json.dumps(spec))
        self.assertNotIn("return 1", rendered)

    def test_spec_probes_the_allowed_host_once_as_a_source(self):
        config = load_config()
        with tempfile.TemporaryDirectory() as tmp:
            task = write_task(Path(tmp))
            extra = build_spec(task, "deepswe", config, "Example.org")
            known = build_spec(task, "deepswe", config, "gitee.com")
        self.assertEqual(extra["hosts"][0], {"kind": "source", "host": "example.org"})
        self.assertEqual(extra["allow_host"], "example.org")
        self.assertEqual([h["host"] for h in known["hosts"]].count("gitee.com"), 1)
        self.assertIn("attempts model 3\n", render_spec(known))
        self.assertIn("timeout model 30\n", render_spec(known))

    def test_spec_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = write_task(Path(tmp))
            out = Path(tmp) / "out" / "spec.json"
            self.assertEqual(main(["spec", "--task-dir", str(task), "--benchmark", "deepswe", "--out", str(out)]), 0)
            spec = json.loads(out.read_text(encoding="utf-8"))
            self.assertNotIn("declared", {h["kind"] for h in spec["hosts"]})
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(main(["spec", "--task-dir", str(Path(tmp) / "nope"), "--out", str(out)]), 1)
            self.assertIn("missing task dir", err.getvalue())


class SummaryTests(unittest.TestCase):
    def _trial(self, jobs: Path, tid: str, fixture: str) -> None:
        agent = jobs / tid / f"{tid}_canary_7" / f"{tid}__abc" / "agent"
        agent.mkdir(parents=True)
        for key, name in FACT_FILES.items():
            data = facts_for(fixture)[key]
            if data is not None:
                (agent / name).write_text(json.dumps(data), encoding="utf-8")

    def _main(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_all_pass_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "canary"
            self._trial(jobs, "cpython_5", "pass")
            self._trial(jobs, "flink_7", "pass")
            rc, out = self._main(["summarize", "--jobs-dir", str(jobs), "--out-dir", str(jobs)])
            self.assertEqual(rc, 0, out)
            summary = json.loads((jobs / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["counts"], {"tasks": 2, "pass": 2, "fail": 0})
            self.assertIn("canary pass: tasks=2 pass=2 fail=0", out)
            self.assertIn("| cpython_5 | pass |", (jobs / "report.md").read_text(encoding="utf-8"))

    def test_one_leak_or_missing_trial_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "canary"
            self._trial(jobs, "cpython_5", "pass")
            self._trial(jobs, "fastapi_1", "network_leak")
            argv = ["summarize", "--jobs-dir", str(jobs), "--out-dir", str(jobs)]
            rc, out = self._main(argv + ["--task", "cpython_5", "--task", "fastapi_1", "--task", "flink_7"])
            self.assertEqual(rc, 2, out)
            summary = json.loads((jobs / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["counts"], {"tasks": 3, "pass": 1, "fail": 2})
            self.assertEqual(summary["failed_tasks"], ["fastapi_1", "flink_7"])
            self.assertIn("CANARY FAIL task=fastapi_1 check=network: github.com reached (HTTP 200)", out)
            self.assertIn("no canary trial", summary["tasks"]["flink_7"]["checks"]["network"]["detail"])
            self.assertIn("## Failures and warnings", (jobs / "report.md").read_text(encoding="utf-8"))

    def test_empty_jobs_dir_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(summarize(Path(tmp) / "none", load_config())["status"], "fail")

    def test_a_trial_that_never_started_hands_the_canary_to_the_next_question(self):
        """An image pull failure stops the trial before the probe: no isolation fact either way."""
        from provenance import canary_record, isolation_lines, isolation_view

        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "canary"
            trial = jobs / "cpython_5" / "cpython_5_canary_7" / "cpython_5__abc"
            (trial / "agent").mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "exception_info": {
                            "exception_type": "RuntimeError",
                            "exception_message": "docker compose command failed (exit 1)\nError: pull access denied",
                        }
                    }
                ),
                encoding="utf-8",
            )
            self._trial(jobs, "flink_7", "pass")
            rc, out = self._main(["started", "--jobs-dir", str(jobs), "--task", "cpython_5"])
            self.assertEqual(rc, 3)
            why = out.strip()
            self.assertEqual(why, "RuntimeError: exit 1: Error: pull access denied")
            self.assertEqual(self._main(["started", "--jobs-dir", str(jobs), "--task", "flink_7"]), (0, ""))
            self.assertEqual(self._main(["started", "--jobs-dir", str(jobs), "--task", "absent"]), (3, "no canary trial\n"))

            fallback = jobs / "fallback.tsv"
            fallback.write_text(f"cpython_5\t{why}\n", encoding="utf-8")
            argv = ["summarize", "--jobs-dir", str(jobs), "--out-dir", str(jobs), "--fallback-file", str(fallback)]
            rc, out = self._main(argv + ["--task", "flink_7"])
            self.assertEqual(rc, 0, out)
            self.assertIn("canary: cpython_5 did not start (RuntimeError: exit 1: Error: pull access denied)", out)
            summary = json.loads((jobs / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["fallback_from"], [{"task": "cpython_5", "reason": why}])
            self.assertIn("## Questions whose canary trial did not start", (jobs / "report.md").read_text(encoding="utf-8"))

            record = canary_record(jobs / "summary.json")
            self.assertEqual(record["tasks"], ["flink_7"])
            self.assertEqual(record["fallback_from"], [{"task": "cpython_5", "reason": why}])
            line = next(ln for ln in isolation_lines(isolation_view({"canary": record})) if ln.startswith("Canary:"))
            self.assertIn(f"· pass · tasks flink_7 · failed none · warnings none · did not start: cpython_5 ({why})", line)

    def test_when_no_question_starts_every_one_fails_and_none_is_a_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "canary"
            jobs.mkdir()
            fallback = jobs / "fallback.tsv"
            fallback.write_text("alpha\tno canary trial\nbeta\tno canary trial\n", encoding="utf-8")
            argv = ["summarize", "--jobs-dir", str(jobs), "--out-dir", str(jobs), "--fallback-file", str(fallback)]
            rc, _ = self._main(argv + ["--task", "alpha", "--task", "beta"])
            self.assertEqual(rc, 2)
            summary = json.loads((jobs / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["failed_tasks"], ["alpha", "beta"])
            self.assertNotIn("fallback_from", summary)


class CanaryStepTests(unittest.TestCase):
    """evaluate/canary's question loop, read from the script."""

    STEP = LIB.parent / "stages" / "evaluate" / "canary.sh"

    def test_the_canary_moves_on_only_when_a_trial_did_not_start(self):
        text = self.STEP.read_text(encoding="utf-8")
        loop = text[text.index('for tid in "${TASK_IDS[@]}"; do\n    run_canary "$tid"\n    set +e'):]
        self.assertIn('canary_verdict.py" started --jobs-dir "$CANARY_DIR" --task "$tid"', loop)
        self.assertIn('if [ "$started_rc" = 0 ]; then\n      expect=(--task "$tid")\n      break', loop)
        self.assertIn('[ "$started_rc" = 3 ] || die', loop)
        self.assertIn("printf '%s\\t%s\\n' \"$tid\" \"$why\" >>\"$FALLBACK\"", loop)
        self.assertIn('--fallback-file "$FALLBACK"', text)
        self.assertIn("so isolation is not proven; the run stops here", text)
        harbor = (LIB.parent / "stages" / "evaluate" / "harbor_cmd.sh").read_text(encoding="utf-8")
        rollout = harbor[harbor.index("build_run_cmd()"):harbor.index("build_canary_cmd()")]
        canary = harbor[harbor.index("build_canary_cmd()"):harbor.index("append_agent_flags()")]
        self.assertNotIn("spec=", rollout)
        self.assertIn('--ak "spec=$spec"', canary)
        # A probe that ran and found a breach stops the build at once.
        self.assertIn('[ "$rc" = 0 ] || die "isolation canary failed', text)
        proc = subprocess.run(["bash", "-n", str(self.STEP)], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class ProbeScriptTests(unittest.TestCase):
    """canary_probe.sh on the host: stub curl, stub read-only touch, temp repo and search root."""

    MODEL = "api.deepseek.com"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "root"
        self.repo = self.root / "app"
        self.mount = self.root / "opt" / "icode-host"
        self.logs = self.tmp / "logs"
        self.home = self.tmp / "home"
        for path in (self.repo, self.mount, self.logs, self.home):
            path.mkdir(parents=True)
        (self.repo / "src" / "pkg").mkdir(parents=True)
        (self.repo / "src" / "pkg" / "new_mod.py").write_text("in the repo, so pruned\n", encoding="utf-8")
        git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t"}
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=git_env, capture_output=True)
        self.spec = self.tmp / "canary_spec.txt"
        self.spec.write_text(
            render_spec(build_spec(write_task(self.tmp / "task"), "deepswe", load_config())), encoding="utf-8"
        )
        self.mounts_file = self.tmp / "mounts"
        self.mounts_file.write_text(f"/dev/vda1 {self.mount} ext4 ro,relatime 0 0\n", encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self._stub(
            "curl",
            f"""#!/bin/sh
for a in "$@"; do url=$a; done
case "$url" in
  https://{self.MODEL}/) printf 401; exit 0 ;;
esac
echo "curl: (7) Failed to connect to ${{url}}" >&2
printf 000
exit 7
""",
        )
        self._stub(
            "touch",
            f"""#!/bin/sh
case "$1" in
  {self.mount}/*) echo "touch: cannot touch '$1': Read-only file system" >&2; exit 1 ;;
esac
exec {shutil.which("touch")} "$@"
""",
        )

    def _stub(self, name: str, body: str) -> None:
        (self.bin / name).write_text(body, encoding="utf-8")
        (self.bin / name).chmod(0o755)

    def _probe(self, *args: str) -> None:
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.home),
            "CANARY_LOG_DIR": str(self.logs),
            "CANARY_SPEC": str(self.spec),
            "CANARY_SEARCH_ROOT": str(self.root),
            "CANARY_MOUNT": str(self.mount),
            "CANARY_MOUNTS_FILE": str(self.mounts_file),
            "CANARY_CAPTURE": str(CAPTURE),
            "CAPTURE_LOG_DIR": str(self.tmp / "capture"),
            "MAC_K3D_REPO": str(self.repo),
            "DEEPSEEK_API_KEY": "sk-canary-sentinel-0000",
        }
        proc = subprocess.run(["bash", str(PROBE), *args], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def _run_all(self) -> dict:
        self._probe("home", "pre")
        self._probe("probe")
        self._probe("home", "post")
        self._probe("mount")
        shutil.copy(TESTDATA / "pass" / "canary_host.json", self.logs / "canary_host.json")
        for name in FACT_FILES.values():
            self.assertIsInstance(json.loads((self.logs / name).read_text(encoding="utf-8")), dict, name)
        return verdict(load_facts(self.logs), load_config())

    def test_probe_facts_parse_and_pass(self):
        result = self._run_all()
        self.assertEqual(result["status"], "pass", result["checks"])
        probe = json.loads((self.logs / "canary.json").read_text(encoding="utf-8"))
        self.assertEqual(probe["repo"]["path"], str(self.repo))
        self.assertEqual(probe["repo"]["all_count"], 1)
        self.assertEqual(probe["filesystem"]["hits"], [])
        self.assertIn(str(self.repo), probe["filesystem"]["pruned"])
        self.assertTrue(probe["mount"]["read_only"])
        self.assertIn("DEEPSEEK_API_KEY", probe["env_names"])
        self.assertNotIn("sk-canary-sentinel-0000", (self.logs / "canary.json").read_text(encoding="utf-8"))
        self.assertTrue(json.loads((self.logs / "canary_root.json").read_text(encoding="utf-8"))["read_only_error"])

    def test_model_host_is_retried_until_it_answers(self):
        count = self.tmp / "model_tries"
        self._stub(
            "curl",
            f"""#!/bin/sh
for a in "$@"; do url=$a; done
if [ "$url" = "https://{self.MODEL}/" ]; then
  n=$(cat '{count}' 2>/dev/null || echo 0); n=$((n + 1)); echo $n >'{count}'
  [ "$n" -ge 3 ] && {{ printf 401; exit 0; }}
  echo "curl: (28) SSL connection timeout" >&2; printf 000; exit 28
fi
echo "curl: (35) blocked" >&2
printf 000
exit 35
""",
        )
        result = self._run_all()
        self.assertEqual(result["checks"]["model_api"]["status"], "pass", result["checks"]["model_api"])
        probe = json.loads((self.logs / "canary.json").read_text(encoding="utf-8"))
        model = [e for e in probe["network"] if e["kind"] == "model"][0]
        self.assertEqual((model["attempts"], model["http_code"]), (3, "401"))
        self.assertTrue(all(e["attempts"] == 1 for e in probe["network"] if e["kind"] == "source"))

    def test_model_host_down_after_all_attempts_fails(self):
        self._stub("curl", "#!/bin/sh\necho 'curl: (28) SSL connection timeout' >&2\nprintf 000\nexit 28\n")
        detail = self._run_all()["checks"]["model_api"]
        self.assertEqual(detail["status"], "fail")
        self.assertIn("api.deepseek.com unreachable after 3 attempts (curl exit 28", detail["detail"])

    def test_planted_gold_patch_fails_filesystem(self):
        (self.root / "tmp").mkdir()
        planted = self.root / "tmp" / "solution.patch"
        planted.write_text(GOLD_PATCH, encoding="utf-8")
        result = self._run_all()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"]["filesystem"]["status"], "fail")
        self.assertIn(str(planted), result["checks"]["filesystem"]["detail"])
        recorded = (self.logs / "canary.json").read_text(encoding="utf-8")
        self.assertNotIn(GOLD_LINE, recorded)
        self.assertNotIn("hash ", recorded)

    def test_same_name_with_different_bytes_is_not_a_hit(self):
        (self.root / "tmp").mkdir()
        (self.root / "tmp" / "solution.patch").write_text("planted\n", encoding="utf-8")
        (self.root / "usr" / "local" / "go" / "src" / "internal" / "diff").mkdir(parents=True)
        (self.root / "usr" / "local" / "go" / "src" / "internal" / "diff" / "diff.go").write_text(
            "package diff\nfunc somethingElse() {}\n", encoding="utf-8"
        )
        result = self._run_all()
        self.assertEqual(result["checks"]["filesystem"]["status"], "pass", result["checks"]["filesystem"])
        self.assertEqual(json.loads((self.logs / "canary.json").read_text(encoding="utf-8"))["filesystem"]["hits"], [])

    def test_a_failed_mktemp_stops_instead_of_a_false_verdict(self):
        self._stub("mktemp", "#!/bin/sh\nexit 1\n")
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.home),
            "CANARY_LOG_DIR": str(self.logs),
            "CANARY_SPEC": str(self.spec),
            "CANARY_SEARCH_ROOT": str(self.root),
            "CANARY_MOUNT": str(self.mount),
            "CANARY_MOUNTS_FILE": str(self.mounts_file),
            "CANARY_CAPTURE": str(CAPTURE),
        }
        proc = subprocess.run(
            ["bash", str(PROBE), "mount"], env=env, capture_output=True, text=True, check=False
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("cannot create a temp file (out of processes or disk?)", proc.stderr)
        self.assertFalse((self.logs / "canary_root.json").exists())

    def test_writable_mount_and_extra_ref_fail(self):
        self.mounts_file.write_text(f"/dev/vda1 {self.mount} ext4 rw,relatime 0 0\n", encoding="utf-8")
        (self.bin / "touch").unlink()
        subprocess.run(["git", "-C", str(self.repo), "branch", "-q", "other"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "remote", "add", "origin", "https://gitcode.com/x/y.git"], check=True
        )
        result = self._run_all()
        self.assertEqual(result["checks"]["mount"]["status"], "fail")
        self.assertEqual(result["checks"]["history"]["status"], "fail")
        self.assertIn("remotes origin", result["checks"]["history"]["detail"])
        self.assertEqual(sorted(p.name for p in self.mount.iterdir()), [])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
