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
mac-k3d prepare --disk-min-gb 20   # override role disk minimum (labs only)
# Same Mac: worker config alongside a controller (see below)
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
```

See [prepare-wizard.md](prepare-wizard.md) for the full questionnaire design.

### Flags

| Flag | Description |
|------|-------------|
| `-i, --interactive` | Run interactive wizard to generate config |
| `--init-config` | Write default `config.yaml` if missing (no wizard) |
| `--non-interactive` | Validate existing config only; no prompts or writes |
| `--disk-min-gb N` | Override minimum free disk (GB); `0` in config means role default |

Global `-c / --config` is honored: prepare reads and writes that path (not only the default).

### Single-Mac controller + worker test

To exercise **worker prepare** while Jenkins already runs on this Mac:

1. Prepare/start **controller** with the default config (`role: controller` → `mac-k3d start`).
2. Finish Jenkins UI setup; create an API token (for auto-registration) or leave token empty for manual node create.
3. Prepare **worker** into a separate file:

```bash
mac-k3d prepare -i -c ~/.config/mac-k3d/worker.yaml
# Role: CI worker
# Jenkins URL: http://localhost:9080
# k3d agents: 0  (optional local cluster; not required for LoLBench)
```

4. Confirm agent files under the worker remote root / downloads, and that the node appears (or launch script is ready) on the controller.
5. Do **not** `clean --purge-config` the default controller config while testing the worker file.

`mac-k3d start -c ~/.config/mac-k3d/worker.yaml` is optional (second local k3d); LoLBench only needs the host agent + Docker/Harbor.

### Behavior

1. Assert macOS.
2. **Storage**: scan volumes, default to the one with most free space, prompt for base directory under that volume.
3. **Role**: standalone / controller / worker; set `jenkins.enabled` for controller.
4. **Dependencies**: discover Docker Desktop, k3d, kubectl, helm, Harbor (`uv`/`pipx`), Java; prompt to use existing, specify path, or install.
5. **LoLBench**: prefer a found checkout; otherwise print `git clone` / release unpack commands and optionally clone.
6. **Resources**: controller → ensure `CPU_CORES` Lockable Resources label; worker → Jenkins URL, download `agent.jar`, optional API registration, capacity = logical CPU cores.
7. **Disk check**: fail if free space on storage volume is below role minimum (standalone 40 GB, controller 60 GB, worker 100 GB).
8. Write `~/.config/mac-k3d/config.yaml` and run validation.

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
mac-k3d config [--no-merge-kubeconfig] [--show-jenkins] [--skip-agent]
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--no-merge-kubeconfig` | false | Skip merging k3d kubeconfig into `~/.kube/config` |
| `--show-jenkins` | false | Print Jenkins URL and admin password |
| `--skip-agent` | false | Worker: skip Jenkins agent register / launch-script update |

### Behavior

1. If the named k3d cluster exists: merge kubeconfig, select context, wait for API.
2. Worker without a local cluster: skip kubeconfig (agent-only is OK).
3. If Jenkins enabled or `--show-jenkins`: print URL and admin password from the cluster secret.
4. **Worker:** using `jenkins_agent.api_user` / `api_token` from config, create/update the Jenkins node and rewrite `launch-agent.sh` (unless `--skip-agent`).

---

## `teardown`

Stop the cluster and services without deleting data.

```bash
mac-k3d teardown [--stop-docker] [--deregister-agent]
```

### Flags

| Flag | Description |
|------|-------------|
| `--stop-docker` | Quit Docker Desktop after stopping cluster |
| `--deregister-agent` | Worker: also delete Jenkins node + CPU_CORES lockable resources |

### Behavior

1. `k3d cluster stop <name>` if running.
2. Optionally `osascript` quit Docker Desktop.
3. Leave config and state files intact.
4. Worker without `--deregister-agent`: Jenkins agent stays registered (only k3d is stopped).

---

## `clean`

Remove cluster, volumes, and local artifacts.

```bash
mac-k3d clean [--purge-config] [-y|--yes]
mac-k3d clean -c ~/.config/mac-k3d/worker.yaml --purge-config --yes
```

### Flags

| Flag | Description |
|------|-------------|
| `--purge-config` | Default config: remove `~/.config/mac-k3d/`. With `-c FILE`: remove **only that file** |
| `-y, --yes` | Skip confirmation (required to perform deletion) |

### Behavior

Without `--yes`: print warning and exit 0.

With `--yes`:

1. **Worker:** deregister Jenkins agent node and delete `{agent}-core-*` Lockable Resources (needs `api_token` in config).
2. `k3d cluster delete <name>` from the loaded config.
3. Without `-c`: remove `~/.local/state/mac-k3d/`. With `-c`: leave shared state intact.
4. If `--purge-config`: remove the `-c` file only, or the whole config directory when using the default path.

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
