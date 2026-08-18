# mac-k3d

A Rust CLI for preparing, starting, configuring, tearing down, and cleaning up a local development environment on **macOS**:

- **Docker Desktop** — container runtime
- **k3d** — lightweight Kubernetes (k3s in Docker)
- **Jenkins** (optional) — CI/CD deployed in-cluster

## Requirements

- macOS (Apple Silicon or Intel)
- Docker Desktop
- [k3d](https://k3d.io/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)

Optional (for Jenkins):

- [Helm](https://helm.sh/)

## Install

```bash
cargo install --path .
```

Or build locally:

```bash
cargo build --release
./target/release/mac-k3d --help
```

## Quick start

```bash
# Verify prerequisites and write default config
mac-k3d prepare --init-config

# Start cluster (optionally with Jenkins)
mac-k3d start
mac-k3d start --jenkins in-cluster

# Merge kubeconfig and show service URLs
mac-k3d config --show-jenkins

# Check status
mac-k3d status

# Stop without deleting data
mac-k3d teardown

# Remove cluster and artifacts
mac-k3d clean --yes
```

## Configuration

Default config path: `~/.config/mac-k3d/config.yaml`

See [docs/configuration.md](docs/configuration.md) for the full schema.

## Documentation

- [Architecture](docs/architecture.md)
- [Commands](docs/commands.md)
- [Configuration](docs/configuration.md)
- [Deployment](docs/deployment.md)
- [Setup guide](docs/setup.md)
- [Prepare wizard](docs/prepare-wizard.md)

## License

MIT
