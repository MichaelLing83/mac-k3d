# mac-k3d documentation

**Users start here:** [user-guide.md](user-guide.md).

Do not add a new top-level folder for each feature branch. Update these product pages, or if the page is **only** for that branch, add `docs/<branch>/README.md` + `testing.md`. A pre-commit hook (`scripts/check_docs.sh`) enforces layout, start-here (no v0.3 links from the repo README or user-guide), and the **Releases** inventory below.

## Users (current product)

### Get started

| Document | Description |
|----------|-------------|
| [user-guide.md](user-guide.md) | Cloud controller, worker, iCode drop, Jenkins eval |
| [new-machine.md](new-machine.md) | Download the Release binary; `setup` installs Docker; a Mac mini worker's one-time settings |
| [setup.md](setup.md) | Machine roles and network, every wizard question, the controller and worker layouts |

### Run evaluations

| Document | Description |
|----------|-------------|
| [workflow.md](workflow.md) | End-to-end: new machine → controller/worker → JSON |
| [icode-harness-inputs.md](icode-harness-inputs.md) | Jenkins upload vs git clone (`branch` / `tag` / `commit` / `pr`); which Linux drop a Mac needs |
| [jiuwenswarm-harness-inputs.md](jiuwenswarm-harness-inputs.md) | JiuwenSwarm on DeepSWE: git or release (wheel, wheelhouse, bundle), what one rollout runs, `build_jiuwenswarm_headless` |
| [jenkins-jobs.md](jenkins-jobs.md) | The ten jobs `config` writes: three shapes x three benchmarks, and `build_jiuwenswarm_headless` |
| [evaluation.md](evaluation.md) | What a run stores, which job shape to pick, and what the fields mean |
| [export-import.md](export-import.md) | Export, `set`, and import. CPU limit is a live worker edit; model and task go through the controller file |
| [secrets.md](secrets.md) | Jenkins credentials on the controller; local `.env` for `--local` |

### How it works

| Document | Description |
|----------|-------------|
| [pipeline.md](pipeline.md) | The seven phases, how Harbor and its flags are used, anti-cheat layers, outputs, Linux and macOS workers |
| [optimization.md](optimization.md) | Declared per-task resources, how many fit, scheduling on workers, RAM/disk gates |

## Lab runbooks (this team, not the user start-here)

Copy-paste checklists with this lab’s IPs live under [testing/](testing/).

| Document | Description |
|----------|-------------|
| [testing/README.md](testing/README.md) | What belongs in `testing/` |
| [testing/testing-eval-pipeline.md](testing/testing-eval-pipeline.md) | E0–E8 / pipeline phases |
| [testing/cloud-eval-runbook.md](testing/cloud-eval-runbook.md) | This cloud VM + this PC |
| [testing/mac-mini-worker.md](testing/mac-mini-worker.md) | Onboard a Mac mini worker and compare it with this PC on the same questions |
| [testing/testing-binary-initializer.md](testing/testing-binary-initializer.md) | Bootstrap sign-off |
| [testing/clean-machine-binary-test.md](testing/clean-machine-binary-test.md) | Wipe or new PC → eval-ready |
| [testing/question-coverage.md](testing/question-coverage.md) | Questions run on this worker, and Docker memory per question |
| [testing/question-log.md](testing/question-log.md) | Per-run result of every question, open problems and fix notes (`mac-k3d eval record`) |
| [testing/nas-storage.md](testing/nas-storage.md) | What to copy to a NAS after a run, and what stays on the worker |

## Developers

| Document | Description |
|----------|-------------|
| [commands.md](commands.md) | CLI flags |
| [configuration.md](configuration.md) | YAML schema |
| [dependencies.md](dependencies.md) | Every dependency per role (build, binary, controller, worker, pipeline, benchmarks): version, pinned or floating, where the pin lives, what installs and checks it, Linux vs macOS |
| [architecture.md](architecture.md) | Components |
| [integration-log.md](integration-log.md) | Phase 0 integration steps, test evidence, and follow-ups |

## Releases (what each version shipped)

Current install is the GitHub Release binary on **Users** above. This table is the inventory; it is not a second start-here. Repo README and [user-guide.md](user-guide.md) must not link the v0.3 pages.

| Version | What it implemented | Where to read |
|---------|---------------------|---------------|
| v0.5.4 (this tree, not released yet) | macOS workers (Mac mini): the same phases, inputs and outputs as a Linux worker. DeepSWE runs the native arm64 builds that `mac-k3d eval prepare` proved, else its amd64 images under Rosetta, and the report says which; SWE-bench Pro keeps its amd64 images (Rosetta); the iCode release drop is picked by CPU type; git mode builds the iCode runtime in a Linux container; Docker Desktop's memory and CPUs are the limits; LaunchAgent restart and auto-login/sleep warnings. JiuwenSwarm as a second harness on DeepSWE (`build_jiuwenswarm_headless`), and a sha256-checked harness store: every dispatcher prepares the harness once and each worker unpacks it once | [pipeline.md](pipeline.md#linux-and-macos-workers), [new-machine.md](new-machine.md#mac-mini-worker), [testing/mac-mini-worker.md](testing/mac-mini-worker.md), [jiuwenswarm-harness-inputs.md](jiuwenswarm-harness-inputs.md), [pipeline.md](pipeline.md#harness-store) |
| v0.5.3 | One `one_task` build is one question and one rollout. Executors are `cpu_cores / 2`. An out-of-memory trial is queued again. The Linux agent is kept off the host OOM killer's list | [pipeline.md](pipeline.md), [evaluation.md](evaluation.md), [export-import.md](export-import.md) |
| v0.5.2 | Jenkins iCode **git** (`branch` / `tag` / `commit`) or **release** file upload; leftover clone wipe; SWE-bench Pro third job | [user-guide.md](user-guide.md), [icode-tag-commit-release/README.md](icode-tag-commit-release/README.md) |
| binary-initializer (v0.4+) | Download `mac-k3d-{os}-{arch}`; `setup` installs Docker; controller vs worker | [new-machine.md](new-machine.md), [binary-initializer/README.md](binary-initializer/README.md) |
| v0.3.0 | `cargo install` + `prepare`; user installs Docker first | **Historical** below (not the product path) |

## Historical (v0.3.0 `cargo install` / `prepare`)

Not the product path. Keep for old machines only. Do not link these from the repo README or [user-guide.md](user-guide.md).

| Document | Description |
|----------|-------------|
| [initializer-new-machine.md](initializer-new-machine.md) | v0.3.0 blank machine via `cargo install` |
| [testing-initializer.md](testing-initializer.md) | v0.3.0 steps 0–7 |

## Branch process (not start-here)

Each folder is **only** `README.md` (what that git branch shipped) and `testing.md` (how to re-test it). Product behavior belongs in **Users** / **Developers** above.

| Document | Description |
|----------|-------------|
| [icode-tag-commit-release/README.md](icode-tag-commit-release/README.md) | Git or release iCode input; Harbor on DeepSWE, LoLBench, and SWE-bench Pro |
| [binary-initializer/README.md](binary-initializer/README.md) | Release `setup` + Docker install |
| [eval-comparison-report/README.md](eval-comparison-report/README.md) | Comparison report, same-question slots, four open slots, and the per-run container memory log |
| [eval-optimization/README.md](eval-optimization/README.md) | DeepSeek+high, force-commit, eval_protocol, LoLBench fix rewards, active-question mem, cost-token report |
| [harbor-delegation-multiworker/README.md](harbor-delegation-multiworker/README.md) | Harbor owns scheduling; declared resources; per-node locks; three job shapes + sharded full suite; pipeline pinned by git ref |

## Writing docs

- When a step's command differs between Linux and macOS, show both side by side: one-liners in a two-column table (Linux | macOS), longer blocks as a **Linux** block next to a **macOS** block.
- When the command is the same on both, show it once.
- Lab IPs, this PC and sign-off tables go in [testing/](testing/), not in the product pages.

## Goals

1. **Single entry point** — One CLI to manage Docker, k3d, and optional Jenkins on **macOS and Linux**.
2. **Idempotent operations** — Safe to re-run `prepare`, `start`, and `config`.
3. **Sensible defaults** — Works out of the box; config file overrides when needed.
4. **Clear lifecycle** — `setup` (wizard + apply) or prepare → start (controller only) → config → teardown → clean.
5. **Platform adapters** — macOS (Docker Desktop, Homebrew, LaunchAgent) vs Linux (Docker Engine, apt, systemd --user).

## Non-goals (v1)

- Windows support
- Production-grade Jenkins hardening (LDAP, backup, HA)
- Replacing Helm/k3d/kubectl — we orchestrate them, not reimplement them
- In-cluster application deployment beyond Jenkins
- Auto-install package managers on non-Debian Linux (use existing binaries / paths)
