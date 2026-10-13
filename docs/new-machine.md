# Initialize a new Linux or Mac (binary-initializer)

This is the **binary-initializer** path (`setup`, GitHub Release asset). Full story (bootstrap → iCode eval): [workflow.md](workflow.md). Older `cargo install` / `prepare` steps are listed under **Historical** in [README.md](README.md), not this page.

**Users:** download a Release binary from this repo and run it. The binary installs Docker (and the rest) when you choose **Install**. After Jenkins is up, run `mac-k3d eval` for DeepSWE / iCode / DeepSeek ([testing-eval-pipeline.md](testing/testing-eval-pipeline.md)).

**Developers:** pass/fail checks live in [testing-binary-initializer.md](testing/testing-binary-initializer.md). Clean-machine walkthrough + automated scripts: [clean-machine-binary-test.md](testing/clean-machine-binary-test.md) and [`scripts/env_set_up/`](../scripts/env_set_up/README.md).

One CLI (`mac-k3d`) on Linux and macOS.

| You want this machine to… | Follow |
|---------------------------|--------|
| Host Jenkins (UI + job queue) | [A. Controller](#a-controller--host-jenkins) |
| Run Jenkins agent jobs | [B. Worker](#b-worker--jenkins-agent) |
| Do both on **this** PC | [C. One PC, both roles](#c-one-pc-both-roles) |

Workers are Jenkins **agents**. They do not join the controller’s Kubernetes cluster.

```text
GitHub Release  mac-k3d-{os}-{arch}
        →  chmod +x && ./mac-k3d   (or setup)
        →  wizard: controller | worker
        →  binary installs Docker + the rest
        →  controller: k3d + Jenkins
           worker: Java agent connected to Jenkins URL
```

You do **not** need Rust. You do **not** install Docker yourself first unless auto-install fails (see [If auto-install fails](#if-auto-install-fails)).

Developers can use their own `target/release/mac-k3d` instead of a release asset. Do not commit built blobs.

---

## 1. Download the binary

Get the matching asset from the **Latest** GitHub Release: `mac-k3d-linux-x86_64`, `mac-k3d-linux-aarch64`, `mac-k3d-darwin-aarch64` (Apple silicon, including every Mac mini since 2020), or `mac-k3d-darwin-x86_64` (Intel Mac). Open **Terminal** (double-click is not supported).

| Linux | macOS |
|-------|-------|
| `ASSET=mac-k3d-linux-x86_64` | `ASSET=mac-k3d-darwin-aarch64`<br>`xattr -d com.apple.quarantine "./$ASSET"` (the binary is not notarized) |

Then on both:

```bash
chmod +x "./$ASSET"
mkdir -p ~/.local/bin
cp "./$ASSET" ~/.local/bin/mac-k3d
export PATH="$HOME/.local/bin:$PATH"
which mac-k3d
mac-k3d --help    # must list `setup`
```

First-run (TTY): `mac-k3d` or `mac-k3d setup` starts the wizard. The wizard only asks questions; it saves your answers to the YAML file first, then installs what you chose and offers to apply start/config. Re-running `setup` on an existing file offers **Use existing config (finish pending installs, then validate)**, **Re-run wizard (overwrite config)** or **Cancel**.

When Docker is not found, choose **Install**. The binary runs `apt` (Linux) or Homebrew cask (macOS). It asks for `sudo` at most once, and only when something needs root. If you cannot use `sudo`, see [What root must run](#what-root-must-run).

Honest leftovers the binary cannot hide:

- **Linux:** one **logout / login** after the `docker` group is added, then re-run `mac-k3d setup`.
- **macOS:** first **Docker Desktop** window (and Homebrew if it is missing).
- **Worker:** paste a Jenkins **API token** from the UI into the `api_token: ''` line `worker.yaml` already has, then `mac-k3d config -c worker.yaml` if the first pass had no token.

Do **not** pick “Local development only” if you need Jenkins or an agent.

Developers building from git still use `cargo build --release` (see [testing-binary-initializer.md](testing/testing-binary-initializer.md)).

Store the DeepSeek API key on the **controller** when prompted for CI secrets (credential id `deepseek-api-key`), or later via Jenkins Credentials — see [secrets.md](secrets.md). Workers do not keep a local copy of that key.

---

## A. Controller — host Jenkins

Storage: about **60 GB** free. Jenkins UI port: **17070**.

If another program already uses **8080**, **mac-k3d auto-remaps** the cluster HTTP host port (e.g. to `18080`) and prints the change. Leave `jenkins.host_port: 17070` unless that port is also busy (it is remapped the same way). Manual YAML edit is a last resort.

```bash
# 1. Write controller config and apply (wizard → start → config)
mac-k3d setup -c ~/.config/mac-k3d/config.yaml

# Power-user split:
# mac-k3d prepare -i -c ~/.config/mac-k3d/config.yaml
# mac-k3d start -c ~/.config/mac-k3d/config.yaml
# mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins
```

Open **http://localhost:17070** (or `http://<this-machine>:17070`). Username **admin**. Password from `mac-k3d config --show-jenkins` if `setup` already printed it. Do not commit the password.

**Wizard (step 1) — choose:**

| Prompt | Choose |
|--------|--------|
| Base directory | Recommended path with enough space |
| Role | **CI controller (Jenkins in k3d)** |
| Docker / k3d / kubectl / **helm** | **Install** if not found, or use existing |
| Cluster name | Default is fine |
| k3d agent nodes | `0` |
| Jenkins UI host port | `17070` |
| Job defaults | Defaults are fine |
| Enter CI secrets now? | **yes** if you have `deepseek-api-key` (needed for `mac-k3d eval`); else **no** and add later ([secrets.md](secrets.md)) |
| Write configuration? | **yes** |

Do **not** use `worker.yaml` with `start`. `start` is for the controller file only.

After Jenkins is healthy, run evaluations with `mac-k3d eval` (see [workflow.md](workflow.md)).

---

## B. Worker — Jenkins agent

Storage: about **100 GB** free. You need the Jenkins **base URL** (another machine, or this PC after **A**).

The worker **node** is created in Jenkins and the local agent process starts only after Jenkins is up **and** you put an API token in the worker config. That is not `mac-k3d start` on `worker.yaml`.

### 1. Write worker config (wizard)

```bash
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
# or: mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
```

Your answers are saved to `worker.yaml` before anything is installed. If Jenkins is not up yet, `agent.jar` may fail (`curl` to port **17070**). That is OK. Finish **A**, then continue below.

**Wizard — choose:**

| Prompt | Choose |
|--------|--------|
| Base directory | Recommended path with enough space |
| Role | **CI worker (Jenkins agent only)** |
| Docker / java / git | **Install** if not found, or use existing |
| Harbor | Not asked. Setup installs the version pinned in `pipeline/config/toolchain.env` (0.22.0) with `uv tool install` into `~/.local/bin`, no root, and replaces any other version |
| k3d / kubectl / LoLBench | Not asked. A worker hosts no cluster, and the pipeline clones each benchmark itself |
| Add `~/.local/bin` to PATH | **yes** if asked |
| Jenkins controller URL | Wizard default `http://43.107.42.252:17070`. Type `http://<new-ip>:17070` or export `JENKINS_URL` |
| API user / token | Fill in if Jenkins is already up. Otherwise press **Enter**: `worker.yaml` still gets `api_user: ''` and `api_token: ''` so you know where to put them later |
| Agent name / labels / remote root | Defaults are fine |
| Write configuration? | **yes** |

### What root must run

Setup runs everything it can as you. Harbor and the Jenkins agent never need root. Java is the controller's major version (`JAVA_MAJOR=21` in `pipeline/config/toolchain.env`); an older Java already on the machine is not enough, because the controller refuses the agent. Every tool and version is listed in [dependencies.md](dependencies.md).

| Linux | macOS |
|-------|-------|
| When a step needs root and you cannot use `sudo` (common on a shared cloud VM), setup does **not** fail half-way: your answers are already saved, and it stops with one block for an administrator (below) | There is no root block: Homebrew installs as you (`brew install --cask temurin@21`, plus `brew install bash` and `brew install python`, because macOS ships bash 3.2 and python3 3.9). The one `sudo` step is `pmset` in [Mac mini worker](#mac-mini-worker) |

The Linux block lists only what is missing on this machine, for example:

```text
Ask an administrator to run these as root on this machine:

  apt-get install -y openjdk-21-jre-headless   # install java
  apt-get install -y git                       # install git
  usermod -aG docker <you>                     # let <you> use Docker without sudo (then log out and back in)
  loginctl enable-linger <you>                 # keep the Jenkins agent running after you log out
```

After the administrator has run it, log out and back in, then run `mac-k3d setup -c ~/.config/mac-k3d/worker.yaml` again and choose **Use existing config**; setup carries on from the installs.

### 2. Jenkins API token (after Jenkins is up)

The `--show-jenkins` **password** is only for the browser. It is **not** the API token.

1. Open the Jenkins URL (`http://localhost:17070` on this PC).
2. Log in as **admin** + password from `mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins` (controller file).
3. Click **admin** (top right) → **Configure**.
4. **API Token** → **Add new Token** → name it (e.g. `mac-k3d-worker`) → **Generate**.
5. **Copy the token now.** Jenkins will not show it again.

`api_user` is `admin`. This token is only for `mac-k3d` to register the agent. It does not call paid LLM APIs. Do not commit it.

### 3. Put the token in `worker.yaml`

Edit `~/.config/mac-k3d/worker.yaml`. The two keys are already under `jenkins_agent:` (empty if you skipped them). Fill in only:

```yaml
  api_user: admin
  api_token: PASTE_THE_TOKEN_HERE
```

Leave `controller_url` as the Jenkins URL you gave the wizard (`http://127.0.0.1:17070` when the controller is this PC; otherwise `http://<controller-ip>:17070`).

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

Use the **worker** file, not `config.yaml`. Expect: `harbor 0.22.0: ok` (a warning if Harbor or git is missing), download `agent.jar`, register the node with `cpu_cores / cpus_per_executor` executors, write `launch-agent.sh`, create `<agent>-core-1..cpu_cores` locks, start the OS agent service. With the keys still empty it prints which keys to fill in and the command to re-run. A line like `k3d cluster 'ci-worker' not present` is **OK** — workers are not a k3d cluster.

That is the whole cost of adding the Nth worker. No pipeline or job edit is needed: eval builds lock `label: env.NODE_NAME`, so each worker only ever holds its own cores, and the next dispatcher counts the new worker's executors. Scale workers freely, but keep exactly one controller — lockable-resources state is per-controller.

Check the agent service:

| Linux | macOS |
|-------|-------|
| `systemctl --user status mac-k3d-jenkins-agent.service` | `launchctl print "gui/$(id -u)/com.mac-k3d.jenkins-agent" 2>&1 \| head -20` |
| Want **`Active: active (running)`**. If `status` pauses, press **`q`**. `setup` already turned on `loginctl enable-linger`, so the agent runs after you log out | Want **`state = running`**. The LaunchAgent runs while you are logged in, and restarts 5 seconds after it exits |

`config` also prints `Warning:` lines for anything on this host that would take the agent down (on a Mac: automatic login off, or sleep on). Fix those before you queue builds.

### 5. Confirm the node in Jenkins

In the Jenkins UI: **Manage Jenkins → Nodes** (or **Build Executor Status** on the left). The agent name from YAML (for example `linux-Michael-Ubuntu`) should be **online**.

Then this machine can take queued jobs when an executor and locks are free.

Do **not** run `mac-k3d start -c ~/.config/mac-k3d/worker.yaml` to start the agent. The agent is `setup`/`prepare` + token + `config`.

### Add another worker (Nth machine)

One **controller**. Each new Mac or Linux box is another **worker**. They do not join the controller’s k3d cluster.

On the new computer:

1. Download the matching Release asset (`mac-k3d-linux-x86_64`, `mac-k3d-linux-aarch64`, `mac-k3d-darwin-aarch64`, or `mac-k3d-darwin-x86_64`). No Rust.
2. `mac-k3d setup -c ~/.config/mac-k3d/worker.yaml` — role **CI worker**; let it install Docker, Java and git if asked (Harbor installs itself); Jenkins URL defaults to `http://127.0.0.1:17070` or type/export `http://<controller-ip>:17070`.
3. Use a **distinct** agent name. The wizard default is `{os}-<hostname>` (`linux-Michael-Ubuntu` on this PC, `macos-<hostname>` on a Mac).
4. Create or reuse a Jenkins API token; put `api_user` / `api_token` in that machine’s `worker.yaml`.
5. `mac-k3d config -c ~/.config/mac-k3d/worker.yaml` — node **online** in Jenkins.

---

## C. One PC, both roles

Two files: `config.yaml` (controller) and `worker.yaml` (worker). Run in this order. After Jenkins is up, create the node with **B** steps 2–5 (token, `worker.yaml`, worker `config`, Nodes UI).

```bash
# 1. Download the mac-k3d binary (section 1)

# 2. Controller
mac-k3d setup -c ~/.config/mac-k3d/config.yaml
# log in at http://localhost:17070  (admin + printed password)

# 3. Worker (URL default http://127.0.0.1:17070; remote: http://<controller-ip>:17070)
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
# put api_user / api_token in worker.yaml if prompted/empty, then:
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```

### After bootstrap — run an eval

On a machine that can reach Jenkins (usually the controller):

```bash
mac-k3d eval --n-tasks 1 --icode-mode release
# or one phase at a time (mac-k3d eval --stage env|tasks|evaluate|…): see docs/pipeline.md
```

Worker must have Docker (already from setup). Place a `*-full-*` drop under `~/.local/share/mac-k3d/` or use `ICODE_MODE=git`. Local paid stages use a gitignored `.env`; Jenkins E7 uses credential `deepseek-api-key`.

---

## Stop or remove (optional)

```bash
mac-k3d teardown -c ~/.config/mac-k3d/config.yaml
mac-k3d clean -c ~/.config/mac-k3d/worker.yaml --yes
mac-k3d clean -c ~/.config/mac-k3d/config.yaml --yes
```

---

## Mac mini worker

A Mac worker runs the same seven phases and writes the same files as a Linux worker ([pipeline.md](pipeline.md#linux-and-macos-workers)). The differences are in the host: Docker Desktop runs the containers in a VM, and the agent is a LaunchAgent that lives in your login session. Do these once, before `mac-k3d setup`:

1. **Automatic login:** System Settings > Users & Groups > Automatically log in as: the user that runs the agent. Without it, a restart leaves the agent and Docker Desktop down until someone logs in. FileVault must be off for automatic login.
2. **No sleep:**

   ```bash
   sudo pmset -a sleep 0
   pmset -g | grep -E '^ *sleep'    # want: sleep 0
   ```

3. **Docker Desktop** > Settings:
   - General: **Start Docker Desktop when you sign in**, and **Use Rosetta for x86_64/amd64 emulation on Apple Silicon** (DeepSWE's and SWE-bench Pro's published images are linux/amd64 only; the build refuses to start without it).
   - Resources: memory at least 18 GB (two 8 GB DeepSWE tasks plus 2 GB), CPUs as many as you will set `cpu_cores` to, and a disk image of about 200 GB.
   - Apply & restart.
4. **Download** `mac-k3d-darwin-aarch64` and clear quarantine as in [step 1](#1-download-the-binary).

Then follow [B. Worker](#b-worker--jenkins-agent). What `setup` and `config` do differently on the Mac:

| | Linux worker | Mac worker |
|-|--------------|------------|
| Memory the RAM check reads | `/proc/meminfo` | Docker Desktop's memory limit |
| Default `cpu_cores` | logical CPUs | the smaller of the Mac's CPUs and Docker Desktop's; a larger value is refused |
| Agent service | systemd user unit, kept after logout by linger | LaunchAgent, restarts 5 s after it exits; needs automatic login |
| `config` / `status` warnings | none | automatic login off; sleep not 0 |

iCode under test on a Mac:

| Mode | What the Mac needs |
|------|--------------------|
| release | A Linux drop for the task containers' CPU type: `icode-linux-x86_64-full-v*` for DeepSWE and SWE-bench Pro, `icode-linux-aarch64-full-v*` for LoLBench. Upload one `.tar.gz` holding both and the build picks the right one ([icode-harness-inputs.md](icode-harness-inputs.md#linux-and-macos-workers)) |
| git | Nothing extra. The iCode runtime is built inside a Linux container of the task platform (`ICODE_LINUX_BUILD_IMAGE`), because a macOS venv cannot run in a Linux container |

Before the first real run, do the parity trial in [testing/mac-mini-worker.md](testing/mac-mini-worker.md).

---

## If auto-install fails

Install Docker yourself, then re-run `mac-k3d setup`.

| Linux | macOS |
|-------|-------|
| `sudo apt-get install -y docker.io docker-compose-v2`, then log out and in so the `docker` group applies. `docker info` must show a Server section | Homebrew from [https://brew.sh](https://brew.sh), then `brew install --cask docker`, or download [Docker Desktop](https://www.docker.com/products/docker-desktop/); open it once |

Do **not** run `loginctl` or `usermod` on a Mac. Those are Linux-only.

---

## If a command fails

| Message | What to do |
|---------|------------|
| `mac-k3d: command not found` | Copy the release binary to `~/.local/bin/mac-k3d` and add that dir to PATH |
| `permission denied` on `docker.sock` / log out for docker | Linux: **log out and in**, then `mac-k3d setup` again |
| `docker info` fails / no Server | Linux: docker group + new login. macOS: open **Docker Desktop** and wait until it is idle |
| `loginctl: command not found` | macOS — ignore; use LaunchAgent after worker `config` |
| `agent.jar` / curl port 17070 / need token | Finish controller `start`, paste API token, then worker `config` |
| `Left jenkins_agent.api_user / api_token empty` / unit not started | Token missing (`api_token: ''`); fill it in `worker.yaml` and re-run worker `config` |
| `Ask an administrator to run these as root` | See [What root must run](#what-root-must-run) |
| `no config at …; run mac-k3d setup -c … first` | The `-c` file does not exist (check the path and the user name); run `setup` with that path first |
| `mac-k3d-jenkins-agent.service` could not be found | You ran `config` on **controller** YAML, or Jenkins was down / no token. Use `-c worker.yaml` after step 2 |
| `failed to bind host port … 8080` | Re-run `mac-k3d start` on a build that auto-remaps; or set free `cluster.ports` hosts. Keep Jenkins on `17070` unless remapped |
