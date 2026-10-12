# Setup Guide

Step-by-step instructions for setting up `mac-k3d` on **macOS or Linux** (Ubuntu/Debian recommended for Linux auto-install).

For topology and scheduling design, see [deployment.md](deployment.md). For config schema, see [configuration.md](configuration.md). For the interactive initializer, see [prepare-wizard.md](prepare-wizard.md).

---

## Prerequisites

**Users:** download a Release binary and run `mac-k3d setup`. The wizard installs Docker (and k3d/kubectl/helm/Java) when you choose **Install**. See [new-machine.md](new-machine.md).

If auto-install fails: Linux `sudo apt-get install -y docker.io` then log out/in; macOS Homebrew + `brew install --cask docker` (or Docker Desktop from docker.com) and open the app.

---

## Install mac-k3d

**Users:** download a GitHub Release binary (`mac-k3d-{os}-{arch}`), `chmod +x`, run it in Terminal (`mac-k3d setup`). See [new-machine.md](new-machine.md). Rust is not required.

**Developers** from the repository:

```bash
git clone https://github.com/Toby-Yu/mac-k3d.git
cd mac-k3d
cargo install --path .
```

Verify:

```bash
mac-k3d --help
```

---

## Scenario A: Single Mac (local k3d only)

Use this when you want a local Kubernetes cluster without Jenkins.

### 1. Prepare

```bash
mac-k3d prepare
```

On a terminal, this launches an **interactive wizard** that:

1. Picks a volume with the most free space and asks where to store large caches (Docker, k3d, Jenkins).
2. Asks for this Mac's role (standalone, CI controller, or CI worker).
3. Asks only about the tools that role uses (controller: Docker, k3d, kubectl, helm; worker: Docker and git, plus Java at the controller's major version and the pinned Harbor, which are not questions) and whether to use them or install missing ones.
4. Writes `~/.config/mac-k3d/config.yaml`, then installs what you chose. Anything that needs root and cannot get `sudo` is printed as one block for an administrator.

For scripting without prompts:

```bash
mac-k3d prepare --init-config
```

See [prepare-wizard.md](prepare-wizard.md) for the full questionnaire.

### 2. Start

```bash
mac-k3d start
```

Starts Docker Desktop (if needed), creates or starts the k3d cluster.

### 3. Configure

```bash
mac-k3d config
```

Merges kubeconfig and selects the k3d context.

### 4. Verify

```bash
mac-k3d status
kubectl get nodes
kubectl cluster-info
```

### 5. Stop (end of day)

```bash
mac-k3d teardown
```

To fully remove the cluster:

```bash
mac-k3d clean --yes
```

---

## Scenario B: Single Mac (Jenkins controller)

Use this when one Mac runs both the k3d cluster and Jenkins.

### 1. Edit config

```bash
mac-k3d prepare --init-config
```

Edit `~/.config/mac-k3d/config.yaml`:

```yaml
cluster:
  name: ci-controller
  agents: 0

jenkins:
  enabled: true
  host_port: 17070
```

### 2. Start with Jenkins

```bash
mac-k3d start --jenkins in-cluster
```

### 3. Configure and get credentials

```bash
mac-k3d config --show-jenkins
```

Open `http://localhost:17070` and complete the Jenkins setup wizard.

### 4. Jenkins plugins

`mac-k3d start` on the **controller** is the only plugin-install path. Helm `upgrade --install` with `overwritePlugins: true` adds these on top of the chart defaults (Kubernetes, Pipeline, Git, Configuration as Code):

- `lockable-resources` — per-worker `<agent>-core-N` capacity locks
- `plain-credentials` — Secret text for LLM keys and forge PATs
- `file-parameters` — `ICODE_RELEASE_FILE` upload onto the agent workspace
- `copyartifact` — the `some_task` / `full_suite_task` `Aggregate` stage copies each shard's `eval-runs/`
- `pipeline-utility-steps` — `nodesByLabel`, so a dispatcher plans at least one shard per online worker and stops at once when no online worker carries `AGENT_LABEL`
- `hidden-parameter` — `ui_profile: user` hides developer parameters while keeping their defaults

`mac-k3d config` rewrites job XML and credentials. It does not Helm-upgrade plugins. Workers never install plugins.

Per-core lock entries are created when a worker runs `mac-k3d config -c worker.yaml` (`<agent>-core-1..N`). Do not create them by hand under Manage Jenkins, and do not install extra plugins from the Jenkins UI.

### 5. Verify

```bash
mac-k3d status
kubectl get pods -n jenkins
```

---

## Scenario C: Multi-Mac (controller + workers)

Use this when Mac A runs Jenkins and Mac B/C run Jenkins agents. Workers host no k3d cluster: eval builds run Harbor containers on the worker's Docker.

See [deployment.md](deployment.md) for the logical topology and the physical LAN layout.

### Physical connection

If the Macs are in the same place and you have **one Internet RJ45**, connect them like this:

1. Put a **router** on the ISP/wall uplink (skip this if the ISP box already provides NAT/DHCP).
2. Put an **Ethernet switch** on the router LAN.
3. Plug **each Mac Mini's built-in RJ45** into the switch.

Do not daisy-chain the Minis or use macOS Internet Sharing. Give the controller a stable DHCP reservation or static IP so workers can reach Jenkins at `http://<controller-ip>:17070`.

### Overview

| Mac | Role | Config file | Runs |
|-----|------|-------------|------|
| Mac A | Controller | `config.yaml` | k3d cluster `ci-controller` with Jenkins |
| Mac B | Worker | `worker.yaml` | Jenkins agent, Docker, Java, git, Harbor 0.22.0 |
| Mac C | Worker | `worker.yaml` | same as Mac B |

Coordination happens through Jenkins; the workers are not Kubernetes nodes.

---

### Step 1: Set up Mac A (controller)

```bash
mac-k3d prepare --init-config
```

Edit `~/.config/mac-k3d/config.yaml`:

```yaml
cluster:
  name: ci-controller
  agents: 0

jenkins:
  enabled: true
  host_port: 17070

docker:
  startup_timeout_secs: 180
```

Start and configure:

```bash
mac-k3d start --jenkins in-cluster
mac-k3d config --show-jenkins
```

Complete the Jenkins setup wizard at `http://localhost:17070`.

#### Expose Jenkins to workers (remote access)

Workers on other Macs must reach the Jenkins controller. Choose one:

**Option 1 — Shared LAN (recommended when co-located)**

- Connect all Mac Minis through a router and Ethernet switch as in [Physical LAN](deployment.md#physical-lan-co-located-mac-minis).
- Use Mac A's LAN IP or hostname (e.g. `http://192.168.1.10:17070` or `http://mac-a.local:17070`).

**Option 2 — VPN / private overlay (recommended when not co-located)**

- Put all Macs on the same VPN or routed network.
- Use Mac A's VPN IP or hostname (e.g. `https://jenkins.internal:17070`).

**Option 3 — Port forward / reverse proxy**

- Forward port 17070 on Mac A to a stable public hostname with TLS.
- Use a reverse proxy (nginx, Caddy) with HTTPS in front of Jenkins.

**Option 4 — SSH tunnel (development only)**

On each worker Mac:

```bash
ssh -L 17070:localhost:17070 user@mac-a-hostname
```

Point the agent at `http://localhost:17070`.

> Do not expose an unsecured Jenkins instance on the public internet.

---

### Step 2: Set up Mac B (worker)

Repeat on each worker Mac. The worker wizard asks only what an agent needs:

```bash
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
# Role: CI worker
# Docker / git: Install if not found (Java 21+ and Harbor are installed at the pin, not asked)
# Jenkins controller URL: http://<mac-a-ip>:17070
# API user / token: admin + an API token from Mac A, or Enter to skip
```

Skipped the token? `worker.yaml` already has the keys, empty:

```yaml
jenkins_agent:
  api_user: ''
  api_token: ''
```

Fill them in, then:

```bash
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```

Without `sudo`, setup saves the answers and prints the root commands for an administrator (Java/git/Docker install, the `docker` group, `loginctl enable-linger` on Linux); afterwards re-run `setup` and choose **Use existing config**. Full walkthrough: [new-machine.md](new-machine.md#b-worker--jenkins-agent).

Never run `mac-k3d start -c worker.yaml`; it is rejected on purpose.

---

### Step 3: What `config` registers on Mac A

`mac-k3d config -c worker.yaml` is the only command that registers an agent. Using the API token it:

1. Creates or updates the node on Mac A: executors = `cpu_cores / cpus_per_executor`, the agent labels (default `macos docker lolbench` / `linux docker lolbench`) plus `fits-<suite>` and `executors-<n>`, launch method **Inbound**.
2. Creates the lockable resources `<agent>-core-1..N` (one per logical core), labelled with the shared `CPU_CORES` label and the agent name.
3. Writes `launch-agent.sh` and starts the agent service (LaunchAgent `com.mac-k3d.jenkins-agent` on macOS, systemd user unit `mac-k3d-jenkins-agent.service` on Linux).

Do not create nodes or lock entries by hand. Each eval build locks every core of its own node, only for its Evaluate stage ([deployment.md](deployment.md#what-the-eval-jobs-actually-do)).

---

### Step 4: Verify multi-Mac setup

| Check | Command / action |
|-------|------------------|
| Controller cluster | `mac-k3d status` on Mac A |
| Jenkins reachable from worker | `curl -sS -o /dev/null -w '%{http_code}\n' http://<mac-a-ip>:17070/login` from Mac B |
| Agent online | Jenkins UI → Nodes → the agent name shows **online** with 1 executor |
| Worker toolchain | `harbor --version` on Mac B prints the pin (0.22.0); `REQUIRE_WORKER=1 ./scripts/env_set_up/03_check_worker.sh` |
| Job runs on worker | Run `deepswe_one_task`; the console says `Running on <agent>` and shows seven stages with the lock only around Evaluate |

---

## Daily operations

### Start environment (after reboot)

On the controller:

```bash
mac-k3d start --jenkins in-cluster
mac-k3d config
```

Workers need no command: the agent service starts at login (Linux: at boot, with linger). If one stays offline, run `mac-k3d config -c ~/.config/mac-k3d/worker.yaml` on it.

### Stop environment (end of day)

On the controller:

```bash
mac-k3d teardown
```

Optionally quit Docker Desktop:

```bash
mac-k3d teardown --stop-docker
```

### Check status

```bash
mac-k3d status
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `docker info` fails | Docker Desktop not running | Open Docker Desktop; re-run `mac-k3d start` |
| k3d cluster not found | First run or after `clean` | `mac-k3d start` recreates it |
| Jenkins pod not ready | Helm install still rolling out | `kubectl get pods -n jenkins -w` |
| Worker agent offline | Network, blank API keys, or agent process stopped | Check VPN/firewall; fill `api_user` / `api_token` in `worker.yaml`; `mac-k3d config -c worker.yaml` |
| Agent log: `UnsupportedClassVersionError` (class file version 65 vs 61) or "Connection was broken"; node offline although the unit is active | The worker's Java is older than the controller's (`JAVA_MAJOR`, 21) | `mac-k3d setup -c worker.yaml` → **Use existing config**: switches to a Java 21 already on the machine, or installs `openjdk-21-jre-headless` (`temurin@21` on macOS; an administrator block without sudo). `mac-k3d config` prints `java 21: ok` when fixed |
| Jobs queue forever on Mac B | Agent offline, or no `<agent>-core-N` resources | Check Nodes and Lockable Resources; re-run `mac-k3d config -c worker.yaml` |
| Port already in use | Conflicting service on host port | Change `jenkins.host_port` or `cluster.ports` in config |

Enable debug logging:

```bash
RUST_LOG=debug mac-k3d -vv start
```

---

## Cleanup

Remove cluster and state on a single Mac:

```bash
mac-k3d clean --yes
```

Also remove config:

```bash
mac-k3d clean --yes --purge-config
```

On Jenkins controller, delete worker nodes before decommissioning worker Macs.

---

## Next steps

- How builds share workers — see [deployment.md](deployment.md)
- Customize cluster ports and agent count — see [configuration.md](configuration.md)
- Review CLI reference — see [commands.md](commands.md)
