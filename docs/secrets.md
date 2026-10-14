# Secrets and Credentials Design

## Local eval keys (this machine only)

**Supported local method:** a gitignored `.env` in the mac-k3d checkout (or `~/.config/mac-k3d/.env`). Eval scripts load `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` from that file when the process env is empty. For **`ICODE_MODE=git`**, the same file may hold `GITCODE_TOKEN` or `GITHUB_TOKEN` (written by the TTY PAT prompt, mode 600). `DEEPSEEK_MODEL` must be a catalog id: `deepseek-flash` (default) or `deepseek-v4-pro` — copy `data[].id` from `GET /models`, never a product name. `mac-k3d set --check-models` and the `env` phase (when the key is set) enforce that before the paid `evaluate` phase. A full eval does not run the LLM-only baseline. This is the correct way to store keys for `mac-k3d eval --stage evaluate` and private git clones on this PC.

```bash
cp .env.example .env
chmod 600 .env
# edit .env — put the key after DEEPSEEK_API_KEY=
# do not export the key in the terminal or paste it into chat
```

- Git never tracks `.env` (see `.gitignore`). Confirm with `git check-ignore -v .env`.
- The evaluate phase's `eval-runs*/.harbor-env` (mode 600) is gitignored; it copies the key for Harbor and never holds a clone token. It exists only while the evaluate step runs: an `EXIT` trap removes it however the step ends, and the env phase removes a copy an older pipeline left. `.pier-env` from older trees stays ignored.
- `./scripts/check_no_secrets.sh` and `./scripts/check_docs.sh` must pass before commit. The docs check also forbids linking v0.3 historical pages from the repo README or `docs/user-guide.md`, and requires a **Releases** inventory in `docs/README.md`. Install the hook once per clone:

```bash
ln -sf ../../scripts/git-hooks/pre-commit .git/hooks/pre-commit
```

- Scripts parse `KEY=value` only; they do not `source` the file as shell.
- If `DEEPSEEK_API_KEY` is already set (Jenkins `withCredentials`), the file is not used to overwrite it. Same for `GITCODE_TOKEN` / `GITHUB_TOKEN`.
- **Never** `export DEEPSEEK_API_KEY=sk-…` or `export GITCODE_TOKEN=…` (shell history, terminal capture, chat attachments).
- **Never** commit `.env`, put a PAT in `ICODE_GIT_URL` or job parameters, or paste keys into chat.

**Supported CI method (unchanged):** Jenkins credential `deepseek-api-key` on the **controller**. Local `.env` is not a substitute for E7 and is not copied to other workers.

## Principle

**Configure CI secrets once on the Jenkins controller; use them on every agent.**

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
| Jenkins API user/token for `mac-k3d` agent register/clean | CLI on **worker** | Local only, in `config.yaml` / `worker.yaml` written mode 600 (`mac-k3d config` tightens an older file). curl reads it on stdin, never from its arguments. Keychain / encrypted file later; plaintext in YAML is transitional debt. **`mac-k3d export` / `import` never copy `api_token` or `credentials.pending.yaml`.** |
| Remote build trigger token (`jenkins.remote_trigger_token`) | Jenkins job "Trigger builds remotely" on all ten eval jobs | Local only, in the controller `config.yaml` (mode 600). `config` / `start` generate it once when the field is empty and write it into each job as `<authToken>`. **`mac-k3d export` omits it.** `mac-k3d eval` does not use it. |
| Agent JNLP connection secret | LaunchAgent / systemd unit on worker | Local `<remote_fs>/.agent-secret` (mode 600, node-specific, not shared). `launch-agent.sh` (mode 700) passes `-secret @<file>`, so the secret is not in the agent's arguments. |
| Jenkins initial admin password | Helm chart secret in k3d | Cluster secret; printed by `mac-k3d config` |

## Never in process arguments

Every user on a host can read every process's arguments (`ps -eo args`), and the cloud worker is a shared host. So a credential reaches a process only through an environment variable or a mode 600 file, never an argument or a URL.

- **The model key during an eval.** Jenkins binds `deepseek-api-key` into the evaluate step's environment. `write_harbor_env` copies it into `.harbor-env` (mode 600), and Harbor loads that `--env-file` into its own process. Harbor turns an agent's exec environment into `docker compose exec -e KEY=VALUE`, so the iCode agent drops the key from it. Instead the agent writes the key into the trial as `/tmp/.mac-k3d-model.env`, mode 600 and owned by the agent user; `docker compose cp` puts only paths in the arguments. The run command loads that file and deletes it before iCode starts. The canary agent loads the key the same way. There is no `--ae DEEPSEEK_API_KEY=…`.
- **The Jenkins API token, CSRF crumb and Groovy scripts.** `mac-k3d config` and `eval --job` / `eval record` send `user = "…"`, the crumb header and any `/scriptText` body (which embeds a credential's value when `config` uploads one) to curl on stdin (`--config -`). Node and job XML bind credentials by ID, so those bodies may stay arguments. Cookie jars are created mode 600 and removed after the call.
- **The agent secret.** `java -jar agent.jar -secret @<remote_fs>/.agent-secret` reads it from the file.

On a running worker, none of these print a secret:

```bash
ps -eo args | grep -c '[D]EEPSEEK_API_KEY='                  # 0
ps -eo args | grep '[a]gent.jar' | grep -c -- '-secret "\?@'  # 1
stat -c '%a %n' ~/.config/mac-k3d/worker.yaml ~/jenkins-agent/.agent-secret ~/jenkins-agent/launch-agent.sh  # 600 600 700
```

A value that went through arguments before this change (the model key, the API token) should be rotated once both workers run the new build.

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

`gitcode-pat` / `github-pat` cover both the iCode clone and `uv sync` of iCode's git dependencies on the same host, including ones iCode pins as `ssh://` ([icode-harness-inputs.md](icode-harness-inputs.md#2-git-clone-icode_modegit)). A worker needs no SSH key for them. Every token is unset before Harbor.

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

- Default `HARNESS` (`icode`)
- Default `TASK`
- Default `DEEPSEEK_MODEL` catalog id (`deepseek-flash` or `deepseek-v4-pro`)

**Secrets** (never in `config.yaml`):

- Optional password prompts for OpenRouter, DeepSeek, OpenLux, OpenAI, Anthropic, GitCode PAT, GitHub PAT
- Or env vars `MAC_K3D_OPENROUTER_API_KEY`, `MAC_K3D_DEEPSEEK_API_KEY`, `MAC_K3D_GITCODE_PAT`, …
- Pending file uploaded on `mac-k3d config`; job parameter **defaults** are only harness/task/model — secrets are bound by credential ID

## Related docs

- [jenkins-jobs.md](jenkins-jobs.md) — the ten eval jobs
- [setup.md](setup.md#how-the-machines-fit-together) — controller vs worker topology
- [architecture.md](architecture.md) — security baseline
- [configuration.md](configuration.md) — YAML fields (including transitional plaintext API token)
- [commands.md](commands.md) — `export` / `import` copy role YAML without secrets
- [export-import.md](export-import.md) — portable YAML never has keys; `config --skip-secrets` keeps the Jenkins store
