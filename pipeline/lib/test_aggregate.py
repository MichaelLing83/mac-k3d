#!/usr/bin/env python3
"""Shard merge tests (PF.5): one report out of many workers' trials."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = LIB.parent.parent
sys.path.insert(0, str(LIB))

from aggregate_runs import main, merge_anticheat, merge_harness, pipeline_status, shard_dirs  # noqa: E402


def write_trial(job: Path, task: str, attempt: int, reward: float, patch_bytes: int = 120) -> None:
    trial = job / f"{task}_icode_{attempt}" / "trial"
    (trial / "verifier").mkdir(parents=True)
    (trial / "verifier" / "reward.json").write_text(
        json.dumps(
            {
                "reward": reward,
                "f2p": reward,
                "f2p_total": 4,
                "f2p_passed": int(4 * reward),
                "p2p": 1.0,
                "p2p_total": 2,
                "p2p_passed": 2,
            }
        ),
        encoding="utf-8",
    )
    # Harbor 0.22: task_name is <org>/<id> and task_id names the task dir.
    (trial / "result.json").write_text(
        json.dumps({"task_name": f"datacurve/{task}", "task_id": {"path": f"/eval-runs/deep-swe/tasks/{task}"}}),
        encoding="utf-8",
    )
    (trial / "agent").mkdir(exist_ok=True)
    (trial / "agent" / "capture.json").write_text(
        json.dumps({"patch": {"bytes": patch_bytes}}), encoding="utf-8"
    )


def make_shard(
    root: Path,
    build: str,
    tasks: dict[str, list[float]],
    commit: str = "84c66ededd24",
    inputs: dict | None = None,
) -> Path:
    """One shard's archived ``eval-runs`` tree, as copyArtifacts would leave it."""
    shard = root / build / "eval-runs"
    job = shard / "harness" / "harbor_runs" / build / "icode_deepswe"
    for task, rewards in tasks.items():
        for attempt, reward in enumerate(rewards, start=1):
            write_trial(job, task, attempt, reward)
    shard.mkdir(parents=True, exist_ok=True)
    (shard / "selected_tasks.txt").write_text("\n".join(tasks) + "\n", encoding="utf-8")
    pipeline = {"source": "binary", "commit": commit, "dirty": False, "pipeline_hash": f"hash-{commit}"}
    (shard / "eval_protocol_inputs.json").write_text(
        json.dumps(
            {
                "model": "deepseek-flash",
                "api_base": "https://api.deepseek.com/v1",
                "pipeline": pipeline,
                **(inputs or {}),
            }
        ),
        encoding="utf-8",
    )
    (shard / "eval_resources.json").write_text(
        json.dumps({"applied": {"slots": 2, "cpus_each": 2}}), encoding="utf-8"
    )
    ac = shard / "harness" / "anticheat"
    ac.mkdir(parents=True, exist_ok=True)
    (ac / "summary.json").write_text(
        json.dumps(
            {
                "version": "mac-k3d-anticheat-v1",
                "counts": {"clean": len(tasks), "flagged": 0, "rejected": 0, "not_run": 0},
                "attempts": sum(len(v) for v in tasks.values()),
                "no_transcript": 0,
                "overrides": 0,
                "rejected": [],
                "flagged": [],
            }
        ),
        encoding="utf-8",
    )
    return shard


# What a shard's post step archives (src/prepare/jenkins_job.rs), relative to eval-runs/.
SHARD_ARCHIVE = (
    "harness/harbor_runs/jenkins-{build}",
    "harness/anticheat",
    "selected_tasks.txt",
    "suite_tasks.txt",
    "skipped_tasks.txt",
    "eval_protocol_inputs.json",
    "eval_resources.json",
    "harness/container_mem.jsonl",
)


def archive_like_a_shard(work: Path, build: str, dest: Path) -> None:
    """Copy a worker's eval-runs/ the way the shard archive and copyArtifacts would."""
    for pattern in SHARD_ARCHIVE:
        rel = pattern.format(build=build)
        src, target = work / rel, dest / rel
        if src.is_dir():
            shutil.copytree(src, target)
        elif src.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


class ShardDiscoveryTests(unittest.TestCase):
    def test_finds_every_shard_under_the_copied_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_shard(root, "101", {"alpha": [1.0]})
            make_shard(root, "102", {"beta": [0.0]})
            got = shard_dirs(root)
            self.assertEqual(len(got), 2)
            self.assertEqual({p.parent.name for p in got}, {"101", "102"})

    def test_a_hand_assembled_tree_also_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness").mkdir()
            self.assertEqual(shard_dirs(root), [root])

    def test_no_shards_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(shard_dirs(Path(tmp)), [])


class MergeTests(unittest.TestCase):
    def test_trials_from_two_workers_land_under_one_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_shard(root, "101", {"alpha": [1.0, 0.0]})
            make_shard(root, "102", {"beta": [1.0, 1.0]})
            merged = merge_harness(shard_dirs(root), root / "out")
            self.assertEqual(merged["tasks"], ["alpha", "beta"])
            self.assertEqual(merged["shards"], 2)
            builds = sorted(p.name for p in (merged["harness"] / "harbor_runs").iterdir())
            # Prefixed per shard, so two workers' builds cannot collide.
            self.assertEqual(builds, ["shard1-101", "shard2-102"])
            self.assertEqual(merged["protocol"]["model_params"]["model"], "deepseek-flash")
            self.assertEqual(merged["anticheat"]["attempts"], 4)
            self.assertEqual(merged["anticheat"]["counts"]["clean"], 2)

    def test_verdict_counts_add_up_across_shards(self):
        got = merge_anticheat(
            [
                {
                    "version": "v1",
                    "counts": {"clean": 3, "flagged": 1, "rejected": 0},
                    "attempts": 4,
                    "rejected": [],
                    "flagged": [{"task": "a"}],
                },
                {
                    "version": "v1",
                    "counts": {"clean": 1, "flagged": 0, "rejected": 2, "not_run": 3},
                    "attempts": 6,
                    "rejected": [{"task": "b"}, {"task": "c"}],
                    "flagged": [],
                },
            ]
        )
        # The first shard predates not_run; it counts as 0 there.
        self.assertEqual(got["counts"], {"clean": 4, "flagged": 1, "rejected": 2, "not_run": 3})
        self.assertEqual(got["attempts"], 10)
        self.assertEqual(len(got["rejected"]), 2)
        self.assertEqual(got["shards"], 2)
        self.assertNotIn("status", got)

    def test_shards_on_different_pipelines_are_flagged_not_averaged(self):
        got = merge_anticheat(
            [
                {"version": "v1", "counts": {"clean": 1, "flagged": 0, "rejected": 0}, "attempts": 1},
                {"version": "v2", "counts": {"clean": 1, "flagged": 0, "rejected": 0}, "attempts": 1},
            ]
        )
        self.assertEqual(got["status"], "mixed_versions")
        self.assertEqual(got["versions"], ["v1", "v2"])

    def test_no_summaries_means_no_merged_verdict(self):
        self.assertIsNone(merge_anticheat([]))

    def test_pipeline_status_compares_commit_and_hash(self):
        self.assertIsNone(pipeline_status([]))
        same = {"commit": "a", "pipeline_hash": "h"}
        self.assertEqual(pipeline_status([same, dict(same)]), {"pipeline_status": "same"})
        # Same commit, different embedded scripts: a dirty build on one worker.
        got = pipeline_status([same, {"commit": "a", "pipeline_hash": "other"}])
        self.assertEqual(got["pipeline_status"], "mixed")
        self.assertEqual(len(got["pipelines"]), 2)


def run_aggregate(shards: Path, out: Path, rollouts: int = 2, extra: tuple[str, ...] = ()) -> int:
    argv = sys.argv
    sys.argv = [
        "aggregate_runs.py",
        "--shards",
        str(shards),
        "--benchmark",
        "deepswe",
        "--run-group",
        "deepswe_full_suite_task-7",
        "--n-rollouts",
        str(rollouts),
        *extra,
        "--out",
        str(out),
    ]
    try:
        return main()
    finally:
        sys.argv = argv


class CliTests(unittest.TestCase):
    def run_main(self, shards: Path, out: Path, rollouts: int = 2) -> int:
        return run_aggregate(shards, out, rollouts)

    def test_one_report_covers_every_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # One worker ran both shards, one after the other.
            make_shard(root, "101", {"alpha": [1.0, 0.0]}, inputs={"worker": {"node": "mac-cloud"}})
            make_shard(root, "102", {"beta": [1.0, 1.0]}, inputs={"worker": {"node": "mac-cloud"}})
            out = root / "aggregate"
            self.assertEqual(self.run_main(root, out), 0)
            doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["run_group"], "deepswe_full_suite_task-7")
            self.assertEqual(doc["shards"], 2)
            self.assertEqual(doc["n_tasks"], 2)
            self.assertEqual(doc["n_rollouts"], 2)
            tasks = {row["id"]: row for row in doc["icode"]["tasks"]}
            self.assertEqual(set(tasks), {"alpha", "beta"})
            # tasks x rollouts records, from two different workers. A task whose
            # every attempt passed is stored compacted, so count n, not rollouts.
            self.assertEqual(sum(row["n"] for row in tasks.values()), 4)
            self.assertEqual(len(tasks["alpha"]["rollouts"]), 2)
            self.assertEqual(tasks["beta"]["c"], 2)
            self.assertEqual(tasks["alpha"]["c"], 1)
            self.assertEqual(doc["concurrency"], 2)
            self.assertEqual(doc["cpus_each"], 2)
            self.assertEqual(doc["pipeline_status"], "same")
            self.assertNotIn("pipelines", doc)
            self.assertTrue((out / "summary.md").is_file())
            self.assertTrue((out / "report.html").is_file())

    def test_the_combined_tar_gz_holds_every_shard(self):
        """The Aggregate stage packs aggregate/ with archive_run.py (src/prepare/jenkins_job.rs)."""
        import tarfile

        from archive_run import main as archive_main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_shard(root / "shards", "101", {"alpha": [1.0]})
            make_shard(root / "shards", "102", {"beta": [0.0]})
            out = root / "aggregate"
            self.assertEqual(self.run_main(root / "shards", out, rollouts=1), 0)
            self.assertEqual((out / "selected_tasks.txt").read_text(encoding="utf-8"), "alpha\nbeta\n")
            group = "deepswe_full_suite_task-7"
            self.assertEqual(
                archive_main(
                    [
                        "--report-dir", str(out),
                        "--harness-dir", str(out / "harness"),
                        "--task-file", str(out / "selected_tasks.txt"),
                        "--suite", "deepswe",
                        "--backup-root", str(root / "backup"),
                        "--run-folder", group,
                    ]
                ),
                0,
            )
            with tarfile.open(root / "backup" / "deepswe" / f"{group}.tar.gz") as tar:
                names = set(tar.getnames())
            self.assertIn(f"{group}/artifact.json", names)
            self.assertIn(f"{group}/tasks.txt", names)
            self.assertIn(f"{group}/anticheat/summary.json", names)
            for task in ("alpha", "beta"):
                self.assertTrue(
                    any(n.startswith(f"{group}/icode/{task}/attempt-01/") for n in names), f"{task}: {sorted(names)}"
                )

    def test_shards_from_reused_workspaces_merge_only_their_own_build(self):
        """Each worker still holds a pre-PF.1 build of the same task, scored 0."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copied = root / "shards"
            for build, task in (("101", "alpha"), ("102", "beta")):
                work = make_shard(root / "workspaces", f"jenkins-{build}", {task: [1.0]})
                write_trial(work / "harness" / "harbor_runs" / "jenkins-40" / task / "job", task, 1, 0.0)
                archive_like_a_shard(work, build, copied / build / "eval-runs")
            out = root / "aggregate"
            self.assertEqual(self.run_main(copied, out, rollouts=1), 0)
            doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
            builds = sorted(p.name for p in (out / "harness" / "harbor_runs").iterdir())
            self.assertEqual(builds, ["shard1-jenkins-101", "shard2-jenkins-102"])
            self.assertEqual(doc["shards"], 2)
            self.assertEqual({row["id"]: row["c"] for row in doc["icode"]["tasks"]}, {"alpha": 1, "beta": 1})
            self.assertEqual(doc["icode"]["macro_pass@1"], 1.0)
            self.assertEqual(doc["anticheat"]["status"], "ok")
            self.assertEqual(doc["anticheat"]["attempts"], 2)
            self.assertEqual(doc["anticheat"]["counts"]["clean"], 2)
            self.assertIn("clean **2**", (out / "summary.md").read_text(encoding="utf-8"))

    def test_shards_from_different_pipeline_builds_are_marked_mixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_shard(root, "101", {"alpha": [1.0]}, commit="aaaa")
            make_shard(root, "102", {"beta": [1.0]}, commit="bbbb")
            out = root / "aggregate"
            self.assertEqual(self.run_main(root, out, rollouts=1), 0)
            doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["pipeline_status"], "mixed")
            self.assertEqual(
                doc["pipelines"],
                [
                    {"commit": "aaaa", "pipeline_hash": "hash-aaaa"},
                    {"commit": "bbbb", "pipeline_hash": "hash-bbbb"},
                ],
            )

    def test_float_cpus_each_from_an_old_plan_is_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard = make_shard(root, "101", {"alpha": [1.0]})
            (shard / "eval_resources.json").write_text(
                json.dumps({"applied": {"slots": 1, "cpus_each": 2.0}}), encoding="utf-8"
            )
            out = root / "aggregate"
            self.assertEqual(self.run_main(root, out, rollouts=1), 0)
            doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["cpus_each"], 2)
            self.assertIsInstance(doc["cpus_each"], int)

    def test_an_empty_collection_says_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_main(root, root / "out"), 1)

    def test_every_shard_keeps_its_own_worker_and_model_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for build, node in (("101", "mac-cloud"), ("102", "mac-home")):
                make_shard(
                    root,
                    build,
                    {f"task{build}": [1.0]},
                    inputs={
                        "provider": "DeepSeek",
                        "reasoning_effort": "high",
                        "max_tokens": 65536,
                        "max_iterations": 500,
                        "concurrency": 2,
                        "cpus_each": 2,
                        "worker": {"node": node, "nproc": 16},
                        "images": {f"task{build}": {"id": f"sha256:{build}"}},
                    },
                )
            out = root / "aggregate"
            self.assertEqual(self.run_main(root, out, rollouts=1), 0)
            doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
            protocol = doc["eval_protocol"]
            self.assertEqual(protocol["model_params_status"], "same")
            self.assertEqual([s["worker"]["node"] for s in protocol["shards"]], ["mac-cloud", "mac-home"])
            self.assertEqual([s["builds"] for s in protocol["shards"]], [["101"], ["102"]])
            self.assertEqual(protocol["model_params"]["max_tokens"], 65536)
            # Both shards' image pins, not only the first shard's.
            self.assertEqual(sorted(protocol["images"]), ["task101", "task102"])
            md = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("- Worker (shard 1 101): node mac-cloud", md)
            self.assertIn("- Worker (shard 2 102): node mac-home", md)
            self.assertIn("max_tokens `65536` · max_iterations `500`", md)
            page = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("Worker (shard 2 102): node mac-home", page)

    def test_shards_with_different_max_tokens_are_marked_mixed(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_shard(root, "101", {"alpha": [1.0]}, inputs={"max_tokens": 65536, "worker": {"node": "a"}})
            make_shard(root, "102", {"beta": [1.0]}, inputs={"max_tokens": 8192, "worker": {"node": "b"}})
            out = root / "aggregate"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(self.run_main(root, out, rollouts=1), 0)
            doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["eval_protocol"]["model_params_status"], "mixed")
            self.assertEqual(doc["eval_protocol"]["model_params_mismatch"], {"max_tokens": [8192, 65536]})
            self.assertIn("WARNING: shards ran different model params or iCode (max_tokens [8192, 65536])", err.getvalue())
            md = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("- Shards differ: max_tokens [8192, 65536]. Not one measurement of one iCode setup.", md)

    def test_a_shards_own_report_supplies_the_icode_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard = make_shard(root, "101", {"alpha": [1.0]})
            report = shard / "output" / "deepswe" / "101-20261007T000000Z"
            report.mkdir(parents=True)
            own = {
                "icode": {
                    "mode": "git",
                    "git": {"kind": "pr", "ref": "2", "sha": "eea9d66", "subject": "pin"},
                },
                "model_params": {"model": "deepseek-flash", "provider": "DeepSeek", "max_tokens": 65536},
                "resources": {"concurrency": 2, "cpus_each": 2},
                "worker": {"node": "mac-cloud"},
            }
            (report / "artifact.json").write_text(
                json.dumps({"run_id": "101", "eval_protocol": own}), encoding="utf-8"
            )
            out = root / "aggregate"
            self.assertEqual(self.run_main(root, out, rollouts=1), 0)
            doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertNotIn("version", doc["eval_protocol"]["icode"])
            self.assertEqual(doc["eval_protocol"]["icode"]["git"]["sha"], "eea9d66")
            self.assertEqual(doc["eval_protocol"]["shards"][0]["icode_version"], "eea9d66")
            self.assertNotIn("iCode version", summary)
            self.assertIn("- iCode under test: `git pr 2 -> eea9d66 (pin)`", summary)


SHARD_URL = "http://jenkins:8080/job/deepswe_one_task/{build}/"
DISPATCHER_URL = "http://jenkins:8080/job/deepswe_some_task/5/"
DEFAULT_PROBE = "alpine:3.23.4@sha256:5b10"
SUBSTITUTE_PROBE = "alpine:3.19@sha256:6baf"


def shard_inputs(build: str, node: str, probe: str = DEFAULT_PROBE, **extra) -> dict:
    """What a shard's tasks phase records, naming the build that wrote it."""
    return {
        "requester": {"user": "toby", "build_url": SHARD_URL.format(build=build)},
        "worker": {"node": node, "nproc": 16},
        "max_tokens": 65536,
        "max_iterations": 500,
        "isolation": {
            "egress_probe": {"harbor_default": DEFAULT_PROBE, "image": probe, "substituted": probe != DEFAULT_PROBE}
        },
        **extra,
    }


def set_anticheat(shard: Path, clean: int, attempts: int) -> None:
    summary = shard / "harness" / "anticheat" / "summary.json"
    doc = json.loads(summary.read_text(encoding="utf-8"))
    doc["counts"]["clean"] = clean
    doc["attempts"] = attempts
    summary.write_text(json.dumps(doc), encoding="utf-8")


class DispatcherPlanTests(unittest.TestCase):
    """The dispatcher's shards/plan.txt (src/prepare/jenkins_job.rs) and reused shard workspaces."""

    def collect(self, root: Path, build: str, work: Path) -> None:
        archive_like_a_shard(work, build, root / "shards" / build / "eval-runs")

    def run_plan(
        self, root: Path, plan: list[str], rollouts: int = 4, extra: tuple[str, ...] = ()
    ) -> tuple[int, str]:
        import contextlib
        import io

        (root / "shards").mkdir(parents=True, exist_ok=True)
        (root / "shards" / "plan.txt").write_text("".join(f"{line}\n" for line in plan), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = run_aggregate(
                root / "shards",
                root / "aggregate",
                rollouts,
                ("--plan", str(root / "shards" / "plan.txt"), "--build-url", DISPATCHER_URL, *extra),
            )
        return code, err.getvalue()

    def report(self, root: Path) -> tuple[dict, str, str]:
        out = root / "aggregate"
        return (
            json.loads((out / "artifact.json").read_text(encoding="utf-8")),
            (out / "summary.md").read_text(encoding="utf-8"),
            (out / "report.html").read_text(encoding="utf-8"),
        )

    def test_a_shard_that_failed_early_does_not_ship_the_previous_builds_files(self):
        """deepswe_some_task #5: cloud shard #61 stopped before Harbor and still held build #58's files."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Written before the image table kept only the run's tasks.
            images = {"Yb43UkF": {"id": "sha256:y"}, "abs-module-cache-flags": {"id": "sha256:old"}}
            local = make_shard(
                root / "home", "jenkins-60", {"Yb43UkF": [1.0, 1.0, 1.0, 0.0]},
                inputs=shard_inputs("60", "mac-Michael-Ubuntu", images=images),
            )
            set_anticheat(local, clean=4, attempts=4)
            self.collect(root, "60", local)
            stale = make_shard(
                root / "cloud", "jenkins-58", {"Yb43UkF": [0.0, 0.0]}, commit="a3d2e19",
                inputs=shard_inputs("58", "mac-iZt4ndd2dff7gqjta7mppaZ", SUBSTITUTE_PROBE, max_tokens=8192),
            )
            set_anticheat(stale, clean=2, attempts=2)
            self.collect(root, "61", stale)
            code, err = self.run_plan(
                root,
                [
                    "1|60|SUCCESS|0|1|Yb43UkF",
                    "2|61|FAILURE|1|2|wazero-multi-module-snapshots,ytt-jsonpath-query-api",
                ],
            )
            self.assertEqual(code, 0, err)
            doc, md, page = self.report(root)

            self.assertIn(f"WARNING: shard 2 #61 holds files from {SHARD_URL.format(build='58')}; ignored", err)
            self.assertEqual(doc["anticheat"]["counts"]["clean"], 4)
            self.assertEqual(doc["anticheat"]["attempts"], 4)
            self.assertEqual(doc["pipeline_status"], "same")
            protocol = doc["eval_protocol"]
            self.assertEqual(protocol["model_params_status"], "same")
            self.assertNotIn("Shards differ", md)
            self.assertEqual(doc["shards"], 2)
            tasks = {row["id"]: row for row in doc["icode"]["tasks"]}
            self.assertEqual((tasks["Yb43UkF"]["c"], tasks["Yb43UkF"]["n_scored"]), (3, 4))
            for tid in ("wazero-multi-module-snapshots", "ytt-jsonpath-query-api"):
                self.assertEqual((tasks[tid]["c"], tasks[tid]["n_scored"], tasks[tid]["unscored"]), (0, 0, 4))
            self.assertIn("(missing/excluded: 2)", md)
            failed = protocol["shards"][1]
            self.assertEqual(failed["builds"], ["jenkins-61"])
            self.assertEqual(failed["result"], "FAILURE")
            self.assertEqual(failed["worker"], {})
            self.assertEqual(failed["not_run"], ["wazero-multi-module-snapshots", "ytt-jsonpath-query-api"])
            line = (
                "Worker (shard 2 jenkins-61): FAILURE before any trial · "
                "2 questions not run: wazero-multi-module-snapshots, ytt-jsonpath-query-api"
            )
            self.assertIn(f"- {line}", md)
            self.assertIn(line, page)
            self.assertIn("- Worker (shard 1 jenkins-60): node mac-Michael-Ubuntu", md)
            self.assertIn(f"egress probe Harbor default {DEFAULT_PROBE}", md)
            self.assertNotIn(SUBSTITUTE_PROBE, md)
            self.assertIn(f"- Requester: `toby` `{DISPATCHER_URL}`", md)
            self.assertIn(
                "- Coverage: 1 of 3 planned questions have trials; 2 not run (shard 2 jenkins-61 FAILURE)", md
            )
            self.assertEqual(doc["model"], "openai/deepseek-flash")
            self.assertEqual(sorted(protocol["images"]), ["Yb43UkF"])
            self.assertNotIn("abs-module-cache-flags", md)
            # Only the shard's own trials were merged; the stale shard archived none.
            runs = sorted(p.name for p in (root / "aggregate" / "harness" / "harbor_runs").iterdir())
            self.assertEqual(runs, ["shard1-jenkins-60"])

    def test_a_shard_that_archived_nothing_still_gets_a_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = make_shard(root / "home", "jenkins-60", {"alpha": [1.0]}, inputs=shard_inputs("60", "a"))
            self.collect(root, "60", local)
            code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|alpha", "2|61|ABORTED|1|1|beta"], rollouts=1)
            self.assertEqual(code, 0, err)
            doc, md, _ = self.report(root)
            self.assertEqual(doc["n_tasks"], 2)
            self.assertIn("- Worker (shard 2 jenkins-61): ABORTED before any trial · 1 question not run: beta", md)

    def test_rollouts_of_one_question_from_two_builds_merge_in_rollout_order(self):
        """Per-rollout dispatch: each build is one question x one rollout."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for build, rollout, reward in (("70", 2, 1.0), ("71", 1, 0.0)):
                work = make_shard(root / f"ws{build}", f"jenkins-{build}", {"alpha": [reward]},
                                  inputs=shard_inputs(build, "node"))
                jobs = work / "harness" / "harbor_runs" / f"jenkins-{build}"
                (jobs / "rollout.txt").write_text(f"{rollout}\n", encoding="utf-8")
                self.collect(root, build, work)
            code, err = self.run_plan(
                root, ["1|70|SUCCESS|0|1|alpha|2|1", "1|71|SUCCESS|0|1|alpha|1|3"], rollouts=2
            )
            self.assertEqual(code, 0, err)
            doc, _, _ = self.report(root)
            self.assertEqual(doc["n_tasks"], 1)
            self.assertEqual(doc["coverage"]["planned"], 1)
            row = {r["id"]: r for r in doc["icode"]["tasks"]}["alpha"]
            self.assertEqual(row["n"], 2)
            self.assertEqual(row["c"], 1)
            # Rollout 1 (build 71) failed, so first-rollout Pass@1 is 0.
            self.assertEqual(doc["icode"]["pass@1"], 0.0)
            self.assertEqual(
                doc["eval_protocol"]["oom_requeues"],
                [{"tasks": ["alpha"], "offset": 0, "rollout": 1, "attempts": 3}],
            )

    def test_a_plan_reads_old_and_per_rollout_lines(self):
        from aggregate_runs import planned_questions, read_plan

        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.txt"
            plan.write_text("1|60|SUCCESS|0|2|a,b\n1|61|UNSTABLE|0|1|c|3|2\nbad|line\n", encoding="utf-8")
            rows = read_plan(plan)
            self.assertEqual([(r["rollout"], r["attempts"]) for r in rows], [(1, 1), (3, 2)])
            self.assertEqual(planned_questions(rows), 3)
            per_rollout = [r for r in rows if r["count"] == 1] * 4
            self.assertEqual(planned_questions(per_rollout), 1)

    def offset_shard(self, root: Path, build: str, tasks: dict[str, list[float]], suite: list[str]) -> None:
        """A full_suite_task shard: selected by offset, with the suite list its tasks phase wrote."""
        work = make_shard(root / f"ws{build}", f"jenkins-{build}", tasks, inputs=shard_inputs(build, f"node{build}"))
        (work / "suite_tasks.txt").write_text("".join(f"{tid}\n" for tid in suite), encoding="utf-8")
        self.collect(root, build, work)

    def test_an_offset_shard_that_ran_nothing_is_named_from_the_suite_list(self):
        """full_suite_task names no ids, so a failed shard's questions come from the shards' suite_tasks.txt."""
        suite = ["alpha", "beta", "delta", "epsilon", "gamma", "zeta"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.offset_shard(root, "60", {"alpha": [1.0]}, suite)
            code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|", "2|61|FAILURE|1|5|"], rollouts=1)
            self.assertEqual(code, 0, err)
            doc, md, page = self.report(root)
            failed = doc["eval_protocol"]["shards"][1]
            self.assertEqual(failed["not_run"], suite[1:])
            self.assertNotIn("not_run_count", failed)
            self.assertEqual(doc["n_tasks"], 6)
            self.assertIn("(missing/excluded: 5)", md)
            self.assertIn(
                "- Worker (shard 2 jenkins-61): FAILURE before any trial · "
                "5 questions not run: beta, delta, epsilon, gamma, zeta",
                md,
            )
            coverage = "Coverage: 1 of 6 planned questions have trials; 5 not run (shard 2 jenkins-61 FAILURE)"
            self.assertIn(f"- {coverage}", md)
            self.assertIn(coverage, page)
            self.assertEqual(
                {k: doc["coverage"][k] for k in ("planned", "with_trials", "not_named", "in_metrics")},
                {"planned": 6, "with_trials": 1, "not_named": 0, "in_metrics": 6},
            )
            self.assertIn("5 questions count as missing", err)
            self.assertNotIn("Pass@k covers", err)

    def test_an_offset_shard_stays_unnamed_when_the_suite_lists_disagree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.offset_shard(root, "60", {"alpha": [1.0]}, ["alpha", "beta", "eta"])
            self.offset_shard(root, "62", {"eta": [1.0]}, ["alpha", "eta", "beta"])
            code, err = self.run_plan(
                root, ["1|60|SUCCESS|0|1|", "2|61|FAILURE|1|5|", "3|62|SUCCESS|6|1|"], rollouts=1
            )
            self.assertEqual(code, 0, err)
            doc, md, _ = self.report(root)
            failed = doc["eval_protocol"]["shards"][1]
            self.assertEqual((failed["not_run_count"], failed["not_run_offset"]), (5, 1))
            self.assertIn("WARNING: the shards listed different suites", err)
            self.assertIn("its 5 questions at offset 1 are not named, so the report does not count them", err)
            self.assertIn("WARNING: the dispatcher planned 7 questions but Pass@k covers 2", err)
            self.assertIn("- Worker (shard 2 jenkins-61): FAILURE before any trial · 5 questions at offset 1 not run", md)
            self.assertIn(
                "- Coverage: 2 of 7 planned questions have trials; 5 not run (shard 2 jenkins-61 FAILURE); "
                "5 not named, so Pass@k covers 2 questions",
                md,
            )

    def test_a_slice_overlapping_another_shards_questions_is_flagged(self):
        """Two workers that sorted the suite differently cut overlapping slices."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.offset_shard(root, "60", {"beta": [1.0]}, ["alpha", "beta", "gamma"])
            code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|", "2|61|FAILURE|1|2|"], rollouts=1)
            self.assertEqual(code, 0, err)
            self.assertIn(
                "WARNING: shard 2 jenkins-61's slice overlaps questions another shard ran (beta); "
                "the shards cut different slices of the suite",
                err,
            )

    def test_the_dispatchers_user_is_the_requester(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = make_shard(root / "home", "jenkins-60", {"alpha": [1.0]}, inputs=shard_inputs("60", "a"))
            self.collect(root, "60", local)
            code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|alpha"], rollouts=1, extra=("--requester-user", "Toby"))
            self.assertEqual(code, 0, err)
            doc, md, page = self.report(root)
            self.assertEqual(doc["eval_protocol"]["requester"], {"user": "Toby", "build_url": DISPATCHER_URL})
            self.assertIn(f"- Requester: `Toby` `{DISPATCHER_URL}`", md)
            self.assertIn(f"Requester Toby {DISPATCHER_URL}", page)

    def test_a_fresh_shard_from_its_own_build_counts_in_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for build, task, node in (("60", "alpha", "home"), ("61", "beta", "cloud")):
                work = make_shard(root / node, f"jenkins-{build}", {task: [1.0, 0.0]}, inputs=shard_inputs(build, node))
                self.collect(root, build, work)
            code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|alpha", "2|61|SUCCESS|1|1|beta"], rollouts=2)
            self.assertEqual(code, 0, err)
            self.assertNotIn("WARNING", err)
            doc, md, _ = self.report(root)
            self.assertEqual({row["id"] for row in doc["icode"]["tasks"]}, {"alpha", "beta"})
            self.assertEqual(doc["anticheat"]["attempts"], 4)
            self.assertNotRegex(md, r"\d+ (questions? )?not run")
            self.assertIn("· not run **0** ·", md)
            self.assertIn("- Coverage: 2 of 2 planned questions have trials", md)
            self.assertIn("- Worker (shard 2 jenkins-61): node cloud", md)
            self.assertIn(f"- Egress probe: Harbor default {DEFAULT_PROBE}", md)

    def test_shards_with_different_egress_probes_each_name_their_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for build, task, node, probe in (
                ("60", "alpha", "home", DEFAULT_PROBE),
                ("61", "beta", "cloud", SUBSTITUTE_PROBE),
            ):
                work = make_shard(root / node, f"jenkins-{build}", {task: [1.0]}, inputs=shard_inputs(build, node, probe))
                self.collect(root, build, work)
            code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|alpha", "2|61|SUCCESS|1|1|beta"], rollouts=1)
            self.assertEqual(code, 0, err)
            doc, md, _ = self.report(root)
            self.assertNotIn("egress_probe", doc["eval_protocol"].get("isolation") or {})
            self.assertNotIn("- Egress probe:", md)
            lines = [line for line in md.splitlines() if line.startswith("- Worker (shard")]
            self.assertTrue(lines[0].startswith("- Worker (shard 1 jenkins-60): node home · nproc 16"), lines[0])
            self.assertIn(f"egress probe Harbor default {DEFAULT_PROBE}", lines[0])
            self.assertIn(f"egress probe substituted {SUBSTITUTE_PROBE}", lines[1])

    def test_each_worker_line_names_its_trial_networks(self):
        for pools, merged in (
            (("10.213.0.0/16", "10.213.0.0/16"), "- Trial networks: own /28 subnets from 10.213.0.0/16 · 4090 of 4096"),
            (("10.213.0.0/16", "10.214.0.0/16"), None),
        ):
            with self.subTest(pools=pools), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for (build, task, node, free), pool in zip((("60", "alpha", "home", 4090), ("61", "beta", "cloud", 4093)), pools):
                    inputs = shard_inputs(build, node)
                    inputs["isolation"]["trial_network"] = {
                        "mode": "subnets", "pool": pool, "prefix": 28, "free": free, "total": 4096, "need": 2,
                    }
                    work = make_shard(root / node, f"jenkins-{build}", {task: [1.0]}, inputs=inputs)
                    self.collect(root, build, work)
                code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|alpha", "2|61|SUCCESS|1|1|beta"], rollouts=1)
                self.assertEqual(code, 0, err)
                _, md, page = self.report(root)
                lines = [line for line in md.splitlines() if line.startswith("- Worker (shard")]
                self.assertIn(f"egress probe Harbor default {DEFAULT_PROBE} · trial networks {pools[0]} /28 (4090 free)", lines[0])
                self.assertIn(f"trial networks {pools[1]} /28 (4093 free)", lines[1])
                if merged:
                    self.assertIn(merged, md)
                else:
                    self.assertNotIn("- Trial networks:", md)

    def seven_shard(self, root: Path, build: str, node: str, tasks: dict[str, list[float]], **facts) -> Path:
        """One shard of deepswe_some_task #7, with what its own build recorded."""
        inputs = shard_inputs(build, node)
        isolation = inputs["isolation"]
        isolation["trial_network"] = {
            "mode": "subnets", "pool": "10.213.0.0/16", "prefix": 28, "free": facts["free"], "total": 4096,
            "need": facts["need"], "checked_at": "2026-10-07T00:00:00Z",
        }
        isolation["leak_scan"] = {
            "scanner": "mac-k3d-leakscan-v1", "hit_tasks": facts.get("hits", []), "statuses": facts["statuses"],
            "report_sha256": f"leak{build}",
        }
        if facts.get("canary", True):
            isolation["canary"] = {
                "version": "mac-k3d-canary-v1", "status": facts.get("canary_status", "pass"), "node": node,
                "tasks": [facts["canary_task"]], "failed_tasks": [], "warn_tasks": facts.get("warn", []),
                "counts": {"tasks": 1, "pass": 1, "fail": 0}, "summary_sha256": f"canary{build}",
            }
        inputs.update({"cpu_lock_qty": 16, "concurrency": facts["slots"], "cpus_each": 2})
        work = make_shard(root / node, f"jenkins-{build}", tasks, inputs=inputs)
        (work / "eval_resources.json").write_text(
            json.dumps(
                {
                    "declared": {
                        "per_task": {tid: {"memory_gb": 8, "cpus": 2} for tid in tasks},
                        "peak": {"memory_gb": 8, "cpus": facts.get("peak_cpus", 2)},
                        "tasks": len(tasks),
                        "declared_tasks": len(tasks),
                    },
                    "applied": {"slots": facts["slots"], "cpus_each": 2, "cpu_lock_qty": 16},
                    "host": {"mem_total_gb": facts["mem_total"]},
                    "reasons": [f"{node}: {facts['slots']} slots"],
                }
            ),
            encoding="utf-8",
        )
        (work / "harness" / "container_mem.jsonl").write_text(
            "".join(json.dumps({"question": tid, "peak_gb": facts["mem_gb"], "slots": facts["slots"]}) + "\n" for tid in tasks),
            encoding="utf-8",
        )
        if facts.get("skipped"):
            (work / "skipped_tasks.txt").write_text(
                "".join(f"{tid}\tleak scan hit\n" for tid in facts["skipped"]), encoding="utf-8"
            )
        self.collect(root, build, work)
        return work

    def test_the_merged_report_covers_every_shard_not_only_the_first(self):
        """deepswe_some_task #7: the leak scan, canary, resources and memory came from shard 1 alone."""
        cloud, home = "mac-iZt4ndd2dff7gqjta7mppaZ", "mac-Michael-Ubuntu"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seven_shard(
                root, "67", cloud, {"helm-unified-manifest-stream": [1.0], "httpx-streaming-json-iteration": [1.0]},
                slots=8, need=8, free=4090, statuses={"clean": 2}, canary_task="helm-unified-manifest-stream",
                mem_total=182.4, mem_gb=1.848,
            )
            self.seven_shard(
                root, "68", home,
                {"cattrs-partial-structuring-recovery": [0.0], "drizzle-orm-window-function-builders": [1.0]},
                slots=1, need=1, free=4093, statuses={"clean": 2, "hit": 1}, hits=["etree-xml-diff-patch"],
                canary_task="cattrs-partial-structuring-recovery", warn=["cattrs-partial-structuring-recovery"],
                mem_total=14.8, mem_gb=1.571, skipped=["etree-xml-diff-patch"], peak_cpus=4,
            )
            code, err = self.run_plan(
                root,
                [
                    "1|67|SUCCESS|0|2|helm-unified-manifest-stream,httpx-streaming-json-iteration",
                    "2|68|SUCCESS|2|3|cattrs-partial-structuring-recovery,drizzle-orm-window-function-builders,"
                    "etree-xml-diff-patch",
                ],
                rollouts=1,
            )
            self.assertEqual(code, 0, err)
            doc, md, page = self.report(root)
            protocol = doc["eval_protocol"]
            isolation = protocol["isolation"]

            leak = isolation["leak_scan"]
            self.assertEqual(leak["statuses"], {"clean": 4, "hit": 1})
            self.assertEqual(leak["hit_tasks"], ["etree-xml-diff-patch"])
            self.assertNotIn("report_sha256", leak)
            self.assertEqual([row["leak_scan_sha256"] for row in protocol["shards"]], ["leak67", "leak68"])
            self.assertIn("- Leak scan: mac-k3d-leakscan-v1 · hit tasks etree-xml-diff-patch · clean 4, hit 1", md)

            canary = isolation["canary"]
            self.assertEqual(canary["status"], "pass")
            self.assertEqual(canary["tasks"], ["cattrs-partial-structuring-recovery", "helm-unified-manifest-stream"])
            self.assertEqual(canary["warn_tasks"], ["cattrs-partial-structuring-recovery"])
            self.assertEqual(canary["nodes"], sorted([cloud, home]))
            self.assertEqual(canary["counts"], {"tasks": 2, "pass": 2, "fail": 0})

            network = isolation["trial_network"]
            self.assertEqual((network["need_by_shard"], network["free"]), ([8, 1], 4090))
            self.assertIn("- Trial networks: own /28 subnets from 10.213.0.0/16 · 4090 of 4096 free at check · need 8 · 1 (by shard)", md)

            self.assertEqual(protocol["resources"]["concurrency"], 9)
            self.assertEqual(protocol["resources"]["cpu_lock_qty"], 32)
            self.assertEqual(doc["concurrency"], 9)
            self.assertIn("- Resources: cpu_lock_qty `32` · concurrency `9` · cpus_each `2`", md)
            self.assertIn(f"  - node `{cloud}`: cpu_lock_qty `16` · concurrency `8` · cpus_each `2`", md)
            self.assertIn(f"  - node `{home}`: cpu_lock_qty `16` · concurrency `1` · cpus_each `2`", md)
            self.assertIn("concurrency: **9**", md)
            self.assertIn("concurrency 9", page)

            declared = doc["resources"]["declared"]
            self.assertEqual(len(declared["per_task"]), 4)
            self.assertEqual((declared["tasks"], declared["declared_tasks"]), (4, 4))
            self.assertEqual(declared["peak"], {"memory_gb": 8, "cpus": 4})
            self.assertEqual(doc["resources"]["applied"]["slots"], 9)
            self.assertEqual(doc["resources"]["by_node"][home]["host"], {"mem_total_gb": 14.8})

            self.assertAlmostEqual(doc["container_mem_max_gb"], 1.848)
            self.assertIn("- Container memory — max peak: **1.85 GB**", md)
            rows = (root / "aggregate" / "container_mem.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line)["shard"] for line in rows], [1, 1, 2, 2])
            self.assertEqual(doc["skipped_questions"], ["etree-xml-diff-patch: leak scan hit"])
            self.assertEqual(
                (root / "aggregate" / "skipped_questions.txt").read_text(encoding="utf-8"),
                "etree-xml-diff-patch: leak scan hit\n",
            )

    def test_a_shard_without_a_canary_or_with_a_failed_one_shows_in_the_merge(self):
        for second, status in (({"canary": False}, "missing"), ({"canary_status": "fail"}, "fail")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                common = dict(slots=1, need=1, free=4090, statuses={"clean": 1}, mem_total=14.8, mem_gb=1.0)
                self.seven_shard(root, "60", "home", {"alpha": [1.0]}, canary_task="alpha", **common)
                self.seven_shard(root, "61", "cloud", {"beta": [1.0]}, canary_task="beta", **common, **second)
                code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|alpha", "2|61|SUCCESS|1|1|beta"], rollouts=1)
                self.assertEqual(code, 0, err)
                doc, md, _ = self.report(root)
                self.assertEqual(doc["eval_protocol"]["isolation"]["canary"]["status"], status)
                self.assertIn(f"· {status} · tasks", md)

    def test_a_shard_that_stopped_before_harbor_adds_no_slots_or_leak_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seven_shard(
                root, "60", "home", {"alpha": [1.0]}, slots=1, need=1, free=4090,
                statuses={"clean": 1}, canary_task="alpha", mem_total=14.8, mem_gb=1.0,
            )
            stopped = self.seven_shard(
                root / "stopped", "61", "cloud", {"beta": [1.0]}, slots=8, need=8, free=4090,
                statuses={"clean": 1}, canary_task="beta", canary_status="fail", mem_total=182.4, mem_gb=3.0,
            )
            shutil.rmtree(root / "stopped" / "shards" / "61" / "eval-runs" / "harness" / "harbor_runs")
            shutil.copytree(root / "stopped" / "shards" / "61", root / "shards" / "61")
            del stopped
            code, err = self.run_plan(root, ["1|60|SUCCESS|0|1|alpha", "2|61|FAILURE|1|1|beta"], rollouts=1)
            self.assertEqual(code, 0, err)
            doc, _, _ = self.report(root)
            isolation = doc["eval_protocol"]["isolation"]
            self.assertEqual(isolation["leak_scan"]["statuses"], {"clean": 1})
            # Its failed canary still fails the merge: one worker could not prove isolation.
            self.assertEqual(isolation["canary"]["status"], "fail")
            self.assertEqual(doc["concurrency"], 1)
            self.assertAlmostEqual(doc["container_mem_max_gb"], 1.0)

    def test_the_report_names_the_shard_builds_that_keep_the_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = dict(slots=1, need=1, free=4090, statuses={"clean": 1}, mem_total=14.8, mem_gb=1.0)
            self.seven_shard(root, "70", "home", {"alpha": [1.0]}, canary_task="alpha", **common)
            self.seven_shard(root, "9", "cloud", {"beta": [1.0]}, canary_task="beta", **common)
            code, err = self.run_plan(
                root,
                ["1|70|SUCCESS|0|1|alpha", "2|9|SUCCESS|1|1|beta", "3|71|FAILURE|2|1|gamma"],
                rollouts=1,
                extra=("--shard-job", "deepswe_one_task"),
            )
            self.assertEqual(code, 0, err)
            doc, md, page = self.report(root)
            # Shard 3 never ran Harbor, so it has no transcripts to point at.
            self.assertEqual(doc["eval_protocol"]["transcripts"], {"job": "deepswe_one_task", "builds": ["9", "70"]})
            where = "(deepswe_one_task #9, #70), gzipped under eval-runs/harness/harbor_runs/*/*/agent/icode-project"
            line = next(ln for ln in md.splitlines() if ln.startswith("- Transcripts:"))
            self.assertTrue(line.endswith(where), line)
            self.assertGreater(md.index(line), md.index("### Provenance"))
            self.assertIn(where, page)
            self.assertGreater(page.index(where), page.index("<h2>Provenance</h2>"))

        # A single build keeps its own transcripts; the line is for merged reports.
        from provenance import provenance_markdown

        self.assertFalse(any("Transcripts" in ln for ln in provenance_markdown(shard_inputs("58", "cloud"))))

    def test_a_per_rollout_plan_counts_questions_and_keeps_every_rollout(self):
        """Two questions x two rollouts: planned is the questions, rows are the units."""
        from score_results import harbor_task_trials

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Rollout 2 is the lower build number, so order has to come from rollout.txt.
            later = make_shard(
                root / "home", "jenkins-10", {"alpha": [0.0]}, inputs=shard_inputs("10", "home")
            )
            (later / "harness" / "harbor_runs" / "jenkins-10" / "rollout.txt").write_text("2\n", encoding="utf-8")
            self.collect(root, "10", later)
            first = make_shard(
                root / "cloud", "jenkins-11", {"alpha": [1.0]}, inputs=shard_inputs("11", "cloud")
            )
            (first / "harness" / "harbor_runs" / "jenkins-11" / "rollout.txt").write_text("1\n", encoding="utf-8")
            self.collect(root, "11", first)
            beta = make_shard(
                root / "home", "jenkins-12", {"beta": [1.0]}, inputs=shard_inputs("12", "home")
            )
            (beta / "harness" / "harbor_runs" / "jenkins-12" / "rollout.txt").write_text("1\n", encoding="utf-8")
            self.collect(root, "12", beta)
            code, err = self.run_plan(
                root,
                [
                    "1|10|SUCCESS|0|1|alpha|2|3",
                    "2|11|SUCCESS|0|1|alpha|1|1",
                    "3|12|SUCCESS|1|1|beta|1|2",
                ],
                rollouts=2,
            )
            self.assertEqual(code, 0, err)
            doc, _, _ = self.report(root)
            self.assertEqual(doc["coverage"]["planned"], 2)
            self.assertEqual(len(doc["eval_protocol"]["shards"]), 3)
            self.assertEqual(
                doc["eval_protocol"]["oom_requeues"],
                [
                    {"tasks": ["alpha"], "offset": 0, "rollout": 2, "attempts": 3},
                    {"tasks": ["beta"], "offset": 1, "rollout": 1, "attempts": 2},
                ],
            )
            harness = root / "aggregate" / "harness"
            names = [p.parent.name for p in harbor_task_trials(harness, "alpha")]
            self.assertEqual(names[0].rsplit("_", 1)[0], "alpha_icode")
            rewards = []
            for trial in harbor_task_trials(harness, "alpha"):
                rewards.append(json.loads((trial / "verifier" / "reward.json").read_text())["reward"])
            self.assertEqual(rewards, [1.0, 0.0])

    def test_without_a_plan_every_archived_shard_counts_as_before(self):
        """An older dispatcher passes no --plan; nothing is dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = make_shard(root / "cloud", "jenkins-58", {"alpha": [1.0]}, inputs=shard_inputs("58", "cloud"))
            archive_like_a_shard(stale, "58", root / "shards" / "61" / "eval-runs")
            self.assertEqual(run_aggregate(root / "shards", root / "aggregate", 1), 0)
            doc, _, _ = self.report(root)
            self.assertEqual(doc["n_tasks"], 1)
            self.assertEqual(doc["anticheat"]["counts"]["clean"], 1)
            self.assertEqual(doc["eval_protocol"]["requester"]["build_url"], SHARD_URL.format(build="58"))


class EmptyPatchP2PTests(unittest.TestCase):
    def test_a_trial_that_changed_nothing_does_not_count_as_broken_p2p(self):
        """LoLBench reports applied=0 -> p2p 0/N for an empty patch. That is not a regression."""
        from score_results import rates_for_trial

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier" / "reward.json").write_text(
                json.dumps(
                    {
                        "applied": 0.0,
                        "build_ok": 0.0,
                        "f2p_pass": 0,
                        "f2p_pass_rate": 0.0,
                        "f2p_total": 27,
                        "p2p_pass": 0,
                        "p2p_pass_rate": 0.0,
                        "p2p_total": 52,
                        "reward": 0.0,
                    }
                ),
                encoding="utf-8",
            )
            (trial / "agent").mkdir()
            (trial / "agent" / "capture.json").write_text(
                json.dumps({"patch": {"bytes": 0}}), encoding="utf-8"
            )
            rates = rates_for_trial(trial)
            self.assertTrue(rates["empty_patch"])
            self.assertIsNone(rates["p2p"])
            self.assertIsNone(rates["p2p_pass"])
            self.assertIsNone(rates["p2p_total"])
            self.assertIsNone(rates["partial"])
            # What the grader said is kept for the report, outside the averages.
            self.assertEqual((rates["grader_p2p_pass"], rates["grader_p2p_total"]), (0, 52))
            # F2P still counts: the agent genuinely did not fix the bug.
            self.assertEqual(rates["f2p"], 0.0)

    def test_deepswe_passes_the_base_repo_and_that_is_kept_as_the_graders_count(self):
        """DeepSWE runs the untouched base's tests for an empty patch, so they pass (1/1)."""
        from score_results import rates_for_trial

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier" / "reward.json").write_text(
                json.dumps(
                    {"reward": 0, "f2p": 0.0, "f2p_total": 103, "f2p_passed": 0, "p2p": 1.0, "p2p_total": 1, "p2p_passed": 1}
                ),
                encoding="utf-8",
            )
            (trial / "agent").mkdir()
            (trial / "agent" / "capture.json").write_text(json.dumps({"patch": {"bytes": 0}}), encoding="utf-8")
            rates = rates_for_trial(trial)
            self.assertIsNone(rates["p2p"])
            self.assertEqual((rates["grader_p2p_pass"], rates["grader_p2p_total"]), (1, 1))

    def test_a_real_patch_keeps_its_p2p(self):
        from score_results import rates_for_trial

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier" / "reward.json").write_text(
                json.dumps(
                    {"reward": 0, "f2p": 0.5, "f2p_total": 2, "f2p_passed": 1, "p2p": 0.0, "p2p_total": 9, "p2p_passed": 0}
                ),
                encoding="utf-8",
            )
            (trial / "agent").mkdir()
            (trial / "agent" / "capture.json").write_text(
                json.dumps({"patch": {"bytes": 900}}), encoding="utf-8"
            )
            rates = rates_for_trial(trial)
            self.assertNotIn("empty_patch", rates)
            self.assertEqual(rates["p2p"], 0.0)
            self.assertEqual(rates["p2p_total"], 9)

    def test_no_receipt_leaves_the_rates_alone(self):
        from score_results import rates_for_trial, trial_patch_bytes

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier" / "reward.json").write_text(
                json.dumps({"reward": 1, "p2p": 1.0, "p2p_total": 3, "p2p_passed": 3}), encoding="utf-8"
            )
            self.assertIsNone(trial_patch_bytes(trial))
            self.assertEqual(rates_for_trial(trial)["p2p"], 1.0)


if __name__ == "__main__":
    unittest.main()
