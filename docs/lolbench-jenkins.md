# LoLBench Jenkins Job Design

## Job: `lolbench_one_task`

A parameterized Jenkins job that evaluates **exactly one** LoLBench task per build on a **worker Mac**. The controller only queues and reports; Harbor/Docker run on the agent host.

LoLBench is a Harbor + Docker benchmark. It does **not** run as pods in k3d. See [deployment.md](deployment.md) for controller/worker topology.

To run many tasks, trigger `lolbench_one_task` once per task (or add a separate wrapper job later).

---

## Goals

1. One Jenkins job (`lolbench_one_task`) a human (or API) can trigger with **task**, **harness** (Harbor agent), and **model**.
2. The controller **dispatches** to a capable Jenkins agent (worker Mac with Docker + Harbor).
3. Each build isolates Harbor output with a unique **`--jobs-dir`** / **`--job-name`** (includes `BUILD_NUMBER`).
4. The build **fails or succeeds** from the LoLBench reward, with artifacts and a readable summary.

## Non-goals (v1)

- Orchestrating Harbor inside k3d (`--env gke` is not k3d).
- A multi-task matrix in one build (use multiple `lolbench_one_task` builds or a wrapper job later).
- Auto-enrolling workers or provisioning API keys.

---

## Topology

```text
  User / API
       |
       v
  Mac A  Jenkins controller (in k3d)
       |  job: lolbench_one_task (parameterized Pipeline)
       |  queue + credentials + UI
       v
  Mac B/C  Jenkins agent  label: lolbench
       |  Docker Desktop + harbor CLI + LoLBench checkout
       |  lockable resource: CPU_CORES (quantity = logical cores on that Mac)
       v
  harbor run  (docker containers on the worker)
       |
       v
  reward.json  -> Jenkins status, artifacts, description
```

The agent process is a **Jenkins inbound/SSH agent** on the worker Mac. It is not a k3d node and not a Kubernetes Job.

---

## Job parameters

| Parameter | Type | Example | Maps to |
|-----------|------|---------|---------|
| `TASK` | Choice | `ruff_1` | `harbor_tasks/<id>` / `scripts/run_task.sh` arg 1 |
| `HARNESS` | Choice | `opencode` | Harbor `-a` / script arg 2 |
| `MODEL` | String | `openrouter/deepseek/deepseek-v4-pro` | Harbor `-m` / script arg 3 |
| `SUITE` | Choice | `union` | `LOLBENCH_SUITE` / script arg 4 |
| `AGENT_LABEL` | String | `lolbench` | Jenkins `agent { label }` |
| `MAX_RETRIES` | String | `2` | Harbor `--max-retries` |
| `ALLOW_AGENT_HOST` | String | *(empty)* | Harbor `--allow-agent-host` if set |

### Suggested `TASK` choices

`cpython_1` … `cpython_12` (skip missing ids), `fastapi_1`, `flink_1`, `flink_7`, `flink_9`, `flink_10`, `flink_11`, `kafka_1`, `kafka_2`, `ruff_1`.

Add `oracle` as a harness value for gold-parity checks (then `MODEL` is ignored; pass `-`).

### Suggested `HARNESS` choices

`opencode`, `codex`, `claude-code`, `aider`, `mini-swe-agent`, `terminus`, `oracle`, `nop`.

`oracle` / `nop` must not pass `-m`.

### `MODEL`

Keep as **string**, not a closed choice: model IDs change often. Document the OpenRouter form `openrouter/<org>/<model>`.

---

## Agent selection (dispatch)

Workers that can run LoLBench get **capability labels**, not per-Mac hardcoding in the job:

| Label | Meaning |
|-------|---------|
| `macos` | Jenkins agent on macOS |
| `docker` | Docker Desktop available |
| `lolbench` | Harbor installed, LoLBench repo path or SCM checkout works, enough disk |

Pipeline:

```groovy
agent { label params.AGENT_LABEL ?: 'lolbench' }
```

Do **not** pin the job to the controller Mac. Jenkins + a 8 GB Harbor container on the same host will starve each other.

If several workers match `lolbench`, Jenkins picks an idle executor. Fine-grained placement can use extra labels (`lolbench && mac-b`).

---

## Filesystem isolation (`--jobs-dir`)

Every `lolbench_one_task` build **must** use a unique Harbor output tree. Do **not** call stock `scripts/run_task.sh` as-is: it hardcodes `--jobs-dir harbor_runs/$TASK` and `--job-name ${TASK}_${HARNESS}_${SUITE}`, which collide across concurrent (or even sequential retry) builds.

### Required layout

```text
harbor_runs/jenkins-${BUILD_NUMBER}/${TASK}/
  └── ${TASK}_${HARNESS}_${SUITE}_${BUILD_NUMBER}/   # Harbor job-name directory
        └── …/verifier/reward.json
```

Invoke Harbor directly (or a thin wrapper) with:

```bash
JOBS_DIR="harbor_runs/jenkins-${BUILD_NUMBER}/${TASK}"
JOB_NAME="${TASK}_${HARNESS}_${SUITE}_${BUILD_NUMBER}"

harbor run \
  -p "harbor_tasks/${TASK}" \
  -a "${HARNESS}" \
  # -m … when not oracle/nop \
  --job-name "${JOB_NAME}" \
  --jobs-dir "${JOBS_DIR}" \
  --no-delete -n 1 -y \
  --ve "LOLBENCH_SUITE=${SUITE}"
```

| Piece | Why |
|-------|-----|
| `BUILD_NUMBER` in `--jobs-dir` | Isolates this Jenkins build from all others on the Mac |
| `TASK` under that dir | Keeps artifacts easy to browse |
| `BUILD_NUMBER` in `--job-name` | Avoids Harbor reusing/caching a previous same-named job under the same dir |
| Unique Jenkins `ws(...)` (recommended) | Avoids SCM checkout races if multiple builds share an agent |

Reward lookup and archives always use `${JOBS_DIR}`, never a shared `harbor_runs/${TASK}` root.

---

## Back pressure (capacity)

Each task is sized like a **large** job: **4 CPUs, 8 GB RAM, ~20 GB disk**, up to **6 hours** agent timeout.

Use the slot model from [deployment.md](deployment.md):

| Resource label | Quantity model | Used by |
|----------------|----------------|---------|
| `CPU_CORES` | capacity = host logical CPUs (from prepare) | `lolbench_one_task` via `lock(label: 'CPU_CORES', quantity: N)` |

Prefer `CPU_CORES` over a single opaque `lolbench-large` slot so jobs can request a core budget.

With `--jobs-dir` isolation, you may raise per-Mac lock quantity (e.g. 2) when RAM allows two × ~8 GB containers. Start at **1**; increase only after measuring host pressure.

Do not run this job as `macb-small`.

---

## Concurrency on one Mac (conflicts)

### Environment variables

Usually **safe**. Jenkins injects credentials/env **per build process**. One build’s `OPENROUTER_API_KEY` / `TASK` / `MODEL` do not overwrite another build’s shell environment.

Caveats:

- A shared checked-in `.env` under the LoLBench repo is sourced only if you use a wrapper that loads it — still process-local.
- Do **not** write global shell profile or Docker Desktop settings from the job.

### Filesystem

Without isolation, stock paths collide:

| Collision | Risk |
|-----------|------|
| Same `TASK` + harness + suite | Shared `harbor_runs/<task>/…` overwrites |
| Same `TASK`, different models | Model is not in stock job name |
| Shared Jenkins `$WORKSPACE` | SCM checkout races |

**Mitigation (required):** per-build `--jobs-dir` / `--job-name` as above. Docker image layers may still be shared (good); containers still compete for CPU/RAM.

### Parallel builds on one Mac

Safe only when:

1. Each build uses unique `--jobs-dir` / `--job-name` (always).
2. Lock quantity / executors match capacity (do not oversubscribe RAM).
3. Prefer a unique workspace per build when executors > 1.

---

## Parallelism design (Harbor vs Jenkins)

Two workable approaches. **Harbor already provides the knobs you need** — no Harbor source fork.

### Harbor already has

| Flag | Role |
|------|------|
| `--jobs-dir` / `-j` | Where Harbor writes results (isolate runs here) |
| `--job-name` | Subdirectory / run identity under `jobs-dir` |
| `-p` / `--path` | Task dir **or** parent of many tasks |
| `-n` / `--n-concurrent` | Max trials in flight **inside one** `harbor run` |
| task filters (e.g. include names) | Subset of a path (Harbor version–dependent) |

LoLBench docs: Harbor’s `-n` is concurrency **within one job**. Cross-task parallel in `scripts/run_all.sh` is **outside** Harbor (`xargs -P` launching many `harbor run -n 1`).

Harbor **does** queue: if you pass 20 tasks and `-n 2`, it runs 2 Docker trials at a time and starts the next when a slot frees. You do not have to hand Harbor only as many tasks as the Mac can run at once.

### Option A — Harbor owns parallelism (`lolbench_tasks`)

One Jenkins build per Mac batch:

```text
lolbench_tasks(TASKS=ruff_1,fastapi_1,..., MAX_CONCURRENT=2)
  → one harbor run -p harbor_tasks  (or filtered list)
       -n 2
       --jobs-dir harbor_runs/jenkins-${BUILD_NUMBER}
```

| Pros | Cons |
|------|------|
| Harbor queues excess tasks | One Jenkins build = many tasks; UI/API feedback is coarser |
| One agent occupancy for the whole batch | Harder to retry a single failed task |
| Matches “fill this Mac” capacity param | Same harness/model/suite for the whole batch (unless you split jobs) |

Jenkins resource rule: **one `lolbench_tasks` build per Mac** (node lock / executor 1). Pass `MAX_CONCURRENT` from node capacity (e.g. floor(RAM/8GB)).

Keep `lolbench_one_task` for single-task debug and oracle checks.

### Option B — Jenkins owns parallelism (`lolbench_one_task` × N)

Many one-task builds; each build **already** isolates via `--jobs-dir` (see above). Raise per-Mac `CPU_CORES` quantity when the host can run more concurrent Harbor containers.

| Pros | Cons |
|------|------|
| Clear per-task SUCCESS/UNSTABLE | Need lock quantity = Mac capacity |
| Easy retry of one task | More Jenkins queue noise |
| Same API as `lolbench_one_task` | Must keep unique `--jobs-dir` forever |

### Recommendation

1. **`lolbench_one_task` always uses per-build `--jobs-dir` / `--job-name`** (required, not optional).
2. Add **`lolbench_tasks`** later for bulk Harbor-queued batches if needed.

Prefer Option B for interactive retries and model sweeps; Option A for overnight multi-task batches on one fat Mac.

Do **not** fork Harbor — `--jobs-dir` is the isolation argument.

---

## Credentials

See **[secrets.md](secrets.md)** for the full design. Short version for this job:

**Configure secrets once on the Jenkins controller** (global Credentials store). Every agent that runs `lolbench_one_task` receives injected env for that build — do **not** install LLM/Git keys on each worker.

| Credential ID | Env var |
|---------------|---------|
| `openrouter-api-key` | `OPENROUTER_API_KEY` |
| `openai-api-key` | `OPENAI_API_KEY` |
| `anthropic-api-key` | `ANTHROPIC_API_KEY` |
| `deepseek-api-key` | `DEEPSEEK_API_KEY` |
| `openlux-api-key` | `OPENLUX_API_KEY` |
| `github-pat` / `gitcode-pat` | forge tokens for clone/push/comments (when jobs need them) |

Bind keys in the Pipeline by credential ID. Harbor uses whichever env matches `-m`. Never put keys in job parameters. `HARNESS=oracle` / `nop` should run without LLM credentials.

---

## Pipeline stages

1. **Checkout** LoLBench-Preview (SCM) at a pinned branch/tag (prefer unique workspace when executors > 1).
2. **Preflight** — `docker info`, `harbor --version`, `test -d harbor_tasks/$TASK`.
3. **Evaluate** — wrap with `lock` + timeout (e.g. 7 hours); call `harbor run` with per-build `--jobs-dir` / `--job-name` (see [Filesystem isolation](#filesystem-isolation---jobs-dir)). Do not use stock `scripts/run_task.sh` paths.

   For `oracle`/`nop`, omit `-m` (or pass model `-` in a wrapper).
4. **Report** — parse `${JOBS_DIR}/**/verifier/reward.json`:
   - `reward == 1.0` → build **SUCCESS**
   - file missing → **FAILURE** (harness/infra)
   - `reward == 0.0` → **UNSTABLE** (eval ran, task not resolved)  
     *(or FAILURE if you want a hard fail on “not solved”)*
5. **Archive** — `${JOBS_DIR}/**/verifier/*.json`, console log.

Set `currentBuild.description` to:

```text
ruff_1 | opencode | openrouter/deepseek/... | union | reward=1.0
```

so the Jenkins UI shows feedback without opening logs.

---

## Feedback surfaces

| Channel | What |
|---------|------|
| Build result | SUCCESS / UNSTABLE / FAILURE |
| Build description | task, harness, model, reward |
| Artifacts | `reward.json`, `agent_report.json` |
| Console | Harbor + Docker pull/run logs |
| Optional | Slack/email on UNSTABLE/FAILURE |

A later dashboard can consume archived `reward.json` across builds (task × harness × model).

---

## Example Jenkinsfile (sketch)

```groovy
pipeline {
  agent { label params.AGENT_LABEL }

  options {
    timestamps()
    timeout(time: 7, unit: 'HOURS')
    // Prefer per-node locks over disableConcurrentBuilds so different Macs can run in parallel.
  }

  parameters {
    choice(name: 'TASK', choices: ['ruff_1', 'fastapi_1', 'cpython_1'], description: 'LoLBench task id')
    choice(name: 'HARNESS', choices: ['opencode', 'codex', 'claude-code', 'oracle'], description: 'Harbor agent')
    string(name: 'MODEL', defaultValue: 'openrouter/deepseek/deepseek-v4-pro', description: 'provider/model')
    choice(name: 'SUITE', choices: ['union', 'orig', 'aug'])
    string(name: 'AGENT_LABEL', defaultValue: 'lolbench')
    string(name: 'MAX_RETRIES', defaultValue: '2')
  }

  environment {
    OPENROUTER_API_KEY = credentials('openrouter-api-key')
    JOBS_DIR = "harbor_runs/jenkins-${env.BUILD_NUMBER}/${params.TASK}"
    HARBOR_JOB_NAME = "${params.TASK}_${params.HARNESS}_${params.SUITE}_${env.BUILD_NUMBER}"
  }

  stages {
    stage('Evaluate') {
      steps {
        lock(label: 'CPU_CORES', quantity: 4) {
          sh '''
            set -euo pipefail
            test -d "harbor_tasks/${TASK}"
            docker info >/dev/null
            harbor --version

            mkdir -p "${JOBS_DIR}"
            model_args=()
            if [ "$HARNESS" != oracle ] && [ "$HARNESS" != nop ]; then
              model_args=(-m "$MODEL")
            fi
            extra=()
            [ "$MAX_RETRIES" != "0" ] && extra+=(--max-retries "$MAX_RETRIES")

            harbor run \
              -p "harbor_tasks/${TASK}" \
              -a "${HARNESS}" ${model_args[@]+"${model_args[@]}"} \
              --job-name "${HARBOR_JOB_NAME}" \
              --jobs-dir "${JOBS_DIR}" \
              --no-delete -n 1 -y \
              --ve "LOLBENCH_SUITE=${SUITE}" \
              "${extra[@]}"
          '''
        }
      }
    }
    stage('Report') {
      steps {
        sh '''
          python3 - <<'PY'
          import json, glob, os, pathlib
          jobs_dir = os.environ["JOBS_DIR"]
          paths = glob.glob(f"{jobs_dir}/**/verifier/reward.json", recursive=True)
          if not paths:
              raise SystemExit(f"no reward.json under {jobs_dir}")
          data = json.load(open(paths[0]))
          reward = data.get("reward")
          pathlib.Path("lolbench.properties").write_text(f"REWARD={reward}\\n")
          print(data)
          PY
        '''
        script {
          def props = readProperties file: 'lolbench.properties'
          currentBuild.description = "${params.TASK} | ${params.HARNESS} | ${params.MODEL} | reward=${props.REWARD}"
          if (props.REWARD != '1.0') {
            currentBuild.result = 'UNSTABLE'
          }
        }
      }
    }
  }

  post {
    always {
      archiveArtifacts artifacts: "${env.JOBS_DIR}/**/verifier/*.json", allowEmptyArchive: true
    }
  }
}
```

Keep this Jenkinsfile in **LoLBench-Preview** (for example `Jenkinsfile.lolbench_one_task`) if you prefer SCM-driven jobs later.

**mac-k3d:** `start` / `config` on a controller with Jenkins enabled create/update an inline Pipeline job named `lolbench_one_task`. Override the LoLBench checkout URL via job parameter `LOLBENCH_GIT_URL` (defaults to `lolbench.git_url` from config). Credentials are **not** required for `HARNESS=oracle`; for model runs, add Secret text credentials on the **controller** (see [secrets.md](secrets.md)) — not on each agent.

---

## Worker Mac setup

On each `lolbench` agent:

1. `mac-k3d prepare` / `start` if you still want a local k3d (optional for this job).
2. Docker Desktop with a large disk (images 2–17 GB each).
3. `uv tool install harbor` (or `pipx install harbor`).
4. Jenkins agent connected, labels `macos docker lolbench`.
5. Lockable Resources totaling `CPU_CORES` = logical CPU count (created/documented by `mac-k3d prepare` on the worker).

k3d does not need the LoLBench images imported.

---

## API / automation

Trigger without the UI:

```bash
curl -X POST "https://jenkins.example.com/job/lolbench_one_task/buildWithParameters" \
  --user "$USER:$TOKEN" \
  --data-urlencode TASK=ruff_1 \
  --data-urlencode HARNESS=opencode \
  --data-urlencode MODEL=openrouter/deepseek/deepseek-v4-pro \
  --data-urlencode SUITE=union
```

A wrapper job can fan out over a task list by triggering `lolbench_one_task` N times (each acquires `CPU_CORES` on some worker).

---

## Recommended defaults

| Setting | Value | Why |
|---------|--------|-----|
| `SUITE` | `union` | Canonical LoLBench verdict |
| `HARNESS` | `opencode` | LoLBench reference |
| `MODEL` | `openrouter/deepseek/deepseek-v4-pro` | LoLBench reference |
| Parallel LoLBench on one Mac | start at 1; raise lock qty only with `--jobs-dir` isolation | RAM / Docker pressure |
| Unresolved task | UNSTABLE | Distinguishes “not solved” from infra failure |

---

## Implementation order

1. Create lockable resource + worker labels (`prepare` / `config` on workers).
2. `mac-k3d start` / `config` on the controller creates Pipeline job `lolbench_one_task` (inline Jenkinsfile with per-build `--jobs-dir` / `--job-name`).
3. Add API key credentials on the Jenkins controller (see [secrets.md](secrets.md)) for non-oracle harnesses.
4. Run `HARNESS=oracle` on `ruff_1` (no model cost) — expect SUCCESS / reward 1.0; confirm artifacts under `harbor_runs/jenkins-<n>/ruff_1/`.
5. Trigger two builds of the same task; confirm separate `JOBS_DIR` trees and no overwrite.
6. Optionally: Slack notification and a separate `lolbench_tasks` wrapper job.
