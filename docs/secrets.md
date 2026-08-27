# Secrets and Credentials Design

## Principle

**Configure secrets once on the Jenkins controller; use them on every agent.**

LLM API keys, Git forge PATs, and similar CI secrets live in the **Jenkins Credentials store** on the controller Mac. Jobs bind them by stable credential IDs. When a build runs on any inbound agent (any worker Mac), Jenkins **injects** those values into that build’s environment for the duration of the step. Workers do **not** keep local copies of these secrets.

```text
  Admin configures once
         |
         v
  Mac A  Jenkins controller
         Credentials (global store)
         - openrouter-api-key
         - deepseek-api-key
         - github-pat
         - gitcode-pat
         ...
         |
         |  job binds credentials('…')
         v
  Mac B/C  agent runs harbor / git / gh
         secrets appear as env vars for this build only
```

## What belongs where

| Secret | Consumer | Store |
|--------|----------|--------|
| LLM API keys (OpenRouter, DeepSeek, OpenLux, OpenAI, Anthropic, …) | Harbor / harnesses in Jenkins builds | Jenkins Credentials (Secret text) on **controller** |
| GitHub / GitCode (etc.) PAT — clone, push, PR comments | Pipeline `git` / `gh` / REST on agent | Jenkins Credentials on **controller** |
| Jenkins API user/token for `mac-k3d` agent register/clean | CLI on **worker** | Local only (Keychain / encrypted file later; plaintext in YAML is transitional debt) |
| Agent JNLP connection secret | LaunchAgent on worker | Local `launch-agent.sh` (node-specific, not shared) |
| Jenkins initial admin password | Helm chart secret in k3d | Cluster secret; printed by `mac-k3d config` |

## Jenkins Credentials (shared across all agents)

### Location

- **Manage Jenkins → Credentials → System → Global credentials (unrestricted)**  
  (or the domain your jobs use; default global store is fine for a lab controller)

### Recommended IDs

Stable IDs so jobs and docs stay aligned:

| Credential ID | Kind | Typical env / use |
|---------------|------|-------------------|
| `openrouter-api-key` | Secret text | `OPENROUTER_API_KEY` |
| `deepseek-api-key` | Secret text | `DEEPSEEK_API_KEY` |
| `openlux-api-key` | Secret text | `OPENLUX_API_KEY` |
| `openai-api-key` | Secret text | `OPENAI_API_KEY` |
| `anthropic-api-key` | Secret text | `ANTHROPIC_API_KEY` |
| `github-pat` | Secret text or Username/password | `GITHUB_TOKEN` / git HTTPS |
| `gitcode-pat` | Secret text or Username/password | `GITCODE_TOKEN` / git HTTPS |

Add providers as needed; keep **one credential per provider**, not one mega-token.

### Binding in jobs

- Bind by ID in the Pipeline (`withCredentials` or `environment { X = credentials('id') }`).
- Prefer binding **only for stages that need the secret** (LLM keys in Evaluate; forge PAT in Checkout / comment).
- Soft/optional binding when possible so `HARNESS=oracle` works with no LLM keys.
- **Never** put secrets in job parameters, build descriptions, or archived artifacts.
- Do **not** bake keys into worker LaunchAgent plists or `worker.yaml`.

Harbor picks up whichever env var matches the selected model (`-m`); unused bound keys are harmless if scoped to the Evaluate stage.

### Why not per-worker copies

- One place to create and rotate.
- Same credential IDs for every `lolbench` (or other) node.
- No secret sprawl on disk across Macs.
- Matches Jenkins’ model: controller owns credentials; agents execute with injected env.

## Out of scope for the shared Jenkins store

These stay **off** the shared CI credential list (or are node-local by nature):

- mac-k3d’s own Jenkins API token used by the CLI to register/deregister agents (CLI credential, not a build secret).
- Per-node agent JNLP secrets.
- Human interactive `gh auth` / Keychain entries used outside Jenkins.

## Future `mac-k3d` help (optional)

Possible later enhancements (not required for the model above):

1. ~~Controller `config`: ensure credential **IDs** exist~~ — **implemented**: prepare can write `~/.config/mac-k3d/credentials.pending.yaml` (0600); `config` creates Secret text credentials in Jenkins and binds present IDs into `lolbench_one_task`. Use `--update-secrets` to re-prompt.
2. Move `jenkins_agent.api_token` from plaintext YAML into macOS Keychain.
3. Document rotation checklist (Jenkins UI + revoke at provider).

### What prepare / config ask (controller)

**Non-secret job defaults** (saved in `config.yaml` → `jenkins_job`):

- Default `HARNESS` (e.g. `oracle`, `icode`)
- Default `TASK`
- Default `MODEL`

**Secrets** (never in `config.yaml`):

- Optional password prompts for OpenRouter, DeepSeek, OpenLux, OpenAI, Anthropic, GitCode PAT, GitHub PAT
- Or env vars `MAC_K3D_OPENROUTER_API_KEY`, `MAC_K3D_DEEPSEEK_API_KEY`, `MAC_K3D_GITCODE_PAT`, …
- Pending file uploaded on `mac-k3d config`; job parameter **defaults** are only harness/task/model — secrets are bound by credential ID

## Related docs

- [lolbench-jenkins.md](lolbench-jenkins.md) — job design and LLM env table for `lolbench_one_task`
- [deployment.md](deployment.md) — controller vs worker topology
- [architecture.md](architecture.md) — security baseline
- [configuration.md](configuration.md) — YAML fields (including transitional plaintext API token)
