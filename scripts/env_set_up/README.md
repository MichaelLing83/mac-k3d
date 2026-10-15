# Environment setup checks (`scripts/env_set_up`)

Automated **verify + light smoke** scripts for the binary-initializer path. They download the Release `mac-k3d` binary and assert that this computer’s Jenkins **controller**, **worker**, and **eval-ready** state look healthy.

They do **not** replace interactive `mac-k3d setup` (wizard needs a TTY for role, secrets, and API token). Human bootstrap steps: [docs/testing/clean-machine-binary-test.md](../../docs/testing/clean-machine-binary-test.md).

Default Jenkins UI: **`http://localhost:17070`**.

## Quick start

```bash
# From the repo root (after you have run setup for controller + worker):
./scripts/env_set_up/run_all.sh

# Already have mac-k3d on PATH / ~/.local/bin — skip GitHub download:
SKIP_DOWNLOAD=1 ./scripts/env_set_up/run_all.sh

# Also run mac-k3d eval --stage env (the pipeline's bare-metal phase):
SKIP_DOWNLOAD=1 RUN_EVAL_SMOKE=1 ./scripts/env_set_up/run_all.sh
```

Expected final line on success:

```text
OK ALL CHECKS PASSED
```

Any `FAIL` / `ERROR` → exit non-zero.

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `MAC_K3D_RELEASE_TAG` | `v0.5.3` | GitHub Release tag to download |
| `MAC_K3D_REPO` | `Toby-Yu/mac-k3d` | GitHub `owner/repo` |
| `JENKINS_URL` | `http://localhost:17070` | Controller UI base URL |
| `CONTROLLER_CONFIG` | `~/.config/mac-k3d/config.yaml` | Controller YAML |
| `WORKER_CONFIG` | `~/.config/mac-k3d/worker.yaml` | Worker YAML |
| `INSTALL_DIR` | `~/.local/bin` | Where `01_download_binary.sh` installs `mac-k3d` |
| `MAC_K3D_BIN` | (auto) | Override path to the CLI |
| `SKIP_DOWNLOAD` | `0` | `1` = do not download; use existing binary |
| `REQUIRE_WORKER` | `1` in `run_all.sh` | `1` = fail if worker/agent missing; `0` = skip OK |
| `RUN_EVAL_SMOKE` | `0` | `1` = run `mac-k3d eval --stage env` inside `04` |

---

## Scripts

### `01_download_binary.sh`

**Purpose:** Download the matching Release asset (`mac-k3d-linux-x86_64`, `linux-aarch64`, `darwin-aarch64`, or `darwin-x86_64`) for tag `MAC_K3D_RELEASE_TAG` and install it as `~/.local/bin/mac-k3d`.

**Expected output (success):**

```text
OK downloaded mac-k3d-linux-x86_64 → /home/…/.local/bin/mac-k3d
mac-k3d 0.4.0
OK help lists setup and eval
OK 01_download_binary complete
```

Uses `gh release download` when `gh` is available; otherwise `curl` to the GitHub download URL.

### `02_check_controller.sh`

**Purpose:** Confirm controller config exists, `mac-k3d status` shows Docker + k3d + Jenkins Running, Jenkins login returns HTTP 200, and jobs `lolbench_one_task`, `deepswe_one_task`, and `swebenchpro_one_task` exist.

**Expected output (success):**

```text
OK mac-k3d=…
OK controller status healthy
OK Jenkins UI http://localhost:17070/login HTTP 200
OK job lolbench_one_task present
OK job deepswe_one_task present
OK job swebenchpro_one_task present
OK 02_check_controller complete
```

**Fail if:** no `config.yaml`, cluster/Jenkins down, wrong port, or jobs missing → finish `mac-k3d setup` / `start` / `config` for the controller first.

### `03_check_worker.sh`

**Purpose:** Confirm worker YAML exists (unless skipped), `mac-k3d start -c worker.yaml` is **rejected**, status reports worker role, and the agent daemon is active (Linux systemd user unit or macOS LaunchAgent).

**Expected output (success, one-PC both roles):**

```text
OK worker start rejected (exit 1)
OK worker status role=worker
OK Linux agent unit mac-k3d-jenkins-agent.service active
OK 03_check_worker complete
```

**Skip:** no `worker.yaml` and `REQUIRE_WORKER=0` → `OK 03_check_worker skipped`.

**Fail if:** agent not active and `REQUIRE_WORKER=1` → put `api_user` / `api_token` in `worker.yaml`, then `mac-k3d config -c ~/.config/mac-k3d/worker.yaml`.

### `04_check_eval_ready.sh`

**Purpose:** Confirm Docker has a Server section, `docker compose` and `buildx` are at the pinned versions (the pipeline's `env/compose` step), CLI lists `eval`, Jenkins UI is up, and (when present) the local agent looks active. Optionally runs `mac-k3d eval --stage env` when `RUN_EVAL_SMOKE=1`.

**Expected output (success):**

```text
OK docker Server section present
OK docker compose …
OK docker buildx …
OK docker compose and buildx present
OK mac-k3d lists eval
OK Jenkins UI reachable for eval
OK eval-ready — safe to start an evaluation task …
OK 04_check_eval_ready complete
```

Does **not** prove DeepSeek credentials or the evaluate phase onwards. For those see [testing-eval-pipeline.md](../../docs/testing/testing-eval-pipeline.md).

### `05_check_export_import.sh`

**Purpose:** File-only `mac-k3d export` / `import` on this machine’s live `worker.yaml` or `config.yaml`. Writes scratch files under `/tmp`. Never `--force` onto `~/.config/mac-k3d/`. Does **not** queue Jenkins or call the LLM.

Needs a binary that has `export` (GitHub v0.5.3 or this checkout’s `target/release/mac-k3d`).

```bash
MAC_K3D_BIN="$PWD/target/release/mac-k3d" ./scripts/env_set_up/05_check_export_import.sh
```

**Expected output (success):** `OK 05_check_export_import complete`; the scratch YAML has no `api_token` value (a worker file keeps the empty `api_token: ''` slot).

Not part of `run_all.sh` (that suite is Release-binary health). DeepSWE TASK change + Jenkins: [docs/export-import.md](../../docs/export-import.md). `./scripts/list_deepswe_tasks.sh` prints sorted DeepSWE ids.

### `run_all.sh`

**Purpose:** Run `01` → `02` → `03` (`REQUIRE_WORKER` default 1) → `04` in order.

**Expected output (success):** ends with `OK ALL CHECKS PASSED`.

---

## Relationship to `pipeline/`

| Directory | Focus |
|-----------|--------|
| `scripts/env_set_up/` | Machine bootstrap: binary, controller, worker, eval-**ready** |
| `pipeline/stages/` | Eval pipeline: seven phases (`env`, `tasks`, `evaluate`, `anticheat`, `score`, `report`, `archive`) and their steps — [docs/pipeline.md](../../docs/pipeline.md) |
| `pipeline/lib/` | Harbor agents, isolation and anti-cheat checks, scoring, report, archive |

After env checks pass, continue with `mac-k3d eval --stage env` or `pipeline/stages/run_all.sh`. Operator start: [docs/user-guide.md](../../docs/user-guide.md).
