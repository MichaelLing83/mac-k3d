# mac-k3d Design Documents

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | System overview, components, data flow |
| [commands.md](commands.md) | CLI commands, flags, and behavior |
| [configuration.md](configuration.md) | Config file schema and state layout |
| [deployment.md](deployment.md) | Single-Mac and multi-Mac topology, including physical LAN cabling |
| [setup.md](setup.md) | Step-by-step setup for single- and multi-Mac environments |
| [prepare-wizard.md](prepare-wizard.md) | Interactive `prepare` questionnaire design |
| [lolbench-jenkins.md](lolbench-jenkins.md) | `lolbench_one_task` Jenkins job (one LoLBench task per build) |
| [secrets.md](secrets.md) | CI secrets: configure once on Jenkins controller, use on all agents |

## Goals

1. **Single entry point** — One CLI to manage Docker Desktop, k3d, and optional Jenkins on macOS.
2. **Idempotent operations** — Safe to re-run `prepare`, `start`, and `config`.
3. **Sensible defaults** — Works out of the box; config file overrides when needed.
4. **Clear lifecycle** — Distinct phases: prepare → start → config → teardown → clean.

## Non-goals (v1)

- Linux or Windows support
- Production-grade Jenkins hardening (LDAP, backup, HA)
- Replacing Helm/k3d/kubectl — we orchestrate them, not reimplement them
- In-cluster application deployment beyond Jenkins

## Status

This is the initial scaffold. Command handlers are stubs; see each command doc for planned behavior.
