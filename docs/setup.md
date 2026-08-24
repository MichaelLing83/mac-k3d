# Setup Guide

Step-by-step instructions for setting up `mac-k3d` on one or more Macs.

For topology and scheduling design, see [deployment.md](deployment.md). For config schema, see [configuration.md](configuration.md).

---

## Prerequisites

Each Mac needs:

| Requirement | Notes |
|-------------|-------|
| macOS 13+ | Apple Silicon or Intel |
| Docker Desktop | Container runtime |
| k3d | Cluster management |
| kubectl | Kubernetes CLI |
| Helm | Required only on the Jenkins controller Mac |

Install via Homebrew:

```bash
brew install --cask docker
brew install k3d kubectl helm
```

Open Docker Desktop once and accept the license. Ensure `docker info` succeeds before continuing.

---

## Install mac-k3d

From the repository:

```bash
git clone https://github.com/MichaelLing83/mac-k3d.git
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
2. Discovers already-installed tools (Docker Desktop, k3d, kubectl, helm) and asks whether to use them or install missing ones.
3. Asks for this Mac's role (standalone, CI controller, or CI worker).
4. Writes `~/.config/mac-k3d/config.yaml`.

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
  host_port: 9080
```

### 2. Start with Jenkins

```bash
mac-k3d start --jenkins in-cluster
```

### 3. Configure and get credentials

```bash
mac-k3d config --show-jenkins
```

Open `http://localhost:9080` and complete the Jenkins setup wizard.

### 4. Jenkins plugins

`mac-k3d start` installs **Lockable Resources** via Helm (`controller.additionalPlugins`) in addition to the chart defaults (Kubernetes, Pipeline, Git, Configuration as Code).

After first login you still create the actual `CPU_CORES` lock entries under **Manage Jenkins → Lockable Resources** (plugin is present; resources are operator-defined).

Optional extras (UI): Credentials Binding, etc.

### 5. Verify

```bash
mac-k3d status
kubectl get pods -n jenkins
```

---

## Scenario C: Multi-Mac (controller + workers)

Use this when Mac A runs Jenkins and Mac B/C run Jenkins agents with their own local k3d clusters.

See [deployment.md](deployment.md) for the logical topology and the physical LAN layout.

### Physical connection

If the Macs are in the same place and you have **one Internet RJ45**, connect them like this:

1. Put a **router** on the ISP/wall uplink (skip this if the ISP box already provides NAT/DHCP).
2. Put an **Ethernet switch** on the router LAN.
3. Plug **each Mac Mini's built-in RJ45** into the switch.

Do not daisy-chain the Minis or use macOS Internet Sharing. Give the controller a stable DHCP reservation or static IP so workers can reach Jenkins at `http://<controller-ip>:9080`.

### Overview

| Mac | Role | Jenkins | Cluster name (example) |
|-----|------|---------|------------------------|
| Mac A | Controller | enabled | `ci-controller` |
| Mac B | Worker | disabled | `ci-worker-b` |
| Mac C | Worker | disabled | `ci-worker-c` |

Each Mac is independent at the Kubernetes layer. Coordination happens through Jenkins.

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
  host_port: 9080

docker:
  startup_timeout_secs: 180
```

Start and configure:

```bash
mac-k3d start --jenkins in-cluster
mac-k3d config --show-jenkins
```

Complete the Jenkins setup wizard at `http://localhost:9080`.

#### Expose Jenkins to workers (remote access)

Workers on other Macs must reach the Jenkins controller. Choose one:

**Option 1 — Shared LAN (recommended when co-located)**

- Connect all Mac Minis through a router and Ethernet switch as in [Physical LAN](deployment.md#physical-lan-co-located-mac-minis).
- Use Mac A's LAN IP or hostname (e.g. `http://192.168.1.10:9080` or `http://mac-a.local:9080`).

**Option 2 — VPN / private overlay (recommended when not co-located)**

- Put all Macs on the same VPN or routed network.
- Use Mac A's VPN IP or hostname (e.g. `https://jenkins.internal:9080`).

**Option 3 — Port forward / reverse proxy**

- Forward port 9080 on Mac A to a stable public hostname with TLS.
- Use a reverse proxy (nginx, Caddy) with HTTPS in front of Jenkins.

**Option 4 — SSH tunnel (development only)**

On each worker Mac:

```bash
ssh -L 9080:localhost:9080 user@mac-a-hostname
```

Point the agent at `http://localhost:9080`.

> Do not expose an unsecured Jenkins instance on the public internet.

---

### Step 2: Set up Mac B (worker)

Repeat on each worker Mac.

```bash
mac-k3d prepare --init-config
```

Edit `~/.config/mac-k3d/config.yaml`:

```yaml
cluster:
  name: ci-worker-b
  agents: 0

jenkins:
  enabled: false

docker:
  startup_timeout_secs: 120
```

Start and configure:

```bash
mac-k3d start
mac-k3d config
mac-k3d status
```

---

### Step 3: Register worker agents in Jenkins

On Mac A (Jenkins UI):

1. **Manage Jenkins → Nodes → New Node**
2. Name: `mac-b` (match the label you will use in pipelines)
3. Type: **Permanent Agent**
4. Remote root directory: `/Users/<username>/jenkins-agent`
5. Labels: `mac-b`
6. Launch method: choose based on network:
   - **Launch agent via SSH** — if Mac A can SSH to Mac B
   - **Inbound agents** — worker connects out to controller (good for NAT/VPN)
7. Executors: start conservative (e.g. `2`)

Repeat for Mac C with name `mac-c` and label `mac-c`.

#### Inbound agent (worker connects to controller)

On Mac B, download the agent JAR from Jenkins:

**Manage Jenkins → Nodes → mac-b → Launch**

Run on Mac B:

```bash
java -jar agent.jar -url https://jenkins.example.com:9080/ -secret <SECRET> -name mac-b -webSocket
```

Use `-webSocket` when direct TCP from worker to controller is blocked by firewalls.

---

### Step 4: Configure lockable resources (back pressure)

On Mac A (Jenkins UI):

1. **Manage Jenkins → Lockable Resources Manager**
2. Create resources for Mac B:

| Name | Labels | # of resources |
|------|--------|----------------|
| macb-small-slot-1 | `macb-small` | (label count = 4) |
| macb-small-slot-2 | `macb-small` | |
| macb-small-slot-3 | `macb-small` | |
| macb-small-slot-4 | `macb-small` | |
| macb-medium-slot-1 | `macb-medium` | (label count = 2) |
| macb-medium-slot-2 | `macb-medium` | |
| macb-large-slot-1 | `macb-large` | (label count = 1) |

Alternatively, create resources with label `macb-small` and set **Reserved by label** count to 4.

See [deployment.md](deployment.md) for sizing guidance and Jenkinsfile examples.

---

### Step 5: Create a test pipeline

On Mac A, create a Pipeline job with:

```groovy
pipeline {
  agent { label 'mac-b' }
  stages {
    stage('Hello') {
      steps {
        lock(label: 'macb-small', quantity: 1) {
          sh 'echo "Running on Mac B" && hostname && docker info --format "{{.Name}}"'
        }
      }
    }
  }
}
```

Run the job and confirm it executes on Mac B.

---

### Step 6: Verify multi-Mac setup

| Check | Command / action |
|-------|------------------|
| Controller cluster | `mac-k3d status` on Mac A |
| Worker cluster | `mac-k3d status` on Mac B |
| Jenkins reachable from worker | `curl -k https://jenkins.example.com:9080/login` from Mac B |
| Agent online | Jenkins UI → Nodes → mac-b shows **online** |
| Job runs on worker | Trigger test pipeline; build log shows Mac B hostname |
| Back pressure works | Queue two `macb-large` jobs; second waits in queue |

---

## Daily operations

### Start environment (after reboot)

On each Mac:

```bash
mac-k3d start          # or: mac-k3d start --jenkins in-cluster on controller
mac-k3d config
```

Restart Jenkins agents if they do not auto-reconnect.

### Stop environment (end of day)

On each Mac:

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
| Worker agent offline | Network or agent process stopped | Check VPN/firewall; restart agent JAR |
| Jobs queue forever on Mac B | Lock slots exhausted or executors = 0 | Check Lockable Resources; increase slots or reduce load |
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

- Tune lockable resource slot counts — see [deployment.md](deployment.md)
- Customize cluster ports and agent count — see [configuration.md](configuration.md)
- Review CLI reference — see [commands.md](commands.md)
