# Binary-initializer testing (Mac + Linux)

Lab runbook (this team), not the user start-here. **Users:** [user-guide.md](../user-guide.md).

This is the **binary-initializer** path (`setup`, Release asset). Full workflow: [workflow.md](../workflow.md). The **v0.3.0 / initializer** path (`cargo install`, `prepare` steps 0–7) is [testing-initializer.md](../testing-initializer.md).

Use this document to **verify** a prebuilt `mac-k3d` on macOS and Linux.

**Linux bootstrap sign-off is Task 0–5, Task 7, and Task 8.** Task 6 is macOS (later). Eval pipeline (Task 9, the phases after `env`): [testing-eval-pipeline.md](testing-eval-pipeline.md). After setup, run [`scripts/env_set_up/run_all.sh`](../../scripts/env_set_up/run_all.sh).

If live YAML already exists, `setup` shows **Config already exists** (Validate). To test as a **new user**, wipe first (Task 8a), then follow Task 8b / [clean-machine-binary-test.md](clean-machine-binary-test.md).

Related design: [setup.md](../setup.md#wizard-questions), [commands.md](../commands.md).

### Which document

| Document | Purpose |
|----------|---------|
| **This file** ([testing-binary-initializer.md](testing-binary-initializer.md)) | **Developer sign-off.** Copy-paste Tasks 0–9, pass/fail, wipe-to-first-run, `scripts/env_set_up`. |
| [new-machine.md](../new-machine.md) | **End-user product guide.** How a person on a blank Linux/Mac downloads a Release binary, picks controller vs worker vs both, honest leftovers (docker logout, API token, Docker Desktop). Not a numbered lab checklist; `02`/`03`/`run_all` are not the main path. |
| [clean-machine-binary-test.md](clean-machine-binary-test.md) | **Lab walkthrough for testers.** Same product as the user guide, ordered as wipe-or-new-PC → `01_download_binary.sh` → controller wizard → worker wizard → `run_all.sh`. Wizard prompt table lives there. |

---

## What this path is for

Turn a new Linux or Mac into a Jenkins **controller** or **worker** by downloading a Release asset. No Rust. The binary installs Docker when you choose **Install**. Jenkins UI: **http://localhost:17070**. Workers do **not** join the controller’s k3d cluster. Worker `start` is rejected.

| Role | Job | How it is achieved |
|------|-----|-------------------|
| **CLI on PATH** | Same binary on macOS and Linux | GitHub pre-release `mac-k3d-{os}-{arch}` or `cargo build --release` |
| **Controller host** | Jenkins in local k3d | `setup -c config.yaml`, role **CI controller** |
| **Worker host** | Jenkins inbound agent | `setup -c worker.yaml` then `config` (not `start`) |
| **Dual config** | One binary, two YAML files, no clobber | `-c config.yaml` vs `-c worker.yaml` |

Constants used below: tag `v0.5.3`, repo `~/Documents/Toby/mac-k3d`, `PATH="$HOME/.local/bin:$PATH"`.

Do not commit Jenkins passwords or API tokens.

---

## Task 0 — unit / build (developer)

Purpose: check the tree compiles, unit tests pass, and the local release binary reports `0.4.x` with `setup` in `--help`.

command:

```bash
cd ~/Documents/Toby/mac-k3d
unset CARGO_TARGET_DIR
export CARGO_TARGET_DIR="$PWD/target"
cargo test
cargo build --release
./target/release/mac-k3d --version
./target/release/mac-k3d --help
```

expected results:

- `cargo test` green (lib + `tests/cli.rs`; help tests mention `setup` and `eval`).
- `cargo build --release` finishes.
- `--version` prints `mac-k3d 0.4.x` (or later).
- `--help` lists `setup`.

---

## Task 1 — Release binary as a user (Linux lab)

Purpose: check a GitHub Release asset runs without Cargo on PATH, `--help` lists `setup`/`eval`, and a TTY `setup` on a throwaway file opens the wizard.

command:

```bash
cd ~/Documents/Toby/mac-k3d
export MAC_K3D_RELEASE_TAG=v0.5.3
./scripts/env_set_up/01_download_binary.sh
export PATH="$HOME/.local/bin:$PATH"
hash -r
which mac-k3d
mac-k3d --version
mac-k3d --help

cp ~/.local/bin/mac-k3d /tmp/mac-k3d-user
chmod +x /tmp/mac-k3d-user
env -i HOME="$HOME" USER="$USER" PATH="/usr/bin:/bin:/tmp" /tmp/mac-k3d-user --help

mkdir -p /tmp/mac-k3d-wizard-test
mac-k3d setup -c /tmp/mac-k3d-wizard-test/fresh.yaml
```

expected results:

- `OK downloaded mac-k3d-linux-x86_64 → …/.local/bin/mac-k3d`
- `mac-k3d 0.4.0` (or later 0.4.x)
- `OK help lists setup and eval`
- `which mac-k3d` is `$HOME/.local/bin/mac-k3d` (not `~/.cargo/bin`)
- stripped-PATH `--help` lists `setup` (binary does not need rustc/cargo)
- TTY wizard starts (`mac-k3d prepare — interactive setup`, then **Select base directory**). Press **Ctrl+C**. Do **not** Write / Apply — that throwaway path must not clobber the lab `config.yaml`.

---

## Task 2 — controller setup from binary

Purpose: check `setup -c config.yaml` brings up k3d + Jenkins on **http://localhost:17070**, status is healthy, and jobs exist.

command:

```bash
export PATH="$HOME/.local/bin:$PATH"
mac-k3d setup -c ~/.config/mac-k3d/config.yaml
```

If **Config already exists** (re-test on this lab): choose **Validate existing config only**, then **yes** on **Continue and apply now (start/config)?**. If credentials are offered: **n** unless you have keys ready; empty Enter skips a key. GitCode/GitHub PATs are not required for Task 2.

To test as a **completely new machine** (first-run Role wizard, not Validate), wipe live YAML first — **Task 8a** — then **Task 8b**. First-write prompt table: [clean-machine-binary-test.md](clean-machine-binary-test.md) (role **CI controller**, Jenkins port **17070**). If host **8080** is taken, **`Host port 8080 in use → using 18080` is PASS**; Jenkins stays **17070**.

Then verify:

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Documents/Toby/mac-k3d
mac-k3d status -c ~/.config/mac-k3d/config.yaml
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:17070/login
./scripts/env_set_up/02_check_controller.sh
```

expected results:

- Docker Engine already running; k3d cluster `ci-controller` running (or created).
- Jenkins UI: **http://localhost:17070** (ignore Helm notes about `:8080`).
- Jobs `lolbench_one_task` and `deepswe_one_task` created/updated.
- `=== mac-k3d setup complete ===` with Role `controller`.
- `status`: Docker running, `ci-controller` running, Jenkins pod Running, `http://localhost:17070`.
- `curl` prints `200`.
- `OK controller status healthy`
- `OK Jenkins UI http://localhost:17070/login HTTP 200`
- `OK job lolbench_one_task present`
- `OK job deepswe_one_task present`
- `OK 02_check_controller complete`

If Docker was just installed, a clear **log out, log in, run `mac-k3d setup` again** error is **PASS** for that attempt (re-run after login).

Optional: `mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins` prints admin user/password for the browser. If it asks to create CI credentials again, choose **n**. Do not commit the password.

---

## Task 3 — worker setup from binary

Purpose: check `setup -c worker.yaml` registers a Jenkins agent (`config` only, not `start`), the systemd unit is active, and `start` on the worker file is rejected.

Prerequisite: Jenkins API token from the UI (**admin** → **Configure** → **API Token**). `api_user` is **`admin`**. Paste the **token secret string**, not the token **name**. If you skipped it, `worker.yaml` still has `api_user: ''` / `api_token: ''`; fill them in, then `mac-k3d config -c ~/.config/mac-k3d/worker.yaml`.

command:

```bash
export PATH="$HOME/.local/bin:$PATH"
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
```

If **Config already exists** (re-test): **Use existing config (finish pending installs, then validate)**, then **yes** on apply. Worker apply runs **config** only.

First-run (no live `worker.yaml`): Task 8a wipe, then Task 8b. At **Jenkins controller URL**, the wizard default is `http://43.107.42.252:17070`. Type a new URL or export `JENKINS_URL` for another controller. Same-PC controller: type `http://127.0.0.1:17070`. Do not paste a command block while prompts are open. If URL/name/labels are garbled: edit `jenkins_agent` in `worker.yaml` (`controller_url`, `api_user`, `api_token`, `name`, `labels`, `remote_fs`) then `mac-k3d config`, or **Re-run wizard (overwrite config)**.

Then:

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Documents/Toby/mac-k3d
REQUIRE_WORKER=1 ./scripts/env_set_up/03_check_worker.sh
```

Do **not** run `mac-k3d start -c ~/.config/mac-k3d/worker.yaml`.

expected results:

- `Worker: registering Jenkins agent (not starting k3d/Jenkins)…`
- `k3d cluster 'ci-worker' not present` is OK.
- `Jenkins agent systemd user unit started (mac-k3d-jenkins-agent.service)` (or already running).
- Setup complete with Role `worker`, Jenkins UI `http://localhost:17070`.
- `OK worker start rejected (exit 1)`
- `OK worker status role=worker`
- `OK Linux agent unit mac-k3d-jenkins-agent.service active`
- `OK 03_check_worker complete`
- `harbor: 0.22.0 found` or Harbor installed with `uv` into `~/.local/bin` (no root); `harbor 0.22.0: ok` from `config`. No k3d, kubectl or LoLBench question. Optional: Jenkins **Manage Jenkins → Nodes** shows the agent **online**.

---

## Task 4 — full env check + eval-ready (`env` phase)

Purpose: check controller, worker, and eval-ready scripts together, including `mac-k3d eval --stage env`.

command:

```bash
cd ~/Documents/Toby/mac-k3d
export PATH="$HOME/.local/bin:$PATH"
SKIP_DOWNLOAD=1 RUN_EVAL_SMOKE=1 ./scripts/env_set_up/run_all.sh
```

expected results:

- `OK SKIP_DOWNLOAD=1 — using existing binary` and `mac-k3d 0.4.0`
- `OK 02_check_controller complete`
- `OK 03_check_worker complete`
- `OK docker compose and buildx present`
- `OK eval --stage env` / `PROGRESS 10% env complete`
- `OK 04_check_eval_ready complete`
- `OK ALL CHECKS PASSED`

The `env` phase checks RAM and disk, Docker Server, compose and buildx, the pinned Harbor, CLI `eval`, the agent unit, and (with a key) that the model is served. It does not clone DeepSWE or call an LLM.

---

## Task 5 — isolation / dual YAML

Purpose: check `prepare` / `start` / `config` still work, the two YAML files keep different roles, worker `start` stays rejected, and the controller stays healthy.

command:

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Documents/Toby/mac-k3d

grep -E '^role:' ~/.config/mac-k3d/config.yaml ~/.config/mac-k3d/worker.yaml

mac-k3d prepare --non-interactive -c ~/.config/mac-k3d/config.yaml
mac-k3d prepare --non-interactive -c ~/.config/mac-k3d/worker.yaml

mac-k3d start -c ~/.config/mac-k3d/config.yaml
mac-k3d config -c ~/.config/mac-k3d/config.yaml --skip-secrets

mac-k3d start -c ~/.config/mac-k3d/worker.yaml ; echo "exit=$?"

mac-k3d status -c ~/.config/mac-k3d/config.yaml
```

If `config --skip-secrets` still asks for credentials, choose **n**. Do not re-run the wizard (it overwrites YAML). Do not use another OS account.

expected results:

- `config.yaml:role: controller` and `worker.yaml:role: worker`
- `prepare --non-interactive` exits 0 on both files
- Controller `start` / `config` complete; Jenkins still **http://localhost:17070**
- Worker `start`: `Error: Config("start is for controller/standalone configs. …")` and `exit=1`
- `status`: Docker running, `ci-controller` running, Jenkins pod Running, `http://localhost:17070`

---

## Task 7 — Intel Mac asset (`mac-k3d-darwin-x86_64`)

Purpose: check tag `v0.5.3` publishes all four OS/arch assets and `mac-k3d-darwin-x86_64` is Mach-O **x86_64** (not arm64). Runs on Linux; no Intel Mac required for 7a/7b.

The Intel binary is **cross-compiled** on GitHub `macos-latest` (`x86_64-apple-darwin`). It is **not** built on `macos-13`.

command:

```bash
gh release view v0.5.3 --repo Toby-Yu/mac-k3d --json assets --jq '.assets[].name'

cd /tmp
gh release download v0.5.3 --repo Toby-Yu/mac-k3d --pattern mac-k3d-darwin-x86_64 --clobber
file ./mac-k3d-darwin-x86_64
```

Also open the **Release binaries** workflow for that tag: job **build** / matrix `x86_64-apple-darwin` green; step **Verify Intel Mac asset is x86_64** prints `x86_64` and not `arm64`.

expected results:

- Asset names include all four:
  - `mac-k3d-linux-x86_64`
  - `mac-k3d-linux-aarch64`
  - `mac-k3d-darwin-aarch64`
  - `mac-k3d-darwin-x86_64`
- `file` prints `Mach-O 64-bit x86_64 executable` (reject arm64, ELF, or arm64-only).

Optional Task 7c (Intel Mac or Rosetta): `xattr -d com.apple.quarantine ./mac-k3d-darwin-x86_64`, `chmod +x`, `./mac-k3d-darwin-x86_64 --help` lists `setup` and `eval`. Native Intel is the real 7c.

---

## Task 8a — wipe live YAML (keep `.bak`) for first-run

Purpose: remove cluster, agent unit, and **live** `config.yaml` / `worker.yaml` so the next `setup` asks Role / base directory — not **Config already exists**. Keep `.bak` backups. Docker stays installed. Product walkthrough after wipe: [clean-machine-binary-test.md](clean-machine-binary-test.md).

command:

```bash
export PATH="$HOME/.local/bin:$PATH"

mac-k3d teardown -c ~/.config/mac-k3d/worker.yaml --deregister-agent 2>/dev/null || true
mac-k3d clean -c ~/.config/mac-k3d/worker.yaml --yes 2>/dev/null || true
mac-k3d teardown -c ~/.config/mac-k3d/config.yaml 2>/dev/null || true
mac-k3d clean -c ~/.config/mac-k3d/config.yaml --yes 2>/dev/null || true
```

Remove the agent service. **Linux:**

```bash
systemctl --user stop mac-k3d-jenkins-agent.service 2>/dev/null || true
systemctl --user disable mac-k3d-jenkins-agent.service 2>/dev/null || true
rm -f ~/.config/systemd/user/mac-k3d-jenkins-agent.service
systemctl --user daemon-reload 2>/dev/null || true
```

**macOS:**

```bash
launchctl bootout "gui/$(id -u)/com.mac-k3d.jenkins-agent" 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.mac-k3d.jenkins-agent.plist
```

Then on both:

```bash
mkdir -p ~/.config/mac-k3d
[ -f ~/.config/mac-k3d/config.yaml ] && mv -f ~/.config/mac-k3d/config.yaml ~/.config/mac-k3d/config.yaml.bak
[ -f ~/.config/mac-k3d/worker.yaml ] && mv -f ~/.config/mac-k3d/worker.yaml ~/.config/mac-k3d/worker.yaml.bak
[ -f ~/.config/mac-k3d/credentials.pending.yaml ] && mv -f ~/.config/mac-k3d/credentials.pending.yaml ~/.config/mac-k3d/credentials.pending.yaml.bak

k3d cluster delete ci-controller 2>/dev/null || true
k3d cluster delete ci-worker 2>/dev/null || true

ls -la ~/.config/mac-k3d/
test ! -f ~/.config/mac-k3d/config.yaml && echo "no live config.yaml OK"
test ! -f ~/.config/mac-k3d/worker.yaml && echo "no live worker.yaml OK"
curl -sS -o /dev/null -w "17070:%{http_code}\n" --max-time 3 http://localhost:17070/login || echo "Jenkins down OK"
```

expected results:

- `config.yaml.bak` / `worker.yaml.bak` present (and optional `credentials.pending.yaml.bak`).
- `no live config.yaml OK` and `no live worker.yaml OK`.
- Jenkins `:17070` down (`Could not connect` / `Jenkins down OK`).
- Worker teardown when YAML is already gone may print default cluster `mac-k3d` and ignore `--deregister-agent` — harmless.
- `Leaving ~/.local/state/mac-k3d intact` is expected.
- Restore only if abandoning the test: copy `.bak` back over live YAML.

To only prove prompts without applying, skip 8b and use a throwaway file:

```bash
export PATH="$HOME/.local/bin:$PATH"
mkdir -p /tmp/mac-k3d-wizard-test
mac-k3d setup -c /tmp/mac-k3d-wizard-test/fresh.yaml
# Ctrl+C before Write / apply now
```

---

## Task 8b — first-run wizard (controller + worker + run_all)

Purpose: check a **new-user** path after Task 8a: Release binary, first-run controller wizard, worker with API token, then `run_all.sh`. Same sequence as [clean-machine-binary-test.md](clean-machine-binary-test.md). Do **not** pick Local development only.

command:

```bash
cd ~/Documents/Toby/mac-k3d
export PATH="$HOME/.local/bin:$PATH"
export MAC_K3D_RELEASE_TAG=v0.5.3
./scripts/env_set_up/01_download_binary.sh
which mac-k3d
mac-k3d --version
mac-k3d --help

mac-k3d setup -c ~/.config/mac-k3d/config.yaml
```

Controller wizard (type answers; do not paste a multi-line block into a prompt):

| Prompt | Choose |
|--------|--------|
| Base directory | recommended |
| Role | **CI controller (Jenkins in k3d)** |
| Docker / k3d / kubectl / helm | **Use this installation** (or **Install**) |
| Jenkins UI host port | **17070** |
| Job defaults | Enter |
| CI secrets | **yes** if you have `deepseek-api-key`; else skip / empty Enter |
| Write + apply | **yes** |
| Continue apply | **yes** |

Then:

```bash
cd ~/Documents/Toby/mac-k3d
./scripts/env_set_up/02_check_controller.sh
```

Create a Jenkins API token in the UI (**admin** → **Configure** → **API Token**). Copy the **secret**, not the token name.

```bash
export PATH="$HOME/.local/bin:$PATH"
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
```

| Prompt | Choose |
|--------|--------|
| Role | **CI worker (Jenkins agent only)** |
| Docker / Java / git | Use this installation |
| Harbor | Not asked: setup installs the pinned 0.22.0 with `uv` (no root) |
| Jenkins controller URL | wizard default `http://127.0.0.1:17070`. This lab cloud: type `http://43.107.42.252:17070` |
| Jenkins API user | `admin` |
| Jenkins API token | paste the **secret string** (Enter at the user skips both; fill them in `worker.yaml` later) |
| Agent name / labels / remote root | Enter |
| Write + apply | **yes** |

If URL became `interactive setup` or `agent.jar` curl is malformed: Ctrl+C or finish, then edit `jenkins_agent` in `worker.yaml` and `mac-k3d config -c ~/.config/mac-k3d/worker.yaml`, or **Re-run wizard (overwrite)**. Never `start -c worker.yaml`.

```bash
cd ~/Documents/Toby/mac-k3d
REQUIRE_WORKER=1 ./scripts/env_set_up/03_check_worker.sh
SKIP_DOWNLOAD=1 RUN_EVAL_SMOKE=1 ./scripts/env_set_up/run_all.sh
```

expected results:

- `01_download_binary`: `OK downloaded … → ~/.local/bin/mac-k3d`, version `0.4.x`, help lists `setup` and `eval`.
- Controller first prompts are base dir / Role, not **Config already exists**.
- `Host port 8080 in use → using 18080` is **PASS**; Jenkins UI **http://localhost:17070**.
- `OK 02_check_controller complete` (status healthy, login HTTP 200, both jobs).
- Worker: `agent.jar` from `http://localhost:17070/jnlpJars/agent.jar`; unit `mac-k3d-jenkins-agent.service` started; `ci-worker` missing is OK.
- `OK 03_check_worker complete` (`start` rejected, role=worker, unit active).
- `OK ALL CHECKS PASSED` (includes the `env` phase).

---

## Remaining (not required to sign off Linux bootstrap)

### Task 6 — macOS (checklist)

Purpose: check the Apple Silicon Release asset runs in Terminal.app, the same wizard works, and a Mac worker uses LaunchAgent `com.mac-k3d.jenkins-agent`. Double-click of an unsigned binary is **not** supported. Mac can be signed off later than Linux.

command:

```bash
# download mac-k3d-darwin-aarch64 from tag v0.5.3
xattr -d com.apple.quarantine ./mac-k3d-darwin-aarch64
chmod +x ./mac-k3d-darwin-aarch64
./mac-k3d-darwin-aarch64 --help

./mac-k3d-darwin-aarch64 setup -c ~/.config/mac-k3d/config.yaml
# or worker: setup -c ~/.config/mac-k3d/worker.yaml
#   Jenkins URL http://<linux-controller-ip>:17070
```

If Docker is missing: choose **Install**, then open **Docker Desktop** once and wait until it is idle.

expected results:

- `--help` lists `setup` and `eval`.
- Same wizard as Linux (controller or worker).
- Worker after token + `config`: LaunchAgent `com.mac-k3d.jenkins-agent`; node **online** in Jenkins.

```bash
launchctl print "gui/$(id -u)/com.mac-k3d.jenkins-agent" 2>&1 | head -20
```

---

### Task 9 — eval pipeline phases after `env`

Purpose: check Process 2 (Harbor, DeepSWE, iCode). **The `env` phase is already covered in Task 4.** A full eval does not run the LLM-only baseline. Phases and steps: [pipeline.md](../pipeline.md); checks: [testing-eval-pipeline.md](testing-eval-pipeline.md). Paid phases need Jenkins credential **`deepseek-api-key`** (or a local `.env`).

command:

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Documents/Toby/mac-k3d

mac-k3d eval --stage tasks --n-tasks 1
mac-k3d eval --stage evaluate --n-tasks 1
mac-k3d eval --stage anticheat
mac-k3d eval --stage score
mac-k3d eval --stage report --n-tasks 1
mac-k3d eval --stage archive
```

Then once:

```bash
mac-k3d eval --n-tasks 1 --icode-mode release
# or local: mac-k3d eval --local --n-tasks 1
```

expected results:

- **tasks:** `$WORKDIR/deep-swe/tasks` exists (`https://github.com/datacurve-ai/deep-swe` at its pin); `OK icode helps` (`ICODE_MODE=release` drop or `ICODE_MODE=git` clone); `icode_harbor_agent:ICodeAgent` imports; `OK isolation`; `OK leak scan`.
- **evaluate:** `OK slots`, then `PROGRESS n% evaluate` lines; one DeepSWE task through iCode in one `harbor run` (`--n-tasks 1`); `reward.json` under `harness/`.
- **baseline:** not part of a full eval. A hand `mac-k3d eval --stage baseline` still exists and is not published.
- **anticheat / score:** a verdict per trial; temp JSON with `reward` / `f2p` / `p2p` rates / `pass_at_1`.
- **report / archive:** `eval-runs/output/<benchmark>/<run>/artifact.json`, `summary.md`, `report.html`, `cost-token-report.md`, and `output/<benchmark>/<run>.tar.gz`.
- Full runner: Jenkins job `deepswe_one_task` (or `--local`) runs the harness and archives that folder.

The `evaluate` phase costs time, disk, and API usage. GitHub/GitCode PATs are only needed for **private** iCode clone or tarball.

---

## Linux lab (2026-09-11, `mac-k3d 0.4.0` / tag `v0.4.0-rc.4`)

| Check | Result |
|-------|--------|
| Task 0 | `cargo test` green; `cargo build --release`; `--help` lists `setup` |
| Task 1 | `01_download_binary.sh`; `/tmp/mac-k3d-user --help` lists `setup` without cargo PATH; TTY wizard on throwaway YAML (Ctrl+C) |
| Task 2 | `ci-controller` running; Jenkins `http://localhost:17070` HTTP 200; `OK 02_check_controller complete` |
| Task 3 | `start -c worker.yaml` rejected; `mac-k3d-jenkins-agent.service` active; `OK 03_check_worker complete` |
| Task 4 | `SKIP_DOWNLOAD=1 RUN_EVAL_SMOKE=1 ./scripts/env_set_up/run_all.sh` → `OK ALL CHECKS PASSED`; P0 complete |
| Task 5 | dual YAML; worker `start` `exit=1`; controller `status` healthy |
| Task 6 | macOS later |
| Task 7 | four Release assets; `file` → Mach-O 64-bit x86_64 executable (Actions job green still confirm on GitHub) |
| Task 8 | wipe to `.bak`; first-run controller + worker (`v0.4.0-rc.4`); `Host port 8080 in use → using 18080`; `OK ALL CHECKS PASSED` |
| Task 9 | P1–P8 not run (P0 via Task 4) |

---

## Troubleshooting

| Message | What to do |
|---------|------------|
| `permission denied` on `docker.sock` / log out for docker | Linux: **log out and in**, then `mac-k3d setup` again |
| `docker info` fails / no Server | Linux: docker group + new login. macOS: open **Docker Desktop** and wait until it is idle |
| `agent.jar` / curl port 17070 / need token | Finish controller `start`, paste API **secret** (not the token name), then worker `config` |
| `curl: URL rejected` / `interactive setup/jnlpJars/agent.jar` | Wizard ate leftover prompt text as `controller_url`. Set `jenkins_agent.controller_url` to `http://127.0.0.1:17070` (or this lab’s `http://43.107.42.252:17070`), fix `api_user` / `api_token` / `name` / `labels` / `remote_fs`, then `mac-k3d config -c worker.yaml`. Or Task 8a wipe and Task 8b again. |
| `start is for controller/standalone` | Expected on `worker.yaml`. Use `mac-k3d config -c worker.yaml` |
| `failed to bind host port … 8080` | Prefer a build that auto-remaps on `setup`/`start`. Last resort: set free `cluster.ports` hosts; keep Jenkins on `17070` unless remapped |
| Release missing `darwin-x86_64` | Tag was cut **before** the `macos-latest` cross-compile workflow. Push this branch, cut a **new** `v*` tag (or upload the CI artifact onto the old release). See **Task 7**. |

User commands: [new-machine.md](../new-machine.md).  
Eval pipeline stages: [testing-eval-pipeline.md](testing-eval-pipeline.md).
