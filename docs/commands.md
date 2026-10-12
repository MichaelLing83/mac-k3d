# Commands

All commands accept global flags:

| Flag | Description |
|------|-------------|
| `-c, --config <PATH>` | Config file (default: `~/.config/mac-k3d/config.yaml`) |
| `-v, --verbose` | Increase log verbosity (repeatable: `-vv` for debug) |

Logging uses `tracing`; override with `RUST_LOG=debug`.

---

## `setup`

First-run for a downloadable binary: interactive wizard, then apply.

```bash
mac-k3d                 # TTY only: same as setup
mac-k3d setup
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
mac-k3d setup --disk-min-gb 20
```

### Flags

| Flag | Description |
|------|-------------|
| `--disk-min-gb N` | Override minimum free disk (GB) for prepare |

Global `-c / --config` is honored.

### Behavior

1. Run `prepare`: the wizard (questions only) if there is no config, or the existing-config menu (**Use existing config** / **Re-run wizard** / **Cancel**). The YAML is saved before anything is installed; then the installs and the pinned Harbor are applied. If a step needs root and `sudo` is not available, it stops with one block for an administrator and how to resume ([prepare-wizard.md](prepare-wizard.md#part-6-apply-root-steps-disk-check)).
2. Prompt **Continue and apply now?**
3. **Controller / standalone:** `start` then `config` (k3d, Jenkins, the nine eval jobs).
4. **Worker:** `config` only (Jenkins agent). Does **not** run `start`.
5. Print role, config path, Jenkins URL, agent unit/LaunchAgent name.

If stdin is not a TTY and no subcommand is given, the CLI exits 2 with a short usage line.

Keep `prepare` / `start` / `config` for power users. Controller `config`/`start` ensure **nine** jobs: `one_task`, `some_task` and `full_suite_task` for each of **deepswe**, **lolbench** and **swebenchpro**, and delete the retired `eval_aggregate` job if an older version created it. They share one pipeline of seven phases ([pipeline.md](pipeline.md)); pick the job or `--benchmark`. Which shape to use: [evaluation.md](evaluation.md#which-job-to-run).

---

## `eval`

Run the iCode **agent harness** with a DeepSeek catalog model through Harbor on DeepSWE, LoLBench or SWE-bench Pro (Process 2). Phases, steps and Harbor flags: [pipeline.md](pipeline.md). See [user-guide.md](user-guide.md), [icode-harness-inputs.md](icode-harness-inputs.md), and [testing/testing-eval-pipeline.md](testing/testing-eval-pipeline.md).

```bash
mac-k3d eval --stage env
mac-k3d eval --stage tasks --n-tasks 1
mac-k3d eval --stage evaluate --n-tasks 1
mac-k3d eval --local --benchmark deepswe --n-tasks 1 --icode-mode release --model deepseek-v4-pro
mac-k3d eval --local --benchmark deepswe --n-tasks 1 --icode-mode git \
  --icode-git-url https://github.com/org/icode.git --icode-git-ref v0.1.41 \
  --icode-git-ref-kind tag
mac-k3d eval --local --benchmark deepswe --n-tasks 1 --model deepseek-flash
mac-k3d eval --local --benchmark swebenchpro --n-tasks 1 --icode-mode release
mac-k3d eval --job some --tasks ipython-session-bundle-replay,ytt-jsonpath-query-api --n-rollouts 1
mac-k3d eval --job one --benchmark lolbench --task ruff_1 --icode-mode git
mac-k3d eval                         # interactive → local or Jenkins *_one_task
# Jenkins release: open the job page and upload ICODE_RELEASE_FILE (the CLI cannot attach a file)
```

### Queue a Jenkins job from the CLI

`--job one|some|full` queues `<benchmark>_one_task`, `_some_task` or `_full_suite_task` with the same fields as its Build with Parameters page. Only the fields given as flags are sent; every other field keeps the job's default, exactly as pressing **Build** with the form untouched does. Environment variables and `jenkins_job.*` are not read for this, so the CLI and the UI start the same build from the same inputs.

```bash
# what would be sent; nothing is queued
mac-k3d eval --job some --tasks ipython-session-bundle-replay,ytt-jsonpath-query-api,\
wazero-multi-module-snapshots,abs-module-cache-flags --n-rollouts 1 --dry-run

# the same, queued; prints the build URL and the `eval record` line for when it ends
mac-k3d eval --job some --tasks ipython-session-bundle-replay,ytt-jsonpath-query-api,\
wazero-multi-module-snapshots,abs-module-cache-flags --n-rollouts 1

mac-k3d eval --job full                                   # the whole suite, job defaults
mac-k3d eval --job one --task abs-module-cache-flags --param AGENT_LABEL=linux-Michael-Ubuntu
```

Run it on any machine whose config (or `~/.config/mac-k3d/worker.yaml`) has `jenkins_agent.controller_url`, `api_user` and `api_token`. curl gets the credentials on stdin, never in its arguments or the URL. Before queueing, the CLI reads the live job's field names; a field the live job lacks means its jobs are older than this binary, and it stops and asks for a controller redeploy instead of letting Jenkins drop the value.

A flag the job has no field for is refused rather than ignored: `--tasks` on `--job one`, `--n-rollouts` other than 1 on `--job one` (several rollouts of one question use `--job some --tasks ID`), `--task` on `--job some`, `--n-tasks` above 1 on `--job one` (use `--job some`), `--icode-mode release` anywhere (the release drop is a file upload; use the job page), and any `--param` that names a field with its own flag, a dispatcher field (`TASKS`, `N_TASKS`, `TASK_OFFSET`, `RUN_GROUP`, `SHARD` on `one_task`) or a field the job does not have.

### Flags

| Flag | Description |
|------|-------------|
| `--stage PHASE` | Run one phase: `env`, `tasks`, `evaluate`, `anticheat`, `score`, `report`, `archive`, or `all`; `baseline` runs the manual LLM-only arm (`pipeline/tools/baseline.sh`). An old `p0`…`p8` exits with the phase that replaced it, for example `--stage p5 is gone; … Use --stage evaluate` |
| `--local` | Full local `run_all.sh` (no Jenkins). `--local` and `--stage` set `CPU_LOCK_QTY` to `jenkins_agent.cpu_cores` from `~/.config/mac-k3d/worker.yaml`, the count Jenkins locks on this node, unless `CPU_LOCK_QTY` is already set; with no worker.yaml it falls back to the loaded config, then the logical CPU count. See [optimization.md](optimization.md#how-many-fit) |
| `--job one\|some\|full` | Queue `<benchmark>_one_task`, `_some_task` or `_full_suite_task` with only the fields given ([above](#queue-a-jenkins-job-from-the-cli)). Not with `--local`, `--stage` or `--icode-release` |
| `--tasks a,b,c` | With `--job some`: the question list (`TASKS`); wins over `--n-tasks` |
| `--n-rollouts N` | With `--job some` or `--job full`: attempts per question. `--job one` is always one rollout |
| `--param NAME=VALUE` | With `--job`, repeatable: any other field of the job page, for example `CANARY=on` or `AGENT_LABEL=<node name>` |
| `--dry-run` | With `--job`: print the job and the fields it would send; queue nothing and contact no controller |
| `--n-tasks N` | Number of tasks when `--task` is empty (default 1 locally). With `--job some` or `full`: `N_TASKS` |
| `--benchmark deepswe\|lolbench\|swebenchpro` | Suite (default `deepswe`; Jenkins job follows this) |
| `--task ID` | One question id (empty DeepSWE/SWE-bench Pro = first alphabetical; LoLBench example `ruff_1`) |
| `--icode-mode release\|git` | `release` (`*-full-*` drop; `binary` is an alias) or `git` (clone URL + ref); default `release` locally. With `--job`, `release` is refused on every shape: the CLI cannot attach a file, so upload `ICODE_RELEASE_FILE` on the job page. See [icode-harness-inputs.md](icode-harness-inputs.md) |
| `--icode-release PATH\|URL` | `release` mode for `--local` / `--stage`; empty = persist file then `~/.local/share/mac-k3d/icode-*-full-*` or `icode`. Jenkins release uses UI upload `ICODE_RELEASE_FILE` (the CLI does not attach a file) |
| `--icode-git-url URL` | `git` mode: `https://` on github.com or gitcode.com (no tokens in the URL) |
| `--icode-git-ref REF` | `git` mode: branch name, tag, commit SHA, or pull-request number when kind is `pr`. Default `main` |
| `--icode-git-ref-kind KIND` | `branch` (default), `tag`, `commit` (PR SHA), or `pr` (pull-request number). Leftover `auto` still maps: 7–40 hex → commit, else branch |
| `--workdir PATH` | Eval workdir (default `./eval-runs`) |
| `--model ID` | DeepSeek Chat Completions id. Catalog: `deepseek-flash` (default) or `deepseek-v4-pro`. Env `DEEPSEEK_MODEL`. TTY Select when flags are omitted. iCode is called with `ICODE_PROVIDER=DeepSeek`, `ICODE_REASONING_EFFORT=high`, `ICODE_API_BASE=https://api.deepseek.com/v1`, `ICODE_MAX_TOKENS=65536` and `ICODE_MAX_ITERATIONS=500`; the HTTP model id stays this catalog id |
| `--yes` | Skip prompts. Without `--local` or `--stage` it queues `<benchmark>_one_task` exactly as `--job one` does. Also skips the git PAT prompt |

Private `ICODE_MODE=git` clones: on a TTY, `eval` asks for a GitCode or GitHub PAT (hidden input), writes `GITCODE_TOKEN` / `GITHUB_TOKEN` to gitignored `.env` (mode 600), and never prints it. Jenkins uses controller credentials `gitcode-pat` / `github-pat` — add them with `mac-k3d config --update-secrets` on the controller. Do not put a PAT in the URL or job parameters.

v1 harness=`icode`, llm=`deepseek`. Catalog models: `deepseek-flash` (default) and `deepseek-v4-pro`. Benchmark is `deepswe`, `lolbench`, or `swebenchpro`. Output: `eval-runs/output/<benchmark>/jenkins-<build>-<UTC>/artifact.json`, with `summary.md` and `report.html` in that folder, and a backup at `output/<benchmark>/<same run folder>.tar.gz` under the pipeline root (`$WORKSPACE/mac-k3d-pipeline` on Jenkins, the checkout locally). Field glossary: [evaluation.md](evaluation.md). Slot packing: [optimization.md](optimization.md).

There is no `--sync-pipeline`. A Jenkins build runs the `pipeline/` embedded in its worker's installed binary (see [`pipeline`](#pipeline)), so the pipeline under test is always the commit that binary was built from — see the development loop in [workflow.md](workflow.md#development-loop-commit-push-redeploy). `--stage` and `--local` still run the working tree directly, which is the fast path for a fixture-level change.

### Jenkins job parameters

Every job starts with `HARNESS`, `LLM` and `BENCHMARK`. Each is a one-option choice fixed by the job, so the page and every build name (`#12 icode/deepseek-v4-pro/deepswe`) say what is being evaluated. Change harness or LLM with `mac-k3d set --harness/--llm` on the controller, then `config --skip-secrets`; change the benchmark by running another suite's job.

What else a job shows depends on its shape and on `jenkins_job.ui_profile` in the controller's config (`mac-k3d set --ui-profile user|developer`, default `user`). The developer page is the user page, field for field and with the same defaults, followed by extra testing fields; a test on the developer page is therefore a test of what a user gets. Each extra field's help text starts with `Developer (testing):`, so the page shows which ones a user never sees. A unit test (`developer_page_is_the_user_page_plus_extras`) keeps it that way.

**Shown in both profiles:**

| Job | Parameters |
|---|---|
| `<suite>_one_task` | `TASK`, `N_ROLLOUTS` (default 1), `ICODE_MODE`, `ICODE_RELEASE_FILE`, `ICODE_GIT_URL`, `ICODE_GIT_REF`, `ICODE_GIT_REF_KIND`, `DEEPSEEK_MODEL` |
| `<suite>_some_task` | `TASKS` (a list; wins if set), `N_TASKS` (first N sorted when `TASKS` is empty), `N_ROLLOUTS` (default 4), `ICODE_MODE`, `ICODE_RELEASE_FILE`, the three `ICODE_GIT_*`, `DEEPSEEK_MODEL` |
| `<suite>_full_suite_task` | `N_ROLLOUTS` (default 4), `ICODE_MODE`, `ICODE_RELEASE_FILE`, the three `ICODE_GIT_*`, `DEEPSEEK_MODEL` |

**Developer profile only.** In `user` profile these are hidden parameters (Hidden Parameter plugin) that keep their defaults:

| Parameter | Default | Meaning |
|---|---|---|
| `AGENT_LABEL` | `lolbench` | label a worker must carry (every worker has `lolbench`). On a dispatcher, a label no online worker carries fails the build at once, naming the online workers |
| `AGGREGATE_LABEL` | empty | `some_task` / `full_suite_task`: label of the worker that runs the `Aggregate` stage (merge, cost report, combined archive). Empty means `AGENT_LABEL`. Shards ignore it |
| `HARBOR_VERSION`, `DEEPSWE_REF`, `LOLBENCH_REF` | pinned | `env` (Harbor, default from `pipeline/config/toolchain.env`) and `tasks` (benchmark) pins. The iCode SHA is not a field: the build prints and records the commit or release sha256 it actually ran |
| `MAX_IN_FLIGHT` | `0` | `some_task` / `full_suite_task`: how many `one_task` builds may sit in Jenkins at once. `0` means twice the online executors that can run the suite |
| `OOM_RETRIES` | `2` | How many times an out-of-memory rollout is queued again. `2` means three tries in all |
| `N_TASKS` | suite size | `full_suite_task` only, hidden unless the profile is `developer`: lower it for a rehearsal |
| `TEST_MEMORY_MB` | empty | testing only: Harbor `--override-memory-mb` for a trial, to force an out of memory. Empty leaves the `task.toml` limit. The dispatcher forwards it to every `one_task` build |
| `TEST_MEMORY_ATTEMPTS` | `1` | testing only: `TEST_MEMORY_MB` applies while `ATTEMPT` is at most this number. `1` limits the first try; a re-queued try runs as declared |

**Always hidden** on `one_task`, because the dispatcher sets them on a shard build: `TASKS`, `N_TASKS`, `TASK_OFFSET`, `RUN_GROUP`, `SHARD`, `ICODE_RELEASE_FROM`, `ICODE_RELEASE_SHA256`. A hidden parameter still accepts a value from `build job:` or `buildWithParameters`, so a developer can override one in `user` profile through the API. `mac-k3d eval --job … --param OFFICIAL=1` (and `CANARY`, `CANARY_ALLOW_HOST`, `ICODE_EXPECT_SHA`) is refused: those fields are gone.

There is no `CPU_LOCK_QTY`, `SHARD_SIZE`, `SHARDS` or `RESUME` parameter. Executors are `cpu_cores / cpus_per_executor` (default 2). Evaluate locks the CPUs that question declares, and Harbor runs `-n 1`. There is no pipeline URL or ref parameter either: the pipeline is the worker binary's, and `scripts/redeploy.sh` is how a commit reaches it.

### `eval record`

Write one row per question of a finished run into [testing/question-log.md](testing/question-log.md), and mark the question `run` in [testing/question-coverage.md](testing/question-coverage.md). Run it from your mac-k3d checkout, since those docs live there.

```bash
mac-k3d eval record --job deepswe_one_task --build 54   # a Jenkins build, any worker
mac-k3d eval record                                       # the last local run
mac-k3d eval record --run eval-runs/output/deepswe/<run folder>
```

| Flag | Description |
|------|-------------|
| `--job JOB --build N` | Reads the build's result, node, start time, `consoleText` and archived `artifact.json` through the Jenkins API. The URL and API user/token come from `jenkins_agent` in the loaded config, else from `~/.config/mac-k3d/worker.yaml`; curl gets the credentials on stdin, never in its arguments or the URL. A build that is still running is refused |
| `--run DIR` | One local output folder (`artifact.json` inside) |
| `--workdir PATH` | Where the last local run left `last_output.txt` and `last_console.log` (default `./eval-runs`) |

An `artifact.json` from another run (a reused Jenkins workspace) is ignored. A `one_task` report belongs to the build when its `run_id` is `jenkins-<build>`. A `some_task` or `full_suite_task` merge belongs to the dispatcher build when its `run_id` is `<job>-<build>` (for example `deepswe_some_task-9`); that one command writes a row for every question in the merged report. A run that stopped before `report` still gets rows, from the console: the selected questions, the last `== phase/step` and the first `ERROR:` line, masked for keys and tokens. `--local` and `--stage all` copy their console to `eval-runs/last_console.log` and record themselves when they end, passed or failed. Re-recording a run keeps the `fix` column.

---

## `pipeline`

Extract the `pipeline/` this binary was built with. Every Jenkins eval build runs it in its first stage (Environment); no config file is read.

```bash
mac-k3d pipeline --extract-to "$WORKSPACE/mac-k3d-pipeline"
mac-k3d pipeline --extract-to "$WORKSPACE/mac-k3d-pipeline" --require-clean   # every Jenkins build
mac-k3d --version     # mac-k3d 0.5.3 (<commit>[, dirty])
```

| Flag | Description |
|------|-------------|
| `--extract-to DIR` | Write `DIR/pipeline/` plus `DIR/pipeline/BUILD.json` (`version`, `commit`, `dirty`, `pipeline_hash`, `binary`), then print `mac-k3d pipeline <commit> (mac-k3d <version>, <binary>)` |
| `--require-clean` | Exit non-zero, before writing anything, when the binary was built with uncommitted changes in `src/` or `pipeline/`, or outside a git checkout |

`provenance.py` copies `BUILD.json` into `artifact.json` as `eval_protocol.pipeline` with `source: binary`. A local run from a checkout has no `BUILD.json` and records `source: checkout` with the commit and dirty flag from git.

---

## `prepare`

Verify prerequisites and generate configuration via an interactive wizard.

```bash
mac-k3d prepare                    # wizard if no config; else prompt (see below)
mac-k3d prepare -i                 # force interactive wizard (overwrite)
mac-k3d prepare --init-config      # write defaults, no prompts
mac-k3d prepare --non-interactive  # validate only
mac-k3d prepare --disk-min-gb 20   # override role disk minimum (labs only)
# Same Mac: worker config alongside a controller (see below)
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
```

See [prepare-wizard.md](prepare-wizard.md) for the full questionnaire design.

### Flags

| Flag | Description |
|------|-------------|
| `-i, --interactive` | Run interactive wizard to generate config |
| `--init-config` | Write default `config.yaml` if missing (no wizard) |
| `--non-interactive` | Validate existing config only; no prompts or writes |
| `--disk-min-gb N` | Override minimum free disk (GB); `0` in config means role default |

Global `-c / --config` is honored: prepare reads and writes that path (not only the default).

### Single-Mac controller + worker test

To exercise **worker prepare** while Jenkins already runs on this Mac:

1. Prepare/start **controller** with the default config (`role: controller` → `mac-k3d start`).
2. Finish Jenkins UI setup; create an API token (for auto-registration) or leave token empty for manual node create.
3. Prepare **worker** into a separate file:

```bash
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
# Role: CI worker
# Jenkins URL: wizard default http://43.107.42.252:17070; same-PC: type http://localhost:17070
# API user: Enter to skip; worker.yaml keeps api_user: '' and api_token: ''
```

4. `mac-k3d config -c ~/.config/mac-k3d/worker.yaml`, then confirm the node appears on the controller.
5. Do **not** `clean --purge-config` the default controller config while testing the worker file.

`mac-k3d start -c ~/.config/mac-k3d/worker.yaml` is rejected: workers only need the host agent, Docker, Java, git and the pinned Harbor. Job parameters: [lolbench-jenkins.md](lolbench-jenkins.md).

### Behavior

If the config file already exists and stdin is a TTY, `prepare` (without `-i`) prompts: **Use existing config (finish pending installs, then validate)**, **Re-run wizard (overwrite config)**, or **Cancel**. Use `prepare -i` to skip that menu and always overwrite.

1. Assert macOS or Linux.
2. **Storage**: scan volumes, default to the one with most free space, prompt for base directory under that volume.
3. **Role**: standalone / controller / worker; set `jenkins.enabled` for controller.
4. **Tools for that role only**: worker → Docker, Java, git; controller → Docker, k3d, kubectl, helm; standalone → Docker, k3d, kubectl, and Harbor + git when it will run `eval --local`. Harbor is not a question: it is installed at `HARBOR_VERSION` from `pipeline/config/toolchain.env` with `uv` (no root). Nor is a worker's Java: one at or above `JAVA_MAJOR` (the controller image's Java) is used, otherwise it is installed ([dependencies.md](dependencies.md)).
5. **Worker agent block**: Jenkins URL, API user and token (Enter skips both; the YAML keeps `api_user: ''` / `api_token: ''`), agent name (default `{os}-<hostname>`), labels, remote root. **Controller**: cluster, job defaults, CI secrets to the pending file.
6. **Save** the YAML, then **apply**: storage directories, installs (root steps that cannot run here are printed as one block), worker host settings.
7. **Disk check**: fail if free space on storage volume is below role minimum (standalone 40 GB, controller 60 GB, worker 40 GB). RAM preflight is 8 GB.
8. Validate. Agent registration and the `<agent>-core-1..N` lockable resources are done by `config`, not here.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed / config written |
| 1 | Missing dependency, invalid config, or user cancelled |

---

## `start`

Start Docker Desktop (if needed), create or start the k3d cluster, optionally deploy Jenkins.

**Worker YAML is rejected** (`role: worker`): use `mac-k3d config` / `setup` instead. Workers are not a k3d cluster.

```bash
mac-k3d start [--jenkins <skip|in-cluster>] [--no-wait-docker] [--skip-job]
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--jenkins` | (config) | Override Jenkins mode for this run (`skip` or `in-cluster`) |
| `--no-wait-docker` | false | Skip Docker Desktop readiness wait |
| `--skip-job` | false | Skip creating Pipeline job `lolbench_one_task` after Jenkins install |

### Behavior

1. Open Docker Desktop if not running (`open -a Docker`).
2. Poll `docker info` until ready or timeout (`docker.startup_timeout_secs`).
3. If cluster missing: `k3d cluster create` with port mappings from config.
4. If cluster exists but stopped: `k3d cluster start`.
5. If Jenkins is enabled (config or `--jenkins in-cluster`): Helm install/upgrade Jenkins with `additionalPlugins` (`lockable-resources`, `plain-credentials`, `file-parameters`, `copyartifact`, `pipeline-utility-steps`, `hidden-parameter`), then create/update the nine eval jobs (and delete a leftover `eval_aggregate`) unless `--skip-job`. That rewrite uses `jenkins.remote_trigger_token` from `config.yaml`, generating and saving one the first time, so "Trigger builds remotely" stays ticked.
6. Write state file under `~/.local/state/mac-k3d/`.

`--jenkins` overrides `jenkins.enabled` for this invocation only. If omitted, the config file value is used.

### Idempotency

- Second `start` on a running cluster is a no-op aside from Helm upgrade when Jenkins is enabled.
- Job create/update refreshes the nine eval jobs on each `start`/`config`, rendered for the current `ui_profile`. Plugin list changes require **controller** `start` (Helm), not `config`.

---

## `config`

Apply post-start configuration: kubeconfig merge, context selection, service URLs.

```bash
mac-k3d config [--no-merge-kubeconfig] [--show-jenkins] [--skip-agent] [--skip-job] [--skip-secrets] [--update-secrets]
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--no-merge-kubeconfig` | false | Skip merging k3d kubeconfig into `~/.kube/config` |
| `--show-jenkins` | false | Print Jenkins URL and admin password |
| `--skip-agent` | false | Worker: skip Jenkins agent register / launch-script update |
| `--skip-job` | false | Skip creating Pipeline job `lolbench_one_task` when Jenkins is enabled |
| `--skip-secrets` | false | Skip creating/updating Jenkins Credentials from pending secrets; still lists existing IDs so job XML keeps `withCredentials` binds |
| `--update-secrets` | false | Re-prompt for CI secrets even if credentials already exist |

### Behavior

1. If the named k3d cluster exists: merge kubeconfig, select context, wait for API.
2. Worker without a local cluster: skip kubeconfig (agent-only is OK).
3. If Jenkins enabled or `--show-jenkins`: print URL and admin password from the cluster secret.
4. **Controller / Jenkins enabled:** upload pending CI secrets into Jenkins Credentials (see [secrets.md](secrets.md)); create/update the nine eval jobs with `ICODE_MODE` (`release` / `git`), `ICODE_RELEASE_FILE` (upload), `ICODE_GIT_URL`, `ICODE_GIT_REF`, `ICODE_GIT_REF_KIND`, `TASK`. Parameter defaults come from `jenkins_job.*`. See [lolbench-jenkins.md](lolbench-jenkins.md) and [icode-harness-inputs.md](icode-harness-inputs.md). The same rewrite sets `<authToken>` from `jenkins.remote_trigger_token`, which ticks **Trigger builds remotely** on every job. The first time that field is empty, `config` generates a token, saves it in `config.yaml` (mode 600), and prints one example URL (`…/job/deepswe_one_task/buildWithParameters?token=…`). Later `config` and `start` reuse the saved token and do not print it again. `mac-k3d eval` still queues with the API user; the token is only for that remote URL.
5. **Worker:** warn when the installed Harbor is not `HARBOR_VERSION` or git is missing; using `jenkins_agent.api_user` / `api_token` from config, create/update the Jenkins node (executors = `cpu_cores / cpus_per_executor`, default 2), write the node's connection secret to `{remote_fs}/.agent-secret` (mode 600) and rewrite `launch-agent.sh` (mode 700), which passes it as `-secret @<file>` so it never appears in the agent's arguments (an older script's inline secret moves to the file), create the `<agent>-core-1..N` resources (labelled with the shared `CPU_CORES` label and the agent name, N = `jenkins_agent.cpu_cores`) and delete any `<agent>-core-M` above N that no build holds, and **start the agent daemon** (systemd user unit `mac-k3d-jenkins-agent.service` on Linux, LaunchAgent `com.mac-k3d.jenkins-agent` on macOS) unless `--skip-agent`. The Linux unit sets `OOMScoreAdjust=-1000` so the host OOM killer skips the agent. A blank `jenkins_agent.name` becomes `{os}-<hostname>` (`linux` or `macos`, then the short hostname). A saved name that is exactly the old default `mac-<hostname>` is renamed the same way: the old node is deleted and the new one registered, the old node and its `<old>-core-N` locks are removed, the new name is written into the YAML, and the agent starts under the new node. Any other saved name is left alone. With blank keys it prints which keys to fill in and the command to re-run. `config` is the only command that registers an agent.
6. Every role: extract `~/.local/share/mac-k3d/pipeline` for `eval --local` and manual runs (does not overwrite `icode`). Builds extract their own copy.

`config` first sets the YAML it loaded to mode 600, since it holds `jenkins_agent.api_token`; every YAML mac-k3d writes is 600 too. Its Jenkins REST calls give curl the API token, the CSRF crumb and any Groovy script on stdin, never as arguments ([secrets.md](secrets.md#never-in-process-arguments)).

A `-c` path that does not exist fails at once with `no config at <path>; run mac-k3d setup -c <path> first` (`start`, `config`, `eval`, `teardown`, `clean`, `status`). `setup` and `prepare` create the file instead.

`agent.jar` is downloaded beside the running one and swapped in by rename only when its bytes differ. A running agent is restarted only when `agent.jar`, `launch-agent.sh`, `.agent-secret` or the unit/plist changed; otherwise `config` prints `Jenkins agent unchanged, left running` and a build on that worker keeps its connection. The daemon survives closing the terminal and restarts if the Java process exits. Logs: `{remote_fs}/jenkins-agent.stdout.log`.

---

## `export`

Write a **sanitized** copy of this machine’s config YAML (controller `config.yaml` or worker `worker.yaml`). Host paths are stripped, `jenkins_agent.api_token` is blanked (a worker file keeps `api_token: ''` so the slot stays visible), and `jenkins.remote_trigger_token` is omitted. `credentials.pending.yaml` is never read or copied.

```bash
mac-k3d export -o /tmp/controller.yaml
mac-k3d export -c ~/.config/mac-k3d/worker.yaml -o /tmp/worker.yaml
```

### Flags

| Flag | Description |
|------|-------------|
| `-o, --output <PATH>` | Destination YAML |

Global `-c` selects the **source** file (same resolve as other commands). Refuses to write `-o` onto the source path.

Kept: `role`, cluster/Jenkins ports, `jenkins_job.*`, Harbor `source`, labels, `controller_url`, `api_user`. Dropped: token, storage paths, tool `binary`/`app`, `lolbench.path`, `platform`, `cpu_cores`, `remote_fs`.

How to change DeepSWE TASK (use `mac-k3d set`, not sed) and queue Jenkins, and why `config --skip-secrets` does not mean the YAML has keys: [export-import.md](export-import.md).

---

## `import`

Copy a sanitized YAML onto this machine. **Write only** — does not install Docker, start k3d, or register the agent.

```bash
mac-k3d import /tmp/controller.yaml
mac-k3d import /tmp/worker.yaml -c ~/.config/mac-k3d/worker.yaml
mac-k3d import /tmp/worker.yaml --force
```

### Flags

| Flag | Description |
|------|-------------|
| `--force` | Overwrite the destination if it already exists |

Positional argument is the incoming file. Global `-c` is the **dest**; if omitted, `role: worker` → `~/.config/mac-k3d/worker.yaml`, otherwise `config.yaml`. Import re-sanitizes (so a hand-copied live `worker.yaml` still gets `api_token: ''`), sets `platform` to this OS, and prints `setup` / `config` next steps.

`--skip-secrets` after a controller import refreshes job XML only; it does not mean the imported file contained credentials. See [export-import.md](export-import.md).

---

## `set`

Edit **non-secret** `jenkins_job` fields on a YAML file (harness, LLM family, DeepSeek model, benchmark, questions). Also persist the worker iCode **release drop** path with `--icode-release` (`~/.config/mac-k3d/icode-paths.yaml`; not the YAML you pass with `-c`). Writes YAML only for job fields — does not upload Jenkins credentials or queue an eval. Allowed values come from the catalog (`src/eval_catalog.rs`); unknown ids are rejected with `allowed: …`.

```bash
mac-k3d set --list
mac-k3d set -c /tmp/imported-config.yaml --harness icode --llm deepseek --benchmark deepswe --task abs-stepped-slices
mac-k3d set -c ~/.config/mac-k3d/config.yaml --model deepseek-flash
mac-k3d set --check-models
mac-k3d set -c ~/.config/mac-k3d/config.yaml --benchmark deepswe --n-tasks 2
mac-k3d set --icode-release /path/to/icode-linux-x86_64-full-v0.1.41
mac-k3d set --ui-profile developer   # controller, while developing; `user` for handover
```

TTY with no flags: select harness / LLM family / DeepSeek model / benchmark, then question mode. Non-TTY requires flags (or `--check-models` / `--list`).

### Flags

| Flag | Description |
|------|-------------|
| `--list` | Print allowed harness / LLM / model / benchmark values and exit |
| `--harness <ID>` | Catalog harness (v1: `icode`) |
| `--llm <ID>` | Catalog LLM family (v1: `deepseek`) |
| `--model <ID>` | DeepSeek Chat Completions id (`deepseek-flash` default, or `deepseek-v4-pro`) |
| `--check-models` | Optional live `GET /models` against `.env` key; fail if YAML/`--model` id is not a provider id |
| `--benchmark <ID>` | `deepswe`, `lolbench`, or `swebenchpro` (which job receives TASK / N_TASKS / TASKS defaults) |
| `--task <ID>` | One question id |
| `--n-tasks N` | First N sorted questions (clears TASK / TASKS). `N>1` is slower and costs more LLM calls |
| `--tasks a,b` | Explicit comma-separated ids |
| `--icode-release PATH` | Worker `*-full-*` drop; writes `~/.config/mac-k3d/icode-paths.yaml` |
| `--ui-profile user\|developer` | Jenkins job pages: `user` shows each shape's short list; `developer` shows the same list followed by `AGENT_LABEL`, `AGGREGATE_LABEL` (dispatchers), the pins, canary, `MAX_IN_FLIGHT`, `OOM_RETRIES`, `TEST_MEMORY_MB` and `TEST_MEMORY_ATTEMPTS`. Apply with `config --skip-secrets` on the controller |
| `--max-in-flight N` | Builds one dispatcher may have in Jenkins at once. `0` means twice the online executors for the suite |
| `--oom-retries N` | How many times an OOM rollout is queued again (default 2) |

`--task`, `--n-tasks`, and `--tasks` are mutually exclusive. `set` updates only the flags you pass, then `save`.

`--check-models` is optional and does **not** rewrite Chat Completions. It `GET`s `https://api.deepseek.com/models` (a trailing `/v1` on `ICODE_API_BASE` is stripped; OpenAI list JSON, `data[].id`) using `DEEPSEEK_API_KEY` from the environment or `.env`. Fail if the YAML / `--model` id is missing from that list. The catalog id is not `openai/deepseek-flash`; that prefix is only the artifact label. Cloud `set` on YAML often has **no** key — run this on a **worker / local** machine with `.env`, not as a hard requirement of controller `set`. The `env` phase does the same check when the key is present (before the paid `evaluate` phase). A full eval does not run the LLM-only baseline.

```bash
# this PC / worker (has .env)
mac-k3d set -c ~/.config/mac-k3d/worker.yaml --check-models
mac-k3d set -c /tmp/controller-lab.yaml --model deepseek-flash --check-models
```

To add a model later: copy an id from `GET /models` (or DeepSeek “Models & Pricing”), append that **exact** string to `MODELS` in `src/eval_catalog.rs`, rebuild, then `set --model <id>` and controller `config --skip-secrets`. Never use marketing / product names (`deepseek-v4.1-flash` is not an API id).

Push the YAML into Jenkins job XML on a live controller (still **no** secrets in the file):

```bash
mac-k3d config -c ~/.config/mac-k3d/config.yaml --skip-secrets
```

Do not use `sed` on `default_task`. See [export-import.md](export-import.md).

---

## `teardown`

Stop the cluster and services without deleting data.

```bash
mac-k3d teardown [--stop-docker] [--deregister-agent]
```

### Flags

| Flag | Description |
|------|-------------|
| `--stop-docker` | Quit Docker Desktop after stopping cluster |
| `--deregister-agent` | Worker: also delete Jenkins node + CPU_CORES lockable resources |

### Behavior

1. `k3d cluster stop <name>` if running.
2. Optionally `osascript` quit Docker Desktop.
3. Leave config and state files intact.
4. **Worker:** unload LaunchAgent (stop agent process). With `--deregister-agent`, also delete the Jenkins node and locks.
---

## `clean`

Remove cluster, volumes, and local artifacts.

```bash
mac-k3d clean [--purge-config] [-y|--yes]
mac-k3d clean -c ~/.config/mac-k3d/worker.yaml --purge-config --yes
```

### Flags

| Flag | Description |
|------|-------------|
| `--purge-config` | Default config: remove `~/.config/mac-k3d/`. With `-c FILE`: remove **only that file** |
| `-y, --yes` | Skip confirmation (required to perform deletion) |

### Behavior

Without `--yes`: print warning and exit 0.

With `--yes`:

1. **Worker:** stop LaunchAgent; deregister Jenkins agent node and delete `{agent}-core-*` Lockable Resources (needs `api_token` in config).
2. `k3d cluster delete <name>` from the loaded config.
3. Without `-c`: remove `~/.local/state/mac-k3d/`. With `-c`: leave shared state intact.
4. If `--purge-config`: remove the `-c` file only, or the whole config directory when using the default path.

---

## `status`

Report current environment state (read-only).

```bash
mac-k3d status
```

### Output (example)

```text
Docker Desktop:  running
k3d cluster:     mac-k3d (running, 1 server, 0 agents)
kubectl context: k3d-mac-k3d
Jenkins:         configured, pod Running, http://localhost:17070
```

After `clean` / while the cluster is gone, Jenkins still shows as **configured** from the YAML (so the next `start` will redeploy it), not as live:

```text
k3d cluster:     ci-controller (missing, 0 server, 0 agents)
Jenkins:         configured, not running (cluster missing), http://localhost:17070
```

Pod lookup uses `--context k3d-<cluster>` from the loaded config, not whatever the shell’s current kubectl context happens to be.
---

## Typical workflows

### Minimal (no Jenkins)

```bash
mac-k3d prepare --init-config
mac-k3d start
mac-k3d config
kubectl get nodes
mac-k3d teardown   # end of day
```

### With Jenkins

```bash
mac-k3d prepare --init-config
mac-k3d start --jenkins in-cluster
mac-k3d config --show-jenkins
# open http://localhost:17070
mac-k3d clean --yes
```
