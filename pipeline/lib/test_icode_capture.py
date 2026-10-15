#!/usr/bin/env python3
"""icode_capture.sh driven in temp git repos (no Docker), plus capture_receipt.py.

Every card case runs for each suite. The grader's view is reproduced exactly:
DeepSWE and SWE-bench Pro run their verifier.collect command, LoLBench reads
solution.patch. Each patch must apply cleanly to a fresh clone of the base.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from capture_receipt import (  # noqa: E402
    annotate,
    base_mismatch,
    binary_paths,
    declared_repo,
    trial_flags,
)

CAPTURE = LIB / "icode_capture.sh"
FIXTURES = LIB / "testdata"
SUITES = ("deepswe", "lolbench", "swebenchpro")
COLLECT = {
    "deepswe": "git diff --binary {base} HEAD",
    "swebenchpro": "git add -A && git diff --cached --binary {base}",
}
RECEIPT_KEYS = {
    "schema",
    "stage",
    "benchmark",
    "repo",
    "base_sha",
    "base_source",
    "declared_base",
    "declared_base_sha",
    "base_matches_declared",
    "head_before_commit",
    "final_head",
    "status_porcelain",
    "cleanup",
    "new_files",
    "diff_stat",
    "patch",
    "committed_patch_sha256",
    "delivery",
    "commit",
    "uid",
    "user",
    "safe_directory_complaint",
    "oversize",
    "flags",
    "errors",
}
APP_BASE = "def run():\n    return 1\n"
APP_EDIT = "def run():\n    return 2\n"


class Sandbox:
    """One task container: a repo at <root>/workspace/proj plus /logs dirs."""

    def __init__(self, tc: unittest.TestCase, root: Path, suite: str):
        self.tc = tc
        self.suite = suite
        self.tmp = root
        self.home = root / "home"
        self.logs = root / "logs" / "agent"
        self.arts = root / "logs" / "artifacts"
        self.workspace = root / "workspace"
        self.repo = self.workspace / "proj"
        self.home.mkdir(parents=True)
        self.repo.mkdir(parents=True)
        self._fresh = 0
        self.git("init", "-q")
        self.write("src/app.py", APP_BASE)
        self.write("src/build/tracked.py", "X = 1\n")
        self.write("README.md", "proj\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("tag", "lolbench-base")
        self.base_sha = self.git("rev-parse", "HEAD").strip()

    def env(self, **extra: str) -> dict:
        prefixes = ("GIT_", "MAC_K3D_", "LOLBENCH_", "CAPTURE_")
        env = {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}
        env.update(
            {
                "HOME": str(self.home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "agent",
                "GIT_AUTHOR_EMAIL": "agent@local",
                "GIT_COMMITTER_NAME": "agent",
                "GIT_COMMITTER_EMAIL": "agent@local",
                "PATH": f"{FIXTURES}{os.pathsep}{os.environ.get('PATH', '')}",
                "CAPTURE_LOG_DIR": str(self.logs),
                "CAPTURE_ARTIFACTS_DIR": str(self.arts),
                "CAPTURE_SEARCH_ROOTS": str(self.workspace),
                "LOLBENCH_METADATA": str(self.tmp / "no-metadata.json"),
                "LOLBENCH_WORKSPACE": str(self.workspace),
                "LOLBENCH_BASE_UNTRACKED": str(self.tmp / "no-base-untracked.txt"),
                "LOLBENCH_PATCH_OUT": str(self.arts / "solution.patch"),
                "MAC_K3D_BENCHMARK": self.suite,
                "MAC_K3D_REPO": str(self.repo),
            }
        )
        env.update(extra)
        return env

    def git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=cwd or self.repo, env=self.env(), capture_output=True, text=True
        )
        self.tc.assertEqual(proc.returncode, 0, f"git {args}: {proc.stderr}")
        return proc.stdout

    def write(self, rel: str, text: str | bytes) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def run(self, sub: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(CAPTURE), sub], cwd=self.repo, env=self.env(**extra), capture_output=True, text=True
        )

    def base(self, **extra: str) -> None:
        proc = self.run("base", **extra)
        self.tc.assertEqual(proc.returncode, 0, proc.stderr)
        self.tc.assertEqual(proc.stdout.strip(), str(self.repo))

    def capture(self, **extra: str) -> dict:
        proc = self.run("capture", **extra)
        self.tc.assertEqual(proc.returncode, 0, proc.stderr)
        self.last_stderr = proc.stderr
        return self.receipt()

    def receipt(self) -> dict:
        return json.loads((self.logs / "capture.json").read_text(encoding="utf-8"))

    def patch(self) -> bytes:
        return (self.logs / "capture.patch").read_bytes()

    def graded(self) -> bytes:
        """The bytes this suite's grader would receive."""
        if self.suite == "lolbench":
            path = self.arts / "solution.patch"
            return path.read_bytes() if path.is_file() else b""
        cmd = COLLECT[self.suite].format(base=self.base_sha)
        proc = subprocess.run(["bash", "-c", cmd], cwd=self.repo, env=self.env(), capture_output=True)
        self.tc.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def applies(self, patch: bytes, expect: dict[str, str | None]) -> None:
        """Apply to a fresh clone at base, then check file contents (None = absent)."""
        self._fresh += 1
        fresh = self.tmp / f"fresh-{self._fresh}"
        self.git("clone", "-q", str(self.repo), str(fresh), cwd=self.tmp)
        self.git("checkout", "-q", self.base_sha, cwd=fresh)
        if patch:
            patch_file = self.tmp / f"fresh-{self._fresh}.patch"
            patch_file.write_bytes(patch)
            self.git("apply", "--check", str(patch_file), cwd=fresh)
            self.git("apply", str(patch_file), cwd=fresh)
        for rel, content in expect.items():
            path = fresh / rel
            if content is None:
                self.tc.assertFalse(path.exists(), rel)
            else:
                self.tc.assertEqual(path.read_text(encoding="utf-8"), content, rel)

    def check(self, receipt: dict, expect: dict[str, str | None], files: int) -> None:
        patch = self.patch()
        self.tc.assertEqual(self.graded(), patch, f"{self.suite}: grader must read the patch of record")
        self.tc.assertEqual(receipt["diff_stat"]["files"], files)
        self.tc.assertEqual(receipt["patch"]["bytes"], len(patch))
        self.tc.assertEqual(receipt["patch"]["sha256"], hashlib.sha256(patch).hexdigest())
        self.tc.assertEqual(receipt["errors"], [])
        self.applies(patch, expect)


class CaptureCardCases(unittest.TestCase):
    """The six cases from the P0.4 card, each for DeepSWE, LoLBench and SWE-bench Pro."""

    def run_suites(self, case) -> None:
        for suite in SUITES:
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as tmp:
                case(Sandbox(self, Path(tmp), suite))

    def test_uncommitted_edits(self):
        def case(box: Sandbox) -> None:
            box.base()
            box.write("src/app.py", APP_EDIT)
            receipt = box.capture()
            box.check(receipt, {"src/app.py": APP_EDIT}, files=1)
            self.assertTrue(receipt["commit"]["ran"])
            self.assertEqual(receipt["commit"]["exit_code"], 0)
            self.assertEqual(receipt["patch"]["source"], "base_diff")
            self.assertEqual(receipt["flags"], [])
            if box.suite == "lolbench":
                self.assertTrue(receipt["delivery"]["submit_ran"])
                self.assertEqual(receipt["delivery"]["submit_exit_code"], 0)
                self.assertFalse(receipt["delivery"]["replaced_submit_output"], "official output used as is")
            else:
                self.assertEqual(receipt["delivery"]["method"], "verifier.collect")

        self.run_suites(case)

    def test_edits_the_agent_already_committed(self):
        def case(box: Sandbox) -> None:
            box.base()
            box.write("src/app.py", APP_EDIT)
            box.git("commit", "-qam", "agent work")
            box.write("README.md", "proj\nmore\n")
            receipt = box.capture()
            box.check(receipt, {"src/app.py": APP_EDIT, "README.md": "proj\nmore\n"}, files=2)
            if box.suite == "lolbench":
                self.assertTrue(receipt["delivery"]["replaced_submit_output"])
                self.assertNotEqual(receipt["delivery"]["submit_sha256"], receipt["patch"]["sha256"])

        self.run_suites(case)

    def test_new_untracked_files(self):
        def case(box: Sandbox) -> None:
            box.base()
            box.write("src/new_mod.py", "Y = 2\n")
            box.write("pkg/__init__.py", "")
            box.write("pkg/core.py", "Z = 3\n")
            receipt = box.capture()
            box.check(
                receipt,
                {"src/new_mod.py": "Y = 2\n", "pkg/__init__.py": "", "pkg/core.py": "Z = 3\n"},
                files=3,
            )
            self.assertEqual(receipt["new_files"], 3)

        self.run_suites(case)

    def test_agent_already_submitted_then_reverted_keeps_patch(self):
        def case(box: Sandbox) -> None:
            box.base()
            box.write("src/app.py", APP_EDIT)
            if box.suite == "lolbench":
                proc = subprocess.run(
                    ["lolbench-submit", str(box.repo)], cwd=box.repo, env=box.env(), capture_output=True, text=True
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                earlier = (box.arts / "solution.patch").read_bytes()
            else:
                box.capture()
                earlier = box.patch()
            self.assertTrue(earlier)
            box.git("reset", "-q", "--hard", box.base_sha)
            receipt = box.capture()
            self.assertTrue(receipt["patch"]["kept_existing"])
            self.assertEqual(receipt["patch"]["source"], "kept_existing")
            self.assertIn("keeping the existing non-empty patch", box.last_stderr)
            self.assertEqual(box.patch(), earlier)
            if box.suite == "lolbench":
                self.assertEqual((box.arts / "solution.patch").read_bytes(), earlier)
            box.applies(box.patch(), {"src/app.py": APP_EDIT})

        self.run_suites(case)

    def test_no_edits_gives_empty_patch(self):
        def case(box: Sandbox) -> None:
            box.base()
            receipt = box.capture()
            box.check(receipt, {"src/app.py": APP_BASE}, files=0)
            self.assertEqual(receipt["patch"]["bytes"], 0)
            self.assertFalse(receipt["commit"]["ran"])
            self.assertEqual(receipt["final_head"], box.base_sha)
            if box.suite == "lolbench":
                self.assertTrue((box.arts / "solution.patch").is_file())

        self.run_suites(case)

    def test_cleanup_removes_untracked_target_keeps_tracked(self):
        def case(box: Sandbox) -> None:
            box.base()
            box.write("target/debug/app.o", "obj\n")
            box.write("pkg/mod.py", "M = 1\n")
            box.write("pkg/__pycache__/mod.cpython-313.pyc", "pyc\n")
            box.write("src/build/new_helper.py", "H = 1\n")
            box.write(".agent_history/session.json", "{}\n")
            box.write("src/app.py", APP_EDIT)
            receipt = box.capture()
            self.assertEqual(
                sorted(receipt["cleanup"]["removed"]), [".agent_history/", "pkg/__pycache__/", "target/"]
            )
            self.assertFalse((box.repo / "target").exists())
            self.assertTrue((box.repo / "src/build/tracked.py").is_file())
            self.assertTrue((box.repo / "src/build/new_helper.py").is_file())
            box.check(
                receipt,
                {
                    "src/app.py": APP_EDIT,
                    "pkg/mod.py": "M = 1\n",
                    "src/build/new_helper.py": "H = 1\n",
                    "src/build/tracked.py": "X = 1\n",
                    "target/debug/app.o": None,
                    "pkg/__pycache__/mod.cpython-313.pyc": None,
                },
                files=3,
            )

        self.run_suites(case)


class CaptureProtocolTests(unittest.TestCase):
    def box(self, tmp: str, suite: str = "deepswe") -> Sandbox:
        return Sandbox(self, Path(tmp), suite)

    def test_receipt_has_card_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp)
            box.base()
            box.write("src/app.py", APP_EDIT)
            receipt = box.capture()
            self.assertEqual(set(receipt), RECEIPT_KEYS)
            self.assertEqual(receipt["schema"], "mac-k3d-capture-v1")
            self.assertEqual(receipt["repo"]["path"], str(box.repo))
            self.assertEqual(receipt["repo"]["source"], "declared")
            self.assertEqual(receipt["base_sha"], box.base_sha)
            self.assertEqual(receipt["base_source"], "file")
            self.assertEqual(receipt["head_before_commit"], box.base_sha)
            self.assertNotEqual(receipt["final_head"], box.base_sha)
            self.assertEqual(receipt["status_porcelain"], {"before": 1, "after": 0})
            self.assertEqual(receipt["diff_stat"], {"files": 1, "insertions": 1, "deletions": 1})
            self.assertEqual(receipt["committed_patch_sha256"], receipt["patch"]["sha256"])
            self.assertEqual(receipt["uid"], os.getuid())
            self.assertFalse(receipt["safe_directory_complaint"])
            self.assertFalse(receipt["oversize"])
            self.assertEqual((box.logs / "base_sha.txt").read_text(encoding="utf-8").strip(), box.base_sha)

    def test_files_untracked_at_base_are_left_alone(self):
        for suite in SUITES:
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as tmp:
                box = self.box(tmp, suite)
                box.write("local/image_cache.txt", "from the image\n")
                box.base()
                box.write("src/app.py", APP_EDIT)
                receipt = box.capture()
                self.assertTrue((box.repo / "local/image_cache.txt").is_file())
                self.assertEqual(box.git("ls-files", "local").strip(), "")
                self.assertNotIn(b"image_cache", box.patch())
                self.assertEqual(receipt["diff_stat"]["files"], 1)
                if suite == "swebenchpro":
                    # Its collect hook runs `git add -A`, so it sweeps the file in.
                    # P7 flags this trial capture_mismatch.
                    self.assertNotEqual(box.graded(), box.patch())
                else:
                    self.assertEqual(box.graded(), box.patch())
                box.applies(box.patch(), {"src/app.py": APP_EDIT})

    def test_lolbench_image_base_untracked_list_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp, "lolbench")
            box.base()
            box.write("gen/prebuilt.txt", "x\n")
            listing = box.tmp / "base_untracked.txt"
            listing.write_text("gen/prebuilt.txt\n", encoding="utf-8")
            box.write("src/app.py", APP_EDIT)
            receipt = box.capture(LOLBENCH_BASE_UNTRACKED=str(listing))
            self.assertNotIn(b"prebuilt", box.patch())
            self.assertEqual(box.graded(), box.patch())
            self.assertEqual(receipt["diff_stat"]["files"], 1)

    def test_binary_new_file_is_captured_and_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp)
            box.base()
            box.write("assets/logo.bin", bytes(range(256)) * 4)
            receipt = box.capture()
            self.assertEqual(receipt["patch"]["binary_paths"], ["assets/logo.bin"])
            self.assertEqual(box.graded(), box.patch())
            box.applies(box.patch(), {})

    def test_oversize_patch_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp)
            box.base()
            box.write("data/big.txt", "".join(f"line {i} {'x' * 60}\n" for i in range(20000)))
            receipt = box.capture()
            self.assertTrue(receipt["oversize"])
            self.assertIn("patch_oversize", receipt["flags"])
            self.assertGreater(receipt["patch"]["bytes"], 1_048_576)

    def test_declared_repo_that_is_not_top_level_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp)
            proc = box.run("base", MAC_K3D_REPO=str(box.repo / "src"))
            self.assertEqual(proc.returncode, 3)
            self.assertIn("not a git top-level", proc.stderr)
            receipt = box.receipt()
            self.assertEqual(receipt["stage"], "base")
            self.assertIn("capture_error", receipt["flags"])
            self.assertTrue(receipt["errors"])

    def test_repo_from_lolbench_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp, "lolbench")
            meta = box.tmp / "metadata.json"
            meta.write_text(json.dumps({"instance_id": "x_1", "project": "proj"}, indent=2), encoding="utf-8")
            box.base(MAC_K3D_REPO="", LOLBENCH_METADATA=str(meta))
            box.write("src/app.py", APP_EDIT)
            receipt = box.capture(MAC_K3D_REPO="", LOLBENCH_METADATA=str(meta))
            self.assertEqual(receipt["repo"]["source"], "metadata")
            self.assertEqual(receipt["repo"]["path"], str(box.repo))
            self.assertEqual(box.graded(), box.patch())

    def test_declared_base_cross_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp, "lolbench")
            box.base()
            box.write("src/app.py", APP_EDIT)
            receipt = box.capture(MAC_K3D_BASE_COMMIT="lolbench-base")
            self.assertTrue(receipt["base_matches_declared"])
            self.assertEqual(receipt["declared_base_sha"], box.base_sha)
            moved = box.git("rev-parse", "HEAD").strip()
            (box.logs / "base_sha.txt").write_text(moved + "\n", encoding="utf-8")
            receipt = box.capture(MAC_K3D_BASE_COMMIT="lolbench-base")
            self.assertFalse(receipt["base_matches_declared"])
            self.assertIn("base_mismatch", receipt["flags"])

    def test_capture_without_base_step_uses_declared_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp)
            box.write("src/app.py", APP_EDIT)
            box.git("commit", "-qam", "agent work")
            receipt = box.capture(MAC_K3D_BASE_COMMIT=box.base_sha)
            self.assertEqual(receipt["base_source"], "declared")
            self.assertIn("capture_error", receipt["flags"])
            box.applies(box.patch(), {"src/app.py": APP_EDIT})


class CaptureReceiptHostTests(unittest.TestCase):
    def _task(self, root: Path, tid: str, toml: str, gold: str = "") -> Path:
        task = root / tid
        (task / "solution").mkdir(parents=True)
        (task / "task.toml").write_text(toml, encoding="utf-8")
        (task / "solution" / "solution.patch").write_text(gold, encoding="utf-8")
        return task

    def test_declared_repo_per_suite(self):
        sha = "cb1b3b671d0ee9fa9da9f7b02f86967953ffd10a"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lol = self._task(root, "fastapi_1", '[metadata]\nproject = "fastapi"\n')
            deep = self._task(
                root,
                "abs",
                '[metadata]\nbase_commit_hash = "x"\n[[verifier.collect]]\ncommand = "cd /app && mkdir -p '
                f'/logs/artifacts && git diff --binary {sha} HEAD > /logs/artifacts/model.patch"\n',
            )
            pro = self._task(
                root,
                "nodebb",
                '[metadata]\nbase_commit = "' + sha + '"\n[[verifier.collect]]\ncommand = "cd /app && '
                'git add -A && git diff --cached --binary > /logs/artifacts/model.patch"\n',
            )
            self.assertEqual(declared_repo(lol, "lolbench"), ("/workspace/fastapi", "lolbench-base"))
            self.assertEqual(declared_repo(deep, "deepswe"), ("/app", sha))
            self.assertEqual(declared_repo(pro, "swebenchpro"), ("/app", sha))
            short = self._task(
                root,
                "eicrud",
                '[metadata]\nbase_commit_hash = "zzz"\n[[verifier.collect]]\ncommand = "cd /app && '
                'git config --global --add safe.directory /app && git diff --binary 68dafce HEAD > x"\n',
            )
            self.assertEqual(declared_repo(short, "deepswe"), ("/app", "68dafce"))
            self.assertEqual(declared_repo(root / "missing", "deepswe"), ("", ""))

    def test_declared_repo_matches_real_task_layout(self):
        for tasks in (
            Path.home() / "jenkins-agent/workspace/deepswe_one_task/eval-runs/deep-swe/tasks",
            Path.home() / "jenkins-agent/workspace/lolbench_one_task/eval-runs/lolbench/harbor_tasks",
        ):
            if not tasks.is_dir():
                continue
            suite = "lolbench" if "lolbench" in str(tasks) else "deepswe"
            for task in sorted(p for p in tasks.iterdir() if (p / "task.toml").is_file())[:5]:
                repo, base = declared_repo(task, suite)
                self.assertTrue(repo.startswith("/app" if suite == "deepswe" else "/workspace/"), task)
                self.assertTrue(base, task)

    def test_binary_paths(self):
        patch = (
            "diff --git a/img.png b/img.png\nnew file mode 100644\nGIT binary patch\nliteral 3\n"
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
            "diff --git a/old.bin b/old.bin\nBinary files a/old.bin and b/old.bin differ\n"
        )
        self.assertEqual(binary_paths(patch), {"img.png", "old.bin"})

    def _trial(self, root: Path, suite: str, receipt: dict | None, grader: bytes | None) -> Path:
        trial = root / "harness" / "harbor_runs" / "jenkins-1" / "alpha" / "alpha_icode_1_a01" / "alpha__abc"
        (trial / "verifier").mkdir(parents=True)
        (trial / "verifier" / "reward.json").write_text('{"reward": 1}', encoding="utf-8")
        (trial / "agent").mkdir()
        if receipt is not None:
            (trial / "agent" / "capture.json").write_text(json.dumps(receipt), encoding="utf-8")
        if grader is not None:
            name = "solution.patch" if suite == "lolbench" else "model.patch"
            out = trial / "artifacts" / "logs" / "artifacts" / name
            out.parent.mkdir(parents=True)
            out.write_bytes(grader)
        return trial

    def _receipt(self, data: bytes, base: str = "", **patch: object) -> dict:
        body = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "binary_paths": []}
        body.update(patch)
        doc = {"schema": "mac-k3d-capture-v1", "patch": body, "oversize": False, "errors": []}
        if base:
            doc["base_sha"] = base
        return doc

    def test_base_mismatch_compares_on_the_shorter_side(self):
        full = "a" * 40
        # task.toml usually declares an abbreviated sha.
        self.assertFalse(base_mismatch(full, "aaaaaaa"))
        self.assertFalse(base_mismatch(full, full))
        self.assertTrue(base_mismatch(full, "b" * 7))
        self.assertTrue(base_mismatch("aaaaaaa", "b" * 40))
        # Nothing to compare is not a mismatch.
        self.assertFalse(base_mismatch("", full))
        self.assertFalse(base_mismatch(full, ""))
        # LoLBench declares a sentinel, not a commit.
        self.assertFalse(base_mismatch(full, "lolbench-base"))
        self.assertFalse(base_mismatch(full, "v1.2.3"))
        self.assertFalse(base_mismatch("HEAD", full))

    def test_a_trial_that_started_off_the_declared_base_is_flagged(self):
        """The agent env no longer carries the base, so P7 does the check on the host."""
        data = b"diff --git a/x b/x\n"
        declared = "0" * 40
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = self._trial(root / "good", "deepswe", self._receipt(data, base=declared), data)
            self.assertEqual(trial_flags(good, "deepswe", set(), declared)["flags"], [])
            drifted = self._trial(root / "bad", "deepswe", self._receipt(data, base="f" * 40), data)
            result = trial_flags(drifted, "deepswe", set(), declared)
            self.assertEqual(result["flags"], ["base_commit_mismatch"])
            self.assertEqual(result["declared_base"], declared)
            self.assertEqual(result["base_sha"], "f" * 40)
            # No declared base means no verdict either way.
            self.assertEqual(trial_flags(drifted, "deepswe", set())["flags"], [])

    def test_annotate_reads_the_declared_base_out_of_task_toml(self):
        data = b"diff --git a/x b/x\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._trial(root, "deepswe", self._receipt(data, base="f" * 40), data)
            tasks = root / "tasks"
            self._task(
                tasks,
                "alpha",
                "[[verifier.collect]]\ncommand = \"cd /workspace/repo && git diff abc1234\"\n",
            )
            summary = annotate(root / "harness", tasks, ["alpha"], "deepswe")
            self.assertEqual(summary, {"trials": 1, "base_commit_mismatch": 1})

    def test_check_trials_fails_on_a_trial_no_selected_task_claims(self):
        """A trial nothing claims used to vanish into a 0% report; now the anti-cheat stage stops."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            jobs = harness / "harbor_runs" / "jenkins-52"
            for tid in ("alpha", "beta"):
                trial = jobs / "icode_deepswe_52" / f"{tid}__x"
                (trial / "verifier").mkdir(parents=True)
                (trial / "verifier" / "reward.json").write_text('{"reward": 1}', encoding="utf-8")
                (trial / "result.json").write_text(json.dumps({"task_name": f"datacurve/{tid}"}), encoding="utf-8")
            (harness / "harbor_jobs_dir.txt").write_text(f"{jobs}\n", encoding="utf-8")
            selected = Path(tmp) / "selected_tasks.txt"

            def check(*tids: str) -> subprocess.CompletedProcess:
                selected.write_text("".join(f"{tid}\n" for tid in tids), encoding="utf-8")
                return subprocess.run(
                    [
                        sys.executable,
                        str(LIB / "capture_receipt.py"),
                        "check-trials",
                        "--harness-dir",
                        str(harness),
                        "--task-file",
                        str(selected),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            ok = check("alpha", "beta")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("OK trials: 2 matched for 2 tasks", ok.stdout)
            stray = check("alpha")
            self.assertEqual(stray.returncode, 1)
            self.assertIn("icode_deepswe_52/beta__x: beta, datacurve/beta", stray.stderr)
            self.assertNotIn("alpha__x", stray.stderr)

    def test_job_summary_of_a_run_that_crashed_is_not_a_trial(self):
        """Build 53: Harbor raised before any trial ran, leaving its job result.json and a bare trial.log."""
        from score_results import trial_dirs

        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            jobs = harness / "harbor_runs" / "jenkins-53"
            job = jobs / "icode_deepswe_53"
            (job / "ipython-session-bundle-replay__x").mkdir(parents=True)
            (job / "ipython-session-bundle-replay__x" / "trial.log").write_text("ValueError\n", encoding="utf-8")
            (job / "result.json").write_text(
                json.dumps({"id": "j", "n_total_trials": 1, "stats": {}}), encoding="utf-8"
            )
            (harness / "harbor_jobs_dir.txt").write_text(f"{jobs}\n", encoding="utf-8")
            self.assertEqual(trial_dirs(jobs), [])
            selected = Path(tmp) / "selected_tasks.txt"
            selected.write_text("ipython-session-bundle-replay\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(LIB / "capture_receipt.py"), "check-trials",
                 "--harness-dir", str(harness), "--task-file", str(selected)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("no task recorded", proc.stderr)

            trial = job / "ipython-session-bundle-replay__x"
            (trial / "result.json").write_text(json.dumps({"trial_name": "x", "task_name": "t"}), encoding="utf-8")
            self.assertEqual(trial_dirs(jobs), [trial])

    def test_trial_flags_same_check_for_every_suite(self):
        data = b"diff --git a/x b/x\n"
        for suite in SUITES:
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as tmp:
                ok = self._trial(Path(tmp) / "ok", suite, self._receipt(data), data)
                self.assertEqual(trial_flags(ok, suite, set())["flags"], [])
                bad = self._trial(Path(tmp) / "bad", suite, self._receipt(data), b"")
                self.assertEqual(trial_flags(bad, suite, set())["flags"], ["capture_mismatch"])
                gone = self._trial(Path(tmp) / "gone", suite, self._receipt(data), None)
                self.assertEqual(trial_flags(gone, suite, set())["flags"], ["capture_mismatch"])
                missing = self._trial(Path(tmp) / "missing", suite, None, data)
                self.assertEqual(trial_flags(missing, suite, set())["flags"], ["capture_missing"])
                empty = self._trial(Path(tmp) / "empty", suite, self._receipt(b""), None)
                self.assertEqual(trial_flags(empty, suite, set())["flags"], [])

    def test_trial_flags_oversize_and_binary_not_in_gold(self):
        data = b"diff --git a/x b/x\n"
        with tempfile.TemporaryDirectory() as tmp:
            trial = self._trial(Path(tmp), "deepswe", self._receipt(data, binary_paths=["out.bin"]), data)
            result = trial_flags(trial, "deepswe", {"logo.png"})
            self.assertEqual(result["flags"], ["patch_oversize"])
            self.assertEqual(result["binary_not_in_gold"], ["out.bin"])
            self.assertEqual(trial_flags(trial, "deepswe", {"out.bin"})["flags"], [])

    def test_annotate_writes_flags_beside_receipt(self):
        data = b"diff --git a/x b/x\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial = self._trial(root, "lolbench", self._receipt(data), b"other")
            tasks = root / "tasks"
            self._task(tasks, "alpha", '[metadata]\nproject = "p"\n')
            summary = annotate(root / "harness", tasks, ["alpha"], "lolbench")
            self.assertEqual(summary, {"trials": 1, "capture_mismatch": 1})
            flags = json.loads((trial / "agent" / "capture_flags.json").read_text(encoding="utf-8"))
            self.assertEqual(flags["flags"], ["capture_mismatch"])
            self.assertEqual(flags["task"], "alpha")
            self.assertEqual(flags["grader_patch"]["path"], "artifacts/logs/artifacts/solution.patch")

    def test_backup_copies_capture_receipts(self):
        from archive_run import backup_run

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial = self._trial(root, "deepswe", self._receipt(b""), b"")
            (trial / "agent" / "base_sha.txt").write_text("abc\n", encoding="utf-8")
            (trial / "agent" / "capture_flags.json").write_text("{}", encoding="utf-8")
            (trial / "agent" / "capture.patch").write_text("", encoding="utf-8")
            report = root / "report"
            report.mkdir()
            dest = backup_run(
                backup_root=root / "out",
                suite="deepswe",
                task_ids=["alpha"],
                report_dir=report,
                run_folder="run-1",
                harness_dir=root / "harness",
            )
            attempt = dest / "icode" / "alpha" / "attempt-01" / "agent"
            for name in ("capture.json", "base_sha.txt", "capture_flags.json", "capture.patch"):
                self.assertTrue((attempt / name).is_file(), name)


class CaptureWiringTests(unittest.TestCase):
    def test_agents_share_the_capture_script(self):
        icode = (LIB / "icode_harbor_agent.py").read_text(encoding="utf-8")
        patch = (LIB / "patch_harbor_agent.py").read_text(encoding="utf-8")
        for src in (icode, patch):
            self.assertIn("install_capture", src)
            self.assertIn("{CAPTURE} base", src)
            self.assertIn("{CAPTURE} capture", src)
            self.assertNotIn("lolbench-submit", src)
            self.assertNotIn("git add -A", src)
        run = "icode -p /logs/agent/icode-project"
        self.assertLess(icode.find("{CAPTURE} base"), icode.find(run))
        self.assertLess(icode.find(run), icode.find("{CAPTURE} capture"))
        self.assertLess(patch.find("{CAPTURE} base"), patch.find("git apply"))
        self.assertLess(patch.find("git apply"), patch.find("{CAPTURE} capture"))

    def test_submit_runs_before_commit(self):
        src = CAPTURE.read_text(encoding="utf-8")
        body = src[src.index("cmd_capture() {") :]
        self.assertLess(body.index("  deliver\n"), body.index("  commit_work\n"))
        self.assertLess(body.index("  keep_existing\n"), body.index("  deliver\n"))

    def test_stages_pass_declared_repo_and_check_receipts(self):
        stages = ROOT / "pipeline" / "stages"
        p5 = (stages / "evaluate" / "harbor_cmd.sh").read_text(encoding="utf-8")
        receipts = (stages / "anticheat" / "receipts.sh").read_text(encoding="utf-8")
        self.assertIn("capture_receipt.py\" declared-repo", p5)
        # One Harbor job covers many tasks, so the evaluate phase declares every
        # selected task's repo once and the trial picks the one its own image has.
        self.assertIn("MAC_K3D_REPO_CANDIDATES=", p5)
        self.assertNotIn('--ae "MAC_K3D_REPO=', p5)
        self.assertIn("capture_receipt.py\" annotate", receipts)
        guard = receipts.index("capture_receipt.py\" check-trials")
        annotate_at = receipts.index("capture_receipt.py\" annotate")
        self.assertLess(guard, annotate_at)
        # Only annotate may warn and carry on; the guard's exit status is the step's.
        self.assertNotIn("||", receipts[guard:annotate_at])
        run_all = (stages / "run_all.sh").read_text(encoding="utf-8")
        self.assertIn("PHASES=(env tasks evaluate anticheat score report archive)", run_all)

    def test_capture_picks_the_candidate_repo_its_image_has(self):
        capture = (ROOT / "pipeline" / "lib" / "icode_capture.sh").read_text(encoding="utf-8")
        self.assertIn("REPO_CANDIDATES=${MAC_K3D_REPO_CANDIDATES:-}", capture)
        # A declared single repo still wins, so older builds behave the same.
        self.assertLess(
            capture.index('if [ -z "$REPO_DECLARED" ] && [ -n "$REPO_CANDIDATES" ]'),
            capture.index('if [ -n "$REPO_DECLARED" ]; then\n    REPO=$REPO_DECLARED'),
        )


if __name__ == "__main__":
    unittest.main()
