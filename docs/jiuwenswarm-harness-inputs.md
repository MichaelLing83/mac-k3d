# JiuwenSwarm as the harness under test

JiuwenSwarm (the Python package `workswarm`) can be evaluated in place of iCode on **DeepSWE only**. Each rollout runs JiuwenSwarm's headless runner, `jiuwenswarm-process --run-json`, inside the DeepSWE task container. The grader, the anti-cheat checks and the report are the same as for iCode.

Every DeepSWE job runs it with `HARNESS=jiuwenswarm`:

| Job | What it runs |
|-----|--------------|
| `deepswe_one_task` | One question, one rollout |
| `deepswe_some_task` | A list of questions (`TASKS`), or else the first `N_TASKS`, each question × `N_ROLLOUTS`. It prepares JiuwenSwarm once, then queues one `deepswe_one_task` build per question and rollout with `HARNESS=jiuwenswarm`, and merges them into one report |
| `deepswe_full_suite_task` | The same for all 113 questions |

The LoLBench and SWE-bench Pro jobs offer iCode only. `mac-k3d config` deletes `build_jiuwenswarm_headless`, the separate JiuwenSwarm dispatcher of earlier versions; its runs are `deepswe_some_task` and `deepswe_full_suite_task` builds now. Every job and its page: [jenkins-jobs.md](jenkins-jobs.md).

## The job page

The DeepSWE pages carry both harnesses' fields, and show the ones of the `HARNESS` you pick. With `HARNESS=jiuwenswarm`, the `JIUWENSWARM_*` text and choice fields are drawn with their help, and each `ICODE_*` one says `Not used with this HARNESS`; picking `icode` swaps them. The Active Choices plugin (`uno-choice`) redraws them. `mac-k3d start` installs it, and `mac-k3d config` pre-approves the fields' scripts on **Manage Jenkins > In-process Script Approval**. When `config` cannot pre-approve them it prints a warning. Until an administrator approves them there, those fields show `Could not draw this field. The build uses its default.`

Jenkins cannot hide a file field, so `ICODE_RELEASE_FILE` and `JIUWENSWARM_RELEASE_FILE` stay on the page, labelled `iCode only (HARNESS=icode).` and `JiuwenSwarm only (HARNESS=jiuwenswarm).`. On a controller without the plugin, `config` says to run `mac-k3d start` and writes plain fields, each labelled the same way.

A field left out of a remote trigger (`buildWithParameters`) or of a dispatcher's call keeps the job's default, as a field left untouched on the page does.

## Two inputs

`JIUWENSWARM_MODE` picks one of two inputs, the same choice iCode offers ([icode-harness-inputs.md](icode-harness-inputs.md)):

| `JIUWENSWARM_MODE` | What you provide | Fields to fill |
|--------------------|------------------|----------------|
| `git` (default) | An allow-listed `https://` URL on github.com or gitcode.com, and a ref | `JIUWENSWARM_GIT_URL`, `JIUWENSWARM_GIT_REF`, `JIUWENSWARM_GIT_REF_KIND` |
| `release` | An uploaded file: a wheel, a wheelhouse or a mac-k3d bundle | `JIUWENSWARM_RELEASE_FILE` |

Fill only the fields of the mode you chose and leave the other group as it is.

The defaults are pull request 2 of `https://gitcode.com/michaelling/jiuwenswarm`: `JIUWENSWARM_GIT_URL=https://gitcode.com/michaelling/jiuwenswarm`, `JIUWENSWARM_GIT_REF=2`, `JIUWENSWARM_GIT_REF_KIND=pr`. The controller config sets them ([Defaults](#defaults-on-the-controller)).

## Git (`JIUWENSWARM_MODE=git`)

`JIUWENSWARM_GIT_REF_KIND` is `branch`, `tag`, `commit` or `pr`, with the same meaning as for iCode ([the kinds](icode-harness-inputs.md#2-git-clone-icode_modegit)). For `pr`, `JIUWENSWARM_GIT_REF` is the pull-request number; gitcode fetches `refs/merge-requests/<n>/head`. For `commit`, checkout fetches that SHA when the server allows it; otherwise the one branch, tag, or pull-request tip that points at it. A private repo uses the controller's `gitcode-pat` or `github-pat` credential (or `GITCODE_TOKEN` / `GITHUB_TOKEN` in a worker's gitignored `.env`). Never put a token in the URL.

The build:

1. Resolves the ref to one commit. A dispatcher resolves it once and passes the commit to every shard as `JIUWENSWARM_GIT_SHA`, so every rollout of a run evaluates the same commit even if the branch moves.
2. Clones that commit under the eval workdir (`eval-runs/jiuwenswarm-src`). The commit must have a `uv.lock`.
3. Builds JiuwenSwarm's own Linux offline pack inside `ICODE_LINUX_BUILD_IMAGE` (pinned in `pipeline/config/toolchain.env`) on the task containers' platform, for that platform only: `OFFLINE_CI_PLATFORMS=linux-x86_64 make offline-tarballs-ci` (`linux-aarch64` for arm64 task images). The pack is a standalone CPython 3.11 with `workswarm` and every dependency from `uv.lock`, wheels for `manylinux_2_28`, so it also starts in DeepSWE images older than the build image (`docs/en/OfflineProcessBuild.md` in the JiuwenSwarm repo). The build unpacks `dist/offline/workswarm-process-linux-<cpu>-<version>.tar.gz` as the tree and compiles its bytecode, because the trial mounts the tree read-only. A commit whose `Makefile` has no `offline-tarballs-ci` target is built with `uv sync --frozen --no-dev --no-editable --python 3.11 --python-platform <cpu>-manylinux_2_28` instead, and the build says so.
4. Fetches JiuwenSwarm's own git dependencies, which its `uv.lock` pins to commits over `https://` on gitcode: `openjiuwen` (`openJiuwen/agent-core`), `gateway-protocol` and `intelli-router` (`openJiuwen/agent-protocol`). The PAT of the JiuwenSwarm host reaches them by variable name. A dependency pinned as `ssh://git@<host>/` is rewritten to `https://` for this build only, as for iCode, so a worker needs no SSH key.

The tree mounted at `/opt/jiuwenswarm-host` holds the runtime, not the checkout. From the offline pack:

| Path | What it is |
|------|------------|
| `BUNDLE` | the pack's name, version, platform, Python and ripgrep versions |
| `python/` | the pack's CPython 3.11, with `workswarm` and every dependency in its `site-packages` |
| `bin/jiuwenswarm-process`, `bin/jiuwenswarm-python`, `bin/rg` | the pack's launchers (they run `python -I`) and ripgrep |
| `bin/jiuwenswarm-run` | the launcher a trial runs: `bin/jiuwenswarm-process` |
| `runtime.json` | package version, Python version, wheel platform, CPU type, `build` (`offline-tarballs-ci`) and the `BUNDLE` fields |
| `freeze.txt` | `uv pip freeze` of the pack's Python |

A `uv sync` build, a wheel and a wheelhouse have `python/` (uv-managed CPython 3.11, copied from this worker's uv cache) and `venv/` (`workswarm` and every dependency) instead, with `bin/jiuwenswarm-run` running `venv/bin/jiuwenswarm-process`; `runtime.json`'s `build` is `uv-sync`, `wheel` or `wheelhouse`.

The build ends with two probes from an empty directory: `jiuwenswarm-run --help`, and an import of `jiuwenswarm.channels.process_cli.machine_entry`. It prints `OK JiuwenSwarm runtime <version> (<build>) starts in /opt/jiuwenswarm-host`. uv's cache, and the pack's CPython and ripgrep downloads, are kept per platform in `~/.local/share/mac-k3d/uv-cache-linux-<arch>`. The pack installs its wheels with `--no-cache`, so each new commit downloads them again; the harness store builds a commit once.

A worker whose Docker bridge cannot reach PyPI (for example behind a VPN with a smaller MTU) can set `MAC_K3D_BUILD_NETWORK=host` in its environment. The build container then uses the host's network; trials keep their own network.

## Release (`JIUWENSWARM_MODE=release`)

Jenkins names the upload after its field, so the build tells the three formats apart by **content**, not by file name:

| Format | How it is recognised | What the build does |
|--------|----------------------|---------------------|
| Wheel | a zip holding a `workswarm-*.dist-info` folder | installs it into a fresh venv; its dependencies come from PyPI |
| Wheelhouse | a `.tar.gz` holding a `workswarm-*.whl` and the dependency wheels | installs it with `--no-index --find-links` on the unpacked folder, so every dependency must be inside |
| Bundle | a `.tar.gz` whose `bundle.json` is a mac-k3d harness bundle (`harness_store.py pack`) | uses it as it is, after checking it was built for this build's task platform |

Any other file stops the build with the list of names it holds. The bundle is the format the harness store writes, so the `bundle.tar.gz` an earlier run archived can be uploaded again to evaluate exactly that runtime.

The build records `icode_release.json` with the file name, its sha256 and the format, and prints `JiuwenSwarm under test: release <file> (<format>) sha256 <hash>`. To compare runs, upload the same file to each.

A dispatcher hands a wheel or a wheelhouse to its shards too, archived as `jiuwenswarm-release/JIUWENSWARM_RELEASE_FILE`, so a shard whose task platform differs from the dispatcher's can build its own from the same file. An uploaded bundle is used as it is, so every shard must run its platform.

`mac-k3d eval` cannot attach a file, so a release run starts from the job's **Build with Parameters** page. Outside Jenkins, `JIUWENSWARM_RELEASE` names the file: a path on the worker or an `https://` URL.

## One rollout

`JiuwenSwarmAgent` (`pipeline/lib/jiuwenswarm_harbor_agent.py`) runs each rollout in the task container:

1. **Data dir.** `jiuwenswarm_trial.py seed` seeds `JIUWENSWARM_DATA_DIR=/tmp/jiuwenswarm` from the packaged config with `prepare_workspace`, in English, with no interactive `jiuwenswarm-init`. The permissions profile is `full_access` and `file_guard` is off, because a `--run-json` run rejects every permission prompt on its own. `config/.env` holds the endpoint and model only: `API_BASE`, `MODEL_NAME` (the `DEEPSEEK_MODEL` id), `MODEL_PROVIDER=OpenAI` and `ENDPOINT_PROFILE`, plus browser, search and evolution features turned off.
2. **Model key.** The key is copied in as a private 0600 file, as for iCode. The run shell loads it, deletes the file and exports it as `API_KEY`, the name JiuwenSwarm's config reads. It is never in argv, in `.env` or in the run document.
3. **Run document.** `jiuwenswarm_trial.py rundoc` writes `/tmp/jiuwenswarm_run.json`. Its fields are `input` (the task instruction), `mode`, `workspace` (`cwd`, `project_dir` and `trusted_dirs`, all the repo) and `max_turns`, plus `timeout_seconds` when set. It carries no `permissions` field, because run-level tool levels would turn the permission policy back on.
4. **Run.** `jiuwenswarm-run --run-json /tmp/jiuwenswarm_run.json` writes its events to `/logs/agent/jiuwenswarm.jsonl`: one `event` line per runtime event, then one `result` line. Its exit code goes to `jiuwenswarm-exit.txt`; exit 124 is a timeout.
5. **Usage and patch.** `jiuwenswarm_usage.py` reads the `result` line (status, exit code, model calls and tokens) into `jiuwenswarm-usage.json`, which the report reads as it reads iCode's usage. The patch capture is iCode's (`icode_capture.sh`). A failed turn keeps its exit code, and the verifier grades whatever patch was written.

| Setting | Default | Where it is set |
|---------|---------|-----------------|
| `JIUWENSWARM_AGENT_MODE` | `agent.code.normal` | job field; or `agent.code.plan`. Team mode does not run headless |
| `JIUWENSWARM_MAX_TURNS` | `500` | job field; at most 1000, JiuwenSwarm's own limit |
| `JIUWENSWARM_TIMEOUT_SECONDS` | unset (JiuwenSwarm's default) | the worker's environment only. Harbor's agent timeout from `task.toml` applies either way |

Before any rollout, the JiuwenSwarm canary checks that the mount is read-only, that the bundle's Python starts in the task image, that the headless runner imports and that the seed works. It never calls the model. This catches a wheel that needs a newer glibc than the task image has before a rollout spends tokens.

## What the report records

The identity line is `JiuwenSwarm under test: git <kind> <ref> -> <sha> (<subject>)` or `release <file> (<format>) sha256 <hash>`. It is printed before the canary and is in `summary.md`, `report.html` and `provenance.md`. `eval_protocol.harness` holds the harness name, the mode, the bundle sha256, its platform, where the build got it (`dispatcher`, `store`, `worker` or `upload`; see [the harness store](pipeline.md#harness-store)), and `settings` (`agent_mode`, `max_turns`, `timeout_seconds`). JiuwenSwarm has no sanitizer: `eval_protocol.isolation` records the tree's digest, which the store re-checked when the build took the tree.

## Defaults on the controller

The page defaults come from `jenkins_job` in the controller's `config.yaml`:

| Key | Default | Field |
|-----|---------|-------|
| `default_jiuwenswarm_git_url` | `https://gitcode.com/michaelling/jiuwenswarm` | `JIUWENSWARM_GIT_URL` |
| `default_jiuwenswarm_git_ref` | `2` | `JIUWENSWARM_GIT_REF` |
| `default_jiuwenswarm_git_ref_kind` | `pr` | `JIUWENSWARM_GIT_REF_KIND` |

After changing them, run `mac-k3d config --skip-secrets` on the controller to rewrite the jobs, or use `scripts/push_controller_job_defaults.sh`. `mac-k3d set --harness jiuwenswarm` makes JiuwenSwarm the default `HARNESS` of the three DeepSWE jobs; the LoLBench and SWE-bench Pro jobs keep iCode.

## From the CLI

`mac-k3d eval --harness jiuwenswarm` queues a git build with `HARNESS=jiuwenswarm`. `--job one`, `--job some` and `--job full` queue `deepswe_one_task`, `deepswe_some_task` and `deepswe_full_suite_task`:

```bash
mac-k3d eval --job one --harness jiuwenswarm --task abs-module-cache-flags --yes
mac-k3d eval --job some --harness jiuwenswarm --tasks abs-module-cache-flags,ytt-jsonpath-query-api --n-rollouts 2 \
  --jiuwenswarm-git-ref main --jiuwenswarm-git-ref-kind branch --yes
mac-k3d eval --job full --harness jiuwenswarm --yes
```

Without `--tasks`, `--job some` runs the first `N_TASKS` questions, as the page does. Each harness takes only its own flags: `--icode-git-*` with `--harness jiuwenswarm` is refused, and so is `--jiuwenswarm-git-*` without it. Every flag: [commands.md](commands.md#mac-k3d-eval).

`mac-k3d eval --local` evaluates iCode only. To build and store JiuwenSwarm on a worker without a rollout, run the dispatcher's prepare step from an extracted pipeline:

```bash
mac-k3d pipeline --extract-to /tmp/mac-k3d-pipeline
HARNESS=jiuwenswarm bash /tmp/mac-k3d-pipeline/pipeline/tools/prepare_harness.sh
```

It prints the identity line and `HARNESS_BUNDLE_SHA256=<sha> (<size>)`. The size depends on JiuwenSwarm's dependencies.
