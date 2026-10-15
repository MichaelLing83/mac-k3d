# Dependencies and versions

Every tool, library and image mac-k3d uses, where it runs, which version, and whether that version is **pinned** (one value in one file) or **floating** (whatever the installer fetches today). Each row names the file the version comes from, the code that installs it, and the code that checks it.

Pins live in [`pipeline/config/toolchain.env`](../pipeline/config/toolchain.env). `pipeline/stages/_common.sh` reads it at the start of every pipeline step, and the `mac-k3d` binary compiles it in (`src/prepare/toolchain.rs`), so `setup` installs and checks the same values the pipeline asserts. `test_dependencies_doc_lists_every_pin` (in `pipeline/lib/test_pipeline_layout.py`) fails when a pin below differs from that file.

Current pins:

| Pin | Used for |
|-----|----------|
| `HARBOR_VERSION=0.22.0` | Harbor on every worker; the env phase refuses any other version |
| `DOCKER_COMPOSE_VERSION=v2.40.3` | Compose plugin downloaded when `docker compose` is missing |
| `DOCKER_BUILDX_VERSION=v0.29.1` | Buildx plugin downloaded when `docker buildx` is missing |
| `JAVA_MAJOR=21` | Jenkins controller image (`jdk21`) and the oldest Java an agent may run |
| `BASH_MIN=4.4` | Oldest bash the pipeline scripts run on |
| `PYTHON_MIN=3.11` | Oldest python3 the host scripts run on (`tomllib`) |
| `MIN_RAM_GB=8` | RAM every eval host needs |
| `WORKER_MIN_DISK_GB=40` | Free disk at `storage.base_dir` and at the agent root |
| `ICODE_LINUX_BUILD_IMAGE=ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm` | Builds and checks iCode's Linux runtime on a host that cannot run it (macOS, or another CPU type than the task containers) |
| `DEEPSWE_BASE_IMAGE=public.ecr.aws/x8v8d7g8/mars-base@sha256:91db850db926024eed328c4bf519d54986bc10aad75302cbb074f8e9d79b4c46` | Base of a DeepSWE task image built natively on an arm64 worker (replaces the Dockerfiles' `FROM …/mars-base:latest` through `docker build --build-context`); the same base the published images were built on |
| `DEEPSWE_REF=0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea` | DeepSWE checkout (`_common.sh`, a job parameter overrides it) |
| `LOLBENCH_REF=1b10d10bb4a10cea54374ac34b8f76b69dc8ce75` | LoLBench checkout (`_common.sh`, a job parameter overrides it) |

## Build (developer machine)

| Component | Version | Pinned? | Source of truth | Installed by | Checked by |
|-----------|---------|---------|-----------------|--------------|------------|
| Rust toolchain | stable, edition 2021 | floating (stable) | `Cargo.toml` `edition` | rustup | `cargo build` |
| Crates | `anyhow 1`, `clap 4`, `serde 1`, `serde_json 1`, `serde_yaml 0.9`, `thiserror 2`, `tokio 1`, `tracing 0.1`, `tracing-subscriber 0.3`, `atty 0.2`, `dialoguer 0.11`, `libc 0.2`, `include_dir 0.7`; tests: `assert_cmd 2`, `predicates 3`, `tempfile 3` | pinned exactly in `Cargo.lock` | `Cargo.toml`, `Cargo.lock` | cargo | `cargo test --locked` (CI) |
| Release runners | `ubuntu-latest` (x86_64 Linux), `ubuntu-24.04-arm` (aarch64 Linux), `macos-latest` (aarch64 and x86_64 macOS) | floating images | `.github/workflows/release-binaries.yml` | GitHub Actions | `.github/workflows/ci.yml` runs the tests on Linux and macOS |

## The `mac-k3d` binary

| Component | Version | Pinned? | Source of truth | Installed by | Checked by |
|-----------|---------|---------|-----------------|--------------|------------|
| glibc (Linux release) | 2.39 or newer on every host | fixed by the build image (Ubuntu 24.04) | release runner image | the OS | `scripts/redeploy.sh` runs the new binary's `--version` on every host before switching any |
| macOS release | Apple Silicon and Intel builds | per release | `release-binaries.yml` matrix | download | CI on `macos-latest`; lab parity trial in [testing/mac-mini-worker.md](testing/mac-mini-worker.md), not run yet |
| Embedded pipeline | the `pipeline/` tree of the commit the binary was built from | pinned per build (`mac-k3d --version` prints the commit) | `build.rs`, `src/prepare/eval_assets.rs` | `mac-k3d config` / `pipeline --extract-to` | every Jenkins build passes `--require-clean`; `check_report` refuses a dirty or mixed pipeline |

## Controller

| Component | Version | Pinned? | Source of truth | Installed by | Checked by |
|-----------|---------|---------|-----------------|--------------|------------|
| Docker | distro / Docker Desktop | floating | — | `setup` (Linux: `apt-get install docker.io docker-compose-v2`; macOS: `brew install --cask docker`) | `docker info` in setup |
| k3d | latest | floating | — | Linux: k3d `install.sh` (curl); macOS: `brew install k3d` | validation (binary present) |
| kubectl | latest stable | floating | — | Linux: apt, else `dl.k8s.io` stable; macOS: `brew install kubectl` | validation (binary present) |
| helm | latest v3 | floating | — | Linux: `get-helm-3` (curl); macOS: `brew install helm` | validation (binary present) |
| k3s image | default of the installed k3d | floating | — | k3d | — |
| Jenkins Helm chart `jenkins/jenkins` | latest chart | floating | `src/runtime/jenkins.rs` | `mac-k3d start` (`helm upgrade --install`) | Helm |
| Jenkins controller image | `jenkins/jenkins` tag label `jdk{JAVA_MAJOR}` (`JAVA_MAJOR=21`) | pinned (major) | `toolchain.env` `JAVA_MAJOR`, written as Helm `controller.image.tagLabel` | `mac-k3d start` | `helm_values_pin_the_controller_java` |
| Jenkins plugins | `lockable-resources`, `plain-credentials`, `file-parameters`, `copyartifact`, `pipeline-utility-steps`, `hidden-parameter`, `uno-choice` | floating (`installLatestPlugins: true`) | `ADDITIONAL_PLUGINS` in `src/runtime/jenkins.rs` | the chart at start | `helm_values_include_lockable_resources` |

## Worker host

| Component | Version | Pinned? | Source of truth | Installed by | Checked by |
|-----------|---------|---------|-----------------|--------------|------------|
| Docker | distro / Docker Desktop | floating | — | `setup` (as on the controller) | Linux worker host steps (`docker info`, `docker` group); env phase `env/host.sh` |
| Compose plugin | `DOCKER_COMPOSE_VERSION=v2.40.3` when downloaded; an existing plugin is kept | pinned download, floating if already present | `toolchain.env` | worker `setup` (`src/prepare/docker_plugins.rs`) and `env/compose.sh`, both into `~/.docker/cli-plugins` | worker `config` report; `env/compose.sh` |
| Buildx plugin | `DOCKER_BUILDX_VERSION=v0.29.1` when downloaded; an existing plugin is kept | as above | `toolchain.env` | as above | as above |
| Java (agent) | `JAVA_MAJOR=21` or newer | pinned (minimum major) | `toolchain.env` | Linux: `apt-get install -y openjdk-21-jre-headless` (sudo or the administrator block); macOS: `brew install --cask temurin@21` | `prepare` validation, worker `config` (`java 21: ok`), `scripts/env_set_up/03_check_worker.sh` |
| git | distro | floating | — | Linux: apt; macOS: brew | `prepare` validation, `env/host.sh` |
| bash | `BASH_MIN=4.4` or newer | pinned (minimum) | `toolchain.env` | Linux: the system bash; macOS: `brew install bash` (setup does it) | `_common.sh` guard (every step), worker validation and `config` |
| python3 | `PYTHON_MIN=3.11` or newer | pinned (minimum) | `toolchain.env` | Linux: the system python3 (`apt-get install -y python3` root step when missing); macOS: `brew install python` | `env/host.sh`, worker validation and `config` |
| uv | latest | floating | — | `curl https://astral.sh/uv/install.sh` (setup and `env/harbor.sh`) | `env/harbor.sh` |
| Harbor | `HARBOR_VERSION=0.22.0` | pinned (exact) | `toolchain.env` | `uv tool install harbor==0.22.0` (setup and `env/harbor.sh`) | worker `config` (`harbor 0.22.0: ok`), `env/harbor.sh` |
| Harbor egress probe image | Harbor's own `alpine:3.23.4@sha256:5b10f432…`; fallbacks Harbor's egress sidecar `harbor-prebuilt:harbor-docker-egress-control-sidecar--…`, then `alpine:3.19@sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1` | pinned by digest (the sidecar by Harbor's build hash) | Harbor's `DockerEnvironment._EGRESS_CONTROL_KERNEL_PROBE_IMAGE`; fallback `FALLBACK_PROBE_IMAGE` in `pipeline/lib/harbor_egress.py` | `docker pull` by `env/egress.sh` when missing; the sidecar is never pulled, only used when Harbor already built it | `env/egress.sh` and the evaluate re-check; a fallback is used only when Docker cannot run Harbor's image |
| Jenkins `agent.jar` | whatever the controller serves | follows the controller | the controller | worker `config` downloads `/jnlpJars/agent.jar` | agent restart only when the jar changes |
| RAM | `MIN_RAM_GB=8` | pinned (minimum) | `toolchain.env` | — | `setup` apply, `env/host.sh` (`ensure_eval_preflight`) |
| Free disk | `WORKER_MIN_DISK_GB=40` | pinned (minimum) | `toolchain.env` (`resources.disk_min_gb` in the YAML overrides it) | — | `setup` at `storage.base_dir` and `jenkins_agent.remote_fs`; env phase at the build's `WORKDIR` |

### Linux and macOS

| | Linux worker | macOS worker |
|-|--------------|--------------|
| Java package | `openjdk-21-jre-headless` (apt) | `temurin@21` (Homebrew cask) |
| Java discovery order | `JAVA_HOME`, `java` on PATH, `/usr/lib/jvm/*/bin/java` | `java_home -v 21+`, `java_home`, `JAVA_HOME`, `java` on PATH |
| Installer | apt with sudo, or one block for an administrator | Homebrew as the user (no root block) |
| bash / python3 | system bash and python3 (Ubuntu 24.04: 5.2 / 3.12) | Homebrew bash and python3; `/bin/bash` 3.2 and `/usr/bin/python3` 3.9 are too old |
| Agent daemon | systemd user unit `mac-k3d-jenkins-agent.service` + `loginctl enable-linger` | LaunchAgent `com.mac-k3d.jenkins-agent` (runs while the user is logged in) |
| Agent PATH | shell PATH, then `~/.local/bin`, `~/.cargo/bin`, `/usr/local/bin`, `/usr/bin`, `/snap/bin` | `/opt/homebrew/bin`, `/usr/local/bin` first, then the shell PATH, `~/.local/bin`, Docker.app's bin |
| Agent restart | systemd `Restart=always`, `RestartSec=5` | LaunchAgent `KeepAlive`, `ThrottleInterval` 5 |
| Host warnings in `config` / `status` | none | automatic login off; `pmset` sleep not 0 |
| RAM probe | `/proc/meminfo` | Docker Desktop's limit (`docker info` `MemTotal`); `sysctl -n hw.memsize` when the daemon is down |
| CPU limit | logical CPUs | `cpu_cores` capped at Docker Desktop's CPUs (`docker info` `NCPU`); `config` refuses more |
| MTU probe | `ip route get` + `ip link` | `route -n get` + `ifconfig` |
| Trial network host routes | `ip route` + `ip addr` | `netstat -rn -f inet` + `ifconfig` |
| Provenance CPU and RAM | `/proc/cpuinfo`, `/proc/meminfo` | `sysctl machdep.cpu.brand_string`, `hw.memsize` |
| Task containers | DeepSWE and SWE-bench Pro `linux/amd64`; LoLBench the Docker host platform | DeepSWE `linux/arm64` when `mac-k3d eval prepare` proved its native build, else the published `linux/amd64` image under Rosetta; SWE-bench Pro `linux/amd64` under Rosetta; LoLBench `linux/arm64` |
| DeepSWE task image | the published image, which Harbor pulls at the trial; nothing to prepare (an arm64 Linux host is treated like a Mac) | `mac-k3d eval prepare --benchmark deepswe` runs `docker build --build-context …/mars-base:latest=docker-image://$DEEPSWE_BASE_IMAGE` (BuildKit, not yet run on Docker Desktop) and Harbor's oracle and nop agents once per question, stored in `~/.local/share/mac-k3d/env-check/deepswe/`. An eval does no build or check: it runs the proven build, else the published image |
| linux/amd64 on an arm64 host | QEMU binfmt (`docker run --privileged --rm tonistiigi/binfmt --install amd64`) | Docker Desktop's Rosetta (Settings > General) |
| iCode release drop | `icode-linux-<task container arch>-full-v*`, picked by ELF header | the same; upload a `.tar.gz` holding both Linux drops |
| iCode git-mode runtime | `uv sync` on the host when its CPU type matches the containers, else in `ICODE_LINUX_BUILD_IMAGE` | always in `ICODE_LINUX_BUILD_IMAGE` on the task platform |
| JiuwenSwarm runtime | always in `ICODE_LINUX_BUILD_IMAGE` on the task platform | the same |
| uv cache for the container build | `~/.local/share/mac-k3d/uv-cache-linux-<arch>` | the same |

The code and the CI tests (`ubuntu-latest` and `macos-latest`) cover macOS. The lab trial that runs the same questions on a Linux and a Mac worker and compares them is [testing/mac-mini-worker.md](testing/mac-mini-worker.md); it has not been run yet.

## Pipeline Python libraries

| Component | Version | Pinned? | Source of truth | Installed by | Checked by |
|-----------|---------|---------|-----------------|--------------|------------|
| Host scripts (`pipeline/lib/*.py`) | standard library only; `capture_receipt.py` and `canary_verdict.py` need `tomllib` | `PYTHON_MIN=3.11` | `toolchain.env` | python3 on the worker | `env/host.sh` |
| PyYAML | any | floating | — | not installed by mac-k3d | `harness_labels.py` imports it only when it reads a harness YAML |
| Harbor agent modules (`patch_harbor_agent.py`, `canary_harbor_agent.py`, `icode_harbor_agent.py`, `jiuwenswarm_harbor_agent.py`) | import `harbor` | follows `HARBOR_VERSION` | `toolchain.env` | Harbor's own uv environment | `tasks/agent.sh`, `harbor run` |
| In-trial JiuwenSwarm scripts (`jiuwenswarm_trial.py`, `jiuwenswarm_usage.py`) | the bundle's CPython 3.11 | follows the bundle | the bundle's `runtime.json` | uploaded into each trial by `JiuwenSwarmAgent` | the JiuwenSwarm canary's `seed` probe |

## Benchmarks and system under test

| Component | Version | Pinned? | Source of truth | Fetched by | Checked by |
|-----------|---------|---------|-----------------|------------|------------|
| DeepSWE | `DEEPSWE_REF=0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea` | pinned | `_common.sh` | `tasks/benchmark.sh` (git) | checkout at the ref |
| LoLBench | `LOLBENCH_REF=1b10d10bb4a10cea54374ac34b8f76b69dc8ce75` | pinned | `_common.sh` | `tasks/benchmark.sh` (git) | checkout at the ref |
| SWE-bench Pro | default branch of `SWEBENCHPRO_GIT_URL` (`--depth 1`) | **not pinned** (known gap) | `_common.sh` | `tasks/benchmark.sh` | — |
| iCode (system under test) | the git ref or release drop the job selects (default PR 2) | recorded, not pinned | the checkout HEAD, or the release file's sha256 | Jenkins upload or git clone (`tasks/harness`) | the console line `iCode under test:` and `eval_protocol.icode` |
| JiuwenSwarm (system under test, DeepSWE only) | the git ref or release the job selects (default PR 2 of `michaelling/jiuwenswarm`) | recorded, not pinned | the checkout HEAD, or the release file's sha256 | Jenkins upload or git clone (`tasks/harness`, `jiuwenswarm_input.sh`) | the console line `JiuwenSwarm under test:` and `eval_protocol.harness` |
| JiuwenSwarm's Python and dependencies | CPython 3.11 (`JIUWENSWARM_PYTHON`), wheels for `manylinux_2_28`; the commit's Linux offline pack (`OFFLINE_CI_PLATFORMS=linux-<cpu> make offline-tarballs-ci`), else `uv sync --frozen`, from the commit's `uv.lock`, including `openjiuwen` (`openJiuwen/agent-core`) and `gateway-protocol` (`openJiuwen/agent-protocol`) pinned by commit on gitcode | pinned by the JiuwenSwarm commit's `uv.lock` (git mode); a wheel's dependencies float on PyPI, a wheelhouse brings its own | `uv.lock`; the bundle's `freeze.txt` records what was installed | `jiuwenswarm_build.sh` in `ICODE_LINUX_BUILD_IMAGE`, with uv's cache per platform under `MAC_K3D_SHARE` | the build's probes, the JiuwenSwarm canary, and `bundle.json`'s `freeze` |
| Harness bundle | the sha256 of the prepared tree's tarball | pinned per run: every shard runs the dispatcher's sha256 | `bundle.json`, `entry.json` in the [harness store](pipeline.md#harness-store) | `Prepare harness` (dispatcher), `tasks/harness` | `harness_store.py get` re-checks the tree digest on every use; `aggregate_runs.py` flags two bundles on one platform |
| Task images | per task (`docker_image` in `task.toml`); on an arm64 worker DeepSWE uses a native build `mac-k3d/deepswe-native:<id>` of `environment/Dockerfile` on `DEEPSWE_BASE_IMAGE` | per task: the benchmark ref, plus the base digest for a native build | the benchmark checkout, `toolchain.env` | Harbor pulls DeepSWE's published images and SWE-bench Pro's; `mac-k3d eval prepare` builds DeepSWE natively on a Mac; `tasks/images.sh` builds LoLBench | DeepSWE on a Mac: Harbor's oracle and nop agents once per native build (`mac-k3d eval prepare`, `env_check.py`), stored under `~/.local/share/mac-k3d/env-check/`; each eval records native or emulated and why in `eval_protocol.env_check`; a native question needs a stored pass, and the image platform must match the task containers (`check_report.py`) |

## Sandbox config

| File | What it fixes |
|------|---------------|
| `pipeline/config/network-allowlist-v1.json` | Hosts an agent container may reach |
| `pipeline/config/canary-v1.json` | Isolation canary probes |
| `pipeline/config/anticheat-v1.json` | Anti-cheat receipts and verdict rules |

Each file carries its version in the name (`-v1`); a change of rules gets a new file, not an edit.

## Known floating versions and their risk

The Jenkins chart, its plugins, k3d, kubectl, helm, the k3s image and uv float: a fresh controller or worker gets whatever is newest that day. The risk is real: the chart moved its default image to JDK 21, and workers that had Java 17 connected and then failed with `UnsupportedClassVersionError`. `JAVA_MAJOR` now pins that one. The follow-up is to pin the others the same way, as `KEY=value` lines in `toolchain.env` that both setup and the pipeline read.

## How to change a version

1. Edit `pipeline/config/toolchain.env` (or the ref in `_common.sh`).
2. Update this page; `test_dependencies_doc_lists_every_pin` fails until you do.
3. Run `cargo test` and `python3 -m unittest discover -s pipeline/lib -p 'test_*.py'`.
4. Ship it: `bash scripts/redeploy.sh --controller <user@controller> --worker <user@worker>`. For `JAVA_MAJOR`, workers then report the new requirement in `mac-k3d config`; `mac-k3d setup` with **Use existing config** switches or installs.
5. Add a row to [integration-log.md](integration-log.md).
