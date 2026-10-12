# Branch process: `int/PF-harbor-delegation`

This folder is **branch process and re-test notes**, not the user start-here. User docs stay under [docs/](../) ([evaluation](../evaluation.md), [architecture](../architecture.md), [workflow](../workflow.md), [user-guide](../user-guide.md)).

Read this one first if you are my mentor: the [responsibility split](#the-responsibility-split-what-mac-k3d-does-and-what-harbor-does) and the [open questions](#open-questions-for-my-mentor) are the two sections written for you.

## What changed on this branch

1. **Harbor schedules the rollouts, not mac-k3d.** The old harness stage used to loop over questions, build one `harbor run` per rollout, keep an in-flight table and a heartbeat scheduler, and wait on `wait -n`. It is now a single `harbor run -p <dataset> -i <id> ... -k <rollouts> -n <slots> -r <retries>`. Harbor expands `n_attempts x tasks x agents` into trials itself and runs `-n` of them at a time.
2. **Declared CPUs are honored; RAM is not an admission limit.** `pipeline/lib/task_resources.py` reads `cpus` out of each `task.toml`. A Jenkins build is one container (`harbor run -n 1`) and locks that many cores. A local run still sets `-n` from `cpu_cores / cpus`, with no RAM cap. The build fails before starting if this worker's CPU limit is below what the task declares.
3. **Executors from the CPU limit.** `jenkins_agent.cpu_cores` is the upper limit (cloud 16, local 4). Executors are `cpu_cores / cpus_per_executor` (`cpus_per_executor` defaults to 2, so cloud 8 and local 2). The node also gets `fits-<suite>` for every suite whose per-task CPUs fit, and `executors-<n>`. Evaluate locks `quantity` equal to the task's declared CPUs out of `<agent>-core-1..cpu_cores`, so mixed suites on one worker never exceed the limit.
4. **Nine jobs, three per suite.** `<suite>_one_task` (one question, one rollout by default — the smoke test, and the shard runner), `<suite>_some_task` (a `TASKS` list or the first `N_TASKS` at the full rollout count — the mentor-PDF comparison arm), `<suite>_full_suite_task` (the whole suite). For `deepswe`, `lolbench` and `swebenchpro`. There is no separate aggregate job.
5. **One build per question and rollout.** `some_task` and `full_suite_task` run on `agent none` and queue one `<suite>_one_task` build per question x rollout. `MAX_IN_FLIGHT` (0 in controller yaml means `2 x` the online executors that carry `fits-<suite>` and `executors-<n>`) is how many of those builds may sit in Jenkins at once. An OOM rollout is appended to the tail, at most `oom_retries` times (default 2). The same dispatcher build merges the final build of each unit in `Aggregate`.
6. **Lock held only for the one trial.** The Jenkinsfile has one stage per pipeline phase (item 12), driven by `MAC_K3D_PHASE`, and only `Evaluate` locks cores, and only as many as the task declares. An OOM build tears the container down, marks itself `UNSTABLE`, and skips Anti-cheat, Score, Report and Archive. `post { always }` removes this build's containers; a sweep removes containers whose owner process is gone.
7. **The pipeline under test ships in the worker's binary and names its commit.** `build.rs` bakes the commit, a dirty flag and a hash of `pipeline/` into `mac-k3d`. Each build extracts that pipeline once (`mac-k3d pipeline --extract-to`) and `artifact.json` records `eval_protocol.pipeline.commit`. `scripts/redeploy.sh` moves a pushed commit to the controller and every worker in one command; an aggregate whose shards ran different pipelines is marked `pipeline_status: mixed`. An earlier version of this branch cloned mac-k3d from GitHub at a `MAC_K3D_GIT_REF` per build; that added three parameters and a push-then-type-the-branch step to every iteration and is gone. `eval --sync-pipeline` and the `MAC_K3D_ROOT` / `~/.local/share` fallback chain are gone too.
8. **Empty-patch P2P no longer counts as a regression.** A trial that produced a zero-byte patch is dropped from the macro P2P average rather than averaged in as 0/N; the verifier only reported 0 because nothing applied. `Pass@1` in the ladder is now labelled "first rollout only" so it is not confused with Macro Pass@1.
9. **Short parameter lists for users.** Each job shows `HARNESS`, `LLM` and `BENCHMARK` first (fixed, so every build names what it evaluated) and only the inputs its shape needs. Pins, canary, `MAX_IN_FLIGHT`, `OOM_RETRIES`, `TEST_MEMORY_MB` and `TEST_MEMORY_ATTEMPTS` are developer-only: `jenkins_job.ui_profile: user` (the default) turns them into hidden parameters with config defaults via the Hidden Parameter plugin, which `mac-k3d start` installs. `CPU_LOCK_QTY`, `SHARDS`, `SHARD_SIZE` and `RESUME` are gone.
10. **A redeploy no longer drops a worker.** Worker `config` used to `curl -o agent.jar` over the jar the running agent had open (`NoClassDefFoundError: hudson/remoting/Channel$OrderlyShutdown` after a remoting bump) and then left the old JVM running. It now downloads beside the jar, swaps it in by rename only when the bytes differ, and restarts the agent only when the jar, `launch-agent.sh` or the unit changed.
11. **Fractional CPU declarations.** DeepSWE's `task.toml` writes `cpus = 2.0`. Build #51 died in the report stage on `--cpus-each 2.0` (`invalid int value`). `task_resources.cpu_count` now keeps whole counts as ints (`2.0` → `2`) and fractions as floats, and every reader (`parallel_degree.sh`, `render_report.py`, `aggregate_runs.py`, `provenance.py`) goes through it.
12. **One phase, one job.** The nine `p0`…`p8` scripts are replaced by seven phases — `env` (bare-metal checks), `tasks` (per-task setup and checks), `evaluate` (slots, canary, one `harbor run`), `anticheat`, `score`, `report`, `archive` — each an ordered list of single-purpose steps under `pipeline/stages/<phase>/`. Steps pass state only through files in `$WORKDIR`. `evaluate/harbor_cmd.sh` is the only file that builds a Harbor command line, so every flag is in one place ([pipeline.md](../pipeline.md#how-harbor-is-called)). The cost/token report now runs in `archive`, and the backup moved out of `render_report.py` into `archive_run.py`. Manual scripts (canary only, baseline, report check, fixtures) moved to `pipeline/tools/`. `mac-k3d eval --stage` takes phase names and answers an old `pN` with the phase that replaced it. The unused Pier adapter is deleted.
13. **Pinned toolchain and a versioned allowlist.** `pipeline/config/toolchain.env` pins Harbor (`0.22.0`), compose and buildx; `mac-k3d setup` and the `env` phase install the same Harbor, and the `env` phase replaces any other version. The agent's `--allow-agent-host` list moved out of the script into `pipeline/config/network-allowlist-v1.json`; its version, hosts and hash go into `eval_protocol.isolation.network_allowlist`, and a test keeps the canary's model hosts equal to it.
14. **Setup and config are modular, and `worker.yaml` always shows its API fields.** The wizard only asks questions (`src/prepare/wizard/`), saves the YAML, then `src/prepare/apply.rs` and `root_steps.rs` do the work, and `config` is split per role (`src/commands/config/`). A worker's `jenkins.api_user` and `api_token` are always written, empty when skipped, so the user can fill them in with an editor later. `config -c <missing file>` now says the file is missing instead of failing on a k3d check.

## The responsibility split: what mac-k3d does and what Harbor does

This is the part you asked me to get right, so here it is explicitly.

| | Owner | Why |
| --- | --- | --- |
| Bare-machine prep (Docker, k3d, pinned Harbor, Jenkins agent, images), re-checked per build by the `env` phase | mac-k3d | Harbor assumes a working Docker host; something has to build one. |
| Per-build prep and checks (`tasks` phase: benchmark checkout, task selection, iCode checkout and sandbox, read-only mount, network allowlist, leak scan) | mac-k3d | These are the inputs to Harbor, not Harbor's job. |
| Container lifecycle, per-task CPU/memory limits, retries, rollout fan-out, verifier execution, reward | **Harbor** | This was the 700 lines of mac-k3d I deleted. Harbor already does it. |
| Job placement across workers, resource admission | Jenkins + lockable-resources | Jenkins is the scheduler; mac-k3d only registers nodes and core tokens. |
| Isolation canary, patch-capture receipts, anti-cheat verdicts, score, report, archive | mac-k3d (`evaluate/canary`, then the `anticheat`, `score`, `report`, `archive` phases) | See below — Harbor has no equivalent. |

**Harbor has no contamination gate.** I checked the installed 0.22.0 rather than assuming: `harbor job summarize` is a removed shim that only prints a deprecation notice, and `harbor analyze` is rubric-only (it asks an LLM to judge a transcript against a rubric). Neither inspects a transcript for solution leakage, reads the gold patch, or can reject a trial. So the integrity layer stays in mac-k3d:

- `icode_capture.sh` writes a receipt (`capture.json`) per trial: repo, base SHA, patch bytes and SHA256.
- The `anticheat` phase cross-checks each receipt against the grader's patch file and the task's declared base commit, and flags `capture_mismatch`, `capture_missing`, `patch_oversize` or `base_commit_mismatch`.
- The anti-cheat pass (`mac-k3d-anticheat-v1`) and the sanitizer run on the transcripts and can mark a trial `flagged` or `rejected`.
- The canary question runs before the real rollouts to prove isolation on the exact `-p` / `-i` path the rollouts will take.

So "Harbor as a black box that returns results" is now true for *scheduling and grading*, which is what it is good at. It is not true for *trusting* the results, and that gap is mine to cover.

## How scalability is guaranteed

Adding a worker is one command: `mac-k3d setup -c worker.yaml`. That registers the Jenkins node, sets `numExecutors` to `cpu_cores / cpus_per_executor`, adds `fits-<suite>` and `executors-<n>`, and creates its `<agent>-core-N` lockable resources. Nothing else changes:

- The next dispatcher counts those `executors-<n>` labels and raises `MAX_IN_FLIGHT` with them, so a new worker gets work without a pipeline edit.
- Jenkins places each `one_task` build on a node whose label is `AGENT_LABEL && fits-<suite>`. Executors cap how many builds run; the core lock caps how many CPUs they hold.
- A worker that finishes a build takes the next queued one. No node list is hardcoded in the pipeline.

**Keep exactly one controller.** Lockable-resources state is per-controller, so a second controller would hand out tokens for cores the first controller already lent out. Scale workers, not controllers.

## Open questions for my mentor

1. **Should an official run honor declared CPUs, or pin them?** Right now it honors `task.toml` (2 CPUs for DeepSWE, 4 for LoLBench). That is faithful to the benchmark, but it means two workers with different core counts run the same question at the same per-task limit yet at different concurrency. If you want bit-comparable timing across machines, I should pin `-n` instead of deriving it.
2. **Compare raw or gated numbers against your PDF?** `artifact.json` carries both: `icode_raw` (every trial, no anti-cheat exclusions) and `icode` (post-gate). Your PDF predates the gate, so `icode_raw` is the apples-to-apples number — but `icode` is the one I would publish. Confirm which I should put in the comparison table.
3. **Is first-rollout Pass@1 or Macro Pass@1 the headline?** They differ whenever a question is inconsistent across rollouts. I report both; tell me which one you consider "the" score.

## How to re-test

Step-by-step log: [testing.md](testing.md). The cloud runbook is [../testing/cloud-eval-runbook.md](../testing/cloud-eval-runbook.md).
