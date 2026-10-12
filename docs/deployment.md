# Deployment and Setup Model

## Scope

This document defines how `mac-k3d` environments are intended to be deployed across one or more Macs.

## Core principle

Each Mac is a self-contained environment:

- Docker Desktop runtime is local.
- k3d cluster is local.
- `kubectl` context is local.

`mac-k3d` does not attempt to create one Kubernetes cluster spanning multiple Macs.

## Roles

### Controller role (Jenkins enabled)

- One Mac runs Jenkins inside its local k3d cluster.
- Jenkins is the CI orchestration point (pipeline definitions, queue, **credentials**, plugins). LLM/Git secrets are configured once on the controller and injected into builds on every agent — see [secrets.md](secrets.md).
- This controller can coordinate builds that run on other Macs.

### Worker role (Jenkins disabled)

- Worker Macs host neither Jenkins nor a k3d cluster (`mac-k3d start -c worker.yaml` is rejected).
- They run Docker, Java, git, the pinned Harbor and the Jenkins agent; eval builds run Harbor containers on the worker's Docker.
- `mac-k3d setup -c worker.yaml` installs that toolchain; `mac-k3d config -c worker.yaml` registers the agent.

## Multi-Mac topology

```text
                  (LAN / VPN / routed network / internet)
+------------------------+      +------------------------+
| Mac A (controller)     |      | Mac B (worker)         |
| - Docker Desktop       |      | - Docker Desktop       |
| - k3d cluster A        |      | - Harbor + iCode runs  |
| - Jenkins in cluster A |<-----| - Jenkins agent        |
+------------------------+      +------------------------+
             ^
             |
             +----------------+------------------------+
                              |
                    +------------------------+
                    | Mac C (worker)         |
                    | - Docker Desktop       |
                    | - Harbor + iCode runs  |
                    | - Jenkins agent        |
                    +------------------------+
```

Important: Mac B and Mac C are not Kubernetes nodes in cluster A. They are separate machines coordinated at the CI layer.

## Physical LAN (co-located Mac Minis)

When several Mac Minis share **one Internet Ethernet drop**, put them on a shared switched LAN. Do not daisy-chain the Minis, and do not use macOS Internet Sharing.

Each current Mac Mini already has its own RJ45. The constraint is the single uplink, not a missing port on each Mini.

```text
ISP / wall RJ45  (one uplink)
        |
        v
   Router               ← NAT, DHCP, firewall
        |                 (skip if the ISP box already NATs)
        v
   Ethernet switch      ← unmanaged 5/8/16-port, gigabit or faster
        |        |        |
        v        v        v
   Mac Mini A  Mac Mini B  Mac Mini C
   controller  worker      worker
```

| Situation | What to buy |
|-----------|-------------|
| ISP box is already a router with one free LAN port | Ethernet switch only |
| ISP box is a modem/ONT with a single public IP | Router first, then a switch if the router does not have enough LAN ports |

After they are on the same LAN:

- Give the controller a **DHCP reservation or static IP** (and a hostname if DNS is available).
- Point workers at `http://<controller-ip>:<jenkins.host_port>` (HTTPS in front if exposed beyond the LAN).
- Prefer **inbound Jenkins agents** (worker connects out to the controller).
- Use Wi-Fi only as a fallback. Wired is the intended path for CI.

A gigabit switch is enough for Jenkins agent traffic. Use 2.5G/10G only if the Minis have matching Ethernet and you need fast LAN copies. Internet speed is still limited by the single uplink.

**Do not:** daisy-chain Minis, share Internet from one Mini to the others, or expose unsecured Jenkins on the public internet.

## Network assumptions

- Co-located Macs share a switched LAN as described above.
- Workers can reach the Jenkins controller endpoint.
- DNS or static host mapping resolves the controller.
- Firewall/NAT allows agent traffic.
- TLS is enabled for remote access.

This can also work outside LAN (for example over VPN or public network) if security and routing are configured. For machines in the same room, prefer the physical LAN over VPN.

## Jenkins scheduling and back pressure on worker Macs

When Mac A is Jenkins controller and Mac B is a Jenkins worker, Jenkins does not automatically infer real-time Docker/k3d CPU and memory pressure on Mac B. Back pressure is created by explicit scheduler constraints.

### Control layers

1. **Node executors**: hard cap of concurrent jobs Jenkins may run on Mac B.
2. **Node labels**: route only compatible jobs to Mac B.
3. **Lockable resources**: admission control for heavy jobs based on declared capacity units.

Use all three. Executors provide coarse safety; locks provide workload-aware scheduling.

### What the eval jobs actually do

`mac-k3d config -c worker.yaml` (which `setup` runs) implements this for evaluation: it sets `numExecutors` to `cpu_cores / cpus_per_executor` and creates lockable resources `<agent>-core-1..cpu_cores`, each labelled with the agent name. Each `one_task` build then locks the CPUs its question declares:

```groovy
lock(label: env.NODE_NAME, quantity: cpusEach, variable: 'HELD_CORES')
```

Locking the node name rather than the shared label is what makes multi-worker safe: with a shared label, a build running on Mac B could hold tokens that represent Mac C's cores, and both machines would oversubscribe. With no quantity the build holds all of its node's cores, and their count sets Harbor's `-n`. One executor matters too: Jenkins' default load balancer prefers the same node for every build of one job, so with several executors per node the shards of one suite would pile onto one worker. The lock wraps only the `Evaluate` stage, one of the seven pipeline stages ([pipeline.md](pipeline.md)).

**Keep exactly one controller.** Lockable-resources state lives on the controller, so a second controller would hand out tokens for cores the first one already lent out. Scale workers, not controllers. Adding a worker needs no pipeline change: the dispatcher counts online workers when it plans shards, the queue hands each shard to the first free worker, and `env.NODE_NAME` resolves per build. See [architecture.md](architecture.md#evaluation-architecture).

### Recommended lock design: capacity slots

Prefer **slot classes** over separate CPU and memory token locks to avoid lock-order deadlocks.

Define lock labels on Mac B such as:

- `macb-small` (for small jobs)
- `macb-medium` (for medium jobs)
- `macb-large` (for large jobs)

Then associate each job type with one slot label and quantity:

| Job class | Typical footprint (example) | Lock label | Quantity |
|-----------|------------------------------|------------|----------|
| small | 1 CPU, 2 GB RAM | `macb-small` | 1 |
| medium | 2 CPU, 4 GB RAM | `macb-medium` | 1 |
| large | 4 CPU, 8 GB RAM | `macb-large` | 1 |

Suggested initial sizing for a 10-core / 32 GB Mac B:

| Slot label | Count | Notes |
|------------|-------|-------|
| `macb-small` | 4 | high-throughput small tasks |
| `macb-medium` | 2 | balanced CI jobs |
| `macb-large` | 1 | heavyweight builds/tests |

If a required slot is unavailable, Jenkins queues the job. This queueing is the intended back-pressure mechanism.

### Jenkins pipeline usage

Small job:

```groovy
pipeline {
  agent { label 'mac-b' }
  stages {
    stage('Build') {
      steps {
        lock(label: 'macb-small', quantity: 1) {
          sh './ci/small-build.sh'
        }
      }
    }
  }
}
```

Large job:

```groovy
pipeline {
  agent { label 'mac-b' }
  stages {
    stage('Integration Test') {
      steps {
        lock(label: 'macb-large', quantity: 1) {
          sh './ci/large-integration.sh'
        }
      }
    }
  }
}
```

### Tuning loop

1. Start with conservative slot counts and low executor count.
2. Observe queue wait time, host CPU saturation, memory pressure, and swap.
3. Increase slot counts only when latency is high and host pressure is low.
4. Decrease slot counts immediately if OOM, swap thrash, or severe build instability appears.

### Optional future automation

Future versions may generate Jenkins lock definitions from `mac-k3d` config and classify jobs automatically, but v1 treats this as an operator-managed Jenkins design.

## Recommended operations flow

See [setup.md](setup.md) for detailed step-by-step instructions. Summary:

1. Controller Mac: `mac-k3d setup -c ~/.config/mac-k3d/config.yaml` (wizard, `start`, `config`).
2. Each worker Mac: `mac-k3d setup -c ~/.config/mac-k3d/worker.yaml` (wizard, installs, `config`). Fill `api_user` / `api_token` in `worker.yaml` if you skipped them, then `mac-k3d config -c ~/.config/mac-k3d/worker.yaml`.
3. After each commit: `bash scripts/redeploy.sh --controller <user>@<controller> --worker <user>@<worker>` puts the same binary everywhere ([workflow.md](workflow.md#development-loop-commit-push-redeploy)).

## Out of scope for v1

- Automatic public endpoint/TLS provisioning
- Building a single multi-host Kubernetes control plane using k3d
