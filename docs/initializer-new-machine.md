# Historical v0.3.0: initialize a new Linux or Mac (`cargo install`)

Not the product path. Current install: [user-guide.md](user-guide.md) and [new-machine.md](new-machine.md). iCode git/release: [icode-harness-inputs.md](icode-harness-inputs.md).

This page is **v0.3.0 only** (`cargo install`, `prepare`, user installs Docker first). Keep for machines still on that bootstrap. New machines use the GitHub Release binary.

**Old machines only:** command lists below. Do not follow this page for a current Release install.

**v0.3 pass/fail:** [testing-initializer.md](testing-initializer.md). This page does not describe how to verify the initializer.

One CLI (`mac-k3d`) on Linux and macOS.

| You want this machine to… | Follow |
|---------------------------|--------|
| Host Jenkins (UI + job queue) | [A. Controller](#a-controller--host-jenkins) |
| Run LoLBench / Harbor jobs (Jenkins agent) | [B. Worker](#b-worker--jenkins-agent) |
| Do both on **this** PC | [C. One PC, both roles](#c-one-pc-both-roles) |

Workers are Jenkins **agents**. They do not join the controller’s Kubernetes cluster.

```text
Controller:  Docker → k3d → Jenkins  (usually http://HOST:17070)
Worker:      Docker + Harbor + Java + agent  → that Jenkins URL
```

---

## Before any role

Run once on a new computer. Then go to **A** or **B**.

| Step | Linux | macOS |
|------|--------|--------|
| Rust / `cargo` on PATH | rustup, then `. "$HOME/.cargo/env"` | **Same** |
| Docker | Engine (`docker.io`) + `docker` group | **Docker Desktop** — install and **open** the app |
| Keep Jenkins agent running after logout | `loginctl enable-linger "$USER"` | **Skip** — `mac-k3d config` installs a LaunchAgent |

Do **not** run `loginctl` or `usermod` on a Mac. Those are Linux-only.

### Linux

```bash
# 1. Rust (cargo lives in ~/.cargo/bin; this line puts it on PATH in *this* terminal)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
. "$HOME/.cargo/env"

# 2. Docker Engine + permission to use it without sudo
sudo apt-get update
sudo apt-get install -y docker.io
sudo usermod -aG docker "$USER"
# log out of the desktop completely, log back in, open a new terminal

# 3. PATH again (new terminal) + keep user systemd running after logout
#    (needed so the Jenkins agent service stays up on a worker)
. "$HOME/.cargo/env"
loginctl enable-linger "$USER"

# 4. Check Docker
docker info   # must show a Server section
```

### macOS

```bash
# 1. Rust — same as Linux
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
. "$HOME/.cargo/env"

# 2. Docker Desktop (not docker.io). Homebrew optional:
brew install --cask docker
# or install from https://www.docker.com/products/docker-desktop/

# 3. Open the app once and wait until the menu-bar whale is idle
open -a Docker
docker info   # must succeed before mac-k3d start / worker setup

# No usermod, no loginctl. The worker agent later uses a LaunchAgent (KeepAlive).
```

If Homebrew is missing, install it from [https://brew.sh](https://brew.sh) or download Docker Desktop from Docker’s site.

### Then install the CLI (both OS)

```bash
git clone https://github.com/MichaelLing83/mac-k3d.git   # or your fork
cd mac-k3d
cargo install --path .
. "$HOME/.cargo/env"
which mac-k3d   # expect ~/.cargo/bin/mac-k3d
```

Use **Use this installation** in the wizard when a tool is already found. Let the wizard install the rest (k3d, kubectl, helm, Harbor, Java).

If `mac-k3d` is not found: `. "$HOME/.cargo/env"`.

Do **not** pick “Local development only” if you need Jenkins or an agent.

---

## A. Controller — host Jenkins

Storage: about **60 GB** free. Jenkins UI port: **17070**.

If another program already uses **8080**, after prepare edit `~/.config/mac-k3d/config.yaml` and change `cluster.ports` `host: 8080` to e.g. `18080`. Leave `jenkins.host_port: 17070`.

```bash
# 1. Write controller config (wizard)
mac-k3d prepare -i -c ~/.config/mac-k3d/config.yaml

# 2. Create k3d + install Jenkins (first run can take several minutes)
mac-k3d start -c ~/.config/mac-k3d/config.yaml

# 3. Print Jenkins URL and initial admin password
mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins
```

Open **http://localhost:17070** (or `http://<this-machine>:17070`). Username **admin**. Password from step 3. Do not commit the password.

**Wizard (step 1) — choose:**

| Prompt | Choose |
|--------|--------|
| Base directory | Recommended path with enough space |
| Role | **CI controller (Jenkins in k3d)** |
| Docker / k3d / kubectl / **helm** | Use existing, or install |
| Harbor / LoLBench | Optional (job can use a LoLBench path if you set one) |
| Cluster name | Default is fine |
| k3d agent nodes | `0` |
| Jenkins UI host port | `17070` |
| Job defaults | `oracle` / `ruff_1` / default model is fine |
| Enter CI secrets now? | **no** unless you already have keys ([secrets.md](secrets.md)) |
| Write configuration? | **yes** |

Do **not** use `worker.yaml` with `start`. `start` is for the controller file only.

---

## B. Worker — Jenkins agent

Storage: about **100 GB** free. You need the Jenkins **base URL** (another machine, or this PC after **A**).

The worker **node** is created in Jenkins and the local agent process starts only after Jenkins is up **and** you put an API token in the worker config. That is not `mac-k3d start` on `worker.yaml`.

### 1. Write worker config (wizard)

```bash
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
```

If Jenkins is not up yet, `agent.jar` may fail (`curl` to port **17070**). That is OK. Config still saves. Finish **A**, then continue below.

**Wizard — choose:**

| Prompt | Choose |
|--------|--------|
| Base directory | Recommended path with enough space |
| Role | **CI worker (Jenkins agent only)** |
| Docker / k3d / kubectl / java | Use existing, or install |
| Harbor | Install via uv/pipx, or use existing |
| Add `~/.local/bin` to PATH | **yes** if asked |
| LoLBench | Clone, or an existing checkout |
| Jenkins controller URL | Wizard default `http://127.0.0.1:17070`. Remote controller: type `http://<controller-ip>:17070` |
| API user / token | Fill in if Jenkins is already up; **empty** if not |
| Agent name / labels / remote root | Defaults are fine |
| Write configuration? | **yes** |

### 2. Jenkins API token (after Jenkins is up)

The `--show-jenkins` **password** is only for the browser. It is **not** the API token.

1. Open the Jenkins URL (`http://localhost:17070` on this PC).
2. Log in as **admin** + password from `mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins` (controller file).
3. Click **admin** (top right) → **Configure**.
4. **API Token** → **Add new Token** → name it (e.g. `mac-k3d-worker`) → **Generate**.
5. **Copy the token now.** Jenkins will not show it again.

`api_user` is `admin`. This token is only for `mac-k3d` to register the agent. It does not call paid LLM APIs. Do not commit it.

### 3. Put the token in `worker.yaml`

Edit `~/.config/mac-k3d/worker.yaml`. Under `jenkins_agent:`, change only:

```yaml
  api_user: admin
  api_token: PASTE_THE_TOKEN_HERE
```

Leave `controller_url` as the Jenkins URL (wizard default `http://127.0.0.1:17070`; remote: `http://<controller-ip>:17070`).

If you use `nano`:

```bash
nano ~/.config/mac-k3d/worker.yaml
```

**Ctrl+O**, **Enter** to save, **Ctrl+X** to quit. Quote the token if it contains `#` or spaces: `api_token: "..."`.

Or re-run step 1 and paste **admin** + token when the wizard asks.

### 4. Register the node and start the agent

```bash
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```

Use the **worker** file, not `config.yaml`. Expect: download `agent.jar`, register the node, write `launch-agent.sh`, create `CPU_CORES` locks, start the OS agent service. A line like `k3d cluster 'ci-worker' not present` is **OK** — workers are not a k3d cluster.

**Linux**

```bash
loginctl enable-linger "$USER"
systemctl --user status mac-k3d-jenkins-agent.service
```

Want **`Active: active (running)`**. If `status` pauses, press **`q`**.

**macOS** — no `loginctl`. `config` installs LaunchAgent `com.mac-k3d.jenkins-agent`:

```bash
launchctl print "gui/$(id -u)/com.mac-k3d.jenkins-agent" 2>&1 | head -20
```

### 5. Confirm the node in Jenkins

In the Jenkins UI: **Manage Jenkins → Nodes** (or **Build Executor Status** on the left). The agent name from YAML (for example `linux-Michael-Ubuntu`) should be **online**.

Then this machine can take queued jobs (`lolbench_one_task`) when an executor and locks are free.

Optional smoke test (**no LLM cost**): open job **lolbench_one_task** → **Build with Parameters** → **HARNESS** `oracle`, **TASK** `ruff_1`. Do not add OpenRouter/OpenAI keys unless you intend to pay for model runs.

Do **not** run `mac-k3d start -c ~/.config/mac-k3d/worker.yaml` to start the agent. The agent is `prepare` + token + `config`.

---

## C. One PC, both roles

Two files: `config.yaml` (controller) and `worker.yaml` (worker). Run in this order. After Jenkins is up, create the node with **B** steps 2–5 (token, `worker.yaml`, worker `config`, Nodes UI).

```bash
# 1. Before any role (Linux: rustup + docker.io + linger; macOS: rustup + Docker Desktop; then cargo install)

# 2. Controller
mac-k3d prepare -i -c ~/.config/mac-k3d/config.yaml
mac-k3d start -c ~/.config/mac-k3d/config.yaml
mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins
# log in at http://localhost:17070  (admin + printed password)

# 3. Worker (URL default http://127.0.0.1:17070; remote: http://<controller-ip>:17070)
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
# put api_user / api_token in worker.yaml, then:
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```

---

## Stop or remove (optional)

```bash
mac-k3d teardown -c ~/.config/mac-k3d/config.yaml
mac-k3d clean -c ~/.config/mac-k3d/worker.yaml --yes
mac-k3d clean -c ~/.config/mac-k3d/config.yaml --yes
```

---

## If a command fails

| Message | What to do |
|---------|------------|
| `mac-k3d: command not found` | `. "$HOME/.cargo/env"` then `cargo install --path .` |
| `permission denied` on `docker.sock` | Linux only: finish `usermod`, **log out and in**; if it still fails, `systemctl --user exit` then sign in |
| `docker info` fails / no Server | Linux: docker group + new login. macOS: open **Docker Desktop** and wait until it is idle |
| `loginctl: command not found` | macOS — ignore; use LaunchAgent after worker `config` |
| `agent.jar` / curl port 17070 | Finish controller `start`, then worker `config` |
| `REPLACE_ME` / unit not started | Token missing or still `api_token: null`; edit `worker.yaml` and re-run worker `config` |
| `mac-k3d-jenkins-agent.service` could not be found | You ran `config` on **controller** YAML, or Jenkins was down / no token. Use `-c worker.yaml` after step 2 |
| `failed to bind host port … 8080` | In controller YAML set host port `8080` → `18080`; keep Jenkins on `17070` |
