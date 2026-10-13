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

Run the same questions on both workers, in both iCode modes. Pin each dispatcher to one worker with `AGENT_LABEL` (developer profile, or `--param` from the CLI), and use one rollout so the two runs differ only by the worker.

| Benchmark | Questions | Container platform on the Mac |
|-----------|-----------|-------------------------------|
| DeepSWE | `TASKS=abs-module-cache-flags,cliffy-config-file-parsing,ipython-session-bundle-replay` | `linux/amd64 emulated` (Rosetta) |
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

The Mac's first git build of a commit downloads uv's Python and wheels into `~/.local/share/mac-k3d/uv-cache-linux-amd64` (DeepSWE, SWE-bench Pro) and `uv-cache-linux-arm64` (LoLBench); the second build of that commit reuses them.

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
PY
```

| Row | DeepSWE | SWE-bench Pro | LoLBench |
|-----|---------|---------------|----------|
| `iCode`, `questions`, `pipeline`, `harbor`, `model` | SAME | SAME | SAME |
| `images` (image id) | SAME: the same amd64 image | SAME: the same amd64 image | DIFF expected: arm64 Hub tag on the Mac, a local amd64 build on Linux |
| `runtime_sha256` (git mode) | may DIFF: the venv is the same commit built in a container | may DIFF | DIFF expected (another CPU type) |
| `leak_scan`, `canary`, `anticheat` | SAME: every rollout `clean`, canary `pass` | SAME | SAME |
| Files under each shard's `eval-runs/` | the same names | the same names | the same names |
| Reward | equal, or a difference the Linux worker also shows between two runs of that question | the same | the same |
| Duration | Mac within 2× of Linux (Rosetta) | within 2× | within 1.5× (native) |
| OOM retries | none on either | none | none |

A row that should be SAME and is DIFF is a defect: stop and file it before running anything else on the Mac. Also check each Mac shard's console for `OK task containers linux/amd64 (emulated on linux/arm64)` or `(native)`, and the report's Worker line ending `os \`Darwin\` · containers \`…\``.

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
