#!/usr/bin/env bash
# Fixture tests for tasks/images.sh's DeepSWE images, with a fake docker and
# harbor (no daemon, no network). An x86-64 host does nothing (Harbor pulls).
# An arm64 eval never builds, pulls or checks: it runs a question natively on
# a prepared, proven build, else on the published image emulated, with the
# reason. mac-k3d eval prepare (MAC_K3D_DEEPSWE_PREPARE=1 and its engine,
# prewarm_task_images.sh) builds on the pinned base, checks once and reuses.
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
prepare() { run_images "$@" MAC_K3D_DEEPSWE_PREPARE=1; }
entry() { record "d['tasks']['$1']['$2']"; }
# An arm64 image under question $1's official name, as an older native build left it.
seed_native_official() {
  mkdir -p "$MACHINE/ctx"
  PATH="$BIN:$PATH" FAKE_STATE="$MACHINE/docker" FAKE_ARCH=aarch64 \
    docker build -t "registry.example/swe:$1-v1.1" "$MACHINE/ctx" >/dev/null
}
PREPARE_HINT="run mac-k3d eval prepare --benchmark deepswe"
OFFICIAL="registry.example/swe:alpha-v1.1"

# 1. x86-64 Linux, as before this work: no build, pull, tag or check; Harbor pulls at the trial.
new_machine linux
run_images alpha,beta x86_64 || { show; fail "amd64 run failed"; }
[ ! -s "$MACHINE/docker/docker.log" ] || { show; fail "an x86-64 host changed images: $(cat "$MACHINE/docker/docker.log")"; }
[ "$(harbor_calls)" = 0 ] || { show; fail "an x86-64 host ran Harbor in tasks/images"; }
[ ! -e "$WORK/env_check.json" ] || fail "an x86-64 host wrote env_check.json"
[ ! -e "$MACHINE/share/env-check" ] || fail "an x86-64 host stored a check"
grep -q "Harbor pulls the published DeepSWE images" "$MACHINE/out.txt" || { show; fail "amd64 message"; }
[ "$(container_platform x86_64)" = linux/amd64 ] || fail "x86-64 task containers not linux/amd64"
ok "x86-64 host leaves DeepSWE images to Harbor: zero docker and harbor calls"

# 2. arm64 eval, nothing prepared: emulated with the prepare hint; an official
#    name left on an arm64 image is untagged so Harbor pulls the published one.
new_machine mac
seed_native_official alpha
run_images alpha aarch64 || { show; fail "unprepared arm64 eval failed"; }
[ "$(grep -c . "$MACHINE/docker/docker.log")" = 1 ] && grep -qx "rmi $OFFICIAL" "$MACHINE/docker/docker.log" \
  || { show; fail "expected only the untag of $OFFICIAL: $(cat "$MACHINE/docker/docker.log")"; }
[ "$(harbor_calls)" = 0 ] || { show; fail "an eval ran a check"; }
[ "$(entry alpha mode)" = emulated ] && [ "$(entry alpha source)" = published ] || fail "alpha: $(record 'd["tasks"]["alpha"]')"
[ "$(entry alpha reason)" = "not prepared on this Mac" ] || fail "reason: $(entry alpha reason)"
[ "$(entry alpha hint)" = "$PREPARE_HINT" ] || fail "hint: $(entry alpha hint)"
[ "$(record 'd["platform"]')" = linux/amd64 ] || fail "platform $(record 'd["platform"]')"
[ "$(container_platform aarch64)" = linux/amd64 ] || fail "unprepared eval did not run emulated"
grep -q "images: 0 native, 1 emulated" "$MACHINE/out.txt" || { show; fail "summary line"; }
ok "arm64 eval with nothing prepared runs emulated, with zero build, pull and harbor calls"

# 3. prepare: a native build on the pinned base, tagged with the official name, checked natively, stored.
prepare alpha aarch64 || { show; fail "prepare failed"; }
grep -q "^build mac-k3d/deepswe-native:alpha platform= context=$BASE_FROM=docker-image://public.ecr.aws/x8v8d7g8/mars-base@sha256:" \
  "$MACHINE/docker/docker.log" || { show; cat "$MACHINE/docker/docker.log" >&2; fail "native build without the pinned base"; }
grep -qx "tag mac-k3d/deepswe-native:alpha $OFFICIAL" "$MACHINE/docker/docker.log" \
  || fail "native build not tagged with the official name"
[ "$(docker_calls pull)" = 0 ] || fail "prepare pulled the published image"
[ "$(cut -d' ' -f1-3 "$MACHINE/docker/harbor.log" | tr '\n' ,)" = "oracle alpha built,nop alpha built," ] \
  || fail "expected oracle then nop on the native build: $(cat "$MACHINE/docker/harbor.log")"
grep -q "DOCKER_DEFAULT_PLATFORM=$" "$MACHINE/docker/harbor.log" || fail "native check ran emulated"
[ "$(python3 -c 'import json,sys; print(list(json.load(open(sys.argv[1]))["results"]))' \
  "$MACHINE/share/env-check/deepswe/alpha.json")" = "['built linux/arm64']" ] || fail "no stored native check"
[ "$(entry alpha mode)" = native ] && [ "$(entry alpha pass)" = True ] || fail "alpha: $(record 'd["tasks"]["alpha"]')"
[ "$(entry alpha base_image)" = "$(sed -n 's/^DEEPSWE_BASE_IMAGE=//p' "$STAGES/../config/toolchain.env")" ] \
  || fail "base image not recorded"
ok "prepare builds natively on the pinned base, tags the official name, and stores oracle + nop"

prepare alpha aarch64 || { show; fail "second prepare failed"; }
[ "$(harbor_calls)" = 0 ] && [ "$(docker_calls build)" = 0 ] || { show; fail "a second prepare did the work again"; }
[ "$(entry alpha reused)" = True ] || fail "second prepare did not reuse the check"
ok "a second prepare reuses the build and its check"

# 4. arm64 eval after prepare: native, with no build, pull or check; the official name is re-tagged.
PATH="$BIN:$PATH" FAKE_STATE="$MACHINE/docker" docker image rm "$OFFICIAL" >/dev/null
run_images alpha aarch64 || { show; fail "prepared arm64 eval failed"; }
[ "$(harbor_calls)" = 0 ] && [ "$(docker_calls build)" = 0 ] && [ "$(docker_calls pull)" = 0 ] \
  || { show; fail "an eval built, pulled or checked"; }
grep -qx "tag mac-k3d/deepswe-native:alpha $OFFICIAL" "$MACHINE/docker/docker.log" || fail "official name not re-tagged"
[ "$(entry alpha mode)" = native ] && [ "$(entry alpha reused)" = True ] || fail "alpha: $(record 'd["tasks"]["alpha"]')"
[ "$(container_platform aarch64)" = linux/arm64 ] || fail "prepared eval did not run linux/arm64"
run_images alpha aarch64 || { show; fail "third eval failed"; }
[ ! -s "$MACHINE/docker/docker.log" ] || fail "an eval with the tag in place changed images: $(cat "$MACHINE/docker/docker.log")"
ok "arm64 eval uses the prepared build natively, with zero build, pull and harbor calls"

# 5. A Harbor upgrade: the stored check no longer matches, so the eval runs emulated and says to prepare.
run_images alpha aarch64 HARBOR_VERSION=0.23.0 || { show; fail "eval after a Harbor change failed"; }
[ "$(entry alpha mode)" = emulated ] || fail "a check for another Harbor version was trusted"
[ "$(entry alpha reason)" = "prepared for other task files or another Harbor version (now 0.23.0)" ] \
  || fail "reason: $(entry alpha reason)"
[ "$(entry alpha hint)" = "$PREPARE_HINT" ] || fail "no prepare hint"
grep -qx "rmi $OFFICIAL" "$MACHINE/docker/docker.log" || fail "official name still names the native build"
[ "$(harbor_calls)" = 0 ] && [ "$(docker_calls build)" = 0 ] || { show; fail "the eval checked or built"; }
ok "a changed HARBOR_VERSION runs emulated with the prepare hint"

# 6. One emulated question makes the whole run emulated (one iCode build per run).
run_images alpha,beta aarch64 || { show; fail "mixed eval failed"; }
[ "$(entry beta reason)" = "not prepared on this Mac" ] || fail "beta: $(entry beta reason)"
[ "$(entry alpha mode)" = emulated ] || fail "alpha ran native next to an emulated question"
[ "$(entry alpha reason)" = "another selected question runs emulated (one iCode build per run)" ] \
  || fail "alpha reason: $(entry alpha reason)"
[ "$(record 'd["platform"]')" = linux/amd64 ] || fail "mixed run platform $(record 'd["platform"]')"
ok "a run with one emulated question runs all its questions emulated"

# 7. --rebuild builds again and checks the new build; the eval then runs it natively.
prepare alpha aarch64 DEEPSWE_REBUILD=1 || { show; fail "rebuild failed"; }
[ "$(docker_calls build)" = 1 ] && [ "$(harbor_calls)" = 2 ] || { show; fail "rebuild did not build and check again"; }
run_images alpha aarch64 || { show; fail "eval after rebuild failed"; }
[ "$(entry alpha mode)" = native ] || fail "rebuilt alpha not native"
ok "DEEPSWE_REBUILD=1 (--rebuild) builds and checks again"

# 8. A changed Dockerfile: the build is out of date, so the eval runs emulated and says to prepare.
printf 'RUN echo changed\n' >>"$WORK/deep-swe/tasks/alpha/environment/Dockerfile"
run_images alpha aarch64 || { show; fail "eval after a Dockerfile change failed"; }
[ "$(entry alpha reason)" = "prepared for another Dockerfile or base pin" ] || fail "reason: $(entry alpha reason)"
[ "$(entry alpha hint)" = "$PREPARE_HINT" ] || fail "no prepare hint"
[ "$(docker_calls build)" = 0 ] || fail "an eval rebuilt"
ok "an out-of-date native build runs emulated with the prepare hint"

# 9. DEEPSWE_DOCKER_PLATFORM: linux/amd64 forces emulation; the host platform needs every question proven.
run_images alpha aarch64 DEEPSWE_DOCKER_PLATFORM=linux/amd64 || { show; fail "forced amd64 run failed"; }
[ "$(entry alpha reason)" = "DEEPSWE_DOCKER_PLATFORM=linux/amd64" ] || fail "forced reason: $(entry alpha reason)"
if run_images beta aarch64 DEEPSWE_DOCKER_PLATFORM=linux/arm64; then
  show
  fail "forced linux/arm64 ran an unprepared question"
fi
grep -q "have no proven native build" "$MACHINE/out.txt" || { show; fail "forced arm64 message"; }
ok "DEEPSWE_DOCKER_PLATFORM forces one platform"

# 10. A failed check: prepare untags the official name and stores the fail; evals run emulated, no hint.
new_machine mac-fail
prepare alpha aarch64 FAKE_ORACLE_BUILT="18 20 3 3" || { show; fail "prepare with a failing check failed"; }
[ "$(cut -d' ' -f1-3 "$MACHINE/docker/harbor.log" | tr '\n' ,)" = "oracle alpha built," ] \
  || fail "harbor calls: $(cat "$MACHINE/docker/harbor.log")"
grep -qx "rmi $OFFICIAL" "$MACHINE/docker/docker.log" || fail "official name left on a build that failed its check"
[ -s "$MACHINE/share/env-check/deepswe/alpha.json" ] || fail "the grader's fail was not stored"
[ "$(entry alpha mode)" = emulated ] && [ "$(entry alpha reason)" = "native check failed: oracle f2p 18/20" ] \
  || fail "alpha: $(record 'd["tasks"]["alpha"]')"
[ "$(entry alpha hint)" = "" ] || fail "a stored fail has a prepare hint"
run_images alpha aarch64 || { show; fail "eval after a failed check failed"; }
[ "$(entry alpha reason)" = "native check failed: oracle f2p 18/20" ] && [ "$(entry alpha hint)" = "" ] \
  || fail "eval reason: $(record 'd["tasks"]["alpha"]')"
[ "$(harbor_calls)" = 0 ] || fail "an eval checked again"
prepare alpha aarch64 FAKE_ORACLE_BUILT="18 20 3 3" || { show; fail "second prepare failed"; }
[ "$(harbor_calls)" = 0 ] || { show; fail "a stored fail was checked again"; }
ok "a failed check is stored; the official name is untagged and evals run emulated without a hint"

# 11. A check with no grader result is not stored: emulated with the hint, and prepare checks again.
new_machine mac-flaky
prepare alpha aarch64 FAKE_ORACLE_BUILT=none || { show; fail "prepare with no reward.json failed"; }
[ "$(entry alpha reason)" = "native check failed: oracle left no reward.json" ] || fail "reason: $(entry alpha reason)"
[ "$(entry alpha hint)" = "$PREPARE_HINT" ] || fail "an unfinished check has no prepare hint"
[ ! -e "$MACHINE/share/env-check/deepswe/alpha.json" ] || fail "a Harbor error was stored"
prepare alpha aarch64 || { show; fail "retry failed"; }
[ "$(harbor_calls)" = 2 ] && [ "$(entry alpha mode)" = native ] || { show; fail "the retry did not check again"; }
ok "a check with no grader result is retried by the next prepare"

# 12. An x86-64-only Dockerfile is never built natively: emulated, with a permanent reason.
new_machine mac-x86
prepare cliffy aarch64 || { show; fail "prepare of an x86-only question failed"; }
[ "$(docker_calls build)" = 0 ] && [ "$(harbor_calls)" = 0 ] || { show; fail "an x86-64-only Dockerfile was built or checked"; }
[ "$(entry cliffy reason)" = "its Dockerfile downloads an x86-64-only build" ] && [ "$(entry cliffy hint)" = "" ] \
  || fail "cliffy: $(record 'd["tasks"]["cliffy"]')"
run_images cliffy aarch64 || { show; fail "eval of an x86-only question failed"; }
[ "$(entry cliffy mode)" = emulated ] && [ "$(entry cliffy hint)" = "" ] || fail "cliffy eval: $(record 'd["tasks"]["cliffy"]')"
ok "an x86-64-only Dockerfile runs emulated with its reason and no hint"

# 13. A failed native build: emulated with the log and the hint; nothing stored.
new_machine mac-nobuild
prepare alpha aarch64 FAKE_BUILD_FAIL=1 || { show; fail "prepare with a failing build failed"; }
[ "$(entry alpha reason)" = "native build failed (see $WORK/env-check/alpha/build.log)" ] \
  || fail "reason: $(entry alpha reason)"
[ "$(entry alpha hint)" = "$PREPARE_HINT" ] || fail "a failed build has no prepare hint"
[ ! -e "$MACHINE/share/env-check/deepswe/alpha.json" ] || fail "a failed build was stored"
ok "a failed native build runs emulated and is tried again by the next prepare"

# 14. The engine (mac-k3d eval prepare): one line per question it could not make
#     native, then evals reuse its work. On x86-64 it has nothing to prepare.
new_machine mac-engine
git -c init.defaultBranch=main init -q "$WORK/deep-swe"
git -C "$WORK/deep-swe" add -A
git -C "$WORK/deep-swe" -c user.name=t -c user.email=t@example.com -c commit.gpgsign=false commit -qm fixture
REF="$(git -C "$WORK/deep-swe" rev-parse HEAD)"
run_engine() {
  local arch="$1"
  shift
  : >"$MACHINE/docker/harbor.log"
  : >"$MACHINE/docker/docker.log"
  env -u DEEPSWE_DOCKER_PLATFORM -u DEEPSWE_DIR -u WORKSPACE -u BUILD_NUMBER -u JOB_NAME -u TASK -u N_TASKS \
    -u MAC_K3D_HARBOR_DRY_RUN -u MAC_K3D_DEEPSWE_PREPARE \
    PATH="$BIN:$PATH" HOME="$MACHINE/home" \
    MAC_K3D_EVAL_WORKDIR="$WORK" MAC_K3D_SHARE="$MACHINE/share" \
    MAC_K3D_HARBOR_PYTHON="$BIN/harbor-python" MAC_K3D_TRIAL_SUBNETS=off \
    FAKE_STATE="$MACHINE/docker" FAKE_ARCH="$arch" \
    BENCHMARK=deepswe DEEPSWE_REF="$REF" DEEPSWE_TASK_COUNT=3 TASKS=alpha,cliffy "$@" \
    bash "$STAGES/../tools/prewarm_task_images.sh" >"$MACHINE/out.txt" 2>&1
}
run_engine aarch64 || { show; fail "prepare engine failed"; }
grep -qx "prepare deepswe (linux/arm64): 2 questions, 1 native, 1 emulated" "$MACHINE/out.txt" || { show; fail "engine summary"; }
grep -qx "  cliffy: its Dockerfile downloads an x86-64-only build" "$MACHINE/out.txt" || { show; fail "engine reason for cliffy"; }
run_images alpha aarch64 || { show; fail "eval after the engine failed"; }
[ "$(harbor_calls)" = 0 ] && [ "$(docker_calls build)" = 0 ] || { show; fail "the eval after prepare did the work again"; }
[ "$(record 'd["platform"]')" = linux/arm64 ] || fail "prepared alpha did not run native"
run_engine aarch64 FAKE_BUILD_FAIL=1 DEEPSWE_REBUILD=1 && { show; fail "a failed build did not fail the engine"; }
grep -q "^  alpha: native build failed (see .*); a rerun may make it native$" "$MACHINE/out.txt" \
  || { show; fail "engine build-fail line"; }
[ -s "$WORK/env-check/alpha/build.log" ] || fail "the next question's run removed alpha's build log"
run_engine x86_64 || { show; fail "engine on x86-64 failed"; }
grep -q "nothing to prepare: Linux workers use the published images, which Harbor pulls" "$MACHINE/out.txt" \
  || { show; fail "x86-64 engine message"; }
[ ! -s "$MACHINE/docker/docker.log" ] && [ "$(harbor_calls)" = 0 ] || { show; fail "the x86-64 engine touched images"; }
ok "the prepare engine summarises each question, exits 1 when a rerun may help, and is a no-op on x86-64"

echo "OK test_env_check.sh"
