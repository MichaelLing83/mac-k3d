# Historical v0.3.0: initializer testing (Mac + Linux)

Not the product path. Current bootstrap sign-off: [testing/testing-binary-initializer.md](testing/testing-binary-initializer.md). iCode git/release: [user-guide.md](user-guide.md) and [icode-harness-inputs.md](icode-harness-inputs.md).

This page is **v0.3.0 only** (`cargo install`, `prepare`). Step numbers **0–7** stay stable for machines still on that path.

If you still need the v0.3 cargo bootstrap (not a current Release install), the matching user copy is [initializer-new-machine.md](initializer-new-machine.md).

Related design: [setup.md](setup.md#wizard-questions), [commands.md](commands.md).

---

## What the initializer is for

`mac-k3d` is **not** a multi-node Kubernetes farm. It bootstraps **CI machines** for LoLBench evals:


| Role                         | Job                                                   | How it is achieved                                              |
| ---------------------------- | ----------------------------------------------------- | --------------------------------------------------------------- |
| **CLI on PATH**              | Same binary on macOS and Linux                        | `cargo install --path .` → `~/.cargo/bin/mac-k3d`               |
| **Platform gate**            | Linux and macOS allowed; other OS rejected            | `prepare --init-config` (Step 1)                                |
| **Validate without prompts** | Fleet / CI path                                       | `prepare --non-interactive` (Step 2; fresh YAML `exit=1` is OK) |
| **Worker host**              | Eval box: Docker, Harbor, Java, LoLBench, agent files | `prepare -i -c worker.yaml`, role **CI worker** (Step 3)        |
| **Controller host**          | Jenkins in local k3d                                  | `prepare -i -c config.yaml`, role **CI controller** (Step 4)    |
| **Dual config**              | One binary, two YAML files, no clobber                | Step 5                                                          |
| **Disk hard-fail**           | Under-disk must fail clearly                          | `--disk-min-gb 99999` (Step 6)                                  |
| **Runtime**                  | Docker + k3d + Jenkins UI                             | `mac-k3d start` then `status` (Step 7A)                         |
| **Jenkins agent online**     | Node + `agent.jar` + systemd/LaunchAgent              | Jenkins token + `mac-k3d config -c worker.yaml` (Step 7B)       |


Lifecycle: `prepare` **→** `start` **(controller only) →** `config` **(agent) →** `status` **/** `teardown` **/** `clean`.

Workers do **not** join the controller’s k3d cluster. They run a **Jenkins inbound agent** and execute Harbor/LoLBench on **host Docker**.

```text
Controller:  Docker → k3d → Jenkins :17070  (job lolbench_one_task)
Worker:      Docker + Harbor + Java + agent.jar  → connects to Jenkins URL
```

On **one lab PC** you can run **both** roles with two files:

- `~/.config/mac-k3d/config.yaml` — `role: controller`
- `~/.config/mac-k3d/worker.yaml` — `role: worker`

---



## Linux lab (proven)

Ubuntu 26.04, account **Toby**, 2026-09-09. Goal: one CLI initializes Linux the same way as macOS, then Jenkins runs in k3d and the same host can become an agent.


| Step        | Result                                                                                                                                                                                        |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0           | `cargo test` 35 passed; `cargo install --path .`; `which mac-k3d` → `~/.cargo/bin/mac-k3d`                                                                                                    |
| 1           | `--init-config` `exit=0` twice                                                                                                                                                                |
| 2           | `--non-interactive` `exit=1` “marked for install” (expected)                                                                                                                                  |
| 3           | Worker prepare `exit=0`; Harbor via uv; LoLBench cloned; `validate_exit=0`; `role: worker` `platform: linux`                                                                                  |
| 3 agent.jar | `curl` to `:17070` failed — **warning only**; config + `launch-agent.sh` saved                                                                                                                 |
| 4           | Controller prepare `exit=0`; `validate_exit=0`; `role: controller`                                                                                                                            |
| 5           | both validates `0`; roles stay controller + worker                                                                                                                                            |
| 6           | `exit=1` free-space error at 99999 GB                                                                                                                                                         |
| 7A          | Remapped host `8080` → `18080` (host Jenkins already on 8080); `start_exit=0`; cluster `ci-controller` running; Jenkins pod Running; `http://localhost:17070`; job `lolbench_one_task` created |
| 7B          | 2026-09-10: API token in `worker.yaml`; `config -c worker.yaml`; node `mac-Michael-Ubuntu` registered; 16 `CPU_CORES` locks; `mac-k3d-jenkins-agent.service` **active (running)** |


**Linux extras that blocked 7A until fixed:** user in group `docker`, then a **full new login** (old `systemd --user` kept stale groups). `docker info` must show a **Server** section. `newgrp`/`sg` may be missing (`util-linux-extra`).

Initializer **sign-off** is Steps **0–6**. Step 7 is runtime. Step **7B** (agent daemon) needs a Jenkins API token after UI login.

---



## Bring up a Jenkins agent (Linux or macOS)

Use this when the goal is “this computer should run LoLBench jobs,” not only unit-test the wizard.

### A. Worker-only machine (Jenkins already exists)

Controller URL must be reachable (example: `http://jenkins-host:17070` or `http://localhost:17070` on a lab PC).

1. **OS access to Docker**
  - **Linux:** `sudo usermod -aG docker "$USER"`, then **log out and log in** (quit lingering user systemd / Cursor if `groups` still lacks `docker`). Check `groups | grep docker` and `docker info` (Server section). `loginctl enable-linger "$USER"` once.
  - **macOS:** Docker Desktop running; `docker info` works.
2. **Install CLI** (Rust/cargo via rustup):

```bash
cd /path/to/mac-k3d
cargo install --path .
. "$HOME/.cargo/env"   # if `mac-k3d` is not found
which mac-k3d
```

1. **Interactive worker prepare**

```bash
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
```


| Prompt                        | Choose                                                              |
| ----------------------------- | ------------------------------------------------------------------- |
| Storage base                  | Volume with space (worker default **100 GB**); e.g. `~/mac-k3d`     |
| Role                          | **CI worker (Jenkins agent only)**                                  |
| docker / k3d / kubectl / java | **Use this installation** if found                                  |
| Harbor                        | Install (uv/pipx) or existing — needed for LoLBench                 |
| LoLBench                      | Clone or existing checkout                                          |
| Jenkins URL                   | Controller base URL (wizard default `http://43.107.42.252:17070`; type `http://localhost:17070` for same-PC) |
| API user / token              | Fill in if Jenkins is already up; **empty** if not (register later) |
| Labels                        | `linux docker lolbench` or `macos docker lolbench`                  |
| Write config                  | **Yes**                                                             |


If Jenkins is down: `agent.jar` download **warns**; `exit=0` and YAML still save. That is OK.

1. **Validate**

```bash
grep -E '^(role|platform):' ~/.config/mac-k3d/worker.yaml
mac-k3d prepare --non-interactive -c ~/.config/mac-k3d/worker.yaml
# expect role: worker; platform: linux|macos; exit=0
```

1. **When Jenkins is up** — create a token and start the agent. Full clicks and YAML: [Step 7B](#7b--start-the-jenkins-agent-and-create-the-jenkins-node) and [initializer-new-machine.md](initializer-new-machine.md) section B.

```bash
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```

**Linux:**

```bash
systemctl --user status mac-k3d-jenkins-agent.service
loginctl enable-linger "$USER"
```

**macOS:**

```bash
launchctl print "gui/$(id -u)/com.mac-k3d.jenkins-agent" 2>&1 | head -20
```

Do **not** `mac-k3d start -c worker.yaml` to “start the agent.” `start` is for a **local k3d cluster**. The agent is `config` + the user service above.

### B. One-machine lab (controller + agent on the same PC)

1. Complete **A** through worker `prepare` (API user empty is fine).
2. Controller prepare (does not overwrite `worker.yaml`):

```bash
mac-k3d prepare -i -c ~/.config/mac-k3d/config.yaml
# Role: CI controller; use existing tools; helm required; Jenkins port 17070
# Skip CI secrets for a smoke test
```

1. If something already listens on **8080**, change `cluster.ports` host `8080` → e.g. `18080`. Keep `jenkins.host_port: 17070`.
2. Start Jenkins:

```bash
mac-k3d start -c ~/.config/mac-k3d/config.yaml
mac-k3d status -c ~/.config/mac-k3d/config.yaml
mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins
```

Open `http://localhost:17070`. **User:** `admin`. **Password:** printed by `--show-jenkins` (cluster secret).

1. Create an API token and run worker `config` as in [7B](#7b--start-the-jenkins-agent-and-create-the-jenkins-node).

---



## Important: what is *not* a bug

`prepare --init-config` writes a **default** YAML where dependencies are still `source: install` (not yet installed).

So this is **expected** and means Step 2 **passed** the OS / validate path:

```text
ERROR docker is marked for install; run `mac-k3d prepare` to install
ERROR k3d is marked for install; ...
exit=1
```


| Outcome                                            | Meaning                                                                            |
| -------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `exit=1` + “marked for install” / missing binary   | **PASS** for Step 2 (validate ran; tools not ready yet)                            |
| `exit=1` + `macOS required` / unsupported platform | **FAIL** (platform gate broken)                                                    |
| `exit=0`                                           | Only after a real interactive prepare (or hand-edited config) made deps `existing` |


**Fix to proceed:** run **Step 3** interactive prepare. Do not treat Step 2’s `exit=1` as a blocker.

---



## Step 0 — Install CLI onto PATH

```bash
cd /path/to/mac-k3d
cargo install --path .
which mac-k3d
mac-k3d --help
```

| Purpose | Put `mac-k3d` in `~/.cargo/bin` so the command works by name |
| Expect | `which` shows `.../.cargo/bin/mac-k3d`; help says macOS and Linux |

`cargo build --release` alone is **not** enough for `mac-k3d` on PATH — that only builds `./target/release/mac-k3d`.

Optional while developing:

```bash
cargo test
cargo install --path .
```

---



## Step 1 — Platform gate

```bash
mac-k3d prepare --init-config -c /tmp/mac-k3d-gate-test.yaml
echo "exit=$?"

mac-k3d prepare --init-config -c /tmp/mac-k3d-gate-test.yaml
echo "exit=$?"
```

| Purpose | Prove Linux/macOS is allowed; write default config; second run is idempotent |
| Expect | `exit=0` both times (no `macOS required`) |

---



## Step 2 — Non-interactive validate (no wizard)

```bash
mac-k3d prepare --non-interactive -c /tmp/mac-k3d-gate-test.yaml
echo "exit=$?"
```

| Purpose | Fleet/CI path: validate without prompts |
| Expect (fresh init-config) | `exit=1` with “docker/k3d/kubectl marked for install” — **this is OK** |
| Fail | Any unsupported-platform / macOS-only error |

After Step 3 succeeds on a real config file, re-run non-interactive on **that** file and expect `exit=0`.

---



## Step 3 — Interactive worker prepare (main eval-box test)

Needs a reachable Jenkins controller only if you register an agent (URL + API token) in this run.

```bash
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
echo "exit=$?"
```

| Purpose | Full initializer: storage, role=worker, Docker/Harbor/Java, LoLBench, agent files, disk check |
| Expect | Wizard finishes; `exit=0`; “Setup complete” |

Then:

```bash
grep -E '^(role|platform):' ~/.config/mac-k3d/worker.yaml
mac-k3d prepare --non-interactive -c ~/.config/mac-k3d/worker.yaml
echo "validate_exit=$?"
```

| Purpose | Confirm YAML + validate now passes |
| Expect | `role: worker`; `platform: linux` or `macos`; `validate_exit=0` |

Agent daemon (only after token + `mac-k3d config`): see [Bring up a Jenkins agent](#bring-up-a-jenkins-agent-linux-or-macos).

---



## Step 4 — Interactive controller prepare

```bash
mac-k3d prepare -i -c ~/.config/mac-k3d/config.yaml
echo "exit=$?"
grep -E '^(role|platform):' ~/.config/mac-k3d/config.yaml
mac-k3d prepare --non-interactive -c ~/.config/mac-k3d/config.yaml
echo "validate_exit=$?"
```

| Purpose | Same CLI initializes controller role |
| Expect | `role: controller`; `validate_exit=0` |

---



## Step 5 — Dual config on one host

```bash
mac-k3d prepare --non-interactive -c ~/.config/mac-k3d/config.yaml
echo "controller_validate=$?"
mac-k3d prepare --non-interactive -c ~/.config/mac-k3d/worker.yaml
echo "worker_validate=$?"
grep '^role:' ~/.config/mac-k3d/config.yaml ~/.config/mac-k3d/worker.yaml
```

| Purpose | One binary, two `-c` files, no clobber |
| Expect | both validates 0; roles controller + worker |

---



## Step 6 — Disk hard-fail

```bash
mac-k3d prepare --non-interactive --disk-min-gb 99999 -c ~/.config/mac-k3d/worker.yaml
echo "exit=$?"
```

| Purpose | Under-disk must fail clearly on both OS volume scanners |
| Expect | `exit=1`, free-space error (not platform error) |

---



## Step 7 — Controller start + optional agent (secondary)

Only after Step 4 and Docker/k3d/helm are usable (`docker info` shows Server).

### 7A — Start cluster and Jenkins

If host port **8080** is taken, remap `cluster.ports` `host: 8080` → e.g. `18080`. Keep Jenkins on **17070**.

```bash
mac-k3d start -c ~/.config/mac-k3d/config.yaml
echo "start_exit=$?"
mac-k3d status -c ~/.config/mac-k3d/config.yaml
mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins
```

| Purpose | Prepare-produced config drives Docker + k3d + Helm Jenkins |
| Expect | `start_exit=0`; cluster running; Jenkins UI `http://localhost:17070`; login **admin** + printed password |

Not required to accept the **initializer** itself (Steps 0–6).

### 7B — Start the Jenkins agent and create the Jenkins node

After **7A**, Jenkins is up. The agent service does **not** start from controller `config --show-jenkins`. You need a Jenkins **API token** in **`worker.yaml`**, then `mac-k3d config -c worker.yaml`.

The `--show-jenkins` password is for the **UI only**. It is not the API token. Token + agent start does **not** bill an LLM; a later `HARNESS` other than `oracle` with a provider key can.

#### Token (Jenkins UI)

1. Open `http://localhost:17070` (or the controller URL).
2. Log in **admin** + password from `--show-jenkins`.
3. **admin** (top right) → **Configure** → **API Token** → **Add new Token** → **Generate**.
4. Copy the token once.

`api_user` is `admin`. Do not commit `worker.yaml` after this.

#### Put token in worker YAML

Under `jenkins_agent:` in `~/.config/mac-k3d/worker.yaml`:

```yaml
  api_user: admin
  api_token: PASTE_THE_TOKEN_HERE
```

Keep `controller_url` (wizard default `http://43.107.42.252:17070`; same-PC lab: `http://localhost:17070`). `nano`: **Ctrl+O**, **Enter**, **Ctrl+X**. Quote the token if it contains `#` or spaces.

Or re-run `mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml` and paste user + token when asked.

#### Register node + start daemon

```bash
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```

| Purpose | Create Jenkins node, download `agent.jar`, start systemd (Linux) or LaunchAgent (macOS) |
| Expect | `Config complete`; `Retrieved agent connection secret`; `Jenkins agent systemd user unit started` (Linux) or LaunchAgent started (macOS) |
| OK noise | `k3d cluster 'ci-worker' not present — skipping kubeconfig` — workers are not a k3d cluster |
| Fail | `REPLACE_ME` / unit not found — token still `null`, or you used `-c config.yaml` |

Then:

**Linux**

```bash
loginctl enable-linger "$USER"
systemctl --user status mac-k3d-jenkins-agent.service
# press q to leave the pager
```

Expect **`Active: active (running)`** (Java `-jar …/agent.jar -url http://localhost:17070`).

**macOS**

```bash
launchctl print "gui/$(id -u)/com.mac-k3d.jenkins-agent" 2>&1 | head -20
```

#### Confirm the node in Jenkins

**Manage Jenkins → Nodes** (or **Build Executor Status**). The name from YAML (e.g. `linux-Michael-Ubuntu`) is **online**.

Optional job (no model cost): **lolbench_one_task** → **Build with Parameters** → **HARNESS** `oracle`, **TASK** `ruff_1`.

User-facing copy of this flow: [initializer-new-machine.md](initializer-new-machine.md) section B.

---



## Cleanup

```bash
mac-k3d clean -c ~/.config/mac-k3d/worker.yaml --yes
mac-k3d clean -c ~/.config/mac-k3d/config.yaml --yes
rm -f /tmp/mac-k3d-gate-test.yaml
```

---



## Progress checklist

Copy and tick as you go:

- [x] Step 0 — `which mac-k3d` works
- [x] Step 1 — `--init-config` exit 0 (twice)
- [x] Step 2 — `--non-interactive` on gate file: exit 1 with “marked for install” (OK)
- [x] Step 3 — interactive worker prepare + validate_exit 0
- [x] Step 3 — agent daemon running (only if token set / after 7B)
- [x] Step 4 — controller prepare + validate_exit 0
- [x] Step 5 — dual-config validates
- [x] Step 6 — disk hard-fail exit 1
- [x] Step 7A — (optional) start/status / Jenkins UI
- [x] Step 7B — (optional) `config -c worker.yaml` + agent service + node online

---



## Troubleshooting



### `mac-k3d-jenkins-agent.service` could not be found / LaunchAgent missing

**Cause:** Worker `prepare` skipped the daemon because `agent.jar` could not download, **or** you ran `config` on the **controller** file (`config.yaml`), **or** `api_token` is still `null` (`REPLACE_ME` in `{remote_fs}/.agent-secret`).

**Fix:** Finish 7A, put `api_user` / `api_token` in `worker.yaml`, then:

```bash
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
systemctl --user status mac-k3d-jenkins-agent.service   # Linux
```

Linger (`loginctl enable-linger`) does **not** create the unit; it only keeps it after logout.

### `curl: (7) Failed to connect to localhost port 17070` / `agent.jar`

**Cause:** Worker prepare tries to download `agent.jar` from the Jenkins controller. Nothing is listening on `:17070` yet (controller not started).

**What still succeeded:** Config is saved; launch script may be written. Harbor / LoLBench may already be on disk.

**Fix:** Finish Docker group + `docker info`, `start` the **controller**, then:

```bash
mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```



### Linux: `docker info` → permission denied on `/var/run/docker.sock`

Account must be in group `docker` (`getent group docker`). A new **login session** must include that group (`id` shows `docker`).

If you logged out but Cursor/`systemd --user` still has old groups: save work, `systemctl --user exit` (or `loginctl terminate-user "$USER"`), sign in again, check `docker info` **before** starting Cursor. `newgrp`/`sg` may be absent.

### `failed to bind host port 0.0.0.0:8080`

Another process (often host Jenkins) owns 8080. Remap k3d `8080` → `18080` in controller YAML. Project Jenkins stays on **17070**.

### `E: Unable to locate package kubectl`

Stock Ubuntu apt often has no `kubectl` package. Prepare falls back to the official curl binary — `which kubectl` (`/usr/local/bin/kubectl` is fine).