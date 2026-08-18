# Commands

All commands accept global flags:

| Flag | Description |
|------|-------------|
| `-c, --config <PATH>` | Config file (default: `~/.config/mac-k3d/config.yaml`) |
| `-v, --verbose` | Increase log verbosity (repeatable: `-vv` for debug) |

Logging uses `tracing`; override with `RUST_LOG=debug`.

---

## `prepare`

Verify prerequisites and generate configuration via an interactive wizard.

```bash
mac-k3d prepare                    # interactive wizard (TTY, no existing config)
mac-k3d prepare -i                 # force interactive wizard
mac-k3d prepare --init-config      # write defaults, no prompts
mac-k3d prepare --non-interactive  # validate only
```

See [prepare-wizard.md](prepare-wizard.md) for the full questionnaire design.

### Flags

| Flag | Description |
|------|-------------|
| `-i, --interactive` | Run interactive wizard to generate config |
| `--init-config` | Write default `config.yaml` if missing (no wizard) |
| `--non-interactive` | Validate existing config only; no prompts or writes |

### Behavior

1. Assert macOS.
2. **Storage**: scan volumes, default to the one with most free space, prompt for base directory under that volume.
3. **Dependencies**: discover Docker Desktop, k3d, kubectl, helm on PATH and common locations; prompt to **use existing**, **specify path**, or **install** (never uninstall without consent).
4. **Role**: ask controller / worker / standalone; set `jenkins.enabled` accordingly.
5. **Cluster**: confirm name, agents, ports.
6. Write `~/.config/mac-k3d/config.yaml` and run validation.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed / config written |
| 1 | Missing dependency, invalid config, or user cancelled |

---

## `start`

Start Docker Desktop (if needed), create or start the k3d cluster, optionally deploy Jenkins.

```bash
mac-k3d start [--jenkins <skip|in-cluster>] [--no-wait-docker]
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--jenkins` | (config) | Override Jenkins mode for this run (`skip` or `in-cluster`) |
| `--no-wait-docker` | false | Skip Docker Desktop readiness wait |

### Behavior

1. Open Docker Desktop if not running (`open -a Docker`).
2. Poll `docker info` until ready or timeout (`docker.startup_timeout_secs`).
3. If cluster missing: `k3d cluster create` with port mappings from config.
4. If cluster exists but stopped: `k3d cluster start`.
5. If Jenkins is enabled (config or `--jenkins in-cluster`): Helm install/upgrade Jenkins chart.
6. Write state file under `~/.local/state/mac-k3d/`.

`--jenkins` overrides `jenkins.enabled` for this invocation only. If omitted, the config file value is used.

### Idempotency

- Second `start` on a running cluster is a no-op aside from Helm upgrade when Jenkins is enabled.

---

## `config`

Apply post-start configuration: kubeconfig merge, context selection, service URLs.

```bash
mac-k3d config [--no-merge-kubeconfig] [--show-jenkins]
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--no-merge-kubeconfig` | false | Skip merging k3d kubeconfig into `~/.kube/config` |
| `--show-jenkins` | false | Print Jenkins URL and admin password |

### Behavior

1. `k3d kubeconfig merge <cluster> --kubeconfig-merge-default --kubeconfig-switch-context`
2. `kubectl config use-context k3d-<cluster>`
3. Wait for API server ready (`kubectl cluster-info`).
4. If Jenkins enabled or `--show-jenkins`: print `http://localhost:<host_port>` and fetch initial admin password from the cluster secret.

---

## `teardown`

Stop the cluster and services without deleting data.

```bash
mac-k3d teardown [--stop-docker]
```

### Flags

| Flag | Description |
|------|-------------|
| `--stop-docker` | Quit Docker Desktop after stopping cluster |

### Behavior

1. `k3d cluster stop <name>` if running.
2. Optionally `osascript` quit Docker Desktop.
3. Leave config and state files intact.

---

## `clean`

Remove cluster, volumes, and local artifacts.

```bash
mac-k3d clean [--purge-config] [-y|--yes]
```

### Flags

| Flag | Description |
|------|-------------|
| `--purge-config` | Also remove `~/.config/mac-k3d/` |
| `-y, --yes` | Skip confirmation (required to perform deletion) |

### Behavior

Without `--yes`: print warning and exit 0.

With `--yes`:

1. `k3d cluster delete <name>`.
2. Remove `~/.local/state/mac-k3d/`.
3. If `--purge-config`: remove config directory.

---

## `status`

Report current environment state (read-only).

```bash
mac-k3d status
```

### Output (example)

```text
Docker Desktop:  running
k3d cluster:     mac-k3d (running, 1 server, 0 agents)
kubectl context: k3d-mac-k3d
Jenkins:         enabled, pod Running, http://localhost:9080
```

---

## Typical workflows

### Minimal (no Jenkins)

```bash
mac-k3d prepare --init-config
mac-k3d start
mac-k3d config
kubectl get nodes
mac-k3d teardown   # end of day
```

### With Jenkins

```bash
mac-k3d prepare --init-config
mac-k3d start --jenkins in-cluster
mac-k3d config --show-jenkins
# open http://localhost:9080
mac-k3d clean --yes
```
