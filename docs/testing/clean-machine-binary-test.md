# Clean machine → binary → controller/worker → eval-ready

Lab runbook (this team), not the user start-here. **Users:** [user-guide.md](../user-guide.md).

Step-by-step for a **new Linux or Mac** (or a wiped lab PC) using the GitHub Release binary. Default Jenkins UI port is **17070**.

To wipe an existing lab and keep `.bak` YAML: [testing-binary-initializer.md](testing-binary-initializer.md) **Task 8a**. Developer pass/fail: same file.

Automated checks after bootstrap: [`scripts/env_set_up/README.md`](../../scripts/env_set_up/README.md).  
Full product story: [workflow.md](../workflow.md). Wizard details: [new-machine.md](../new-machine.md).

Use GitHub **Latest** (v0.5.3 or newer).

---

## 0. Prerequisites (honest leftovers)

- ~60 GB free for a controller, ~100 GB if this box is also a worker.
- Network to GitHub Releases (and later to pull images / DeepSWE if you eval).
- You will use a **Terminal** (double-click of an unsigned Mac binary is not supported).

Docker: the binary can **Install** it. If Docker is already present, choose **Use this installation**.

---

## 1. Download and install the binary

Option A — script (recommended once the repo is cloned, or copy the scripts folder):

```bash
export MAC_K3D_RELEASE_TAG=v0.5.3   # or the tag you were given
./scripts/env_set_up/01_download_binary.sh
export PATH="$HOME/.local/bin:$PATH"
which mac-k3d
mac-k3d --version    # expect 0.5.3 or newer
mac-k3d --help       # must list setup and eval
```

Option B — manual. Pick the asset from the Release page:

| Linux | macOS |
|-------|-------|
| `ASSET=mac-k3d-linux-x86_64` (or `mac-k3d-linux-aarch64`) | `ASSET=mac-k3d-darwin-aarch64` (or `mac-k3d-darwin-x86_64`)<br>`xattr -d com.apple.quarantine "./$ASSET"` |

```bash
chmod +x "./$ASSET"
mkdir -p ~/.local/bin
cp "./$ASSET" ~/.local/bin/mac-k3d
export PATH="$HOME/.local/bin:$PATH"
```

A Mac worker also needs the one-time settings in [new-machine.md](../new-machine.md#mac-mini-worker) before step 3.

---

## 2. Controller (host Jenkins)

```bash
mac-k3d setup -c ~/.config/mac-k3d/config.yaml
```

Wizard choices:

| Prompt | Choose |
|--------|--------|
| Role | **CI controller (Jenkins in k3d)** |
| Docker / k3d / kubectl / helm | Use existing or **Install** |
| Jenkins UI host port | **17070** (the default) |
| CI secrets | Yes if you have `deepseek-api-key`; else skip and add later |
| Write + apply | **yes** |

If host **8080** (or **8443**) is already taken, **mac-k3d auto-remaps** to a free port (e.g. `18080`) and prints `Host port 8080 in use → using 18080` before writing config / creating the cluster. Jenkins UI stays on **17070** unless that port is also busy. Manual YAML edit is only needed if no free candidate port is found.

Open **http://localhost:17070** — user **admin**, password from:

```bash
mac-k3d config -c ~/.config/mac-k3d/config.yaml --show-jenkins
```

Do not commit the password.

Verify:

```bash
./scripts/env_set_up/02_check_controller.sh
# expect: OK controller status healthy, OK Jenkins UI … HTTP 200, OK job …
```

---

## 3. Worker (Jenkins agent) — same PC or another machine

### 3a. API token (from Jenkins UI)

1. Log in as **admin**.
2. **admin** (top right) → **Configure** → **API Token** → **Add new Token** → Generate.
3. Copy the token once. `api_user` is `admin`.

### 3b. Setup worker

```bash
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
```

| Prompt | Choose |
|--------|--------|
| Role | **CI worker (Jenkins agent only)** |
| Docker / Java / git | Use existing or **Install** |
| Harbor | Not asked: setup installs the pinned 0.22.0 with `uv` (no root) |
| Jenkins controller URL | Wizard default `http://43.107.42.252:17070` (Enter). Same-PC controller: type `http://localhost:17070` |
| API user / token | Paste now, or press Enter: `worker.yaml` keeps `api_user: ''` / `api_token: ''` to fill in later |

If the token was empty during setup:

```bash
# Edit ~/.config/mac-k3d/worker.yaml → jenkins_agent.api_user / api_token
mac-k3d config -c ~/.config/mac-k3d/worker.yaml
```

Confirm the node is **online** under Jenkins **Manage Jenkins → Nodes**.

Verify:

```bash
REQUIRE_WORKER=1 ./scripts/env_set_up/03_check_worker.sh
# expect: OK worker start rejected; OK agent unit active
```

Do **not** run `mac-k3d start -c worker.yaml`.

---

## 4. Full env check + eval-ready

```bash
SKIP_DOWNLOAD=1 ./scripts/env_set_up/run_all.sh
# or with p0 smoke:
SKIP_DOWNLOAD=1 RUN_EVAL_SMOKE=1 ./scripts/env_set_up/run_all.sh
```

Expected: `OK ALL CHECKS PASSED`.

Then pipeline stages:

```bash
mac-k3d eval --stage env
# continue: docs/testing/testing-eval-pipeline.md (tasks phase onwards)
```

Ensure Jenkins credential **`deepseek-api-key`** exists before LLM stages (see [secrets.md](../secrets.md)).

---

## 5. One-PC cheat sheet

```bash
./scripts/env_set_up/01_download_binary.sh
export PATH="$HOME/.local/bin:$PATH"

mac-k3d setup -c ~/.config/mac-k3d/config.yaml    # controller, port 17070
# login http://localhost:17070 — create API token

mac-k3d setup -c ~/.config/mac-k3d/worker.yaml    # worker → same URL
mac-k3d config -c ~/.config/mac-k3d/worker.yaml  # if token added later

SKIP_DOWNLOAD=1 RUN_EVAL_SMOKE=1 ./scripts/env_set_up/run_all.sh
```

---

## If something fails

| Symptom | Fix |
|---------|-----|
| `command not found: mac-k3d` | `01_download_binary.sh` or copy asset to `~/.local/bin` |
| Docker permission / no Server | Linux: log out/in after docker group; macOS: open Docker Desktop |
| Jenkins login not 200 | Finish controller `setup`/`start`; check `jenkins.host_port` and `JENKINS_URL` |
| Agent not active | Token in `worker.yaml`, then `mac-k3d config -c worker.yaml` |
| Host port 8080 bind error | Should be rare: `setup`/`start` auto-remap busy cluster ports. If it still fails, free the process or set free `cluster.ports` hosts; keep Jenkins on **17070** unless that port is busy too |

Sign-off tables: [testing-binary-initializer.md](testing-binary-initializer.md). Wipe/retest: tear down with `teardown`/`clean` as in [new-machine.md](../new-machine.md).
