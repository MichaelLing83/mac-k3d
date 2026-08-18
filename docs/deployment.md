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
- Jenkins is the CI orchestration point (pipeline definitions, queue, credentials, plugins).
- This controller can coordinate builds that run on other Macs.

### Worker role (Jenkins disabled)

- Worker Macs do not host Jenkins.
- They run local toolchains and can execute CI jobs as Jenkins agents.
- They may still run local k3d clusters for test/development workloads.

## Multi-Mac topology

```text
                  (VPN / routed network / internet)
+------------------------+      +------------------------+
| Mac A (controller)     |      | Mac B (worker)         |
| - Docker Desktop       |      | - Docker Desktop       |
| - k3d cluster A        |      | - k3d cluster B        |
| - Jenkins in cluster A |<-----| - Jenkins agent        |
+------------------------+      +------------------------+
             ^
             |
             +----------------+------------------------+
                              |
                    +------------------------+
                    | Mac C (worker)         |
                    | - Docker Desktop       |
                    | - k3d cluster C        |
                    | - Jenkins agent        |
                    +------------------------+
```

Important: Mac B and Mac C are not Kubernetes nodes in cluster A. They are separate machines coordinated at the CI layer.

## Network assumptions

- Workers can reach the Jenkins controller endpoint.
- DNS or static host mapping resolves the controller.
- Firewall/NAT allows agent traffic.
- TLS is enabled for remote access.

This can work outside LAN (for example over VPN or public network) if security and routing are configured.

## Jenkins scheduling and back pressure on worker Macs

When Mac A is Jenkins controller and Mac B is a Jenkins worker, Jenkins does not automatically infer real-time Docker/k3d CPU and memory pressure on Mac B. Back pressure is created by explicit scheduler constraints.

### Control layers

1. **Node executors**: hard cap of concurrent jobs Jenkins may run on Mac B.
2. **Node labels**: route only compatible jobs to Mac B.
3. **Lockable resources**: admission control for heavy jobs based on declared capacity units.

Use all three. Executors provide coarse safety; locks provide workload-aware scheduling.

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

1. Initialize every Mac with `mac-k3d prepare --init-config`.
2. Start controller Mac with `mac-k3d start --jenkins in-cluster`.
3. Start worker Macs with `mac-k3d start`.
4. Configure all Macs with `mac-k3d config`.
5. Register workers in Jenkins.

## Out of scope for v1

- Automatic Jenkins agent enrollment
- Automatic public endpoint/TLS provisioning
- Building a single multi-host Kubernetes control plane using k3d
