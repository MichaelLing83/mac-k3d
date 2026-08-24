# Configuration

## Config file location

| Path | Purpose |
|------|---------|
| `~/.config/mac-k3d/config.yaml` | User configuration (created by `prepare --init-config`) |
| `~/.local/state/mac-k3d/` | Runtime state written by `start` (not user-edited) |

Override config path with `--config /path/to/config.yaml`.

## Schema

```yaml
role: standalone          # standalone | controller | worker

cluster:
  name: mac-k3d           # k3d cluster name
  agents: 0               # agent node count
  ports:
    - host: 8080          # host port on macOS
      container: 80       # LB target port in cluster
    - host: 8443
      container: 443

jenkins:
  enabled: false          # set true or use `start --jenkins in-cluster`
  namespace: jenkins
  release_name: jenkins
  host_port: 9080         # localhost URL for Jenkins UI

docker:
  startup_timeout_secs: 120

# Set by `mac-k3d prepare` wizard — large artifacts and caches
storage:
  base_dir: /Volumes/1TB.large/mac-k3d
  docker: /Volumes/1TB.large/mac-k3d/docker
  k3d: /Volumes/1TB.large/mac-k3d/k3d
  jenkins: /Volumes/1TB.large/mac-k3d/jenkins
  downloads: /Volumes/1TB.large/mac-k3d/downloads

# Set by `mac-k3d prepare` wizard — how each tool is resolved
dependencies:
  docker:
    source: existing      # existing | install | skip
    binary: /usr/local/bin/docker
    app: /Applications/Docker.app
  k3d:
    source: existing
    binary: /opt/homebrew/bin/k3d
  kubectl:
    source: install
    binary: /opt/homebrew/bin/kubectl
  helm:
    source: skip
  harbor:
    source: skip          # install via `uv tool install harbor` or pipx
  java:
    source: skip          # required on workers (Jenkins agent)

lolbench:
  path: null
  source: skip            # skip | existing | clone | release
  git_url: https://github.com/MichaelLing83/LoLBench-Preview.git

jenkins_agent:
  controller_url: null    # worker: Jenkins base URL
  name: null
  labels: [macos, docker, lolbench]
  remote_fs: null
  agent_jar: null
  cpu_cores: 0            # logical cores recorded at prepare
  api_user: null          # plaintext for now; encrypt later
  api_token: null         # plaintext for now; encrypt later

resources:
  cpu_cores_label: CPU_CORES
  disk_min_gb: 0          # 0 = role default (40 / 60 / 100)
```

All fields are optional; omitted keys use defaults above.

## Field reference

### `role`

| Value | Meaning |
|-------|---------|
| `standalone` | Local k3d only |
| `controller` | Jenkins in-cluster on this Mac |
| `worker` | Jenkins agent + Harbor/LoLBench; no in-cluster Jenkins |

### `cluster`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `mac-k3d` | Passed to `k3d cluster create --name` |
| `agents` | u8 | `0` | Number of k3d agent nodes |
| `ports` | list | see below | Port mappings via k3d `-p "host:container@loadbalancer"` |

Default ports expose HTTP/HTTPS on the cluster load balancer for Ingress or NodePort services.

**Validation (planned):**
- `name` must match `^[a-z0-9-]+$`
- `host` ports must be 1024–65535 (or 80/443 with rootless caveats documented)
- No duplicate `host` ports

### `jenkins`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Whether Jenkins is managed |
| `namespace` | string | `jenkins` | Kubernetes namespace |
| `release_name` | string | `jenkins` | Helm release name |
| `host_port` | u16 | `9080` | Local port mapped to Jenkins service |

When enabled, `start` adds a port mapping:

```text
-p "9080:8080@loadbalancer"
```

Helm values applied by `mac-k3d start` (controller):

```yaml
controller:
  serviceType: LoadBalancer
  installLatestPlugins: true
  additionalPlugins:
    - lockable-resources   # CPU_CORES capacity locks for workers
```

Chart defaults still install kubernetes, workflow-aggregator, git, and configuration-as-code.

### `docker`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `startup_timeout_secs` | u64 | `120` | Max wait after launching Docker Desktop |

### `storage`

Set by the [prepare wizard](prepare-wizard.md). Paths for large artifacts; config/state stay in `~/.config` and `~/.local/state`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_dir` | path | largest free volume + `/mac-k3d` | Root for caches and data |
| `docker` | path | `{base_dir}/docker` | Docker image data (may need manual Docker Desktop move) |
| `k3d` | path | `{base_dir}/k3d` | k3d cluster and image cache |
| `jenkins` | path | `{base_dir}/jenkins` | Helm charts, plugins, Jenkins PV data |
| `downloads` | path | `{base_dir}/downloads` | Agent JARs and other large downloads |

### `dependencies`

Per-tool resolution. The prepare wizard discovers existing installs first and never removes them without explicit consent.

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `source` | string | `existing`, `install`, `skip` | Use found binary, install via Homebrew, or skip |
| `binary` | path | — | CLI path when `source: existing` |
| `app` | path | — | App bundle (Docker Desktop only) |

| Tool | Required when |
|------|---------------|
| `docker` | Always |
| `k3d` | Always |
| `kubectl` | Always |
| `helm` | Jenkins controller (`jenkins.enabled: true`) |

## Example: Jenkins enabled

```yaml
cluster:
  name: dev-cluster
  agents: 1
  ports:
    - host: 8080
      container: 80

jenkins:
  enabled: true
  host_port: 9090

docker:
  startup_timeout_secs: 180
```

```bash
mac-k3d start   # reads jenkins.enabled from config
# equivalent to:
mac-k3d start --jenkins in-cluster
```

CLI flag `--jenkins` overrides config for that invocation only (does not write back to file unless we add `--save` later).

## State files (planned)

### `~/.local/state/mac-k3d/cluster.json`

```json
{
  "name": "mac-k3d",
  "created_at": "2026-08-18T07:00:00Z",
  "k3d_version": "5.6.0",
  "ports": [{"host": 8080, "container": 80}]
}
```

### `~/.local/state/mac-k3d/jenkins.json`

```json
{
  "namespace": "jenkins",
  "release": "jenkins",
  "host_port": 9080,
  "chart_version": "5.1.0"
}
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `RUST_LOG` | Logging filter (e.g. `mac_k3d=debug`) |
| `KUBECONFIG` | Respected by kubectl invocations |
| `MAC_K3D_CONFIG` | *(planned)* alternate config path |

## Multi-Mac setup guidance

`mac-k3d` currently manages one local cluster per Mac. For a team of Macs:

1. Choose one Mac as Jenkins controller (`jenkins.enabled: true`).
2. Keep other Macs with `jenkins.enabled: false`.
3. Register those Macs to Jenkins as build workers using Jenkins-native agent setup.

This keeps Kubernetes local on each Mac while still enabling centralized CI orchestration.

### Example: controller Mac

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

### Example: worker Mac

```yaml
cluster:
  name: ci-worker-a
  agents: 0
jenkins:
  enabled: false
docker:
  startup_timeout_secs: 120
```

### Future config extensions (planned, not implemented)

The following keys may be added later for better controller/worker automation:

- `role: controller | worker`
- `controller.url: https://jenkins.example.com`
- `controller.tunnel: ssh | inbound | websocket`
- `worker.labels: ["macos", "docker", "k3d"]`

## Homebrew install hints (prepare)

| Dependency | Install command |
|------------|-----------------|
| Docker Desktop | `brew install --cask docker` |
| k3d | `brew install k3d` |
| kubectl | `brew install kubectl` |
| helm | `brew install helm` |
