#!/usr/bin/env python3
"""Give every Harbor trial network its own subnet from a range mac-k3d owns.

Without one, Docker takes each trial's compose `default` network from its
built-in address pools. On a shared worker those run out, and `docker compose
up` fails with `all predefined address pools have been fully subnetted` before
the trial starts. harbor_network_override.py asks allocate() for a /PREFIX
from POOL instead:

  check --out FILE --need N  is the pool valid, what overlaps it, and are at
                             least N subnets free? Writes FILE
                             (trial_network.json); exit 1 when not.
  cleanup --root DIR ...     this worker's own leftovers: the exited
                             containers and the networks of compose projects
                             named after a trial folder under a DIR, then pool
                             networks of compose projects no container uses.
                             A project with a container still running is kept.
  teardown --jobs-dir DIR    every compose project of a trial under DIR,
                             running or not (a cancelled build), and the
                             reservations of those trials.

A subnet is taken when it overlaps a Docker network, `ip -4 route` or
`ip -4 -o addr` (no `ip`, as on macOS: Docker only), or a reservation in
trial-subnets.json whose pid is alive. Reservations change under an fcntl lock,
so concurrent trials and builds of one user never pick the same subnet.
Nothing else is removed: no prune, and never a project without a trial folder.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_POOL = "10.213.0.0/16"
DEFAULT_PREFIX = 28
POOL_ENV = "MAC_K3D_TRIAL_SUBNET_POOL"
PREFIX_ENV = "MAC_K3D_TRIAL_SUBNET_PREFIX"
SWITCH_ENV = "MAC_K3D_TRIAL_SUBNETS"
STATE_ENV = "MAC_K3D_TRIAL_STATE_DIR"
LOCK_FILE = "trial-subnets.lock"
RESERVATIONS_FILE = "trial-subnets.json"
PROJECT_LABEL = "com.docker.compose.project"
# A trial network holds a gateway and the egress sidecar (main shares its namespace).
MAX_PREFIX = 29
# In use on the shared cloud worker (routes, addresses and Docker networks, 2026-10-08).
CLOUD_WORKER_RANGES = (
    "10.63.255.0/24",
    "10.231.0.0/16",
    "10.241.0.0/16",
    "10.242.0.0/16",
    "10.243.0.0/16",
    "10.244.0.0/16",
    "10.245.0.0/16",
    "10.246.0.0/16",
    "10.252.0.0/16",
    "47.84.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
)
DOCKER_TIMEOUT_S = 60
# <root>/jenkins-N/<job>/<trial>; the canary has <root>/jenkins-N/<task>/<job>/<trial>.
TRIAL_DEPTH = 4
TRIAL_FILES = ("lock.json", "config.json", "result.json", "trial.log")
DONE_STATES = ("exited", "dead")
ROUTE_TYPES = ("unicast", "local", "broadcast", "multicast", "throw", "unreachable", "prohibit", "blackhole", "nat", "anycast")
RUNBOOK = "docs/testing/cloud-eval-runbook.md (Troubleshooting)"


class TrialNetworkError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def disabled() -> bool:
    return os.environ.get(SWITCH_ENV, "").strip().lower() == "off"


def pool_settings() -> tuple[ipaddress.IPv4Network, int]:
    text = os.environ.get(POOL_ENV, "").strip() or DEFAULT_POOL
    try:
        pool = ipaddress.ip_network(text)
    except ValueError as exc:
        raise TrialNetworkError(f"{POOL_ENV}={text!r} is not a network address ({exc})") from exc
    if pool.version != 4 or not pool.is_private:
        raise TrialNetworkError(f"{POOL_ENV}={text} must be a private IPv4 range such as {DEFAULT_POOL}")
    raw = os.environ.get(PREFIX_ENV, "").strip() or str(DEFAULT_PREFIX)
    if not raw.isdigit() or not pool.prefixlen <= int(raw) <= MAX_PREFIX:
        raise TrialNetworkError(
            f"{PREFIX_ENV}={raw!r} must be a prefix length from {pool.prefixlen} to {MAX_PREFIX} for {pool}"
        )
    return pool, int(raw)


def run(cmd: list[str], timeout: float = DOCKER_TIMEOUT_S) -> tuple[int | None, str, str]:
    """(returncode or None on timeout / missing binary, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {int(timeout)}s: {' '.join(cmd[:4])}"
    except OSError as exc:
        return None, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def first_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return (lines[0] if lines else "")[:300]


def docker(*args: str) -> str:
    rc, out, err = run(["docker", *args])
    if rc != 0:
        raise TrialNetworkError(f"docker {' '.join(args[:2])} failed: {first_line(err) or f'exit {rc}'}")
    return out


def _network(text: str) -> ipaddress.IPv4Network | None:
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None
    return net if net.version == 4 else None


def docker_networks() -> list[dict]:
    """Id, name, compose project and IPv4 subnets of every Docker network."""
    ids = docker("network", "ls", "-q", "--no-trunc").split()
    if not ids:
        return []
    # A network removed after `ls` fails its part of `inspect`; the rest still print.
    rc, out, err = run(["docker", "network", "inspect", *ids])
    try:
        docs = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        docs = None
    if not isinstance(docs, list):
        raise TrialNetworkError(f"docker network inspect failed: {first_line(err) or f'exit {rc}'}")
    networks = []
    for doc in docs:
        configs = (doc.get("IPAM") or {}).get("Config") or []
        subnets = [net for cfg in configs if (net := _network(str((cfg or {}).get("Subnet") or "")))]
        networks.append(
            {
                "id": str(doc.get("Id") or ""),
                "name": str(doc.get("Name") or ""),
                "project": str((doc.get("Labels") or {}).get(PROJECT_LABEL) or ""),
                "subnets": subnets,
            }
        )
    return networks


def host_ranges() -> list[tuple[ipaddress.IPv4Network, str]]:
    """Routes and interface addresses of this host; none without `ip`."""
    if shutil.which("ip") is None:
        return []
    ranges = []
    rc, out, _ = run(["ip", "-4", "route"])
    for line in out.splitlines() if rc == 0 else []:
        tokens = line.split()
        if tokens and tokens[0] in ROUTE_TYPES:
            tokens = tokens[1:]
        if not tokens or tokens[0] == "default":
            continue
        net = _network(tokens[0])
        dev = tokens[tokens.index("dev") + 1] if "dev" in tokens[:-1] else "?"
        if net:
            ranges.append((net, f"route on {dev}"))
    rc, out, _ = run(["ip", "-4", "-o", "addr"])
    for line in out.splitlines() if rc == 0 else []:
        tokens = line.split()
        if "inet" not in tokens[:-1]:
            continue
        try:
            net = ipaddress.ip_interface(tokens[tokens.index("inet") + 1]).network
        except ValueError:
            continue
        dev = tokens[1].rstrip(":") if len(tokens) > 1 else "?"
        ranges.append((net, f"address on {dev}"))
    return ranges


def taken_ranges(networks: list[dict]) -> list[tuple[ipaddress.IPv4Network, str]]:
    docker_side = [(net, f"Docker network {n['name']}") for n in networks for net in n["subnets"]]
    return docker_side + host_ranges()


def state_dir() -> Path:
    raw = os.environ.get(STATE_ENV, "").strip()
    return Path(raw) if raw else Path.home() / ".cache" / "mac-k3d"


def pid_alive(pid: object) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def reservations() -> Iterator[dict]:
    """Live reservations (subnet -> pid, trial, time) under the lock; saved when the block ends."""
    base = state_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / RESERVATIONS_FILE
    with (base / LOCK_FILE).open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
        held = {
            subnet: row
            for subnet, row in (doc.items() if isinstance(doc, dict) else [])
            if isinstance(row, dict) and _network(subnet) and pid_alive(row.get("pid"))
        }
        yield held
        tmp = path.with_name(f".{RESERVATIONS_FILE}.{os.getpid()}")
        tmp.write_text(json.dumps(held, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def free_subnets(
    pool: ipaddress.IPv4Network,
    prefix: int,
    taken: list[tuple[ipaddress.IPv4Network, str]],
    held: dict,
) -> list[ipaddress.IPv4Network]:
    blocked = [net for net, _ in taken if net.overlaps(pool)]
    blocked += [net for subnet in held if (net := _network(subnet)) and net.overlaps(pool)]
    return [s for s in pool.subnets(new_prefix=prefix) if not any(s.overlaps(net) for net in blocked)]


def allocate(trial: str) -> str | None:
    """Reserve a free subnet for `trial` (its compose project) while this process lives; None when none is free."""
    pool, prefix = pool_settings()
    with reservations() as held:
        free = free_subnets(pool, prefix, taken_ranges(docker_networks()), held)
        if not free:
            return None
        subnet = str(free[0])
        held[subnet] = {"pid": os.getpid(), "trial": trial, "time": now()}
    return subnet


def release(subnet: str) -> None:
    with reservations() as held:
        held.pop(subnet, None)


def check(out_path: Path, need: int) -> dict:
    record: dict = {
        "mode": "docker_default",
        "pool": "",
        "prefix": None,
        "free": None,
        "total": None,
        "need": need,
        "reserved": 0,
        "overlaps": [],
        "checked_at": now(),
    }
    if disabled():
        out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"OK trial networks: Docker's own address pools ({SWITCH_ENV}=off)")
        return record
    pool, prefix = pool_settings()
    with reservations() as held:
        taken = taken_ranges(docker_networks())
        free = free_subnets(pool, prefix, taken, held)
        reserved = len(held)
    overlaps = []
    for net, source in taken:
        row = {"range": str(net), "source": source}
        if net.overlaps(pool) and row not in overlaps:
            overlaps.append(row)
    total = 2 ** (prefix - pool.prefixlen)
    record.update(mode="subnets", pool=str(pool), prefix=prefix, free=len(free), total=total, reserved=reserved, overlaps=overlaps)
    if len(free) < need:
        lines = [
            f"only {len(free)} of {total} /{prefix} subnets in {pool} are free, "
            f"and this build runs {need} trial(s) at once ({reserved} reserved by running trials)"
        ]
        lines += [f"  {row['range']} ({row['source']}) overlaps {pool}" for row in overlaps[:10]]
        lines.append(f"  hint: set {POOL_ENV} to an unused private range, see {RUNBOOK}")
        raise TrialNetworkError("\n".join(lines))
    out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"OK trial networks: {len(free)} of {total} /{prefix} subnets free in {pool} (need {need})")
    for row in overlaps[:10]:
        print(f"note: {row['range']} ({row['source']}) overlaps {pool}; those subnets are skipped")
    return record


def project_name(name: str) -> str:
    """Harbor's compose project name for a session (its _sanitize_docker_compose_project_name)."""
    name = name.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    return re.sub(r"[^a-z0-9_-]", "-", name)


def is_trial_dir(path: Path) -> bool:
    """A Harbor or Pier trial folder (`<task>__<random>` with agent/); job folders have no agent/."""
    return "__" in path.name and (path / "agent").is_dir() and any((path / f).exists() for f in TRIAL_FILES)


def trial_names(roots: list[Path]) -> set[str]:
    """Compose project names of the trial folders under `roots`."""
    names: set[str] = set()
    level = [root for root in roots if root.is_dir()]
    for _ in range(TRIAL_DEPTH):
        below = []
        for folder in level:
            try:
                children = [c for c in folder.iterdir() if c.is_dir() and not c.is_symlink()]
            except OSError:
                continue
            for child in children:
                if is_trial_dir(child):
                    names.add(project_name(child.name))
                else:
                    below.append(child)
        level = below
    return names


def owns(project: str, names: set[str]) -> bool:
    return any(project == name or project.startswith(name + "__") for name in names)


def compose_projects(networks: list[dict]) -> dict[str, dict]:
    """Compose project -> its containers and networks."""
    template = "{{.ID}}\t{{.State}}\t{{.Label \"" + PROJECT_LABEL + "\"}}\t{{.Names}}"
    out = docker("ps", "-a", "--no-trunc", "--filter", f"label={PROJECT_LABEL}", "--format", template)
    projects: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2]:
            continue
        group = projects.setdefault(parts[2], {"containers": [], "networks": []})
        name = parts[3] if len(parts) > 3 and parts[3] else parts[0][:12]
        group["containers"].append({"id": parts[0], "state": parts[1].strip().lower(), "name": name})
    for net in networks:
        if net["project"]:
            projects.setdefault(net["project"], {"containers": [], "networks": []})["networks"].append(net)
    return projects


def held_networks() -> list[ipaddress.IPv4Network]:
    with reservations() as held:
        return [net for subnet in held if (net := _network(subnet))]


def in_use(net: dict, held: list[ipaddress.IPv4Network]) -> bool:
    return any(s.overlaps(h) for s in net["subnets"] for h in held)


def remove_project(verb: str, project: str, group: dict, force: bool) -> bool:
    ids = [c["id"] for c in group["containers"]]
    if ids:
        rc, _, err = run(["docker", "rm", *(["-f"] if force else []), *ids])
        if rc != 0:
            print(f"WARNING: {verb}: could not remove the containers of {project}: {first_line(err) or f'exit {rc}'}")
            return False
    removed = []
    for net in group["networks"]:
        rc, _, err = run(["docker", "network", "rm", net["id"]])
        if rc == 0:
            removed.append(net["name"])
        else:
            print(f"WARNING: {verb}: could not remove network {net['name']}: {first_line(err) or f'exit {rc}'}")
    nets = f"network {', '.join(removed)}" if removed else "no network"
    print(f"{verb}: removed {project} ({len(ids)} container(s), {nets})")
    return True


def cleanup(roots: list[Path]) -> int:
    names = trial_names(roots)
    networks = docker_networks()
    projects = compose_projects(networks)
    held = held_networks()
    done: set[str] = set()
    for project in sorted(projects):
        if not owns(project, names):
            continue
        group = projects[project]
        busy = [c for c in group["containers"] if c["state"] not in DONE_STATES]
        if busy:
            print(f"cleanup: kept {project}: {busy[0]['name']} is {busy[0]['state']}")
            continue
        if any(in_use(net, held) for net in group["networks"]):
            print(f"cleanup: kept {project}: a running trial holds its subnet")
            continue
        if remove_project("cleanup", project, group, force=False):
            done.add(project)
    if not disabled():
        pool, _ = pool_settings()
        for net in networks:
            if net["project"] in done or not net["project"] or in_use(net, held):
                continue
            if not any(s.subnet_of(pool) for s in net["subnets"]):
                continue
            if docker("ps", "-a", "-q", "--filter", f"network={net['id']}").strip():
                continue
            rc, _, err = run(["docker", "network", "rm", net["id"]])
            if rc == 0:
                done.add(net["project"])
                print(f"cleanup: removed unused pool network {net['name']} ({', '.join(map(str, net['subnets']))})")
            else:
                print(f"WARNING: cleanup: could not remove network {net['name']}: {first_line(err) or f'exit {rc}'}")
    if not done:
        print("cleanup: no leftovers of this worker's trials")
    return len(done)


def process_start(pid: int) -> str | None:
    """A token that changes when ``pid`` is reused. None when the process is gone.

    Linux reads the start-time field of ``/proc/<pid>/stat``. macOS has no
    ``/proc``, so ``ps -o lstart=`` is the same idea.
    """
    if pid <= 0:
        return None
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
            text = stat.read_text(encoding="utf-8")
        except OSError:
            return None
        end = text.rfind(")")
        fields = text[end + 2 :].split() if end >= 0 else []
        # Field 22 (starttime) is index 19 of the fields that follow comm.
        return fields[19] if len(fields) > 19 else None
    rc, out, _ = run(["ps", "-o", "lstart=", "-p", str(pid)])
    if rc != 0:
        return None
    token = out.strip()
    return token or None


def write_owner(path: Path) -> None:
    """The shell that started this step. A later sweep removes its containers
    when this process is gone or the pid has been reused."""
    pid = os.getppid()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": pid, "started": process_start(pid) or ""}) + "\n",
        encoding="utf-8",
    )


def owner_alive(doc: dict) -> bool:
    try:
        pid = int(doc.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    now = process_start(pid)
    if now is None:
        return False
    started = str(doc.get("started") or "")
    return not started or now == started


def sweep(roots: list[Path]) -> int:
    """Force-remove trials whose owner process is gone.

    ``owner.json`` sits in the jobs dir. A build that is still running keeps
    its containers; one whose agent died does not.
    """
    count = 0
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for owner in root.rglob("owner.json"):
            jobs = owner.parent
            if jobs in seen:
                continue
            seen.add(jobs)
            try:
                doc = json.loads(owner.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict) or owner_alive(doc):
                continue
            removed = teardown(jobs)
            count += removed
            print(f"sweep: removed {removed} project(s) of dead owner pid {doc.get('pid')} under {jobs}")
    if count == 0:
        print("sweep: no trials of a dead owner")
    return count


def teardown(jobs_dir: Path) -> int:
    names = trial_names([jobs_dir])
    if not names:
        return 0
    projects = compose_projects(docker_networks())
    count = 0
    for project in sorted(projects):
        if owns(project, names) and remove_project("teardown", project, projects[project], force=True):
            count += 1
    with reservations() as held:
        for subnet in [s for s, row in held.items() if owns(str(row.get("trial") or ""), names)]:
            held.pop(subnet)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--out", type=Path, required=True)
    p_check.add_argument("--need", type=int, default=1)
    p_cleanup = sub.add_parser("cleanup")
    p_cleanup.add_argument("--root", type=Path, action="append", default=[])
    p_teardown = sub.add_parser("teardown")
    p_teardown.add_argument("--jobs-dir", type=Path, required=True)
    p_owner = sub.add_parser("owner")
    p_owner.add_argument("--out", type=Path, required=True)
    p_sweep = sub.add_parser("sweep")
    p_sweep.add_argument("--root", type=Path, action="append", default=[])
    args = parser.parse_args(argv)

    try:
        if args.cmd == "check":
            check(args.out, args.need)
        elif args.cmd == "cleanup":
            cleanup(args.root)
        elif args.cmd == "owner":
            write_owner(args.out)
        elif args.cmd == "sweep":
            sweep(args.root)
        else:
            teardown(args.jobs_dir)
    except TrialNetworkError as exc:
        if args.cmd == "check":
            args.out.unlink(missing_ok=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
