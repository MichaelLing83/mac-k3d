#!/usr/bin/env python3
"""env/egress: Harbor's egress probe, the fallback images, and how the run uses it.

A stub `docker` and a stub `harbor` package stand in for the real ones. The
stub DockerEnvironment probes exactly like Harbor 0.22 (`docker container run
--rm IMAGE sh -c SCRIPT` of the class attribute, 30 s), so Harbor's own check
runs through harbor_probe_override.py as it does in a trial.

Stub knobs: STUB_BROKEN (cannot inspect, pull or run: the build #53 store
damage), STUB_RUN_BROKEN (present, but run exits 125: alpine:3.20 there),
STUB_HANG (our named probe sleeps; with STUB_HANG_ONCE only the first time),
STUB_HARBOR_FAIL (only Harbor's own probe fails), STUB_KERNEL_FAIL.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parent
ROOT = LIB.parent.parent
STAGES = ROOT / "pipeline" / "stages"
sys.path.insert(0, str(LIB))

import harbor_egress  # noqa: E402
from harbor_egress import FALLBACK_PROBE_IMAGE  # noqa: E402

DEFAULT = "alpine:3.23.4@sha256:5b10"
SIDECAR = "harbor-prebuilt:harbor-docker-egress-control-sidecar--abc123"
ALREADY_EXISTS = (
    "failed to register layer: unable to prepare extraction snapshot: "
    'AlreadyExists: target snapshot "sha256:29df493b": already exists'
)

DOCKER_STUB = r'''#!/usr/bin/env python3
import os, sys, time
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
def refs(name):
    return set(filter(None, os.environ.get(name, "").split(",")))
broken, run_broken, hang = refs("STUB_BROKEN"), refs("STUB_RUN_BROKEN"), refs("STUB_HANG")
ERR = os.environ["STUB_ERR"]
if args[:2] == ["image", "inspect"]:
    ref = args[2]
    if ref in broken or ("sidecar" in ref and os.environ.get("STUB_NO_SIDECAR")):
        sys.exit(1)
    sys.exit(0)
if args[:1] == ["pull"]:
    if args[1] in broken:
        sys.stderr.write(ERR + "\n")
        sys.exit(1)
    sys.exit(0)
if args[:2] == ["container", "rm"]:
    sys.exit(0)
if args[:2] == ["container", "run"]:
    at = args.index("-c")
    ours = "--name" in args
    ref = args[at - 1] if ours else args[at - 2]
    if ref in broken or ref in run_broken:
        sys.stderr.write("docker: Error response from daemon: " + ERR + "\n")
        sys.exit(125)
    if ours and ref in hang:
        once = os.environ.get("STUB_HANG_ONCE", "")
        if not once or not os.path.exists(once):
            if once:
                open(once, "w").close()
            time.sleep(30)
    if not ours and os.environ.get("STUB_HARBOR_FAIL"):
        sys.exit(1)
    sys.exit(1 if os.environ.get("STUB_KERNEL_FAIL") else 0)
if args[:1] == ["version"]:
    print("linux/amd64")
sys.exit(0)
'''


def container_runs(log: Path) -> list[str]:
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.startswith("container run")]


def harbor_runs(log: Path) -> list[str]:
    """Probe containers Harbor itself started (ours are always named)."""
    return [ln for ln in container_runs(log) if "--name" not in ln]

HARBOR_DOCKER = f'''
import functools, subprocess
from pathlib import Path


class DockerEnvironment:
    _EGRESS_CONTROL_SIDECAR_CONTEXT_PATH = Path(__file__).parent
    _EGRESS_CONTROL_SIDECAR_DOCKER_NAME = "harbor-prebuilt:harbor-docker-egress-control-sidecar"
    _EGRESS_CONTROL_KERNEL_PROBE_IMAGE = "{DEFAULT}"
    _EGRESS_CONTROL_KERNEL_PROBE_SCRIPT = "if [ ! -f /proc/config.gz ]; then exit 0; fi"

    @classmethod
    def _egress_control_sidecar_dockerfile_path(cls):
        return cls._EGRESS_CONTROL_SIDECAR_CONTEXT_PATH / "Dockerfile"

    @staticmethod
    @functools.cache
    def _egress_control_kernel_support():
        try:
            result = subprocess.run(
                ["docker", "container", "run", "--rm", DockerEnvironment._EGRESS_CONTROL_KERNEL_PROBE_IMAGE,
                 "sh", "-c", DockerEnvironment._EGRESS_CONTROL_KERNEL_PROBE_SCRIPT],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:
            return False
        return result.returncode == 0
'''

HARBOR_UTILS = '''
def _compute_image_name(docker_name, hash_key):
    return f"{docker_name}--{hash_key}"


async def default_docker_platform():
    return "linux/amd64"
'''

HARBOR_CACHE = '''
def docker_build_context_hash(*, context, dockerfile_path=None, build_args=None, platform=None):
    return "abc123"
'''


def fake_harbor(root: Path, docker_py: str = HARBOR_DOCKER) -> Path:
    pkg = root / "site"
    files = {
        "harbor/__init__.py": "",
        "harbor/environments/__init__.py": "",
        "harbor/environments/docker/__init__.py": "",
        "harbor/environments/docker/docker.py": docker_py,
        "harbor/environments/docker/utils.py": HARBOR_UTILS,
        "harbor/utils/__init__.py": "",
        "harbor/utils/container_cache.py": HARBOR_CACHE,
    }
    for rel, text in files.items():
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return pkg


class HarborEgressCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        bindir = self.root / "bin"
        bindir.mkdir()
        (bindir / "docker").write_text(DOCKER_STUB, encoding="utf-8")
        (bindir / "docker").chmod(0o755)
        self.log = self.root / "docker.log"
        self.out = self.root / "work" / "egress_probe.json"
        self.env = dict(
            os.environ,
            PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
            PYTHONPATH=str(fake_harbor(self.root)),
            MAC_K3D_HARBOR_PYTHON=sys.executable,
            STUB_LOG=str(self.log),
            STUB_ERR=ALREADY_EXISTS,
        )
        self.env.pop("MAC_K3D_EGRESS_PROBE_IMAGE", None)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, **stub: str) -> subprocess.CompletedProcess[str]:
        env = dict(self.env, **stub)
        return subprocess.run(
            [sys.executable, str(LIB / "harbor_egress.py"), "check", "--out", str(self.out)],
            capture_output=True, text=True, check=False, env=env,
        )

    def check_fast_timeouts(self, **stub: str) -> tuple[dict, str]:
        """check() in this process with 2 s probe timeouts, so a hanging probe costs seconds."""
        out = io.StringIO()
        with mock.patch.dict(os.environ, dict(self.env, **stub), clear=True), \
                mock.patch.object(harbor_egress, "RUN_TIMEOUT_S", 2), \
                mock.patch.object(harbor_egress, "RETRY_TIMEOUT_S", 2), \
                contextlib.redirect_stdout(out):
            record = harbor_egress.check(self.out)
        return record, out.getvalue()

    def probe(self) -> dict:
        return json.loads(self.out.read_text(encoding="utf-8"))

    def override(self) -> str:
        return subprocess.run(
            [sys.executable, str(LIB / "harbor_egress.py"), "override", "--probe", str(self.out)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_harbor_default_probe_runs_so_nothing_is_substituted(self):
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"OK egress control: Harbor default probe {DEFAULT}", proc.stdout)
        self.assertIn(f"OK egress sidecar {SIDECAR} present", proc.stdout)
        doc = self.probe()
        self.assertEqual((doc["image"], doc["substituted"], doc["sidecar"]), (DEFAULT, False, SIDECAR))
        self.assertEqual([t["role"] for t in doc["tried"]], ["harbor_default"])
        self.assertEqual(self.override(), "")
        runs = container_runs(self.log)
        self.assertFalse(any(FALLBACK_PROBE_IMAGE in ln or SIDECAR in ln for ln in runs), runs)
        # Harbor's own probe really ran, unpatched.
        self.assertEqual(len(harbor_runs(self.log)), 1, runs)

    def test_our_probe_is_named_has_no_network_and_its_own_entrypoint(self):
        self.check()
        ours = [ln for ln in container_runs(self.log) if "--name" in ln]
        self.assertEqual(len(ours), 1, ours)
        self.assertIn(f"--name {harbor_egress.PROBE_NAME_PREFIX}", ours[0])
        self.assertIn(f"--network none --entrypoint sh {DEFAULT} -c ", ours[0])

    def test_build_53_store_damage_switches_to_the_sidecar_image(self):
        proc = self.check(STUB_BROKEN=DEFAULT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"WARNING: Harbor's probe image {DEFAULT} cannot run on this host", proc.stdout)
        self.assertIn(f"using {SIDECAR} for the kernel check. Network isolation is unchanged.", proc.stdout)
        self.assertIn(f"  tried Harbor's probe image {DEFAULT}: {ALREADY_EXISTS}", proc.stdout)
        self.assertIn(f"  tried Harbor's egress sidecar {SIDECAR}: ok", proc.stdout)
        self.assertIn("Docker image store is damaged", proc.stdout)
        doc = self.probe()
        self.assertTrue(doc["substituted"])
        self.assertEqual(doc["image"], SIDECAR)
        self.assertEqual(doc["harbor_default"], DEFAULT)
        self.assertIn("unable to prepare extraction snapshot", doc["reason"])
        self.assertEqual(self.override(), SIDECAR)
        runs = container_runs(self.log)
        self.assertEqual(sum(SIDECAR in ln for ln in runs), 1, runs)
        # Harbor's own check reuses the answer: it starts no probe container.
        self.assertEqual(harbor_runs(self.log), [])
        self.assertNotIn(f"pull {SIDECAR}", self.log.read_text(encoding="utf-8"))

    def test_a_repeated_check_prints_the_explanation_once(self):
        first = self.check(STUB_BROKEN=DEFAULT)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(f"WARNING: Harbor's probe image {DEFAULT} cannot run on this host", first.stdout)
        second = self.check(STUB_BROKEN=DEFAULT)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(f"egress: same probe as the last check ({SIDECAR})", second.stdout)
        self.assertNotIn("cannot run on this host", second.stdout)
        self.assertNotIn("Docker image store is damaged", second.stdout)

    def test_a_changed_answer_prints_in_full(self):
        first = self.check()
        self.assertIn(f"OK egress control: Harbor default probe {DEFAULT}", first.stdout)
        second = self.check(STUB_BROKEN=DEFAULT)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(f"WARNING: Harbor's probe image {DEFAULT} cannot run on this host", second.stdout)

    def test_a_new_failure_still_prints_in_full(self):
        first = self.check(STUB_BROKEN=DEFAULT)
        self.assertEqual(first.returncode, 0, first.stderr)
        failed = self.check(STUB_BROKEN=f"{DEFAULT},{FALLBACK_PROBE_IMAGE}", STUB_RUN_BROKEN=SIDECAR)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("Harbor cannot check egress control on this host", failed.stderr)

    def test_build_61_hanging_alpine_is_never_needed(self):
        proc = self.check(STUB_BROKEN=DEFAULT, STUB_HANG=FALLBACK_PROBE_IMAGE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probe()["image"], SIDECAR)
        self.assertFalse(any(FALLBACK_PROBE_IMAGE in ln for ln in container_runs(self.log)))

    def test_a_probe_that_times_out_is_removed_and_retried(self):
        marker = self.root / "hung-once"
        record, out = self.check_fast_timeouts(
            STUB_BROKEN=DEFAULT, STUB_HANG=SIDECAR, STUB_HANG_ONCE=str(marker),
        )
        self.assertEqual(record["image"], SIDECAR)
        self.assertIn(f"egress probe in {SIDECAR} timed out after 2s", out)
        self.assertIn("retrying with 2s", out)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn(f"container rm -f {harbor_egress.PROBE_NAME_PREFIX}", log)
        self.assertEqual(sum(SIDECAR in ln for ln in container_runs(self.log)), 2)

    def test_a_sidecar_that_hangs_twice_falls_through_to_alpine(self):
        record, out = self.check_fast_timeouts(STUB_BROKEN=DEFAULT, STUB_HANG=SIDECAR)
        self.assertEqual(record["image"], FALLBACK_PROBE_IMAGE)
        self.assertEqual([t["role"] for t in record["tried"]], ["harbor_default", "sidecar", "fallback"])
        self.assertIn("timed out after 2s", record["tried"][1]["detail"])
        self.assertEqual(self.log.read_text(encoding="utf-8").count("container rm -f"), 2)

    def test_a_broken_sidecar_falls_through_to_alpine(self):
        proc = self.check(STUB_BROKEN=DEFAULT, STUB_RUN_BROKEN=SIDECAR)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.probe()
        self.assertEqual(doc["image"], FALLBACK_PROBE_IMAGE)
        self.assertEqual([t["result"] for t in doc["tried"]], ["docker", "docker", "ok"])
        self.assertEqual(self.override(), FALLBACK_PROBE_IMAGE)

    def test_every_image_failing_names_each_docker_error(self):
        proc = self.check(STUB_BROKEN=f"{DEFAULT},{FALLBACK_PROBE_IMAGE}", STUB_RUN_BROKEN=SIDECAR)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Harbor cannot check egress control on this host", proc.stderr)
        self.assertIn(f"Harbor's probe image {DEFAULT}: {ALREADY_EXISTS}", proc.stderr)
        self.assertIn(
            f"Harbor's egress sidecar {SIDECAR}: docker: Error response from daemon: {ALREADY_EXISTS}",
            proc.stderr,
        )
        self.assertIn(f"fallback {FALLBACK_PROBE_IMAGE}: {ALREADY_EXISTS}", proc.stderr)
        self.assertIn("hint: this host's Docker image store is damaged", proc.stderr)
        self.assertFalse(self.out.exists())

    def test_a_kernel_answer_never_falls_back(self):
        proc = self.check(STUB_KERNEL_FAIL="1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("CONFIG_NFT_FIB_INET", proc.stderr)
        log = self.log.read_text(encoding="utf-8")
        self.assertNotIn(FALLBACK_PROBE_IMAGE, log)
        self.assertFalse(any(SIDECAR in ln for ln in container_runs(self.log)))
        self.assertFalse(self.out.exists())

    def test_a_kernel_answer_in_the_sidecar_stops_before_alpine(self):
        proc = self.check(STUB_BROKEN=DEFAULT, STUB_KERNEL_FAIL="1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"the egress probe ran in {SIDECAR} but failed", proc.stderr)
        self.assertNotIn(FALLBACK_PROBE_IMAGE, self.log.read_text(encoding="utf-8"))

    def test_harbor_probe_failing_after_ours_passed_substitutes(self):
        proc = self.check(STUB_HARBOR_FAIL="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.probe()
        self.assertTrue(doc["substituted"])
        self.assertEqual(doc["image"], SIDECAR)
        self.assertIn("Harbor's own probe failed within its 30 s timeout", doc["reason"])
        self.assertEqual(doc["tried"][0]["result"], "docker")
        self.assertEqual(len(harbor_runs(self.log)), 1, "only the unpatched check starts Harbor's container")

    def test_missing_sidecar_after_a_fallback_stops_the_run(self):
        proc = self.check(STUB_BROKEN=DEFAULT, STUB_NO_SIDECAR="1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"egress sidecar {SIDECAR} is not on this host", proc.stderr)
        self.assertTrue(any(FALLBACK_PROBE_IMAGE in ln for ln in container_runs(self.log)))

    def test_missing_sidecar_on_a_healthy_host_is_built_by_harbor(self):
        proc = self.check(STUB_NO_SIDECAR="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"note: Harbor builds its egress sidecar {SIDECAR} on the first trial", proc.stdout)

    def test_rate_limit_gets_the_docker_login_hint(self):
        proc = self.check(
            STUB_BROKEN=f"{DEFAULT},{FALLBACK_PROBE_IMAGE}",
            STUB_NO_SIDECAR="1",
            STUB_ERR="toomanyrequests: You have reached your pull rate limit",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("docker login", proc.stderr)


class ProbeOverrideTests(unittest.TestCase):
    def run_override(
        self, docker_py: str, image: str | None, call_probe: bool = False, harbor_fails: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """(the process, the docker commands it ran) for a fresh import of the override."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = fake_harbor(root, docker_py)
            bindir = root / "bin"
            bindir.mkdir()
            (bindir / "docker").write_text(DOCKER_STUB, encoding="utf-8")
            (bindir / "docker").chmod(0o755)
            log = root / "docker.log"
            log.touch()
            env = dict(
                os.environ,
                PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                PYTHONPATH=f"{site}{os.pathsep}{LIB}",
                STUB_LOG=str(log),
                STUB_ERR=ALREADY_EXISTS,
            )
            env.pop("MAC_K3D_EGRESS_PROBE_IMAGE", None)
            if harbor_fails:
                env["STUB_HARBOR_FAIL"] = "1"
            if image is not None:
                env["MAC_K3D_EGRESS_PROBE_IMAGE"] = image
            script = (
                "import harbor_probe_override\n"
                "from harbor.environments.docker.docker import DockerEnvironment as D\n"
                "print(getattr(D, '_EGRESS_CONTROL_KERNEL_PROBE_IMAGE', '<missing>'))\n"
            )
            if call_probe:
                script += "print(D._egress_control_kernel_support())\n"
            proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                  check=False, env=env)
            return proc, log.read_text(encoding="utf-8").splitlines()

    def test_env_set_records_the_image_and_reuses_the_answer(self):
        proc, docker = self.run_override(HARBOR_DOCKER, SIDECAR, call_probe=True, harbor_fails=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), [SIDECAR, "True"])
        self.assertEqual(docker, [], "Harbor must start no probe container of its own")

    def test_env_unset_leaves_harbor_alone(self):
        proc, docker = self.run_override(HARBOR_DOCKER, None, call_probe=True, harbor_fails=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), [DEFAULT, "False"])
        self.assertEqual(proc.stderr, "")
        self.assertEqual(len(docker), 1)
        self.assertTrue(docker[0].startswith(f"container run --rm {DEFAULT} sh -c"), docker)

    def test_a_harbor_without_the_attribute_only_warns(self):
        bare = "class DockerEnvironment:\n    pass\n"
        proc, _ = self.run_override(bare, FALLBACK_PROBE_IMAGE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ["<missing>"])
        self.assertIn("probe left unchanged", proc.stderr)

    def test_a_cached_false_is_replaced_by_the_measured_answer(self):
        import functools

        with tempfile.TemporaryDirectory() as tmp:
            site = fake_harbor(Path(tmp))
            sys.path.insert(0, str(site))
            saved = {k: v for k, v in sys.modules.items() if k == "harbor" or k.startswith("harbor.")}
            prev = os.environ.pop("MAC_K3D_EGRESS_PROBE_IMAGE", None)
            try:
                for key in saved:
                    sys.modules.pop(key)
                from harbor.environments.docker.docker import DockerEnvironment as D

                import harbor_probe_override

                # Harbor caches the probe's answer for the process (functools.cache).
                D._egress_control_kernel_support = staticmethod(functools.cache(lambda: False))
                self.assertFalse(D._egress_control_kernel_support())
                self.assertTrue(harbor_probe_override.apply("img:1"))
                self.assertEqual(D._EGRESS_CONTROL_KERNEL_PROBE_IMAGE, "img:1")
                self.assertTrue(D._egress_control_kernel_support())
                self.assertFalse(harbor_probe_override.apply(""))
            finally:
                if prev is not None:
                    os.environ["MAC_K3D_EGRESS_PROBE_IMAGE"] = prev
                sys.path.remove(str(site))
                for key in [k for k in sys.modules if k == "harbor" or k.startswith("harbor.")]:
                    sys.modules.pop(key)
                sys.modules.update(saved)


class EgressRecordTests(unittest.TestCase):
    def test_record_egress_probe_and_report_line(self):
        from provenance import isolation_lines, isolation_view, record_egress_probe

        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "eval_protocol_inputs.json"
            inputs.write_text(json.dumps({"isolation": {"mode": "git"}}), encoding="utf-8")
            probe = Path(tmp) / "egress_probe.json"
            probe.write_text(json.dumps({
                "harbor_default": DEFAULT, "image": FALLBACK_PROBE_IMAGE, "substituted": True,
                "reason": ALREADY_EXISTS, "sidecar": SIDECAR, "sidecar_present": True,
            }), encoding="utf-8")
            doc = record_egress_probe(inputs, probe)
            stored = json.loads(inputs.read_text(encoding="utf-8"))
            self.assertEqual(stored, doc)
            self.assertEqual(stored["isolation"]["mode"], "git")
            self.assertEqual(stored["isolation"]["egress_probe"]["image"], FALLBACK_PROBE_IMAGE)
            lines = isolation_lines(isolation_view(stored["isolation"]))
            self.assertIn(
                f"Egress probe: substituted {FALLBACK_PROBE_IMAGE} (Harbor's {DEFAULT} could not run: {ALREADY_EXISTS})",
                lines,
            )

            probe.write_text(json.dumps({"harbor_default": DEFAULT, "image": DEFAULT, "substituted": False}),
                             encoding="utf-8")
            stored = record_egress_probe(inputs, probe)
            lines = isolation_lines(isolation_view(stored["isolation"]))
            self.assertIn(f"Egress probe: Harbor default {DEFAULT}", lines)

    def test_report_without_a_probe_record_has_no_egress_line(self):
        from provenance import isolation_lines, isolation_view

        lines = isolation_lines(isolation_view({"mode": "git"}))
        self.assertFalse(any(line.startswith("Egress probe") for line in lines))


class EgressWiringTests(unittest.TestCase):
    def test_env_runs_egress_after_harbor_and_clears_the_last_output(self):
        text = (STAGES / "env.sh").read_text(encoding="utf-8")
        self.assertIn("STEPS=(env/host env/compose env/harbor env/egress env/network env/model_api)", text)
        self.assertIn('rm -f "$WORKDIR/last_output.txt"', text)
        self.assertLess(text.index("last_output.txt"), text.index("run_steps"))
        step = (STAGES / "env" / "egress.sh").read_text(encoding="utf-8")
        self.assertIn('harbor_egress.py" check --out "$WORKDIR/egress_probe.json"', step)

    def test_evaluate_rechecks_after_the_dry_run_exit(self):
        for name in ("canary.sh", "harbor_run.sh"):
            text = (STAGES / "evaluate" / name).read_text(encoding="utf-8")
            with self.subTest(step=name):
                self.assertEqual(text.count("\nensure_harbor_egress\n"), 1)
                dry = text.index("if harbor_dry_run; then")
                self.assertGreater(text.index("\nensure_harbor_egress\n"), dry)
        cmd = (STAGES / "evaluate" / "harbor_cmd.sh").read_text(encoding="utf-8")
        self.assertIn("record-egress-probe", cmd)

    def test_agent_imports_the_override_before_harbor(self):
        src = (LIB / "icode_harbor_agent.py").read_text(encoding="utf-8")
        imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from ")) and "__future__" not in ln]
        self.assertTrue(imports[0].startswith("import harbor_probe_override"), imports[:3])
        for agent in ("canary_harbor_agent.py", "patch_harbor_agent.py"):
            self.assertIn("from icode_harbor_agent import", (LIB / agent).read_text(encoding="utf-8"))

    def _run_cmd_env(self, image: str, inherited: str | None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, MAC_K3D_EVAL_WORKDIR=str(Path(tmp) / "work"))
            env.pop("MAC_K3D_EGRESS_PROBE_IMAGE", None)
            if inherited is not None:
                env["MAC_K3D_EGRESS_PROBE_IMAGE"] = inherited
            proc = subprocess.run(
                ["bash", "-c",
                 'source "$1"; source "$2"; EGRESS_PROBE_IMAGE="$3"; unit_run_dir="$4"; cmd=(env); run_cmd',
                 "_", str(STAGES / "_common.sh"), str(STAGES / "evaluate" / "harbor_cmd.sh"), image, tmp],
                capture_output=True, text=True, check=False, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            found = [ln for ln in proc.stdout.splitlines() if ln.startswith("MAC_K3D_EGRESS_PROBE_IMAGE=")]
            return found[0].split("=", 1)[1] if found else ""

    def test_run_cmd_exports_the_probe_image_only_when_substituted(self):
        self.assertEqual(self._run_cmd_env(SIDECAR, None), SIDECAR)
        self.assertEqual(self._run_cmd_env(FALLBACK_PROBE_IMAGE, None), FALLBACK_PROBE_IMAGE)
        self.assertEqual(self._run_cmd_env("", None), "")
        self.assertEqual(self._run_cmd_env("", "stale:image"), "", "a caller's leftover must not reach Harbor")

    def test_harbor_crash_before_any_trial_fails_with_harbor_error(self):
        text = (STAGES / "evaluate" / "harbor_run.sh").read_text(encoding="utf-8")
        self.assertIn('die "harbor run exited $rc before any trial finished: $(harbor_last_error "$unit_log")', text)
        self.assertIn("-mindepth 3 -maxdepth 3 -name result.json", text)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "harbor.log"
            log.write_text(
                "╭──── Traceback ────╮\n"
                "│ raise ValueError( │\n"
                "╰───────────────────╯\n"
                "ValueError: network_mode='allowlist' is not supported by DockerEnvironment\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", "-c", "source <(sed -n '/^harbor_last_error()/,/^}/p' \"$1\"); harbor_last_error \"$2\"",
                 "_", str(STAGES / "evaluate" / "harbor_run.sh"), str(log)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                proc.stdout.strip(),
                "ValueError: network_mode='allowlist' is not supported by DockerEnvironment",
            )


if __name__ == "__main__":
    unittest.main()
