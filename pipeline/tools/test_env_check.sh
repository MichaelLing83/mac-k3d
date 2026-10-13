#!/usr/bin/env bash
# Fixture tests for tasks/images.sh's DeepSWE environment check, with a fake
# docker and harbor (no daemon, no network): the published image on an x86-64
# host, a pinned native build on arm64, reuse with no Harbor call, the
# fallback to the published image, and questions kept out of Harbor.
set -euo pipefail

STAGES="$(cd "$(dirname "$0")/../stages" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"
mkdir -p "$BIN"
# Not the developer's .env: it could set DEEPSWE_DOCKER_PLATFORM.
export MAC_K3D_ENV_FILE="$TMP/empty.env"
: >"$MAC_K3D_ENV_FILE"
chmod 600 "$MAC_K3D_ENV_FILE"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

ok() {
  echo "OK $*"
}

# docker: tags and images in $FAKE_STATE/state.json, one line per call in $FAKE_STATE/docker.log.
cat >"$BIN/docker" <<'PY'
#!/usr/bin/env python3
import hashlib, json, os, re, sys
from pathlib import Path

state_dir = Path(os.environ["FAKE_STATE"])
state_dir.mkdir(parents=True, exist_ok=True)
path = state_dir / "state.json"
state = json.loads(path.read_text()) if path.is_file() else {"tags": {}, "images": {}, "builds": 0}
args = sys.argv[1:]
host = {"x86_64": "amd64", "aarch64": "arm64"}[os.environ.get("FAKE_ARCH", "x86_64")]


def log(line):
    with (state_dir / "docker.log").open("a") as fh:
        fh.write(line + "\n")


def save():
    path.write_text(json.dumps(state))


def opt(name, default=""):
    return args[args.index(name) + 1] if name in args else default


def render(fmt, image_id):
    img = state["images"][image_id]
    out = fmt.replace("{{.Id}}", image_id).replace("{{.Architecture}}", img["arch"]).replace("{{.Os}}", "linux")
    out = out.replace("{{json .RepoDigests}}", json.dumps(img.get("digests", [])))
    out = re.sub(r'\{\{index \.Config\.Labels "([^"]+)"\}\}',
                 lambda m: img.get("labels", {}).get(m.group(1), "<no value>"), out)
    return out.replace("\\n", "\n")


cmd = args[0] if args else ""
if cmd == "info":
    print(os.environ.get("FAKE_ARCH", "x86_64"))
elif cmd == "version":
    print("29.0.0")
elif cmd == "image" and args[1] == "inspect":
    name = args[-1]
    image_id = state["tags"].get(name)
    if image_id is None:
        sys.exit(0 if name.startswith("alpine") and "--format" not in args else 1)
    if "--format" in args:
        print(render(opt("--format"), image_id))
elif cmd == "image" and args[1] == "rm":
    log(f"rmi {args[-1]}")
    state["tags"].pop(args[-1], None)
    save()
elif cmd == "pull":
    name = args[-1]
    log(f"pull {name} platform={opt('--platform')}")
    if name in os.environ.get("FAKE_PULL_FAIL", "").split(","):
        sys.exit(1)
    image_id = "sha256:pub" + hashlib.sha256(name.encode()).hexdigest()[:12]
    state["images"][image_id] = {"arch": "amd64", "digests": [f"{name.split(':')[0]}@sha256:abc"], "source": "published"}
    state["tags"][name] = image_id
    save()
elif cmd == "build":
    tag = opt("-t")
    log(f"build {tag} platform={opt('--platform')} context={opt('--build-context')}")
    if os.environ.get("FAKE_BUILD_FAIL") == "1":
        print("RUN npm install: network error", file=sys.stderr)
        sys.exit(1)
    state["builds"] += 1
    image_id = f"sha256:built{state['builds']:04d}"
    labels = dict(a.split("=", 1) for i, a in enumerate(args) if i and args[i - 1] == "--label")
    state["images"][image_id] = {"arch": opt("--platform", "linux/" + host).split("/")[1], "labels": labels, "source": "built"}
    state["tags"][tag] = image_id
    save()
elif cmd == "tag":
    log(f"tag {args[1]} {args[2]}")
    state["tags"][args[2]] = state["tags"][args[1]]
    save()
elif cmd == "run":
    plat = opt("--platform", "linux/" + host)
    print("x86_64" if plat.endswith("amd64") else "aarch64")
PY

# harbor: one trial per `harbor run`, graded with FAKE_<AGENT>_<SOURCE>[_<TASK>] =
# "f2p_passed f2p_total p2p_passed p2p_total", or "none" for no reward.json.
cat >"$BIN/harbor" <<'PY'
#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("0.22.0")
    sys.exit(0)


def opt(name):
    return args[args.index(name) + 1]


tid, agent = opt("-i"), opt("-a").split(":")[1].replace("Agent", "").upper()
toml = (Path(opt("-p")) / tid / "task.toml").read_text()
official = re.search(r'^docker_image\s*=\s*"([^"]+)"', toml, re.M).group(1)
state = json.loads((Path(os.environ["FAKE_STATE"]) / "state.json").read_text())
source = state["images"][state["tags"][official]]["source"].upper()
with (Path(os.environ["FAKE_STATE"]) / "harbor.log").open("a") as fh:
    fh.write(f"{agent.lower()} {tid} {source.lower()} DOCKER_DEFAULT_PLATFORM={os.environ.get('DOCKER_DEFAULT_PLATFORM', '')}\n")
default = "20 20 3 3" if agent == "ORACLE" else "0 20 3 3"
want = os.environ.get(f"FAKE_{agent}_{source}_{tid.upper()}") or os.environ.get(f"FAKE_{agent}_{source}") or default
trial = Path(opt("--jobs-dir")) / opt("--job-name") / f"{tid}__fake1"
(trial / "agent").mkdir(parents=True, exist_ok=True)
(trial / "result.json").write_text("{}")
if want != "none":
    fp, ft, pp, pt = (int(x) for x in want.split())
    (trial / "verifier").mkdir()
    reward = {"reward": int(fp == ft > 0 and pp == pt), "f2p_total": ft, "f2p_passed": fp,
              "p2p_total": pt, "p2p_passed": pp}
    (trial / "verifier" / "reward.json").write_text(json.dumps(reward))
PY

# Harbor's interpreter, as harbor_egress.py asks it: probe facts, then its own check.
cat >"$BIN/harbor-python" <<'PY'
#!/usr/bin/env python3
import json, sys
code = sys.argv[-1]
print("true" if "_egress_control_kernel_support" in code else json.dumps({"image": "alpine:fake", "script": "true", "sidecar": ""}))
PY
chmod +x "$BIN/docker" "$BIN/harbor" "$BIN/harbor-python"

BASE_FROM="public.ecr.aws/x8v8d7g8/mars-base:latest"
make_task() {
  local root="$1" tid="$2" dockerfile="${3:-}"
  mkdir -p "$root/$tid/environment" "$root/$tid/tests" "$root/$tid/solution"
  printf '[environment]\ndocker_image = "registry.example/swe:%s-v1.1"\n' "$tid" >"$root/$tid/task.toml"
  printf '%s' "${dockerfile:-FROM $BASE_FROM
RUN echo $tid
}" >"$root/$tid/environment/Dockerfile"
  printf '{"f2p_node_ids": ["t1"], "p2p_node_ids": ["t2"]}\n' >"$root/$tid/tests/config.json"
  printf '#!/bin/sh\ngit apply /solution/solution.patch\n' >"$root/$tid/solution/solve.sh"
}

# One machine: its share (the stored checks) and Docker state survive between builds.
new_machine() {
  MACHINE="$TMP/$1"
  mkdir -p "$MACHINE/home" "$MACHINE/share" "$MACHINE/docker"
  WORK="$MACHINE/eval"
  make_task "$WORK/deep-swe/tasks" alpha
  make_task "$WORK/deep-swe/tasks" beta
  make_task "$WORK/deep-swe/tasks" cliffy "FROM $BASE_FROM
RUN curl -fsSLo deno.zip https://github.com/denoland/deno/releases/download/v1/deno-x86_64-unknown-linux-gnu.zip
"
}

# tasks/images for $1 (comma-separated questions) on host CPU $2; extra VAR=value follow.
run_images() {
  local tasks="$1" arch="$2"
  shift 2
  : >"$MACHINE/docker/harbor.log"
  : >"$MACHINE/docker/docker.log"
  env -u DEEPSWE_DOCKER_PLATFORM -u DEEPSWE_DIR -u WORKSPACE -u BUILD_NUMBER -u JOB_NAME -u TASK \
    -u MAC_K3D_HARBOR_DRY_RUN \
    PATH="$BIN:$PATH" HOME="$MACHINE/home" \
    MAC_K3D_EVAL_WORKDIR="$WORK" MAC_K3D_SHARE="$MACHINE/share" \
    MAC_K3D_HARBOR_PYTHON="$BIN/harbor-python" MAC_K3D_TRIAL_SUBNETS=off \
    FAKE_STATE="$MACHINE/docker" FAKE_ARCH="$arch" \
    BENCHMARK=deepswe TASKS="$tasks" TASK_OFFSET=0 "$@" \
    bash "$STAGES/tasks/images.sh" >"$MACHINE/out.txt" 2>&1
}

container_platform() {
  env -u DEEPSWE_DOCKER_PLATFORM PATH="$BIN:$PATH" MAC_K3D_EVAL_WORKDIR="$WORK" FAKE_STATE="$MACHINE/docker" \
    FAKE_ARCH="$1" BENCHMARK=deepswe bash -c 'source "$1/_common.sh"; task_container_platform' _ "$STAGES" | tail -n 1
}

harbor_calls() { grep -c . "$MACHINE/docker/harbor.log" || true; }
docker_calls() { grep -c "^$1 " "$MACHINE/docker/docker.log" || true; }
record() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {"d": d}))' "$WORK/env_check.json" "$1"; }
show() { cat "$MACHINE/out.txt" >&2; }

# 1. x86-64 Linux: the published image, checked once with oracle then nop, no build.
new_machine linux
run_images alpha x86_64 || { show; fail "first amd64 run failed"; }
[ "$(docker_calls build)" = 0 ] || { show; fail "an x86-64 host built an image"; }
[ "$(docker_calls pull)" = 1 ] || { show; fail "expected one pull of the published image"; }
[ "$(cut -d' ' -f1-3 "$MACHINE/docker/harbor.log" | tr '\n' ,)" = "oracle alpha published,nop alpha published," ] \
  || { show; fail "expected oracle then nop on the published image: $(cat "$MACHINE/docker/harbor.log")"; }
grep -q "DOCKER_DEFAULT_PLATFORM=$" "$MACHINE/docker/harbor.log" || fail "native amd64 check set DOCKER_DEFAULT_PLATFORM"
[ -s "$MACHINE/share/env-check/deepswe/alpha.json" ] || fail "no stored check"
[ "$(record 'd["platform"]')" = linux/amd64 ] || fail "platform $(record 'd["platform"]')"
[ "$(record 'd["tasks"]["alpha"]["oracle"]["f2p_passed"]')" = 20 ] || fail "oracle counts not recorded"
[ "$(container_platform x86_64)" = linux/amd64 ] || fail "x86-64 task containers not linux/amd64"
grep -q "env check: 1 of 1 passed (0 reused, 0 kept out)" "$MACHINE/out.txt" || { show; fail "summary line"; }
ok "x86-64 host checks the published image once (oracle + nop), builds nothing"

# 2. The next build of that question on that machine reuses the stored check.
run_images alpha x86_64 || { show; fail "second amd64 run failed"; }
[ "$(harbor_calls)" = 0 ] || { show; fail "reuse still ran Harbor"; }
[ "$(docker_calls pull)" = 0 ] || { show; fail "reuse pulled again"; }
[ "$(record 'd["tasks"]["alpha"]["reused"]')" = True ] || fail "reuse not recorded"
grep -q "1 reused" "$MACHINE/out.txt" || { show; fail "reuse not in the summary"; }
ok "a stored check is reused with no Harbor call"

# 3. A published fail keeps that question out; the build goes on with the other.
run_images alpha,beta x86_64 FAKE_ORACLE_PUBLISHED_BETA="35 37 3 3" || { show; fail "one failing question stopped the build"; }
grep -qx $'beta\tenvironment check failed: oracle f2p 35/37 on published linux/amd64' "$WORK/skipped_tasks.txt" \
  || { show; fail "skipped_tasks.txt: $(cat "$WORK/skipped_tasks.txt")"; }
grep -q "^alpha" "$WORK/skipped_tasks.txt" && fail "a passing question was kept out"
ok "a published image that fails its check keeps only that question out"

# 4. The stored fail is reused; when every question is out the build stops.
if run_images beta x86_64; then
  show
  fail "a build with every question out passed"
fi
[ "$(harbor_calls)" = 0 ] || { show; fail "a stored fail ran Harbor again"; }
grep -q "no selected question has a proven environment" "$MACHINE/out.txt" || { show; fail "all-out message"; }
ok "a stored fail is reused; every question out stops the build"

# 5. A trial that leaves no reward.json fails this build but is not stored.
new_machine flaky
run_images alpha x86_64 FAKE_ORACLE_PUBLISHED=none && { show; fail "no reward.json passed"; }
grep -q "oracle left no reward.json" "$WORK/skipped_tasks.txt" || fail "reason: $(cat "$WORK/skipped_tasks.txt")"
[ ! -e "$MACHINE/share/env-check/deepswe/alpha.json" ] || fail "a Harbor error was stored"
run_images alpha x86_64 || { show; fail "retry after a Harbor error failed"; }
[ "$(harbor_calls)" = 2 ] || { show; fail "the retry did not check again"; }
ok "a check with no grader result is retried by the next build"

# 6. arm64: a native build on the pinned base, tagged with the official name, checked natively.
new_machine mac
run_images alpha aarch64 || { show; fail "first arm64 run failed"; }
grep -q "^build mac-k3d/deepswe-native:alpha platform= context=$BASE_FROM=docker-image://public.ecr.aws/x8v8d7g8/mars-base@sha256:" \
  "$MACHINE/docker/docker.log" || { show; cat "$MACHINE/docker/docker.log" >&2; fail "native build without the pinned base"; }
grep -q "^tag mac-k3d/deepswe-native:alpha registry.example/swe:alpha-v1.1$" "$MACHINE/docker/docker.log" \
  || fail "native build not tagged with the official name"
[ "$(docker_calls pull)" = 0 ] || fail "a native run pulled the published image"
[ "$(cut -d' ' -f1-3 "$MACHINE/docker/harbor.log" | tr '\n' ,)" = "oracle alpha built,nop alpha built," ] \
  || fail "expected oracle then nop on the native build: $(cat "$MACHINE/docker/harbor.log")"
grep -q "DOCKER_DEFAULT_PLATFORM=$" "$MACHINE/docker/harbor.log" || fail "native check ran emulated"
[ "$(record 'd["platform"]')" = linux/arm64 ] || fail "platform $(record 'd["platform"]')"
[ "$(record 'd["tasks"]["alpha"]["base_image"]')" = "$(sed -n 's/^DEEPSWE_BASE_IMAGE=//p' "$STAGES/../config/toolchain.env")" ] \
  || fail "base image not recorded"
[ "$(container_platform aarch64)" = linux/arm64 ] || fail "native check did not make the task containers linux/arm64"
ok "arm64 host builds natively on the pinned base and checks that build"

# 7. Reuse on arm64: no rebuild, no Harbor call, the tag still names the build.
run_images alpha aarch64 || { show; fail "second arm64 run failed"; }
[ "$(docker_calls build)" = 0 ] || { show; fail "an unchanged Dockerfile was rebuilt"; }
[ "$(harbor_calls)" = 0 ] || { show; fail "arm64 reuse ran Harbor"; }
[ "$(container_platform aarch64)" = linux/arm64 ] || fail "reuse lost the native platform"
ok "arm64 reuses the build and its check"

# 8. A native fail falls back to the published image under emulation and checks it.
new_machine mac-fallback
run_images alpha aarch64 FAKE_ORACLE_BUILT="18 20 3 3" || { show; fail "fallback run failed"; }
grep -q "^rmi registry.example/swe:alpha-v1.1$" "$MACHINE/docker/docker.log" || fail "official tag not moved off the native build"
grep -q "^pull registry.example/swe:alpha-v1.1 platform=linux/amd64$" "$MACHINE/docker/docker.log" || fail "published image not pulled for amd64"
[ "$(cut -d' ' -f1-3 "$MACHINE/docker/harbor.log" | tr '\n' ,)" = "oracle alpha built,oracle alpha published,nop alpha published," ] \
  || fail "harbor calls: $(cat "$MACHINE/docker/harbor.log")"
grep -q "published DOCKER_DEFAULT_PLATFORM=linux/amd64" "$MACHINE/docker/harbor.log" || fail "published check not emulated"
[ "$(record 'd["platform"]')" = linux/amd64 ] || fail "platform $(record 'd["platform"]')"
[ "$(record 'd["tasks"]["alpha"]["attempts"][0]["reason"]')" = "environment check failed: oracle f2p 18/20 on built linux/arm64" ] \
  || fail "native attempt not kept: $(record 'd["tasks"]["alpha"]')"
[ "$(container_platform aarch64)" = linux/amd64 ] || fail "fallback did not make the task containers linux/amd64"
[ ! -s "$WORK/skipped_tasks.txt" ] || fail "a question with a proven published image was kept out"
run_images alpha aarch64 FAKE_ORACLE_BUILT="18 20 3 3" || { show; fail "fallback reuse failed"; }
[ "$(harbor_calls)" = 0 ] || { show; fail "fallback reuse ran Harbor: $(cat "$MACHINE/docker/harbor.log")"; }
# Other builds may be running this question: a stored native fail leaves the tag alone.
[ "$(docker_calls tag)" = 0 ] || { show; fail "fallback reuse moved the official tag to the native build"; }
[ "$(docker_calls rmi)" = 0 ] && [ "$(docker_calls pull)" = 0 ] || { show; fail "fallback reuse pulled the published image again"; }
ok "a native fail falls back to the published image, emulated, and both answers are reused"

# 9. An x86-64-only Dockerfile is not built on arm64; one such question moves the run to amd64.
new_machine mac-mixed
run_images alpha,cliffy aarch64 || { show; fail "mixed run failed"; }
grep -q "^build mac-k3d/deepswe-native:cliffy" "$MACHINE/docker/docker.log" && fail "built an x86-64-only Dockerfile natively"
grep -q "cliffy: its Dockerfile downloads an x86-64-only build" "$MACHINE/out.txt" || { show; fail "no reason for cliffy"; }
[ "$(record 'd["tasks"]["alpha"]["source"]')" = published ] || fail "alpha not moved to the published image"
[ "$(record 'd["tasks"]["cliffy"]["source"]')" = published ] || fail "cliffy not on the published image"
[ "$(record 'd["platform"]')" = linux/amd64 ] || fail "mixed run platform $(record 'd["platform"]')"
ok "an x86-64-only Dockerfile uses the published image and the run follows it"

# 10. DEEPSWE_DOCKER_PLATFORM=linux/amd64 forces the published image on arm64.
new_machine mac-forced
run_images alpha aarch64 DEEPSWE_DOCKER_PLATFORM=linux/amd64 || { show; fail "forced amd64 run failed"; }
[ "$(docker_calls build)" = 0 ] || fail "forced amd64 built natively"
[ "$(record 'd["tasks"]["alpha"]["source"]')" = published ] || fail "forced amd64 did not use the published image"
ok "DEEPSWE_DOCKER_PLATFORM=linux/amd64 forces the published image"

# 11. A native build that fails falls back, and is not stored.
new_machine mac-nobuild
run_images alpha aarch64 FAKE_BUILD_FAIL=1 || { show; fail "build-fail fallback run failed"; }
[ "$(record 'd["tasks"]["alpha"]["attempts"][0]["reason"]')" = \
  "environment check failed: native build failed (see $WORK/env-check/alpha/build.log) on built linux/arm64" ] \
  || fail "build failure reason: $(record 'd["tasks"]["alpha"]')"
[ "$(record 'd["tasks"]["alpha"]["source"]')" = published ] || fail "no fallback after a failed build"
ok "a failed native build falls back to the published image"

# 12. prewarm_task_images.sh prepares each question on its own: alpha stays native
#     next to cliffy, and the next real build of alpha reuses both answers.
new_machine mac-prewarm
git -c init.defaultBranch=main init -q "$WORK/deep-swe"
git -C "$WORK/deep-swe" add -A
git -C "$WORK/deep-swe" -c user.name=t -c user.email=t@example.com -c commit.gpgsign=false commit -qm fixture
REF="$(git -C "$WORK/deep-swe" rev-parse HEAD)"
: >"$MACHINE/docker/harbor.log"
: >"$MACHINE/docker/docker.log"
env -u DEEPSWE_DOCKER_PLATFORM -u DEEPSWE_DIR -u WORKSPACE -u BUILD_NUMBER -u JOB_NAME -u TASK -u N_TASKS \
  -u MAC_K3D_HARBOR_DRY_RUN \
  PATH="$BIN:$PATH" HOME="$MACHINE/home" \
  MAC_K3D_EVAL_WORKDIR="$WORK" MAC_K3D_SHARE="$MACHINE/share" \
  MAC_K3D_HARBOR_PYTHON="$BIN/harbor-python" MAC_K3D_TRIAL_SUBNETS=off \
  FAKE_STATE="$MACHINE/docker" FAKE_ARCH=aarch64 \
  BENCHMARK=deepswe DEEPSWE_REF="$REF" DEEPSWE_TASK_COUNT=3 TASKS=alpha,cliffy \
  bash "$STAGES/../tools/prewarm_task_images.sh" >"$MACHINE/out.txt" 2>&1 || { show; fail "prewarm failed"; }
grep -q "^prewarm deepswe: 2 of 2 questions ready$" "$MACHINE/out.txt" || { show; fail "prewarm summary"; }
[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["results"].keys() == {"built linux/arm64"})' \
  "$MACHINE/share/env-check/deepswe/alpha.json")" = True ] || fail "alpha was not proven on its native build alone"
[ -s "$MACHINE/share/env-check/deepswe/cliffy.json" ] || fail "cliffy not checked"
run_images alpha aarch64 || { show; fail "build after prewarm failed"; }
[ "$(harbor_calls)" = 0 ] && [ "$(docker_calls build)" = 0 ] || { show; fail "the build after prewarm did the work again"; }
[ "$(record 'd["platform"]')" = linux/arm64 ] || fail "prewarmed alpha did not run native"
ok "prewarm proves each question on its own image, and the next build reuses it"

echo "OK test_env_check.sh"
