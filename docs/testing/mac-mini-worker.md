# Mac mini worker: onboarding and parity trial

Lab checklist for adding the first Mac mini as a worker of this lab's controller, and for proving that it evaluates the same way as the Linux worker (this PC, `linux-Michael-Ubuntu`). The product steps it follows are [new-machine.md](../new-machine.md#mac-mini-worker); the controller and the Linux worker are the ones in [cloud-eval-runbook.md](cloud-eval-runbook.md).

**Pass:** the Mac's node is online with no `Warning:` line from `config`, and every row of [What must match](#what-must-match) holds for both iCode modes and all three benchmarks.

When it passes, replace "it has not been run yet" in [dependencies.md](../dependencies.md#linux-and-macos) with the date and the dispatcher build numbers, and add a row to [integration-log.md](../integration-log.md).

---

## 1. Onboard the Mac

On the Mac, as the user that will run the agent:

| Setting | Check | Want |
|---------|-------|------|
| Automatic login (System Settings > Users & Groups; FileVault off) | `defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser` | your user name |
| No sleep: `sudo pmset -a sleep 0` | `pmset -g \| grep -E '^ *sleep'` | `sleep 0` |
| Docker Desktop > Settings > General: start at sign-in, Rosetta on | `docker run --rm --platform linux/amd64 alpine:3.20 uname -m` | `x86_64` |
| Docker Desktop > Settings > Resources: memory ≥ 18 GB, CPUs ≥ 4, disk ≈ 200 GB | `docker info --format '{{.NCPU}} CPUs {{.MemTotal}} bytes'` | `4 CPUs` or more, `19327352832 bytes` or more |
| Homebrew | `brew --version` | any |

The binary must be the same commit as this PC's. `scripts/redeploy.sh` copies only to hosts of its own OS and CPU, so build it on the Mac:

```bash
git clone https://github.com/Toby-Yu/mac-k3d.git && cd mac-k3d
git checkout "$(ssh Toby@<this-pc> '~/.local/bin/mac-k3d --version' | sed -E 's/.*\(([0-9a-f]+).*/\1/')"   # this PC's commit
curl --proto '=https' -sSf https://sh.rustup.rs | sh -s -- -y && . "$HOME/.cargo/env"
bash scripts/redeploy.sh   # this Mac is the local worker; run it after `setup` below
```

A released commit can use the asset instead: download `mac-k3d-darwin-aarch64`, `xattr -d com.apple.quarantine`, and copy it to `~/.local/bin/mac-k3d` ([new-machine.md](../new-machine.md#1-download-the-binary)).

```bash
mac-k3d setup -c ~/.config/mac-k3d/worker.yaml
# Role: CI worker; Docker / git: use existing; URL: Enter (this lab's controller); API user / token: admin + a token
```

**Expect:** `java 21: ok`, `harbor 0.22.0: ok`, bash and python3 from Homebrew with no warning, `cpu_cores` no larger than Docker Desktop's CPUs, and no `Warning:` line. Then:

| Check | Command | Want |
|-------|---------|------|
| Agent service | `launchctl print "gui/$(id -u)/com.mac-k3d.jenkins-agent" \| grep -E 'state\|last exit'` | `state = running` |
| Node | Jenkins > Nodes | `macos-<hostname>` online, labels `macos docker lolbench fits-deepswe fits-lolbench fits-swebenchpro executors-<n>` |
| Status | `mac-k3d status -c ~/.config/mac-k3d/worker.yaml` | no `Warning:` line |
| Restart | Restart the Mac, wait 2 minutes, touch nothing | the node comes back online by itself |

**Checkpoint — paste:** the `config` tail, the `launchctl` lines, and the node's labels.

---

## 2. Parity trial

### Prewarm the DeepSWE images

Each worker proves a DeepSWE question's image once (a native build on the Mac, then Harbor's oracle and nop agents) and reuses that answer ([pipeline.md](../pipeline.md#deepswe-on-linux-and-macos)). Do it before the trial, so the timed builds compare rollouts, not first-time checks. On the Mac and on the Linux worker, as the user that runs the agent (the store is that user's `~/.local/share/mac-k3d/env-check/`, and Docker Desktop's images are per user), with no build running:

```bash
TASKS=abs-module-cache-flags,cliffy-config-file-parsing,ipython-session-bundle-replay \
  bash ~/.local/share/mac-k3d/pipeline/tools/prewarm_task_images.sh
```

`~/.local/share/mac-k3d/pipeline` is the pipeline `setup` and `config` extract from the binary, the same commit the Jenkins builds run. Without `TASKS`, it prewarms every question at `DEEPSWE_REF`: hours, and roughly 120 GB of Docker disk.

**Expect on the Mac:**
- `images: linux/arm64 host, native builds on public.ecr.aws/x8v8d7g8/mars-base@sha256:91db…`;
- for the two questions that build natively, `env check: <id> pass on built linux/arm64 (oracle f2p n/n p2p m/m; nop f2p k/n p2p m/m)`, with k below n;
- for `cliffy-config-file-parsing`, `its Dockerfile downloads an x86-64-only build; the published linux/amd64 image, emulated`, then `env check: cliffy-config-file-parsing pass on published linux/amd64 (…)`;
- at the end, `prewarm deepswe: 3 of 3 questions ready`.

**Expect on Linux:** `images: linux/amd64 host, published images`, the same three `pass on published linux/amd64` lines, and `3 of 3 questions ready`. A second prewarm on either machine prints `reused from local#0` for each question and runs no Harbor trial.

This is also the first run of BuildKit's `--build-context` on Docker Desktop. If `~/.local/share/mac-k3d/eval-runs/env-check/<id>/build.log` shows `unknown flag: --build-context`, or the build pulls `mars-base:latest` instead of the digest, stop and file it. A question that is kept out prints its reason and is listed after `kept out:`.

To compare the two image sources on the Mac itself, with no model, check the published image too:

```bash
DEEPSWE_DOCKER_PLATFORM=linux/amd64 TASKS=abs-module-cache-flags,ipython-session-bundle-replay \
  bash ~/.local/share/mac-k3d/pipeline/tools/prewarm_task_images.sh
python3 - ~/.local/share/mac-k3d/env-check/deepswe/{abs-module-cache-flags,ipython-session-bundle-replay}.json <<'PY'
import json, sys
for path in sys.argv[1:]:
    doc = json.load(open(path))
    for slot, entry in sorted(doc["results"].items()):
        v = entry["verdict"]
        counts = [f'{a} f2p {(v.get(a) or {}).get("f2p_passed")}/{(v.get(a) or {}).get("f2p_total")} '
                  f'p2p {(v.get(a) or {}).get("p2p_passed")}/{(v.get(a) or {}).get("p2p_total")}' for a in ("oracle", "nop")]
        print(f'{doc["task"]} {slot}: {"pass" if v["pass"] else "FAIL"} · ' + " · ".join(counts))
PY
```

Each question should print the same counts for `built linux/arm64` and `published linux/amd64`. The forced run points the official image name at the published image. The next native build points it back. So run it only when no other DeepSWE build is running on the Mac.

### Run the trial

Run the same questions on both workers, in both iCode modes. Pin each dispatcher to one worker with `AGENT_LABEL` (developer profile, or `--param` from the CLI), and use one rollout so the two runs differ only by the worker.

| Benchmark | Questions | Container platform on the Mac |
|-----------|-----------|-------------------------------|
| DeepSWE | `TASKS=abs-module-cache-flags,cliffy-config-file-parsing,ipython-session-bundle-replay` | `linux/arm64` (native) for the two builds that pass their check; `cliffy-config-file-parsing` is `linux/amd64 emulated` and, because one iCode build serves the run, moves the other two with it unless they are separate `one_task` builds ([pipeline.md](../pipeline.md#deepswe-on-linux-and-macos)) |
| LoLBench | `TASKS=ruff_1,fastapi_1,kafka_1` | `linux/arm64` (native) |
| SWE-bench Pro | `N_TASKS=3` (the first three in byte order, the same on both workers) | `linux/amd64 emulated` (Rosetta) |

**Git mode** from the CLI, once per benchmark and worker (12 dispatcher builds in all with release mode):

```bash
for node in linux-Michael-Ubuntu macos-<hostname>; do
  mac-k3d eval --job some --benchmark deepswe \
    --tasks abs-module-cache-flags,cliffy-config-file-parsing,ipython-session-bundle-replay \
    --n-rollouts 1 --icode-mode git --icode-git-url <icode url> --icode-git-ref <sha> --icode-git-ref-kind commit \
    --param AGENT_LABEL="$node" --param AGGREGATE_LABEL="$node"
done
```

**Release mode** needs the upload, so use the job page: `<suite>_some_task` > Build with Parameters, the same questions, `N_ROLLOUTS=1`, `AGENT_LABEL` and `AGGREGATE_LABEL` the node, and the **same** `ICODE_RELEASE_FILE` for both workers: one `.tar.gz` holding `icode-linux-x86_64-full-vX.Y.Z` and `icode-linux-aarch64-full-vX.Y.Z` ([icode-harness-inputs.md](../icode-harness-inputs.md#linux-and-macos-workers)). Uploading the same file keeps the recorded release sha256 equal.

The Mac's first git build of a commit downloads uv's Python and wheels into `~/.local/share/mac-k3d/uv-cache-linux-arm64` (DeepSWE when the native build is used, LoLBench) and `uv-cache-linux-amd64` (DeepSWE when the run fell back to the published image, SWE-bench Pro); the second build of that commit reuses them.

### What must match

Download each dispatcher's `aggregate/artifact.json` (or each shard's `artifact.json`), then compare a Linux run with the Mac run of the same benchmark and mode:

```bash
python3 - linux/artifact.json mac/artifact.json <<'PY'
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
def facts(d):
    ep = d.get("eval_protocol", {})
    icode = ep.get("icode", {})
    iso = ep.get("isolation", {})
    return {
        "iCode": (icode.get("git") or {}).get("sha") or (icode.get("release") or {}).get("sha256"),
        "questions": sorted(t["id"] for t in d["icode"]["tasks"]),
        "images": {k: v.get("id") for k, v in sorted(ep.get("images", {}).items())},
        "env_check counts": {k: (v.get("oracle"), v.get("nop")) for k, v in sorted((ep.get("env_check") or {}).items())},
        "runtime_sha256": iso.get("runtime_sha256"),
        "leak_scan": iso.get("leak_scan", {}).get("statuses"),
        "canary": (iso.get("canary") or {}).get("status"),
        "anticheat": d.get("anticheat", {}).get("counts"),
        "pipeline": ep.get("pipeline", {}).get("commit"),
        "harbor": ep.get("harbor", {}).get("version"),
        "model": (ep.get("model_params") or {}).get("model"),
    }
fa, fb = facts(a), facts(b)
for key in fa:
    print(("SAME " if fa[key] == fb[key] else "DIFF ") + key, "" if fa[key] == fb[key] else f"\n  linux {fa[key]}\n  mac   {fb[key]}")
for ta, tb in zip(sorted(a["icode"]["tasks"], key=lambda t: t["id"]), sorted(b["icode"]["tasks"], key=lambda t: t["id"])):
    print(f'{ta["id"]}: reward {ta.get("reward")} / {tb.get("reward")}  dur_s {ta.get("dur_s", 0):.0f} / {tb.get("dur_s", 0):.0f}  unscored {ta.get("unscored")} / {tb.get("unscored")}')
for tid in sorted(set(a.get("eval_protocol", {}).get("env_check") or {}) | set(b.get("eval_protocol", {}).get("env_check") or {})):
    ca, cb = ((d.get("eval_protocol", {}).get("env_check") or {}).get(tid, {}) for d in (a, b))
    print(f'{tid}: env check {ca.get("source")} {ca.get("platform")} / {cb.get("source")} {cb.get("platform")}')
PY
```

The same facts are in each run's `provenance.md`, next to `summary.md`: the `## Environment check` table shows the source, platform and oracle and nop counts per question, and a merged run has one row per shard with a Worker column.

| Row | DeepSWE | SWE-bench Pro | LoLBench |
|-----|---------|---------------|----------|
| `iCode`, `questions`, `pipeline`, `harbor`, `model` | SAME | SAME | SAME |
| `images` (image id) | DIFF expected when the Mac built natively; SAME when both used the published image | SAME: the same amd64 image | DIFF expected: arm64 Hub tag on the Mac, a local amd64 build on Linux |
| `env_check counts` (oracle and nop F2P/P2P and reward per question) | SAME: this is the proof that a native build grades like the published image | SAME (none recorded) | SAME (none recorded) |
| `runtime_sha256` (git mode) | may DIFF: the venv is the same commit built in a container | may DIFF | DIFF expected (another CPU type) |
| `leak_scan`, `canary`, `anticheat` | SAME: every rollout `clean`, canary `pass` | SAME | SAME |
| Files under each shard's `eval-runs/` | the same names | the same names | the same names |
| Reward | equal, or a difference the Linux worker also shows between two runs of that question | the same | the same |
| Duration | Mac within 1.5× of Linux when native, within 2× under Rosetta | within 2× | within 1.5× (native) |
| OOM retries | none on either | none | none |

A row that should be SAME and is DIFF is a defect: stop and file it before running anything else on the Mac. Also check each Mac shard's console for `OK task containers linux/amd64 (emulated on linux/arm64)` or `(native)` and for `env check: <id> pass … reused from local#0` (the prewarm's answer), and the `summary.md` Worker line ending `os Darwin · containers …`.

To list each shard's files, `tar -tzf` both backups and compare the sorted paths with the build number and timestamps removed:

```bash
for f in linux.tar.gz mac.tar.gz; do tar -tzf "$f" | sed -E 's/jenkins-[0-9]+/jenkins-N/g; s/[0-9]{8}T[0-9]{6}Z/UTC/g' | sort -u >"${f%.tar.gz}.files"; done
diff linux.files mac.files && echo "OK same files"
```

**Checkpoint — paste:** the comparison output for the six pairs (three benchmarks × two modes), and the dispatcher build URLs.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Docker cannot run linux/amd64 containers` in Environment or Tasks | Rosetta is off, or Docker Desktop has not restarted since | Docker Desktop > Settings > General > Use Rosetta; Apply & restart |
| `WARNING: Docker Desktop runs linux/amd64 with QEMU, not Rosetta` | Rosetta setting off; QEMU still works but is slower | Turn Rosetta on |
| `the iCode release is built for aarch64, but this build's task containers are linux/amd64` | Uploaded only the arm64 drop for DeepSWE or SWE-bench Pro | Upload the two-drop `.tar.gz` |
| `Docker Desktop has only … memory; need at least 8 GB` | Docker Desktop's memory limit below `MIN_RAM_GB` | Raise it under Settings > Resources |
| `jenkins_agent.cpu_cores is N but Docker Desktop gives containers M CPUs` from `config` | `cpu_cores` in `worker.yaml` larger than Docker's CPU limit | Lower `cpu_cores`, or raise Docker's CPUs |
| Node offline after a restart | Automatic login off, or FileVault on | Section 1, first row |
| Git build stops in `uv sync` with a TLS or timeout error in the container | A VPN with a small MTU (the Environment stage prints the MTU hint) | Turn the VPN off for the build, or lower Docker's MTU |
| `env check: <id> fail on built linux/arm64 (oracle f2p a/b)`, then the question runs `linux/amd64` | The native build grades differently from the published image; the stored fail keeps it on the published image on this Mac | File it with `eval-runs/env-check/<id>/build.log` and `built-oracle/`. After a fix, delete `~/.local/share/mac-k3d/env-check/deepswe/<id>.json` so it is checked again |
| Tasks fails with `no selected question has a proven environment` | Every selected question failed its check on both images, or its pull or build failed | The reason per question is in `eval-runs/skipped_tasks.txt`; the Harbor logs are under `eval-runs/env-check/<id>/` |
| Tasks fails with `timed out waiting for the node lock images` | Another build's first-time check held the lock longer than `MAC_K3D_IMAGES_LOCK_WAIT` (default 10800 s) | Prewarm the questions ([Prewarm the DeepSWE images](#prewarm-the-deepswe-images)) |
