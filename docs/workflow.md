# Binary-initializer workflow

Full path from a **new Mac or Linux** machine through Jenkins roles to an iCode harness evaluation, with JSON, summary, and HTML output.

This is the **binary-initializer** story. Older `cargo install` / `prepare` steps are listed under **Historical** in [README.md](README.md), not this page.

## Project goal

The product is a CI path for AI harness evaluation: many short jobs, each in its own sandbox, so one agent cannot read answers online or touch another run.

| Piece | What it does now | Why it matches the goal |
|-------|------------------|-------------------------|
| **Jenkins** | Three jobs per benchmark (`_one_task`, `_some_task`, `_full_suite_task`), nine in total. `_one_task` is one question and one rollout. The other two queue one `_one_task` build per question and rollout, then merge in the same build. Executors are `cpu_cores / 2`. Evaluate locks only the CPUs that question declares. | Several trials can run on one worker, each in its own build, without one trial taking the whole machine. |
| **Harbor** | Inside that one build, `evaluate` is one `harbor run -n 1 -k 1` plus `icode_harbor_agent:ICodeAgent`, at the version pinned in `pipeline/config/toolchain.env`. Harbor applies the task's declared limits and grades. `--allow-agent-host` comes from `pipeline/config/network-allowlist-v1.json` (`api.deepseek.com`, `api.deepseek.ai`). | One runner, used as a runner. mac-k3d does not schedule containers. The allowlist is the isolation that ships today: the agent can call the model and cannot browse the answer online. |
| **k3d** | The controller uses k3d to host Jenkins. Eval sandboxes still run as Harbor containers on the worker's Docker. | A later job will `k3d cluster create` per build, apply a default-deny NetworkPolicy (DeepSeek API only), pull images through a Harbor registry proxy cache, run Harbor, then delete the cluster. That cluster, the NetworkPolicy, and the registry cache are **not** implemented yet. |

Pier is not used. It left a long-lived Docker Compose sandbox on the host, with no Kubernetes NetworkPolicy and no registry cache, and it was a second runner beside Harbor.

## Why two processes

| Process | Goal | Why |
|---------|------|-----|
| **1. Machine bootstrap** | Download one `mac-k3d` binary; become a Jenkins **controller** or **worker** | A blank PC has no Rust. The binary installs Docker and the rest. |
| **2. Eval pipeline** | Run DeepSWE, LoLBench, or SWE-bench Pro with harness=iCode and LLM=DeepSeek; write JSON and HTML | Score the iCode harness (f2p / p2p, pass@k, tokens, time). |

Several questions add one hop: `_some_task` or `_full_suite_task` queues one `_one_task` build per question and rollout (a worker runs `cpu_cores / 2` of them at once), then merges them into one report in its own `Aggregate` stage.

```mermaid
flowchart TD
  newHost["New Mac or Linux"]
  bin["Download mac-k3d asset"]
  setup["mac-k3d setup"]
  role{"role?"}
  ctrl["Controller: Docker k3d Jenkins :17070 credentials"]
  work["Worker: Docker Java agent Harbor"]
  evalCli["mac-k3d eval: icode / deepseek / deepswe or lolbench or swebenchpro / TASK"]
  shape{"how many questions?"}
  job["one_task: one question, one rollout, locks that task's CPUs"]
  disp["<benchmark>_some_task or _full_suite_task (agent none, splits into shards)"]
  harborA["Harbor + iCode allowlist"]
  grade["Verifier reward.json"]
  agg["Aggregate stage of the dispatcher: merge shard builds"]
  json["eval JSON, summary, HTML, checkout backup"]

  newHost --> bin --> setup --> role
  role --> ctrl
  role --> work
  ctrl --> evalCli
  work --> job
  evalCli --> shape
  shape -->|one| job
  shape -->|a list or the whole suite| disp
  disp -->|one build per question x rollout| job
  job --> harborA
  harborA --> grade
  grade --> agg
  agg --> json
  grade --> json
```

---

## Process 1 — prepare the machine

**User steps:** [new-machine.md](new-machine.md) · clean-machine walkthrough: [clean-machine-binary-test.md](testing/clean-machine-binary-test.md)  
**Pass/fail:** [testing-binary-initializer.md](testing/testing-binary-initializer.md) (Task 0–5 + Task 7 on Linux; Task 6 macOS later) · automated checks: [`scripts/env_set_up/`](../scripts/env_set_up/README.md)

| Step | What | Why |
|------|------|-----|
| Download `mac-k3d-{os}-{arch}` | Prebuilt CLI from a GitHub **pre-release** (or Latest after a final tag) | No Rust on a new laptop |
| `chmod +x` / quarantine strip | Make the asset executable | Unsigned downloads are blocked on macOS |
| `mac-k3d setup` | Wizard: role, Install Docker / k3d / Java | One entry point for controller or worker |
| Controller: `start` + `config` | k3d cluster + Jenkins UI `:17070` + credentials | Job queue and `deepseek-api-key` live here |
| Worker: token + `config` | Inbound agent with `cpu_cores / cpus_per_executor` executors + `<agent>-core-N` locks | Workloads run on the worker’s Docker, not inside the controller’s k3d nodes. Adding the Nth worker needs no pipeline edit |

Honest leftovers: Linux **logout** after docker group; macOS first **Docker Desktop** window; worker **API token**; DeepSeek key on the **controller** credentials store.

Workers must **not** run `mac-k3d start -c worker.yaml` (rejected on purpose).

---

## Process 2 — evaluation pipeline

**Phases, steps and Harbor flags:** [pipeline.md](pipeline.md). **Pass/fail per phase:** [testing-eval-pipeline.md](testing/testing-eval-pipeline.md) (E0–E8 tracking).

| Stage | What | Why |
|-------|------|-----|
| Choose harness / LLM / model / benchmark | v1: **icode** / **deepseek** / **deepseek-flash** (or **deepseek-v4-pro**) / **deepswe**, **lolbench**, or **swebenchpro** | Catalog ids; unknown `--model` is rejected |
| Choose N and iCode binary vs source | Limit cost; users drop a binary at `~/.local/share/mac-k3d/icode` | The worker that runs Harbor must see iCode |
| Install Harbor | `uv tool install harbor==<HARBOR_VERSION>` from `pipeline/config/toolchain.env`, at setup and again by the `env` phase when the version differs | One runner for all three benchmarks, at one version |
| Clone the suite | `tasks/benchmark` clones DeepSWE, LoLBench-Preview, or SWE-bench_Pro-os at its pin into the job's `eval-runs/`, once per workspace | Not vendored in this repo. The checkout is text: each question's instruction, tests, and the address of its container image. For SWE-bench Pro, `tasks/benchmark` writes a Harbor `task.toml` whose image is `jefzda/sweap-images:…` |
| Pull the question image | Harbor pulls the image named in that question's `task.toml` when its first trial starts (LoLBench: `tasks/images` builds a missing one first); Docker keeps it on the worker | One image per question, fetched only for questions that run. [pipeline.md](pipeline.md#benchmark-checkout-and-question-images) |
| Harbor agent `icode` | `icode_harbor_agent:ICodeAgent` bind-mounts the worker drop at `/opt/icode-host` | Same iCode binary in every suite. LoLBench also calls `lolbench-submit` |
| Harness run | one `harbor run -a icode_harbor_agent:ICodeAgent -p <dataset> -i <ids> -k <rollouts> -n <slots> --allow-agent-host api.deepseek.com --allow-agent-host api.deepseek.ai`, after the isolation canary | iCode under test. Harbor owns the fan-out, the per-task limits and the retries. Allowlist is the isolation that ships today. A full eval does not run the no-harness baseline |
| Anti-cheat | capture receipts and the transcript / similarity verdict per trial | Flags a trial that read the answer or submitted something other than its patch |
| Grade | Harbor `reward.json` → f2p / p2p / `resolved` / pass@1, tokens, time, model | Held-out tests plus API usage |
| JSON | `eval-runs/output/{suite}/jenkins-<build>-<UTC>/artifact.json` plus `summary.md` and `report.html` | One folder per run |
| Archive | `cost-token-report.md` in that folder, then `output/{suite}/<run>.tar.gz` | The kept copy, with the cost analysis |

Trigger:

```bash
mac-k3d eval                  # interactive → Jenkins <benchmark>_one_task (or --local)
mac-k3d eval --job some --tasks a,b,c --n-rollouts 1   # queue a job: same fields as its UI page
mac-k3d eval --stage tasks --n-tasks 1   # one phase on its own (env, tasks, evaluate, …)
```

`--job one|some|full` and the job's **Build with Parameters** page start the same build: the CLI sends only the fields you give and the job keeps its defaults for the rest ([commands.md](commands.md#queue-a-jenkins-job-from-the-cli)).

Nine Jenkins jobs — `one_task`, `some_task` and `full_suite_task` for each of **deepswe**, **lolbench** and **swebenchpro**. Every evaluation runs as a `one_task` build from the same `run_all.sh`; each job pins `HARNESS`, `LLM` and `BENCHMARK` and shows them first. Logs print `PROGRESS n% …`. Agent label `lolbench`. Evaluate locks the CPUs that question declares, not the whole node. Which shape to pick is in [evaluation.md](evaluation.md#which-job-to-run); how the lock becomes Harbor's `-n 1` is in [optimization.md](optimization.md).

---

## Development loop: commit, push, redeploy

A Jenkins build runs the `pipeline/` embedded in the `mac-k3d` binary installed on its worker. `build.rs` bakes the commit the binary was built from into it, so every result still names the code that produced it, and one command moves a change everywhere:

```bash
git commit -am "fix: <what>"
git push
bash scripts/redeploy.sh --controller <user>@<controller> --worker <user>@<worker>
```

`redeploy.sh` builds `target/release/mac-k3d` once, then:

| Host | What it does |
|---|---|
| Controller | installs `~/.local/bin/mac-k3d`, then `config --skip-secrets` (rewrites the nine jobs). With `--start` it runs `start` instead, which is only needed when the Jenkins plugin list changed |
| Each `--worker` | installs `~/.local/bin/mac-k3d`, then `config -c worker.yaml` (node, core locks, agent) |
| This PC | the same as a worker, unless `--no-local-worker` |

It asks each host's SSH password once and copies the binary to every remote host as `mac-k3d.new` first. If the copy fails `--version` on any host (another OS, or an older glibc than the build machine's), it stops before switching any host. It ends by comparing `mac-k3d --version` everywhere. The command is the same whatever changed (pipeline script, job XML, agent registration), so there is no table of which binary has to move.

`--version` prints `mac-k3d 0.5.3 (<commit>)`, plus `, dirty` when `src/` or `pipeline/` had uncommitted changes at build time. The first stage (Environment) runs `mac-k3d pipeline --extract-to $WORKSPACE/mac-k3d-pipeline`, prints `mac-k3d pipeline <commit> (mac-k3d <version>, <binary>)`, and `artifact.json` records `eval_protocol.pipeline` as `{source: binary, version, commit, dirty, pipeline_hash}`. The other six stages reuse that extract, so a redeploy during a build does not change the scripts under it. That gives the loop:

- **Did my fix work?** Redeploy, rebuild, compare `pipeline.commit` with the previous build's.
- **Did that commit break it?** Check out the known-good commit, redeploy, rebuild.
- **Reproduce an old number?** Redeploy its `pipeline.commit`. Every Jenkins build runs `mac-k3d pipeline --require-clean`, which refuses a dirty build or an unknown commit, so a number always names one commit.

A sharded run merges builds from several workers. If they report different `pipeline.commit` or `pipeline_hash` (a worker missed a redeploy), the aggregate `artifact.json` says `pipeline_status: mixed` and lists each one; the report check rejects it.

Worker `config` downloads `agent.jar` next to the running one and swaps it in only when the bytes differ. The agent is restarted only when the jar, `launch-agent.sh` or the systemd unit / LaunchAgent changed; otherwise it prints `Jenkins agent unchanged, left running`, so a redeploy does not drop a connected worker.

Developer parameters (pins, `AGENT_LABEL`, `MAX_IN_FLIGHT`, `OOM_RETRIES`, `TEST_MEMORY_MB`, `TEST_MEMORY_ATTEMPTS`) show only in the `developer` profile. Switch while developing and back for handover:

```bash
mac-k3d set --ui-profile developer && mac-k3d config --skip-secrets   # on the controller
mac-k3d set --ui-profile user && mac-k3d config --skip-secrets        # handover
```

Lab copy-paste with this lab's hosts filled in lives in the gitignored `LOCAL_DEPLOY_CHEATSHEET.md` in the checkout, not in this repo.

---

## Where output lives

Workdir is **`eval-runs/`** (`MAC_K3D_EVAL_WORKDIR`). Jenkins sets it to `$WORKSPACE/eval-runs`.

| What | Path |
|------|------|
| Official report (`report` phase) | `eval-runs/output/deepswe/jenkins-<build>-<UTC>/artifact.json` |
| Summary and HTML | `summary.md` and `report.html` in that same run folder |
| Backup | `output/<benchmark>/jenkins-<build>-<UTC>.tar.gz` under the pipeline root: `$WORKSPACE/mac-k3d-pipeline/output/` on Jenkins (kept across builds; the Environment stage clears only `pipeline/`), the checkout's `output/` for a local run. `MAC_K3D_OUTPUT_ROOT` overrides it |
| On the worker (Jenkins workspace) | `$HOME/jenkins-agent/workspace/deepswe_one_task/eval-runs/output/` |
| Jenkins artifact | that run folder (`eval-runs/last_output.txt`) and the backup `.tar.gz` (`eval-runs/last_backup.txt`; a shard skips it). A `some_task` / `full_suite_task` build has `aggregate/` and one combined `backup/<benchmark>/<RUN_GROUP>.tar.gz`. Every build's files are kept |
| Score scratch (`score` phase) | `eval-runs/results/score-temp.json` |
| Last report path | `eval-runs/last_output.txt` |
| Harness logs/patches | `eval-runs/harness/` |
| DeepSWE clone | `eval-runs/deep-swe/` |
| Copied iCode for the run | `eval-runs/icode-bin/icode` |

If `jenkins_agent.remote_fs` in `worker.yaml` is not the default, the workspace copy is at `{remote_fs}/workspace/deepswe_one_task/eval-runs/output/`. The copy to keep for investigation is the backup under `mac-k3d-pipeline/output/`. Share `~/.local/share/mac-k3d/eval-runs` is not the Jenkins report dir.

---

## Release assets (all machines)

One tag publishes **four** assets (see [`.github/workflows/release-binaries.yml`](../.github/workflows/release-binaries.yml)):

| Asset | Runner | How it is built |
|-------|--------|-----------------|
| `mac-k3d-linux-x86_64` | `ubuntu-latest` | native |
| `mac-k3d-linux-aarch64` | `ubuntu-24.04-arm` | native |
| `mac-k3d-darwin-aarch64` | `macos-latest` (Apple Silicon) | native |
| `mac-k3d-darwin-x86_64` | `macos-latest` (Apple Silicon) | **cross-compile** `--target x86_64-apple-darwin` |

CI checks `file` + `lipo -info` so the Intel asset is **x86_64**, not arm64. There is no `macos-13` job.

---

## Related docs

| Doc | Role |
|-----|------|
| [new-machine.md](new-machine.md) | User bootstrap commands |
| [clean-machine-binary-test.md](testing/clean-machine-binary-test.md) | Clean PC → binary → controller/worker → eval-ready |
| [`scripts/env_set_up/`](../scripts/env_set_up/README.md) | Automated controller/worker/eval-ready checks |
| [testing-binary-initializer.md](testing/testing-binary-initializer.md) | Bootstrap sign-off |
| [cloud-eval-runbook.md](testing/cloud-eval-runbook.md) | Operator runbook: cloud root controller → local worker → JSON |
| [testing-eval-pipeline.md](testing/testing-eval-pipeline.md) | Pipeline stage CLI tests |
| [secrets.md](secrets.md) | Controller credentials (`deepseek-api-key`) |
| [lolbench-jenkins.md](lolbench-jenkins.md) | All three jobs are Harbor + iCode |
| [harbor-delegation-multiworker/README.md](harbor-delegation-multiworker/README.md) | Harbor/mac-k3d responsibility split, job topology, multi-worker scaling |
