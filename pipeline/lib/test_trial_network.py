#!/usr/bin/env python3
"""Tests for trial_network.py, harbor_network_override.py and their wiring (fake docker and ip, no daemon)."""

from __future__ import annotations

import ipaddress
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
STAGES = LIB.parent / "stages"
# FakeHost narrows PATH to the fakes; bash steps still need the host's tools.
HOST_PATH = os.environ.get("PATH", "/usr/bin:/bin")
sys.path.insert(0, str(LIB))

import trial_network as tn  # noqa: E402

LABEL = tn.PROJECT_LABEL

# A docker CLI over a JSON state file: networks, containers and `compose up/down`.
FAKE_DOCKER = r'''#!PYTHON
import ipaddress, json, os, re, sys

state_path = os.environ["FAKE_DOCKER_STATE"]
with open(state_path, encoding="utf-8") as fh:
    st = json.load(fh)
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\n")
LABEL = "com.docker.compose.project"


def save():
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(st, fh)


def fail(msg, code=1):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def net_by(ref):
    return next((n for n in st["networks"] if ref in (n["Id"], n["Name"])), None)


def overlaps(subnet):
    new = ipaddress.ip_network(subnet)
    return any(new.overlaps(ipaddress.ip_network(c["Subnet"])) for n in st["networks"] for c in n["IPAM"]["Config"])


def create_network(name, subnet, labels):
    if subnet is None:
        if os.environ.get("FAKE_POOLS_FULL"):
            fail("failed to create network " + name + ": Error response from daemon: "
                 "all predefined address pools have been fully subnetted")
        pool = ipaddress.ip_network(os.environ.get("FAKE_DEFAULT_POOL", "172.80.0.0/16"))
        subnet = next(str(s) for s in pool.subnets(new_prefix=24) if not overlaps(str(s)))
    elif overlaps(subnet):
        fail("Error response from daemon: invalid pool request: Pool overlaps with other one on this address space")
    net = {"Id": "id-" + name, "Name": name, "Labels": labels, "IPAM": {"Config": [{"Subnet": subnet}]}}
    st["networks"].append(net)
    st.setdefault("created", []).append({"name": name, "subnet": subnet})
    return net


if os.environ.get("FAKE_DOCKER_DOWN"):
    fail("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")
if args[:2] == ["network", "ls"]:
    print("\n".join(n["Id"] for n in st["networks"]))
    sys.exit(0)
if args[:2] == ["network", "inspect"]:
    found = [net_by(ref) for ref in args[2:]]
    print(json.dumps([n for n in found if n]))
    if not all(found):
        fail("Error: No such network")
    sys.exit(0)
if args[:2] == ["network", "rm"]:
    for ref in args[2:]:
        n = net_by(ref)
        if n is None:
            fail("Error: No such network: " + ref)
        if any(n["Id"] in c["Networks"] for c in st["containers"]):
            fail("Error response from daemon: error while removing network: network " + n["Name"] + " has active endpoints")
        st["networks"].remove(n)
    save()
    sys.exit(0)
if args[:2] == ["network", "create"]:
    rest = args[2:]
    subnet = rest[rest.index("--subnet") + 1] if "--subnet" in rest else None
    create_network(rest[-1], subnet, {})
    save()
    sys.exit(0)
if args[0] == "ps":
    rest = args[1:]
    rows = st["containers"]
    for i, arg in enumerate(rest):
        if arg != "--filter":
            continue
        key, _, value = rest[i + 1].partition("=")
        if key == "label":
            rows = [c for c in rows if value in c["Labels"]]
        elif key == "network":
            n = net_by(value)
            rows = [c for c in rows if n and n["Id"] in c["Networks"]]
    fmt = rest[rest.index("--format") + 1] if "--format" in rest else None
    for c in rows:
        if "-q" in rest or fmt is None:
            print(c["Id"])
            continue
        line = fmt.replace("{{.ID}}", c["Id"]).replace("{{.State}}", c["State"]).replace("{{.Names}}", c["Name"])
        print(re.sub(r'\{\{\.Label "([^"]+)"\}\}', lambda m: c["Labels"].get(m.group(1), ""), line))
    sys.exit(0)
if args[0] == "rm":
    force = "-f" in args
    for ref in [a for a in args[1:] if a != "-f"]:
        c = next((c for c in st["containers"] if ref in (c["Id"], c["Name"])), None)
        if c is None:
            fail("Error: No such container: " + ref)
        if c["State"] == "running" and not force:
            fail("Error response from daemon: cannot remove container " + c["Name"] + ": container is running")
        st["containers"].remove(c)
    save()
    sys.exit(0)
if args[0] == "compose":
    rest = args[1:]
    project = rest[rest.index("--project-name") + 1]
    files = [rest[i + 1] for i, a in enumerate(rest) if a == "-f"]
    for f in files:
        if not os.path.exists(f):
            fail("open " + f + ": no such file or directory")
    subnet = None
    for f in files:
        m = re.search(r"subnet:\s*(\S+)", open(f, encoding="utf-8").read())
        if m:
            subnet = m.group(1)
    if "up" in rest:
        net = create_network(project + "_default", subnet, {LABEL: project})
        st["containers"].append({"Id": "c-" + project + "-main-1", "Name": project + "-main-1", "State": "running",
                                 "Labels": {LABEL: project}, "Networks": [net["Id"]]})
        save()
        sys.exit(0)
    if "down" in rest:
        st["containers"] = [c for c in st["containers"] if c["Labels"].get(LABEL) != project]
        st["networks"] = [n for n in st["networks"] if n["Labels"].get(LABEL) != project]
        save()
        sys.exit(0)
fail("fake docker: unsupported " + " ".join(args), 2)
'''

FAKE_IP = r'''#!PYTHON
import os, sys
args = sys.argv[1:]
if "route" in args:
    sys.stdout.write(os.environ.get("FAKE_IP_ROUTE", ""))
elif "addr" in args:
    sys.stdout.write(os.environ.get("FAKE_IP_ADDR", ""))
'''

# Harbor 0.22's DockerEnvironment, reduced to what the override touches.
FAKE_HARBOR_DOCKER = '''
import re
import subprocess
from pathlib import Path


def _sanitize_docker_compose_project_name(name):
    name = name.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    return re.sub(r"[^a-z0-9_-]", "-", name)


class DockerEnvironment:
    def __init__(self, session_id, environment_dir):
        self.session_id = session_id
        self.environment_dir = Path(environment_dir)

    @property
    def _docker_compose_paths(self):
        return [self.environment_dir / "docker-compose-build.yaml"]

    def _compose(self, *command):
        cmd = ["docker", "compose", "--project-name", _sanitize_docker_compose_project_name(self.session_id),
               "--project-directory", str(self.environment_dir)]
        for path in self._docker_compose_paths:
            cmd += ["-f", str(path.resolve().absolute())]
        proc = subprocess.run([*cmd, *command], capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError(f"Docker compose command failed for environment x. Stdout: {proc.stdout}{proc.stderr}")

    async def start(self, force_build):
        self._compose("up", "--detach", "--wait")

    async def stop(self, delete):
        try:
            self._compose("down")
        except RuntimeError as exc:
            print(f"WARNING: Docker compose down failed: {exc}")
'''

# Starts each session's environment, then stops them all; prints what it saw.
DRIVER = r'''
import asyncio, json, sys
from pathlib import Path
import harbor_network_override as o
import trial_network
from harbor.environments.docker.docker import DockerEnvironment

base = Path(sys.argv[1])
envs = [DockerEnvironment(s, base) for s in sys.argv[2:]]
out = {"applied": bool(getattr(DockerEnvironment, o.MARK, False)), "trials": []}
for env in envs:
    row = {"session": env.session_id}
    try:
        asyncio.run(env.start(False))
        row["started"] = True
    except RuntimeError as exc:
        row["started"] = False
        row["error"] = str(exc)
    paths = env._docker_compose_paths
    row["paths"] = [str(p) for p in paths]
    row["extra"] = [p.read_text(encoding="utf-8") for p in paths[1:]]
    out["trials"].append(row)
with trial_network.reservations() as held:
    out["reserved_while_running"] = {k: v["trial"] for k, v in held.items()}
for env, row in zip(envs, out["trials"]):
    asyncio.run(env.stop(False))
    row["files_left"] = [p for p in row["paths"][1:] if Path(p).exists()]
with trial_network.reservations() as held:
    out["reserved_after"] = dict(held)
print(json.dumps(out))
'''


def write_script(path: Path, text: str) -> None:
    path.write_text(text.replace("#!PYTHON", f"#!{sys.executable}", 1), encoding="utf-8")
    path.chmod(0o755)


def network(name: str, subnet: str | None, project: str | None = None) -> dict:
    return {
        "Id": f"id-{name}",
        "Name": name,
        "Labels": {LABEL: project} if project else {},
        "IPAM": {"Config": [{"Subnet": subnet}] if subnet else []},
    }


def container(name: str, state: str, project: str | None, *networks: str) -> dict:
    return {
        "Id": f"c-{name}",
        "Name": name,
        "State": state,
        "Labels": {LABEL: project} if project else {},
        "Networks": [f"id-{n}" for n in networks],
    }


def trial_dir(path: Path) -> Path:
    """A trial folder as Harbor writes it at Trial init."""
    (path / "agent").mkdir(parents=True)
    (path / "verifier").mkdir()
    (path / "lock.json").write_text("{}\n", encoding="utf-8")
    (path / "config.json").write_text("{}\n", encoding="utf-8")
    return path


def job_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "lock.json", "result.json", "job.log"):
        (path / name).write_text("{}\n", encoding="utf-8")
    return path


class FakeHost(unittest.TestCase):
    """A fake docker (and optionally ip) alone on PATH, and a private reservation dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        write_script(self.bin / "docker", FAKE_DOCKER)
        self.state = self.root / "docker.json"
        self.log = self.root / "docker.log"
        self.log.write_text("", encoding="utf-8")
        self.set_docker([], [])
        patcher = mock.patch.dict(
            os.environ,
            {
                "PATH": str(self.bin),
                "HOME": str(self.root / "home"),
                "FAKE_DOCKER_STATE": str(self.state),
                "FAKE_DOCKER_LOG": str(self.log),
                tn.STATE_ENV: str(self.root / "cache"),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in (tn.POOL_ENV, tn.PREFIX_ENV, tn.SWITCH_ENV, "FAKE_POOLS_FULL", "FAKE_DEFAULT_POOL", "FAKE_DOCKER_DOWN"):
            os.environ.pop(key, None)

    def set_docker(self, networks: list[dict], containers: list[dict]) -> None:
        self.state.write_text(json.dumps({"networks": networks, "containers": containers}), encoding="utf-8")

    def docker_state(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def network_names(self) -> set[str]:
        return {n["Name"] for n in self.docker_state()["networks"]}

    def container_names(self) -> set[str]:
        return {c["Name"] for c in self.docker_state()["containers"]}

    def with_ip(self, route: str = "", addr: str = "") -> None:
        write_script(self.bin / "ip", FAKE_IP)
        os.environ["FAKE_IP_ROUTE"] = route
        os.environ["FAKE_IP_ADDR"] = addr

    def reserve(self, subnet: str, pid: int, trial: str) -> None:
        cache = self.root / "cache"
        cache.mkdir(exist_ok=True)
        path = cache / tn.RESERVATIONS_FILE
        doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        doc[subnet] = {"pid": pid, "trial": trial, "time": "2026-10-08T00:00:00+00:00"}
        path.write_text(json.dumps(doc), encoding="utf-8")

    def reservations(self) -> dict:
        path = self.root / "cache" / tn.RESERVATIONS_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(LIB / "trial_network.py"), *args],
            capture_output=True, text=True, check=False, env={**os.environ, **(env or {})},
        )

    def dead_pid(self) -> int:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid


class PoolTests(unittest.TestCase):
    def test_the_default_pool_overlaps_nothing_the_cloud_worker_uses(self):
        with mock.patch.dict(os.environ):
            for key in (tn.POOL_ENV, tn.PREFIX_ENV):
                os.environ.pop(key, None)
            pool, prefix = tn.pool_settings()
        self.assertEqual((str(pool), prefix), ("10.213.0.0/16", 28))
        for text in tn.CLOUD_WORKER_RANGES:
            self.assertFalse(pool.overlaps(ipaddress.ip_network(text)), text)
        self.assertEqual(len(list(pool.subnets(new_prefix=prefix))), 4096)

    def test_a_bad_pool_or_prefix_is_refused(self):
        cases = (
            ("10.213.0.1/16", ""),
            ("8.8.0.0/16", ""),
            ("fd00::/64", ""),
            ("ten.two", ""),
            ("10.213.0.0/16", "30"),
            ("10.213.0.0/16", "12"),
            ("10.213.0.0/16", "x"),
        )
        for pool, prefix in cases:
            with self.subTest(pool=pool, prefix=prefix), mock.patch.dict(
                os.environ, {tn.POOL_ENV: pool, tn.PREFIX_ENV: prefix}
            ):
                with self.assertRaises(tn.TrialNetworkError):
                    tn.pool_settings()

    def test_project_names_follow_harbors_sanitizer(self):
        self.assertEqual(tn.project_name("igel-persist-feature-schema__uZs6SxG"), "igel-persist-feature-schema__uzs6sxg")
        self.assertEqual(tn.project_name("_x.y__AB"), "0_x-y__ab")


class AllocationTests(FakeHost):
    def test_allocation_skips_docker_routes_addresses_and_reservations(self):
        self.set_docker([network("someone_default", "10.213.0.0/28", "someone")], [])
        self.with_ip(
            route="default via 100.69.38.1 dev wlp0s20f3 proto dhcp\n10.213.0.16/28 dev wg0 proto kernel scope link\n",
            addr="5: br-x    inet 10.213.0.33/28 brd 10.213.0.47 scope global br-x\\       valid_lft forever\n",
        )
        self.reserve("10.213.0.48/28", os.getpid(), "running__aaaaaaa__env")
        self.assertEqual(tn.allocate("alpha__bbbbbbb__env"), "10.213.0.64/28")
        held = self.reservations()
        self.assertEqual(held["10.213.0.64/28"]["trial"], "alpha__bbbbbbb__env")
        self.assertEqual(held["10.213.0.64/28"]["pid"], os.getpid())
        self.assertIn("10.213.0.48/28", held)

    def test_each_trial_gets_its_own_subnet_and_release_frees_it(self):
        first = tn.allocate("a__env")
        second = tn.allocate("b__env")
        self.assertNotEqual(first, second)
        tn.release(first)
        self.assertNotIn(first, self.reservations())
        self.assertEqual(tn.allocate("c__env"), first)

    def test_processes_allocating_at_once_never_share_a_subnet(self):
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(LIB)!r})\n"
            "import trial_network as tn\n"
            "print(' '.join(tn.allocate(f'{sys.argv[1]}-{i}__env') for i in range(5)), flush=True)\n"
            "sys.stdin.read()\n"
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", script, f"build{n}"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True)
            for n in range(6)
        ]
        try:
            subnets = [subnet for proc in procs for subnet in proc.stdout.readline().split()]
            self.assertEqual(len(subnets), 30)
            self.assertEqual(len(set(subnets)), 30)
            self.assertEqual(set(self.reservations()), set(subnets))
        finally:
            for proc in procs:
                proc.communicate("")

    def test_a_dead_trials_reservation_is_reclaimed(self):
        self.reserve("10.213.0.0/28", self.dead_pid(), "crashed__env")
        self.reserve("10.213.0.16/28", os.getpid(), "alive__env")
        self.assertEqual(tn.allocate("next__env"), "10.213.0.0/28")
        self.assertEqual(self.reservations()["10.213.0.0/28"]["trial"], "next__env")
        self.assertIn("10.213.0.16/28", self.reservations())

    def test_a_full_pool_allocates_nothing(self):
        os.environ[tn.POOL_ENV] = "10.213.0.0/27"
        self.set_docker([network("a_default", "10.213.0.0/28", "a"), network("b_default", "10.213.0.16/28", "b")], [])
        self.assertIsNone(tn.allocate("c__env"))
        self.assertEqual(self.reservations(), {})

    def test_without_ip_only_docker_counts(self):
        self.assertIsNone(__import__("shutil").which("ip"))
        self.assertEqual(tn.host_ranges(), [])
        self.set_docker([network("x_default", "10.213.0.0/28", "x")], [])
        self.assertEqual(tn.allocate("a__env"), "10.213.0.16/28")

    def test_macos_routes_and_addresses_are_read_from_netstat_and_ifconfig(self):
        builtin_cat = "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done <<'EOF'\n"
        write_script(
            self.bin / "netstat",
            builtin_cat + "Routing tables\n\nInternet:\n"
            "Destination        Gateway            Flags               Netif Expire\n"
            "default            192.168.1.1        UGScg                 en0\n"
            "127                127.0.0.1          UCS                   lo0\n"
            "192.168.1          link#6             UCS                   en0      !\n"
            "10.8/16            utun4              USc                 utun4\n"
            "192.168.1.23/32    link#6             UCS                   en0      !\n"
            "EOF\n",
        )
        write_script(
            self.bin / "ifconfig",
            builtin_cat + "lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n"
            "\tinet 127.0.0.1 netmask 0xff000000\n"
            "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
            "\tinet6 fe80::1%en0 prefixlen 64 secured scopeid 0x6\n"
            "\tinet 192.168.1.23 netmask 0xffffff00 broadcast 192.168.1.255\n"
            "EOF\n",
        )
        with mock.patch.object(tn.platform, "system", return_value="Darwin"):
            got = {(str(net), source) for net, source in tn.host_ranges()}
        self.assertEqual(
            got,
            {
                ("127.0.0.0/8", "route on lo0"),
                ("192.168.1.0/24", "route on en0"),
                ("10.8.0.0/16", "route on utun4"),
                ("192.168.1.23/32", "route on en0"),
                ("127.0.0.0/8", "address on lo0"),
                ("192.168.1.0/24", "address on en0"),
            },
        )

    def test_routes_and_addresses_are_read_like_a_real_host(self):
        self.with_ip(
            route=(
                "default via 100.69.38.1 dev wlp0s20f3 proto dhcp src 100.69.38.126 metric 600 \n"
                "10.14.0.0/16 dev surfshark_wg proto kernel scope link src 10.14.0.2 metric 50 \n"
                "172.16.0.36 dev surfshark_wg proto static scope link metric 50 \n"
                "blackhole 10.213.9.0/24 proto static\n"
            ),
            addr="219: surfshark_wg    inet 10.14.0.2/16 brd 10.14.255.255 scope global noprefixroute surfshark_wg\\\n",
        )
        got = {(str(net), source) for net, source in tn.host_ranges()}
        self.assertEqual(
            got,
            {
                ("10.14.0.0/16", "route on surfshark_wg"),
                ("172.16.0.36/32", "route on surfshark_wg"),
                ("10.213.9.0/24", "route on ?"),
                ("10.14.0.0/16", "address on surfshark_wg"),
            },
        )


class CheckTests(FakeHost):
    def test_check_passes_records_and_names_the_overlap(self):
        os.environ[tn.POOL_ENV] = "10.213.0.0/27"
        self.set_docker([network("foreign_default", "10.213.0.0/28", "foreign")], [])
        out = self.root / "trial_network.json"
        proc = self.cli("check", "--need", "1", "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK trial networks: 1 of 2 /28 subnets free in 10.213.0.0/27 (need 1)", proc.stdout)
        self.assertIn("note: 10.213.0.0/28 (Docker network foreign_default) overlaps 10.213.0.0/27", proc.stdout)
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(
            {k: doc[k] for k in ("mode", "pool", "prefix", "free", "total", "need")},
            {"mode": "subnets", "pool": "10.213.0.0/27", "prefix": 28, "free": 1, "total": 2, "need": 1},
        )
        self.assertEqual(doc["overlaps"], [{"range": "10.213.0.0/28", "source": "Docker network foreign_default"}])
        self.assertTrue(doc["checked_at"])

    def test_check_fails_when_fewer_subnets_are_free_than_trials_run_at_once(self):
        os.environ[tn.POOL_ENV] = "10.213.0.0/27"
        self.set_docker([network("foreign_default", "10.213.0.0/28", "foreign")], [])
        out = self.root / "trial_network.json"
        out.write_text("{}", encoding="utf-8")
        proc = self.cli("check", "--need", "2", "--out", str(out))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(
            proc.stderr.startswith("ERROR: only 1 of 2 /28 subnets in 10.213.0.0/27 are free, "
                                   "and this build runs 2 trial(s) at once"),
            proc.stderr,
        )
        self.assertIn("10.213.0.0/28 (Docker network foreign_default) overlaps 10.213.0.0/27", proc.stderr)
        self.assertIn(f"hint: set {tn.POOL_ENV}", proc.stderr)
        self.assertFalse(out.exists(), "a failed check leaves no record behind")

    def test_running_trials_reservations_count_against_free(self):
        os.environ[tn.POOL_ENV] = "10.213.0.0/27"
        self.reserve("10.213.0.0/28", os.getpid(), "other-build__env")
        proc = self.cli("check", "--need", "2", "--out", str(self.root / "t.json"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("(1 reserved by running trials)", proc.stderr)

    def test_an_invalid_pool_or_no_docker_is_an_error(self):
        proc = self.cli("check", "--out", str(self.root / "t.json"), env={tn.POOL_ENV: "8.8.8.0/24"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"ERROR: {tn.POOL_ENV}=8.8.8.0/24 must be a private IPv4 range", proc.stderr)
        proc = self.cli("check", "--out", str(self.root / "t.json"), env={"FAKE_DOCKER_DOWN": "1"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR: docker network ls failed: Cannot connect to the Docker daemon", proc.stderr)


class OverrideRun(FakeHost):
    def setUp(self):
        super().setUp()
        self.site = self.root / "site"
        self.fake_harbor(FAKE_HARBOR_DOCKER)
        self.base = self.root / "environment"
        self.base.mkdir()
        (self.base / "docker-compose-build.yaml").write_text("services:\n  main: {}\n", encoding="utf-8")

    def fake_harbor(self, docker_py: str | None) -> None:
        pkg = self.site / "harbor" / "environments" / "docker"
        pkg.mkdir(parents=True, exist_ok=True)
        for init in (self.site / "harbor", pkg.parent, pkg):
            (init / "__init__.py").write_text("", encoding="utf-8")
        if docker_py is None:
            (pkg / "docker.py").unlink(missing_ok=True)
        else:
            (pkg / "docker.py").write_text(textwrap.dedent(docker_py), encoding="utf-8")

    def python(self, code: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", code, *args],
            capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONPATH": f"{self.site}:{LIB}", **(env or {})},
        )

    def trials(self, *sessions: str, env: dict | None = None) -> tuple[dict, str]:
        proc = self.python(DRIVER, str(self.base), *sessions, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.splitlines()[-1]), proc.stderr


class OverrideTests(OverrideRun):
    def test_each_environment_reserves_once_and_releases_after_its_own_down(self):
        got, err = self.trials("alpha__Aa11111__env", "beta__Bb22222__env")
        self.assertTrue(got["applied"])
        self.assertEqual(err, "")
        alpha, beta = got["trials"]
        self.assertTrue(alpha["started"] and beta["started"])
        self.assertEqual(len(alpha["paths"]), 2, "one compose file added, once, however often Harbor asks")
        self.assertEqual(
            alpha["extra"],
            ["networks:\n  default:\n    ipam:\n      config:\n        - subnet: 10.213.0.0/28\n"],
        )
        self.assertIn("subnet: 10.213.0.16/28", beta["extra"][0])
        self.assertEqual(
            got["reserved_while_running"],
            {"10.213.0.0/28": "alpha__aa11111__env", "10.213.0.16/28": "beta__bb22222__env"},
        )
        self.assertEqual(
            self.docker_state()["created"],
            [
                {"name": "alpha__aa11111__env_default", "subnet": "10.213.0.0/28"},
                {"name": "beta__bb22222__env_default", "subnet": "10.213.0.16/28"},
            ],
        )
        # The fake `compose down` fails on a missing -f file, so down ran while the file existed.
        self.assertEqual(self.network_names(), set())
        self.assertEqual(got["reserved_after"], {})
        self.assertEqual(alpha["files_left"] + beta["files_left"], [])

    def test_no_free_subnet_warns_and_keeps_dockers_default(self):
        self.set_docker([network("foreign_default", "10.213.0.0/28", "foreign")], [])
        got, err = self.trials("alpha__Aa11111__env", env={tn.POOL_ENV: "10.213.0.0/28"})
        self.assertIn("WARNING: alpha__aa11111__env: every trial subnet is taken; Docker's default address pools apply", err)
        trial = got["trials"][0]
        self.assertTrue(trial["started"])
        self.assertEqual(len(trial["paths"]), 1)
        self.assertEqual(self.docker_state()["created"][0]["subnet"], "172.80.0.0/24")

    def test_a_harbor_without_the_hooks_is_left_unchanged(self):
        check = "import harbor_network_override as o; from harbor.environments.docker.docker import DockerEnvironment as D; print(getattr(D, o.MARK, False))"
        self.fake_harbor("class DockerEnvironment:\n    def _docker_compose_paths(self):\n        return []\n")
        proc = self.python(check)
        self.assertEqual(proc.stdout.strip(), "False")
        self.assertIn("WARNING: this Harbor has no DockerEnvironment._docker_compose_paths property / stop(); "
                      "trial networks left to Docker", proc.stderr)
        self.fake_harbor(None)
        proc = self.python("import harbor_network_override")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("WARNING: Harbor's DockerEnvironment is not importable", proc.stderr)

    def test_importing_twice_patches_once(self):
        code = (
            "import importlib, harbor_network_override as o\n"
            "from harbor.environments.docker.docker import DockerEnvironment as D\n"
            "first = D.stop\n"
            "assert o.apply() is True\n"
            "print(D.stop is first)\n"
        )
        proc = self.python(code)
        self.assertEqual(proc.stdout.strip(), "True", proc.stderr)

    def test_installed_harbor_has_the_hooks(self):
        from harbor_egress import EgressError, harbor_python

        try:
            with mock.patch.dict(os.environ, {"PATH": HOST_PATH}):
                interp = harbor_python()
        except EgressError as exc:
            self.skipTest(f"no Harbor installed here ({exc})")
        code = (
            "import inspect, harbor_network_override as o\n"
            "from harbor.environments.docker.docker import DockerEnvironment as D\n"
            "print(getattr(D, o.MARK, False), isinstance(D.__dict__[o.PATHS], property), inspect.iscoroutinefunction(D.stop))\n"
        )
        proc = subprocess.run(
            [interp, "-c", code], capture_output=True, text=True, check=False,
            env={**os.environ, "PATH": f"{self.bin}:/usr/bin:/bin", "PYTHONPATH": str(LIB)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ["True", "True", "True"], proc.stderr)
        self.assertNotIn("WARNING", proc.stderr)


class WorksWithoutRootTests(OverrideRun):
    """Plan section 4: the trial gets a network in every state a non-root user can meet."""

    def test_dockers_pools_exhausted(self):
        os.environ["FAKE_POOLS_FULL"] = "1"
        self.set_docker([network("tddhost-1-reserve-0", "10.231.0.16/28", "tddhost-1")], [])
        got, _ = self.trials("alpha__Aa11111__env", env={tn.SWITCH_ENV: "off"})
        self.assertFalse(got["trials"][0]["started"])
        self.assertIn("all predefined address pools have been fully subnetted", got["trials"][0]["error"])
        got, err = self.trials("alpha__Aa11111__env")
        self.assertTrue(got["trials"][0]["started"], got)
        self.assertEqual(self.docker_state()["created"][-1]["subnet"], "10.213.0.0/28")
        self.assertEqual(self.network_names(), {"tddhost-1-reserve-0"})

    def test_healthy_or_enlarged_pools_even_inside_10_213(self):
        os.environ["FAKE_DEFAULT_POOL"] = "10.213.0.0/16"
        self.set_docker([network("old-trial__x__env_default", "10.213.0.0/24", "old-trial__x__env")], [])
        got, _ = self.trials("alpha__Aa11111__env")
        self.assertTrue(got["trials"][0]["started"])
        self.assertEqual(self.docker_state()["created"][-1]["subnet"], "10.213.1.0/28")

    def test_someone_elses_10_213_networks_are_skipped_and_kept(self):
        self.set_docker(
            [network("manual-net", "10.213.0.0/28"), network("tddhost-9-reserve-0", "10.213.0.16/28", "tddhost-9")],
            [container("tddhost-9-main", "exited", "tddhost-9", "tddhost-9-reserve-0")],
        )
        proc = self.cli("check", "--out", str(self.root / "t.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("note: 10.213.0.0/28 (Docker network manual-net) overlaps 10.213.0.0/16", proc.stdout)
        self.assertIn("note: 10.213.0.16/28 (Docker network tddhost-9-reserve-0) overlaps", proc.stdout)
        got, _ = self.trials("alpha__Aa11111__env")
        self.assertEqual(self.docker_state()["created"][-1]["subnet"], "10.213.0.32/28")
        proc = self.cli("cleanup", "--root", str(self.root / "no-trials-here"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.network_names(), {"manual-net", "tddhost-9-reserve-0"})

    def test_off_leaves_harbor_and_docker_alone(self):
        got, err = self.trials("alpha__Aa11111__env", env={tn.SWITCH_ENV: "off"})
        self.assertFalse(got["applied"])
        self.assertEqual(err, "")
        self.assertEqual(len(got["trials"][0]["paths"]), 1)
        self.assertEqual(self.docker_state()["created"][0]["subnet"], "172.80.0.0/24")
        out = self.root / "t.json"
        proc = self.cli("check", "--need", "8", "--out", str(out), env={tn.SWITCH_ENV: "OFF"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "OK trial networks: Docker's own address pools (MAC_K3D_TRIAL_SUBNETS=off)")
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["mode"], "docker_default")


# The cloud worker on 2026-10-08: four exited trials of this worker, everything else another user's.
OWN = (
    "igel-persist-feature-schema__uZs6SxG",
    "ts-pattern-match-each__yg65ruF",
    "wazero-multi-module-snapshots__u6wRwxu",
    "ytt-jsonpath-query-api__jbz7ufr",
)
FOREIGN_NETWORKS = {
    "tddhost-4242-reserve-0",
    "rsi-coverage-77_default",
    "instance_flipt-io__flipt-ee02_default",
    "deepswe-x__abc1234__verifier__trial_default",
    "cpython_5__k9j8h7g__env_default",
    "fastapi_1_default",
    "pier-egress-uplink",
    "cg-verified-internal",
    "manual-pool-net",
    "other-user__qqqqqqq__env_default",
}


class CleanupTests(FakeHost):
    def setUp(self):
        super().setUp()
        self.with_ip()
        ws = self.root / "workspace"
        self.workspace = ws / "deepswe_one_task"
        runs = self.workspace / "eval-runs" / "harness" / "harbor_runs"
        for name in OWN:
            trial_dir(job_dir(runs / "jenkins-6" / "icode_deepswe_6") / name)
        trial_dir(runs / "jenkins-7" / "icode_deepswe_7" / "live-task__AbCdEfG")
        # An older per-task layout in another job's workspace, one level deeper.
        old = ws / "lolbench_one_task" / "eval-runs" / "harness" / "harbor_runs" / "jenkins-3"
        trial_dir(old / "abs-module-cache-flags" / "abs-module-cache-flags_icode_3_a01" / "abs-module-cache-flags__dkbj2wm")
        # The canary's <task> folder is named like a foreign project; it is not a trial.
        canary = self.workspace / "eval-runs" / "canary" / "jenkins-6" / "fastapi_1"
        trial_dir(job_dir(canary / "fastapi_1_canary_6") / "fastapi_1__Q1w2E3r")
        own = [tn.project_name(n) for n in OWN]
        networks = [network(f"{p}__env_default", f"192.168.{16 * i}.0/20", f"{p}__env") for i, p in enumerate(own)]
        containers = [container(f"{p}__env-main-1", "exited", f"{p}__env", f"{p}__env_default") for p in own]
        containers.append(container(f"{own[0]}__env-sidecar-1", "exited", f"{own[0]}__env", f"{own[0]}__env_default"))
        networks.append(network(f"{own[0]}__verifier__trial_default", "172.20.0.0/16", f"{own[0]}__verifier__trial"))
        networks.append(network("live-task__abcdefg__env_default", "10.213.0.0/28", "live-task__abcdefg__env"))
        containers.append(container("live-task__abcdefg__env-main-1", "running", "live-task__abcdefg__env",
                                    "live-task__abcdefg__env_default"))
        for suffix in ("default", "pier-egress-internal"):
            networks.append(network(f"abs-module-cache-flags__dkbj2wm_{suffix}", None, "abs-module-cache-flags__dkbj2wm"))
        networks.append(network("gone-task__zzzzzzz__env_default", "10.213.0.16/28", "gone-task__zzzzzzz__env"))
        networks.append(network("busy-task__yyyyyyy__env_default", "10.213.0.32/28", "busy-task__yyyyyyy__env"))
        self.reserve("10.213.0.32/28", os.getpid(), "busy-task__yyyyyyy__env")
        foreign = [
            ("tddhost-4242-reserve-0", "10.231.0.16/28", "tddhost-4242"),
            ("rsi-coverage-77_default", "172.21.0.0/16", "rsi-coverage-77"),
            ("instance_flipt-io__flipt-ee02_default", "172.22.0.0/16", "instance_flipt-io__flipt-ee02"),
            ("deepswe-x__abc1234__verifier__trial_default", "172.23.0.0/16", "deepswe-x__abc1234__verifier__trial"),
            ("cpython_5__k9j8h7g__env_default", "172.24.0.0/16", "cpython_5__k9j8h7g__env"),
            ("fastapi_1_default", "172.25.0.0/16", "fastapi_1"),
            ("other-user__qqqqqqq__env_default", "10.213.0.64/28", "other-user__qqqqqqq__env"),
        ]
        for name, subnet, project in foreign:
            networks.append(network(name, subnet, project))
            containers.append(container(f"{project}-main-1", "exited", project, name))
        networks += [
            network("pier-egress-uplink", "10.63.255.0/24"),
            network("cg-verified-internal", "172.26.0.0/16"),
            network("manual-pool-net", "10.213.0.48/28"),
        ]
        self.set_docker(networks, containers)
        self.foreign_containers = {f"{project}-main-1" for _, _, project in foreign}

    def roots(self) -> list[str]:
        ws = self.workspace.parent
        return [
            "--root", str(self.workspace / "eval-runs" / "harness" / "harbor_runs"),
            "--root", str(self.workspace / "eval-runs" / "canary"),
            "--root", str(ws / "lolbench_one_task" / "eval-runs" / "harness" / "harbor_runs"),
        ]

    def test_cleanup_removes_only_this_workers_stopped_trials(self):
        proc = self.cli("cleanup", *self.roots())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.network_names(),
            FOREIGN_NETWORKS | {"live-task__abcdefg__env_default", "busy-task__yyyyyyy__env_default"},
        )
        self.assertEqual(self.container_names(), self.foreign_containers | {"live-task__abcdefg__env-main-1"})
        out = proc.stdout
        self.assertIn(
            "cleanup: removed igel-persist-feature-schema__uzs6sxg__env (2 container(s), "
            "network igel-persist-feature-schema__uzs6sxg__env_default)",
            out,
        )
        for name in OWN[1:]:
            self.assertIn(f"cleanup: removed {tn.project_name(name)}__env (1 container(s)", out)
        self.assertIn("cleanup: removed igel-persist-feature-schema__uzs6sxg__verifier__trial (0 container(s)", out)
        self.assertIn("cleanup: removed abs-module-cache-flags__dkbj2wm (0 container(s), network "
                      "abs-module-cache-flags__dkbj2wm_default, abs-module-cache-flags__dkbj2wm_pier-egress-internal)", out)
        self.assertIn("cleanup: kept live-task__abcdefg__env: live-task__abcdefg__env-main-1 is running", out)
        self.assertIn("cleanup: removed unused pool network gone-task__zzzzzzz__env_default (10.213.0.16/28)", out)
        log = self.log.read_text(encoding="utf-8")
        self.assertNotIn("prune", log)
        self.assertNotIn("rm -f", log)
        again = self.cli("cleanup", *self.roots())
        self.assertIn("cleanup: no leftovers of this worker's trials", again.stdout)
        self.assertNotIn("removed", again.stdout)

    def test_env_network_step_finds_the_agents_workspaces(self):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{HOST_PATH}",
            "WORKSPACE": str(self.workspace),
            "MAC_K3D_EVAL_WORKDIR": str(self.workspace / "eval-runs"),
        }
        proc = subprocess.run(
            ["bash", str(STAGES / "env" / "network.sh")], capture_output=True, text=True, check=False, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("cleanup: removed abs-module-cache-flags__dkbj2wm", proc.stdout)
        self.assertIn("cleanup: removed ytt-jsonpath-query-api__jbz7ufr__env", proc.stdout)
        self.assertIn("OK trial networks: ", proc.stdout)
        self.assertTrue((self.workspace / "eval-runs" / "trial_network.json").is_file())
        self.assertTrue(FOREIGN_NETWORKS <= self.network_names())

    def test_env_network_step_fails_without_a_free_subnet(self):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{HOST_PATH}",
            "MAC_K3D_EVAL_WORKDIR": str(self.root / "work"),
            tn.POOL_ENV: "10.213.0.0/28",
        }
        proc = subprocess.run(
            ["bash", str(STAGES / "env" / "network.sh")], capture_output=True, text=True, check=False, env=env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR: only 0 of 1 /28 subnets in 10.213.0.0/28 are free", proc.stderr)
        self.assertIn("ERROR: Harbor trials on this worker cannot get a Docker network", proc.stderr)


class TeardownTests(FakeHost):
    def setUp(self):
        super().setUp()
        runs = self.root / "harbor_runs"
        self.jobs = runs / "jenkins-9"
        trial_dir(job_dir(self.jobs / "icode_deepswe_9") / "alpha__Aa11111")
        trial_dir(self.jobs / "icode_deepswe_9" / "beta__Bb22222")
        trial_dir(runs / "jenkins-8" / "icode_deepswe_8" / "gamma__Cc33333")
        networks, containers = [], []
        for project, state, subnet in (
            ("alpha__aa11111__env", "running", "10.213.0.0/28"),
            ("beta__bb22222__env", "exited", "10.213.0.16/28"),
            ("gamma__cc33333__env", "running", "10.213.0.32/28"),
            ("tddhost-1", "running", "10.231.0.16/28"),
        ):
            networks.append(network(f"{project}_default", subnet, project))
            containers.append(container(f"{project}-main-1", state, project, f"{project}_default"))
        containers.append(container("alpha__aa11111__env-sidecar-1", "running", "alpha__aa11111__env",
                                    "alpha__aa11111__env_default"))
        self.set_docker(networks, containers)
        self.reserve("10.213.0.0/28", os.getpid(), "alpha__aa11111__env")
        self.reserve("10.213.0.32/28", os.getpid(), "gamma__cc33333__env")

    def assert_only_this_build_removed(self):
        self.assertEqual(self.network_names(), {"gamma__cc33333__env_default", "tddhost-1_default"})
        self.assertEqual(self.container_names(), {"gamma__cc33333__env-main-1", "tddhost-1-main-1"})
        self.assertEqual(set(self.reservations()), {"10.213.0.32/28"})

    def test_teardown_removes_every_trial_under_the_jobs_dir_even_running(self):
        proc = self.cli("teardown", "--jobs-dir", str(self.jobs))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_only_this_build_removed()
        self.assertIn("teardown: removed alpha__aa11111__env (2 container(s), network alpha__aa11111__env_default)",
                      proc.stdout)
        self.assertIn("teardown: removed beta__bb22222__env (1 container(s)", proc.stdout)
        self.assertNotIn("prune", self.log.read_text(encoding="utf-8"))

    def test_a_missing_or_empty_jobs_dir_touches_nothing(self):
        proc = self.cli("teardown", "--jobs-dir", str(self.root / "harbor_runs" / "jenkins-404"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8"), "")

    def _step(self, script: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{HOST_PATH}",
            "MAC_K3D_EVAL_WORKDIR": str(self.root / "work"),
        }
        harbor_env = self.root / ".harbor-env"
        harbor_env.write_text("DEEPSEEK_API_KEY=placeholder-not-a-token\n", encoding="utf-8")
        return subprocess.run(
            ["bash", "-c", f'source "$1"; source "$2"; HARBOR_ENV="$3"; teardown_on_exit "$4"; {script}',
             "_", str(STAGES / "_common.sh"), str(STAGES / "evaluate" / "harbor_cmd.sh"), str(harbor_env), str(self.jobs)],
            capture_output=True, text=True, check=False, env=env,
        )

    def test_the_evaluate_step_tears_down_when_stopped(self):
        proc = self._step("kill -TERM $$; sleep 5")
        self.assertEqual(proc.returncode, 143, proc.stderr)
        self.assert_only_this_build_removed()
        self.assertFalse((self.root / ".harbor-env").exists())

    def test_the_evaluate_step_tears_down_on_a_normal_exit(self):
        proc = self._step("exit 0")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_only_this_build_removed()
        self.assertFalse((self.root / ".harbor-env").exists())


class WiringTests(unittest.TestCase):
    def test_env_checks_networks_after_egress(self):
        text = (STAGES / "env.sh").read_text(encoding="utf-8")
        self.assertIn("env/egress env/network env/model_api", text)
        self.assertIn('"$WORKDIR/trial_network.json"', text)
        self.assertIn('"$WORKDIR/unscored_rollouts.txt"', text)
        step = (STAGES / "env" / "network.sh").read_text(encoding="utf-8")
        self.assertLess(step.index('trial_network.py" cleanup'), step.index('trial_network.py" check --need 1'))
        self.assertIn('--out "$WORKDIR/trial_network.json"', step)

    def test_evaluate_checks_its_parallel_degree_and_tears_down_on_exit(self):
        cmd = (STAGES / "evaluate" / "harbor_cmd.sh").read_text(encoding="utf-8")
        self.assertIn('trial_network.py" check --need "$EVAL_SLOTS" --out "$record"', cmd)
        self.assertIn('record-trial-network --inputs "$PROTOCOL_INPUTS" --record "$record"', cmd)
        self.assertIn('trial_network.py" teardown --jobs-dir "$TEARDOWN_DIR" || true; rm -f "$HARBOR_ENV"\' EXIT', cmd)
        self.assertIn("trap 'rm -f \"$HARBOR_ENV\"' EXIT", cmd)
        for name, jobs in (("harbor_run.sh", '"$JOBS_DIR"'), ("canary.sh", '"$CANARY_DIR"')):
            text = (STAGES / "evaluate" / name).read_text(encoding="utf-8")
            with self.subTest(step=name):
                self.assertEqual(text.count("\nensure_trial_network\n"), 1)
                self.assertIn(f"\nensure_harbor_egress\nensure_trial_network\nteardown_on_exit {jobs}\n", text)
                self.assertGreater(text.index("\nensure_trial_network\n"), text.index("if harbor_dry_run; then"))
        run = (STAGES / "evaluate" / "harbor_run.sh").read_text(encoding="utf-8")
        self.assertLess(run.index("teardown_on_exit"), run.index("\nrun_harbor\n"))

    def test_agent_imports_both_overrides_before_harbor(self):
        for name in ("icode_harbor_agent.py", "env_check_agents.py"):
            src = (LIB / name).read_text(encoding="utf-8")
            imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from ")) and "__future__" not in ln]
            with self.subTest(agent=name):
                self.assertTrue(imports[0].startswith("import harbor_probe_override"), imports[:3])
                self.assertTrue(imports[1].startswith("import harbor_network_override"), imports[:3])


class ProvenanceTests(unittest.TestCase):
    def test_the_check_is_recorded_and_shown(self):
        from provenance import isolation_lines, isolation_view, main

        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "eval_protocol_inputs.json"
            inputs.write_text(json.dumps({"isolation": {"mode": "git"}}), encoding="utf-8")
            record = Path(tmp) / "trial_network.json"
            record.write_text(
                json.dumps({"mode": "subnets", "pool": "10.213.0.0/16", "prefix": 28, "free": 4093, "total": 4096,
                            "need": 4, "overlaps": [], "checked_at": "2026-10-08T10:00:00+00:00"}),
                encoding="utf-8",
            )
            self.assertEqual(main(["record-trial-network", "--inputs", str(inputs), "--record", str(record)]), 0)
            stored = json.loads(inputs.read_text(encoding="utf-8"))
            self.assertEqual(stored["isolation"]["mode"], "git")
            self.assertEqual(stored["isolation"]["trial_network"]["free"], 4093)
            lines = isolation_lines(isolation_view(stored["isolation"]))
            self.assertIn("Trial networks: own /28 subnets from 10.213.0.0/16 · 4093 of 4096 free at check · need 4", lines)
            record.write_text(json.dumps({"mode": "docker_default", "need": 4}), encoding="utf-8")
            main(["record-trial-network", "--inputs", str(inputs), "--record", str(record)])
            lines = isolation_lines(isolation_view(json.loads(inputs.read_text(encoding="utf-8"))["isolation"]))
            self.assertIn("Trial networks: Docker's default address pools (MAC_K3D_TRIAL_SUBNETS=off)", lines)

    def test_an_older_run_has_no_trial_networks_line(self):
        from provenance import isolation_lines, isolation_view

        lines = isolation_lines(isolation_view({"mode": "git"}))
        self.assertFalse(any(line.startswith("Trial networks") for line in lines))


class OwnerSweepTests(unittest.TestCase):
    def _owner(self, path: Path, pid: int, started: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "owner.json").write_text(json.dumps({"pid": pid, "started": started}) + "\n", encoding="utf-8")

    def test_a_dead_owner_is_swept_and_a_live_one_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._owner(root / "live", os.getpid(), tn.process_start(os.getpid()) or "")
            self._owner(root / "dead", 1 << 30, "gone")
            with mock.patch.object(tn, "teardown", return_value=1) as torn, mock.patch("sys.stdout") as out:
                out.write = mock.Mock()
                self.assertEqual(tn.sweep([root]), 1)
            torn.assert_called_once_with(root / "dead", quiet=True)
            text = "".join(call.args[0] for call in out.write.call_args_list)
            self.assertEqual(text, "sweep: removed 1 project(s) of dead owners\n")
            self.assertNotIn(str(1 << 30), text)
            self.assertTrue(tn.owner_alive({"pid": os.getpid(), "started": tn.process_start(os.getpid())}))
            self.assertFalse(tn.owner_alive({"pid": 1 << 30, "started": "gone"}))

    def test_a_live_owner_is_seen_without_proc_or_ps_on_path(self):
        real_is_file = Path.is_file

        def no_proc(self):
            if str(self).startswith("/proc/"):
                return False
            return real_is_file(self)

        with mock.patch.object(Path, "is_file", no_proc), mock.patch.dict(os.environ, {"PATH": "/no/such/bin"}):
            token = tn.process_start(os.getpid())
            self.assertTrue(token)
            self.assertEqual(tn.process_start(os.getpid()), token)
            self.assertTrue(tn.owner_alive({"pid": os.getpid(), "started": token}))
            self.assertFalse(tn.owner_alive({"pid": 1 << 30, "started": "gone"}))

    def test_sweep_prints_one_line_when_nothing_is_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("old-a", "old-b"):
                self._owner(root / name, 1 << 30, "gone")
            with mock.patch.object(tn, "teardown", return_value=0), mock.patch("sys.stdout") as out:
                out.write = mock.Mock()
                self.assertEqual(tn.sweep([root]), 0)
            text = "".join(call.args[0] for call in out.write.call_args_list)
            self.assertEqual(text, "sweep: 2 dead owners, nothing left to remove\n")
            self.assertNotIn("pid", text)
            with mock.patch("sys.stdout") as out:
                out.write = mock.Mock()
                self.assertEqual(tn.sweep([root / "missing"]), 0)
            self.assertEqual(
                "".join(call.args[0] for call in out.write.call_args_list),
                "sweep: no dead owners\n",
            )


class StoppedRunTests(FakeHost):
    def test_a_stopped_run_is_force_removed_and_a_finished_one_is_cleaned_up(self):
        runs = self.root / "ws" / "deepswe_one_task" / "eval-runs" / "harness" / "harbor_runs"
        dead = runs / "jenkins-9"
        live = runs / "jenkins-10"
        done = runs / "jenkins-8"
        old = runs / "jenkins-7"
        trial_dir(job_dir(dead / "job") / "stopped__Aa11111")
        trial_dir(job_dir(live / "job") / "running__Bb22222")
        trial_dir(job_dir(done / "job") / "finished__Cc33333")
        trial_dir(job_dir(old / "job") / "gone__Dd44444")
        (dead / "owner.json").write_text(json.dumps({"pid": 1 << 30, "started": "gone"}) + "\n", encoding="utf-8")
        (old / "owner.json").write_text(json.dumps({"pid": 1 << 30, "started": "gone"}) + "\n", encoding="utf-8")
        (live / "owner.json").write_text(
            json.dumps({"pid": os.getpid(), "started": tn.process_start(os.getpid())}) + "\n",
            encoding="utf-8",
        )
        networks, containers = [], []
        for project, state, subnet in (
            ("stopped__aa11111__env", "running", "10.213.0.0/28"),
            ("running__bb22222__env", "running", "10.213.0.16/28"),
            ("finished__cc33333__env", "exited", "10.213.0.32/28"),
        ):
            networks.append(network(f"{project}_default", subnet, project))
            containers.append(container(f"{project}-main-1", state, project, f"{project}_default"))
        self.set_docker(networks, containers)
        self.reserve("10.213.0.0/28", 1 << 30, "stopped__aa11111__env")
        self.reserve("10.213.0.16/28", os.getpid(), "running__bb22222__env")

        sweep = self.cli("sweep", "--root", str(runs))
        self.assertEqual(sweep.returncode, 0, sweep.stderr)
        self.assertEqual(sweep.stdout, "sweep: removed 1 project(s) of dead owners\n")
        self.assertNotIn("jenkins-", sweep.stdout)
        self.assertEqual(
            self.container_names(),
            {"running__bb22222__env-main-1", "finished__cc33333__env-main-1"},
        )
        self.assertEqual(set(self.reservations()), {"10.213.0.16/28"})

        clean = self.cli("cleanup", "--root", str(runs))
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIn("cleanup: removed finished__cc33333__env (1 container(s)", clean.stdout)
        self.assertIn(
            "cleanup: kept running__bb22222__env: running__bb22222__env-main-1 is running",
            clean.stdout,
        )
        self.assertEqual(self.container_names(), {"running__bb22222__env-main-1"})
        self.assertEqual(self.network_names(), {"running__bb22222__env_default"})

        again = self.cli("sweep", "--root", str(runs))
        self.assertEqual(again.stdout, "sweep: 2 dead owners, nothing left to remove\n")
        self.assertEqual(self.container_names(), {"running__bb22222__env-main-1"})


if __name__ == "__main__":
    unittest.main()
