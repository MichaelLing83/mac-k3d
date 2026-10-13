#!/usr/bin/env python3
"""Pinned-benchmark checks and the eval_protocol provenance block.

The tasks and evaluate phases write these objects into eval_protocol_inputs.json.
render_report copies them onto the artifact and writes them out as provenance.md,
with a short block in summary.md and report.html. This module does not print
secret values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from icode_sanitize import MANIFEST_NAME, runtime_sha256, tree_sha256
from task_resources import cpu_count

OVERLAY_MARKER = "mac-k3d-lolbench-fix-rewards-v1"
PROVENANCE_KEYS = (
    "harbor",
    "benchmark",
    "grader_overlay",
    "images",
    "env_check",
    "worker",
    "requester",
    "pipeline",
    "isolation",
)
ENV_CHECK_FIELDS = (
    "source",
    "platform",
    "image",
    "image_id",
    "pass",
    "reason",
    "checked_in",
    "checked_at",
    "reused",
    "dockerfile_sha256",
    "base_image",
)
ENV_CHECK_COUNTS = ("f2p_passed", "f2p_total", "p2p_passed", "p2p_total", "reward")
_DOCKER_IMAGE = re.compile(r'^docker_image\s*=\s*"([^"]+)"', re.M)


def count_task_dirs(tasks_dir: Path) -> int:
    """Immediate child directories, matching find -mindepth 1 -maxdepth 1 -type d."""
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"missing tasks dir: {tasks_dir}")
    return sum(1 for child in tasks_dir.iterdir() if child.is_dir() and not child.is_symlink())


def assert_task_count(tasks_dir: Path, expected: int) -> int:
    found = count_task_dirs(tasks_dir)
    if found != expected:
        print(f"ERROR: task count {found} != {expected} under {tasks_dir}", file=sys.stderr)
        raise SystemExit(1)
    return found


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tests_list_sha256(task_dir: Path) -> str:
    """sha256 of the sorted relative file list under tests/, not file contents."""
    tests = task_dir / "tests"
    names: list[str] = []
    if tests.is_dir():
        for path in tests.rglob("*"):
            if path.is_file() and not path.is_symlink():
                names.append(path.relative_to(tests).as_posix())
    names.sort()
    return _sha256_bytes(("\n".join(names) + "\n").encode("utf-8"))


def task_fingerprints(task_dir: Path) -> dict:
    toml = task_dir / "task.toml"
    return {
        "task_toml_sha256": sha256_file(toml) if toml.is_file() else "",
        "tests_list_sha256": tests_list_sha256(task_dir),
    }


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, proc.stdout


def benchmark_record(repo_dir: Path, tasks_dir: Path, selected: list[str]) -> dict:
    url = ""
    sha = ""
    if (repo_dir / ".git").exists():
        code, out = _git(repo_dir, "remote", "get-url", "origin")
        if code == 0:
            url = out.strip()
        code, out = _git(repo_dir, "rev-parse", "HEAD")
        if code == 0:
            sha = out.strip()
    tasks: dict[str, dict] = {}
    for tid in selected:
        task_dir = tasks_dir / tid
        if task_dir.is_dir():
            tasks[tid] = task_fingerprints(task_dir)
    count = count_task_dirs(tasks_dir) if tasks_dir.is_dir() else 0
    return {"url": url, "sha": sha, "task_count": count, "tasks": tasks}


def overlay_record(script: Path, applied: bool) -> dict:
    return {
        "marker": OVERLAY_MARKER,
        "sha256": sha256_file(script) if script.is_file() else "",
        "applied": applied,
    }


def read_harbor_version(workdir: Path | None) -> str:
    if workdir is not None:
        path = workdir / "harbor_version.txt"
        if path.is_file():
            lines = path.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].strip():
                return lines[0].strip()
    try:
        proc = subprocess.run(
            ["harbor", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    lines = proc.stdout.splitlines()
    return lines[0].strip() if lines else ""


def docker_image_name(task_toml: Path) -> str:
    try:
        text = task_toml.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _DOCKER_IMAGE.search(text)
    return match.group(1) if match else ""


def inspect_image(image: str) -> dict:
    if not image:
        return {"image": "", "id": "", "repo_digests": []}
    try:
        proc = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}\n{{json .RepoDigests}}\n{{.Os}}/{{.Architecture}}",
                image,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"image": image, "id": "", "repo_digests": [], "error": str(exc)}
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        message = err[-1] if err else f"docker image inspect exited {proc.returncode}"
        return {"image": image, "id": "", "repo_digests": [], "error": message[:200]}
    lines = proc.stdout.splitlines()
    image_id = lines[0].strip() if lines else ""
    raw = lines[1].strip() if len(lines) > 1 else "[]"
    try:
        digests = json.loads(raw)
    except json.JSONDecodeError:
        digests = []
    if not isinstance(digests, list):
        digests = []
    plat = lines[2].strip() if len(lines) > 2 else ""
    record = {"image": image, "id": image_id, "repo_digests": [str(item) for item in digests]}
    if "/" in plat and not plat.startswith("/") and not plat.endswith("/"):
        record["platform"] = plat
    return record


def inspect_task(task_dir: Path) -> dict:
    return inspect_image(docker_image_name(task_dir / "task.toml"))


def merge_image(images: dict, task_id: str, record: dict) -> None:
    """Keep a previously recorded image id when a later inspect finds nothing."""
    prev = images.get(task_id)
    if isinstance(prev, dict) and str(prev.get("id") or "") and not str(record.get("id") or ""):
        return
    images[task_id] = record


def _sysctl(name: str) -> str:
    try:
        proc = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _mem_total_kb() -> int | None:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    if platform.system() == "Darwin":
        size = _sysctl("hw.memsize")
        if size.isdigit():
            return int(size) // 1024
    return None


def _nproc() -> int | None:
    try:
        proc = subprocess.run(["nproc"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0 and proc.stdout.strip().isdigit():
        return int(proc.stdout.strip())
    count = os.cpu_count()
    return count if isinstance(count, int) else None


def _cpu_model() -> str:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    if platform.system() == "Darwin":
        return _sysctl("machdep.cpu.brand_string")
    return ""


def container_platform_facts(workdir: Path | None) -> dict:
    """What `tasks/icode` recorded: the task containers' platform and whether Docker emulates it."""
    if workdir is None:
        return {}
    doc = _load_json(workdir / "container_platform.json")
    want = str(doc.get("container_platform") or "")
    if not want:
        return {}
    return {"container_platform": want, "emulated": doc.get("emulated") is True}


def _docker_version() -> str:
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def worker_facts(workdir: Path | None = None) -> dict:
    node = os.environ.get("NODE_NAME") or platform.node() or ""
    facts = {
        "node": node,
        "os": platform.system(),
        "nproc": _nproc(),
        "memory_kb": _mem_total_kb(),
        "docker_version": _docker_version(),
        "kernel": platform.release(),
        "cpu_model": _cpu_model(),
    }
    facts.update(container_platform_facts(workdir))
    return facts


def requester_facts() -> dict:
    """Who asked for this run. Jenkins jobs set ``BUILD_USER`` from the build cause."""
    build_url = os.environ.get("BUILD_URL") or ""
    user = os.environ.get("BUILD_USER") or os.environ.get("BUILD_USER_ID") or ""
    if not user and not build_url:
        login = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        user = f"local {login}" if login else ""
    return {"user": user or "unknown", "build_url": build_url or "unknown"}


def pipeline_facts(root: Path) -> dict:
    """Which mac-k3d revision produced this result.

    A Jenkins build runs the pipeline embedded in the worker's mac-k3d binary.
    ``mac-k3d pipeline --extract-to`` writes ``pipeline/BUILD.json`` with the
    commit that binary was built from. A local checkout has no BUILD.json, so
    git answers instead.
    """
    try:
        build = json.loads((root / "pipeline" / "BUILD.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        build = None
    if isinstance(build, dict):
        commit = str(build.get("commit") or "")
        dirty = build.get("dirty")
        return {
            "source": "binary",
            "version": str(build.get("version") or ""),
            "commit": "" if commit == "unknown" else commit,
            "dirty": dirty if isinstance(dirty, bool) else None,
            "pipeline_hash": str(build.get("pipeline_hash") or ""),
        }
    if not (root / ".git").exists():
        return {"source": "unknown", "commit": "", "dirty": None}
    code, out = _git(root, "rev-parse", "HEAD")
    commit = out.strip() if code == 0 else ""
    code, out = _git(root, "status", "--porcelain")
    dirty = bool(out.strip()) if code == 0 else None
    return {"source": "checkout", "commit": commit, "dirty": dirty}


def _env_int(*names: str) -> int:
    for name in names:
        raw = os.environ.get(name) or ""
        if raw.isdigit():
            return int(raw)
    return 0


def model_inputs() -> dict:
    return {
        "model": os.environ.get("ICODE_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "",
        "api_base": os.environ.get("ICODE_API_BASE") or "https://api.deepseek.com/v1",
        "provider": os.environ.get("ICODE_PROVIDER") or "DeepSeek",
        "reasoning_effort": os.environ.get("ICODE_REASONING_EFFORT") or "high",
        "max_tokens": _env_int("ICODE_MAX_TOKENS"),
        "max_iterations": _env_int("ICODE_MAX_ITERATIONS"),
        "cpu_lock_qty": _env_int("CPU_LOCK_QTY"),
        "concurrency": _env_int("EVAL_SLOTS"),
        "cpus_each": cpu_count(os.environ.get("EVAL_CPUS_EACH")) or 0,
        "n_rollouts": _env_int("N_ROLLOUTS"),
    }


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _selected_ids(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def isolation_record(icode_root: Path | None, mounts: object) -> dict:
    """The runtime the agent could read at /opt/icode-host, hashed now rather than trusted from the manifest."""
    mount_list = mounts if isinstance(mounts, list) else []
    first = mount_list[0] if mount_list and isinstance(mount_list[0], dict) else {}
    record = {
        "mode": "release",
        "sanitizer": "",
        "sourceless": None,
        "removed": 0,
        "tree_sha256": "",
        "runtime_sha256": "",
        "manifest_matches_tree": None,
        "mount": {
            "count": len(mount_list),
            "target": str(first.get("target") or ""),
            "read_only": first.get("read_only") is True,
        },
    }
    if icode_root is None or not (icode_root / ".venv" / "sandbox-cpython").is_dir():
        return record
    venv = icode_root / ".venv"
    manifest = _load_json(venv / MANIFEST_NAME)
    tree = tree_sha256(venv)
    runtime = runtime_sha256(icode_root)
    removed = manifest.get("removed")
    record.update(
        {
            "mode": "git",
            "sanitizer": str(manifest.get("sanitizer") or ""),
            "sourceless": manifest.get("sourceless") is True,
            "removed": len(removed) if isinstance(removed, list) else 0,
            "tree_sha256": tree,
            "runtime_sha256": runtime,
            "manifest_matches_tree": bool(manifest)
            and manifest.get("tree_sha256") == tree
            and manifest.get("runtime_sha256") == runtime,
        }
    )
    return record


def _check_counts(result: object) -> dict | None:
    if not isinstance(result, dict):
        return None
    return {name: result.get(name) for name in ENV_CHECK_COUNTS}


def env_check_record(record: Path, selected: list[str]) -> dict:
    """tasks/images' environment check (env_check.json) for the selected questions."""
    tasks = _load_json(record).get("tasks")
    if not isinstance(tasks, dict):
        return {}
    out: dict[str, dict] = {}
    for tid in selected:
        entry = tasks.get(tid)
        if not isinstance(entry, dict):
            continue
        row = {name: entry[name] for name in ENV_CHECK_FIELDS if name in entry}
        row["oracle"] = _check_counts(entry.get("oracle"))
        row["nop"] = _check_counts(entry.get("nop"))
        attempts = [
            {name: item.get(name) for name in ("source", "platform", "pass", "reason", "reused")}
            for item in entry.get("attempts") or []
            if isinstance(item, dict)
        ]
        if attempts:
            row["attempts"] = attempts
        out[tid] = row
    return out


def leakscan_record(report: Path) -> dict:
    doc = _load_json(report)
    tasks = doc.get("tasks") if isinstance(doc.get("tasks"), dict) else {}
    statuses: dict[str, int] = {}
    for entry in tasks.values():
        status = str(entry.get("status") or "") if isinstance(entry, dict) else ""
        if status:
            statuses[status] = statuses.get(status, 0) + 1
    hits = doc.get("hit_tasks")
    return {
        "scanner": str(doc.get("scanner") or ""),
        "hit_tasks": sorted(str(tid) for tid in hits) if isinstance(hits, list) else [],
        "statuses": dict(sorted(statuses.items())),
        "report_sha256": sha256_file(report) if report.is_file() else "",
    }


def record_leakscan(inputs: Path, report: Path) -> dict:
    doc = _load_json(inputs)
    isolation = doc.get("isolation") if isinstance(doc.get("isolation"), dict) else {}
    isolation["leak_scan"] = leakscan_record(report)
    doc["isolation"] = isolation
    _write_json(inputs, doc)
    return doc


def canary_record(summary: Path) -> dict:
    doc = _load_json(summary)
    counts = doc.get("counts") if isinstance(doc.get("counts"), dict) else {}
    tasks = doc.get("tasks") if isinstance(doc.get("tasks"), dict) else {}
    record = {
        "version": str(doc.get("config_version") or ""),
        "status": str(doc.get("status") or ""),
        "node": str(doc.get("node") or ""),
        "tasks": sorted(str(tid) for tid in tasks),
        "failed_tasks": sorted(str(t) for t in doc.get("failed_tasks") or []),
        "warn_tasks": sorted(str(t) for t in doc.get("warn_tasks") or []),
        "counts": {k: v for k, v in counts.items() if isinstance(v, int) and not isinstance(v, bool)},
        "summary_sha256": sha256_file(summary) if summary.is_file() else "",
    }
    fallback = [
        {"task": str(item.get("task") or ""), "reason": str(item.get("reason") or "")}
        for item in doc.get("fallback_from") or []
        if isinstance(item, dict) and item.get("task")
    ]
    if fallback:
        record["fallback_from"] = fallback
    return record


def record_resources(inputs: Path, plan: Path) -> dict:
    """What the selected tasks declared and what the build applied (PF.3).

    The inputs file is written in the tasks phase, before the CPU lock is held,
    so the lock size and slots come from the plan made under the lock.
    """
    doc = _load_json(inputs)
    got = _load_json(plan)
    declared = got.get("declared") if isinstance(got.get("declared"), dict) else {}
    applied = got.get("applied") if isinstance(got.get("applied"), dict) else {}
    doc["resources"] = {
        "declared": declared.get("peak") or {},
        "declared_tasks": declared.get("declared_tasks"),
        "applied": applied,
        "host": got.get("host") or {},
        "reasons": got.get("reasons") or [],
    }
    lock = applied.get("cpu_lock_qty")
    if isinstance(lock, int) and not isinstance(lock, bool) and lock > 0:
        doc["cpu_lock_qty"] = lock
    slots = applied.get("slots")
    if isinstance(slots, int) and not isinstance(slots, bool) and slots > 0:
        doc["concurrency"] = slots
    cpus = cpu_count(applied.get("cpus_each"))
    if cpus:
        doc["cpus_each"] = cpus
    _write_json(inputs, doc)
    return doc


def record_canary(inputs: Path, summary: Path) -> dict:
    doc = _load_json(inputs)
    isolation = doc.get("isolation") if isinstance(doc.get("isolation"), dict) else {}
    isolation["canary"] = canary_record(summary)
    doc["isolation"] = isolation
    _write_json(inputs, doc)
    return doc


def record_canary_reuse(inputs: Path, source: str) -> dict:
    """This worker already passed the canary for this question in this run."""
    doc = _load_json(inputs)
    isolation = doc.get("isolation") if isinstance(doc.get("isolation"), dict) else {}
    isolation["canary"] = {"status": "reused", "source": source}
    doc["isolation"] = isolation
    _write_json(inputs, doc)
    return doc


def record_egress_probe(inputs: Path, probe: Path) -> dict:
    """`harbor_egress.py check` output: which image Harbor's kernel probe ran in."""
    doc = _load_json(inputs)
    facts = _load_json(probe)
    isolation = doc.get("isolation") if isinstance(doc.get("isolation"), dict) else {}
    isolation["egress_probe"] = {
        "harbor_default": str(facts.get("harbor_default") or ""),
        "image": str(facts.get("image") or ""),
        "substituted": bool(facts.get("substituted")),
        "reason": str(facts.get("reason") or ""),
        "sidecar": str(facts.get("sidecar") or ""),
    }
    doc["isolation"] = isolation
    _write_json(inputs, doc)
    return doc


def record_trial_network(inputs: Path, record: Path) -> dict:
    """`trial_network.py check` output: where Harbor's trial networks take their subnets."""
    doc = _load_json(inputs)
    facts = _load_json(record)
    isolation = doc.get("isolation") if isinstance(doc.get("isolation"), dict) else {}
    isolation["trial_network"] = {
        "mode": str(facts.get("mode") or ""),
        "pool": str(facts.get("pool") or ""),
        "prefix": _int_or_none(facts.get("prefix")),
        "free": _int_or_none(facts.get("free")),
        "total": _int_or_none(facts.get("total")),
        "need": _int_or_none(facts.get("need")),
        "checked_at": str(facts.get("checked_at") or ""),
    }
    doc["isolation"] = isolation
    _write_json(inputs, doc)
    return doc


def write_protocol_inputs(
    *,
    out: Path,
    repo_dir: Path,
    tasks_dir: Path,
    selected_file: Path,
    overlay: Path,
    applied: bool,
    workdir: Path,
    pipeline_root: Path,
    icode_root: Path | None = None,
    mounts: object = None,
) -> dict:
    prev = _load_json(out)
    prev_images = prev.get("images") if isinstance(prev.get("images"), dict) else {}
    selected = _selected_ids(selected_file)
    # The workspace keeps earlier builds' inputs; their other tasks are not this run's.
    images = {tid: prev_images[tid] for tid in selected if tid in prev_images}
    for tid in selected:
        merge_image(images, tid, inspect_task(tasks_dir / tid))
    doc = model_inputs()
    doc.update(
        {
            "harbor": {"version": read_harbor_version(workdir)},
            "benchmark": benchmark_record(repo_dir, tasks_dir, selected),
            "grader_overlay": overlay_record(overlay, applied),
            "images": images,
            "worker": worker_facts(workdir),
            "requester": requester_facts(),
            "pipeline": pipeline_facts(pipeline_root),
            "isolation": isolation_record(icode_root, mounts),
        }
    )
    env_check = env_check_record(workdir / "env_check.json", selected)
    if env_check:
        doc["env_check"] = env_check
    _write_json(out, doc)
    return doc


def record_task_image(inputs: Path, task_id: str, task_toml: Path) -> dict:
    doc = _load_json(inputs)
    images = doc.get("images") if isinstance(doc.get("images"), dict) else {}
    merge_image(images, task_id, inspect_image(docker_image_name(task_toml)))
    doc["images"] = images
    _write_json(inputs, doc)
    return doc


def _cell(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def trial_network_text(record: object, short: bool = False) -> str:
    """Where trial networks took their subnets (isolation.trial_network); empty when not recorded."""
    if not isinstance(record, dict):
        return ""
    if record.get("mode") == "docker_default":
        return "Docker default" if short else "Docker's default address pools (MAC_K3D_TRIAL_SUBNETS=off)"
    if record.get("mode") != "subnets" or not record.get("pool"):
        return ""
    prefix = _cell(record.get("prefix"))
    if short:
        return f"{record['pool']} /{prefix} ({_cell(record.get('free'))} free)"
    by_shard = record.get("need_by_shard")
    if isinstance(by_shard, list) and len(by_shard) > 1:
        need = " · ".join(_cell(n) for n in by_shard) + " (by shard)"
    else:
        need = _cell(record.get("need"))
    return (
        f"own /{prefix} subnets from {record['pool']} · {_cell(record.get('free'))} of "
        f"{_cell(record.get('total'))} free at check · need {need}"
    )


def isolation_view(isolation: object) -> dict | None:
    if not isinstance(isolation, dict):
        return None
    mount = isolation.get("mount") if isinstance(isolation.get("mount"), dict) else {}
    leak = isolation.get("leak_scan") if isinstance(isolation.get("leak_scan"), dict) else {}
    statuses = leak.get("statuses") if isinstance(leak.get("statuses"), dict) else {}
    hits = leak.get("hit_tasks") if isinstance(leak.get("hit_tasks"), list) else []
    canary = isolation.get("canary") if isinstance(isolation.get("canary"), dict) else {}
    allow = isolation.get("network_allowlist") if isinstance(isolation.get("network_allowlist"), dict) else {}
    egress = isolation.get("egress_probe") if isinstance(isolation.get("egress_probe"), dict) else {}
    count = mount.get("count")
    return {
        "mode": str(isolation.get("mode") or ""),
        "sanitizer": str(isolation.get("sanitizer") or ""),
        "sourceless": _bool_or_none(isolation.get("sourceless")),
        "tree_sha256": str(isolation.get("tree_sha256") or ""),
        "runtime_sha256": str(isolation.get("runtime_sha256") or ""),
        "manifest_matches_tree": _bool_or_none(isolation.get("manifest_matches_tree")),
        "mount_target": str(mount.get("target") or ""),
        "mount_read_only": _bool_or_none(mount.get("read_only")),
        "mount_count": count if isinstance(count, int) and not isinstance(count, bool) else None,
        "leak_scanner": str(leak.get("scanner") or ""),
        "leak_hits": [str(tid) for tid in hits],
        "leak_statuses": ", ".join(f"{name} {num}" for name, num in sorted(statuses.items())),
        "canary_version": str(canary.get("version") or ""),
        "canary_status": str(canary.get("status") or ""),
        "canary_tasks": [str(t) for t in canary.get("tasks") or []],
        "canary_failed": [str(t) for t in canary.get("failed_tasks") or []],
        "canary_warn": [str(t) for t in canary.get("warn_tasks") or []],
        "canary_fallback": [
            f"{item.get('task')} ({item.get('reason') or 'did not start'})"
            for item in canary.get("fallback_from") or []
            if isinstance(item, dict) and item.get("task")
        ],
        "allowlist_version": str(allow.get("version") or ""),
        "allowlist_hosts": [str(h) for h in allow.get("agent_hosts") or []],
        "egress_default": str(egress.get("harbor_default") or ""),
        "egress_image": str(egress.get("image") or ""),
        "egress_substituted": egress.get("substituted") is True,
        "egress_reason": str(egress.get("reason") or ""),
        "trial_network": trial_network_text(isolation.get("trial_network")),
    }


def isolation_lines(view: dict | None) -> list[str]:
    """Plain-text isolation facts for provenance.md."""
    if not view:
        return []
    lines = [
        f"Isolation: mode {_cell(view['mode'])} · sanitizer {_cell(view['sanitizer'])} · "
        f"sourceless {_cell(view['sourceless'])} · runtime sha256 {_cell(view['runtime_sha256'])} · "
        f"tree sha256 {_cell(view['tree_sha256'])} · manifest matches tree {_cell(view['manifest_matches_tree'])}",
        f"Agent mount: {_cell(view['mount_target'])} read-only {_cell(view['mount_read_only'])} "
        f"({_cell(view['mount_count'])} mount)",
    ]
    if view.get("allowlist_version"):
        lines.append(
            f"Agent network: {view['allowlist_version']} · allowed {', '.join(view['allowlist_hosts']) or '-'}"
        )
    if view.get("egress_substituted"):
        lines.append(
            f"Egress probe: substituted {view['egress_image']} "
            f"(Harbor's {view['egress_default']} could not run: {_cell(view['egress_reason'])})"
        )
    elif view.get("egress_image"):
        lines.append(f"Egress probe: Harbor default {view['egress_image']}")
    if view.get("trial_network"):
        lines.append(f"Trial networks: {view['trial_network']}")
    if view["leak_scanner"]:
        hits = ", ".join(view["leak_hits"]) if view["leak_hits"] else "none"
        lines.append(f"Leak scan: {view['leak_scanner']} · hit tasks {hits} · {_cell(view['leak_statuses'])}")
    if view.get("canary_version"):
        tasks = ", ".join(view["canary_tasks"]) or "-"
        failed = ", ".join(view["canary_failed"]) or "none"
        warned = ", ".join(view["canary_warn"]) or "none"
        line = (
            f"Canary: {view['canary_version']} · {_cell(view['canary_status'])} · tasks {tasks} · "
            f"failed {failed} · warnings {warned}"
        )
        if view.get("canary_fallback"):
            line += f" · did not start: {', '.join(view['canary_fallback'])}"
        lines.append(line)
    return lines


def provenance_view(protocol: dict | None) -> dict | None:
    if not isinstance(protocol, dict):
        return None
    if not any(key in protocol for key in PROVENANCE_KEYS):
        return None
    harbor = protocol.get("harbor") if isinstance(protocol.get("harbor"), dict) else {}
    bench = protocol.get("benchmark") if isinstance(protocol.get("benchmark"), dict) else {}
    overlay = protocol.get("grader_overlay") if isinstance(protocol.get("grader_overlay"), dict) else {}
    worker = protocol.get("worker") if isinstance(protocol.get("worker"), dict) else {}
    requester = protocol.get("requester") if isinstance(protocol.get("requester"), dict) else {}
    pipe = protocol.get("pipeline") if isinstance(protocol.get("pipeline"), dict) else {}
    images = protocol.get("images") if isinstance(protocol.get("images"), dict) else {}
    tasks = bench.get("tasks") if isinstance(bench.get("tasks"), dict) else {}
    rows = []
    for tid in sorted(set(tasks) | set(images)):
        task = tasks.get(tid) if isinstance(tasks.get(tid), dict) else {}
        image = images.get(tid) if isinstance(images.get(tid), dict) else {}
        rows.append(
            {
                "task": tid,
                "image_id": str(image.get("id") or ""),
                "platform": str(image.get("platform") or ""),
                "task_toml_sha256": str(task.get("task_toml_sha256") or ""),
                "tests_list_sha256": str(task.get("tests_list_sha256") or ""),
            }
        )
    task_count = bench.get("task_count")
    if isinstance(task_count, bool) or not isinstance(task_count, int):
        task_count = None
    applied = overlay.get("applied")
    if not isinstance(applied, bool):
        applied = None
    return {
        "isolation": isolation_view(protocol.get("isolation")),
        "harbor_version": str(harbor.get("version") or ""),
        "benchmark_url": str(bench.get("url") or ""),
        "benchmark_sha": str(bench.get("sha") or ""),
        "task_count": task_count,
        "overlay_marker": str(overlay.get("marker") or ""),
        "overlay_sha256": str(overlay.get("sha256") or ""),
        "overlay_applied": applied,
        "commit": str(pipe.get("commit") or ""),
        "dirty": pipe.get("dirty") if isinstance(pipe.get("dirty"), bool) else None,
        "pipeline_source": str(pipe.get("source") or ""),
        "pipeline_hash": str(pipe.get("pipeline_hash") or ""),
        "node": str(worker.get("node") or ""),
        "nproc": worker.get("nproc"),
        "memory_kb": worker.get("memory_kb"),
        "docker_version": str(worker.get("docker_version") or ""),
        "kernel": str(worker.get("kernel") or ""),
        "cpu_model": str(worker.get("cpu_model") or ""),
        "os": str(worker.get("os") or ""),
        "containers": containers_text(worker),
        "user": str(requester.get("user") or ""),
        "build_url": str(requester.get("build_url") or ""),
        "tasks": rows,
        "env_check": env_check_rows(protocol),
        "shards": shard_views(protocol.get("shards")),
        "transcripts": transcripts_line(protocol.get("transcripts")),
        "model_params_status": str(protocol.get("model_params_status") or ""),
        "model_params_mismatch": (
            protocol.get("model_params_mismatch") if isinstance(protocol.get("model_params_mismatch"), dict) else {}
        ),
    }


def containers_text(worker: dict) -> str:
    """`linux/amd64`, or `linux/amd64 emulated` when Docker ran it on a host of another CPU type."""
    want = str(worker.get("container_platform") or "")
    if not want:
        return ""
    return f"{want} emulated" if worker.get("emulated") is True else want


def transcripts_line(record: object) -> str:
    """Where a merged report's iCode transcripts are; empty for a single build.

    The dispatcher copies the shards' trials without them, so they stay in the
    artifacts of the shard builds that ran Harbor.
    """
    if not isinstance(record, dict):
        return ""
    builds = [str(b) for b in record.get("builds") or [] if str(b)]
    if not builds:
        return ""
    job = str(record.get("job") or "").strip()
    where = f"{job} " if job else ""
    return (
        f"Transcripts: in the shard builds' artifacts ({where}{', '.join(f'#{b}' for b in builds)}), "
        "gzipped under eval-runs/harness/harbor_runs/*/*/agent/icode-project"
    )


def shard_views(shards: object) -> list[dict]:
    """One Worker line's fields per shard of an aggregated run."""
    out = []
    for item in shards if isinstance(shards, list) else []:
        if not isinstance(item, dict):
            continue
        worker = item.get("worker") if isinstance(item.get("worker"), dict) else {}
        res = item.get("resources") if isinstance(item.get("resources"), dict) else {}
        params = item.get("model_params") if isinstance(item.get("model_params"), dict) else {}
        builds = [str(b) for b in item.get("builds") or []]
        label = f"shard {item.get('shard')}" + (f" {', '.join(builds)}" if builds else "")
        probe = item.get("egress_probe") if isinstance(item.get("egress_probe"), dict) else {}
        egress = ""
        if probe.get("image"):
            egress = f"{'substituted' if probe.get('substituted') is True else 'Harbor default'} {probe['image']}"
        not_run = [str(t) for t in item.get("not_run") or []] if isinstance(item.get("not_run"), list) else []
        out.append(
            {
                "label": label,
                "has_data": bool(worker or params),
                "result": str(item.get("result") or ""),
                "not_run": not_run,
                "not_run_count": item.get("not_run_count") if isinstance(item.get("not_run_count"), int) else 0,
                "not_run_offset": item.get("not_run_offset"),
                "egress": egress,
                "trial_network": trial_network_text(item.get("trial_network"), short=True),
                "node": str(worker.get("node") or ""),
                "nproc": worker.get("nproc"),
                "memory_kb": worker.get("memory_kb"),
                "docker_version": str(worker.get("docker_version") or ""),
                "kernel": str(worker.get("kernel") or ""),
                "cpu_model": str(worker.get("cpu_model") or ""),
                "os": str(worker.get("os") or ""),
                "containers": containers_text(worker),
                "concurrency": res.get("concurrency"),
                "cpus_each": res.get("cpus_each"),
                "max_tokens": params.get("max_tokens"),
                "max_iterations": params.get("max_iterations"),
                "icode_version": str(item.get("icode_version") or ""),
            }
        )
    return out


SHARD_WORKER_FIELDS = (
    ("node", "node"),
    ("nproc", "nproc"),
    ("memory_kb", "memory_kb"),
    ("docker_version", "docker"),
    ("kernel", "kernel"),
    ("cpu_model", "cpu"),
    ("concurrency", "concurrency"),
    ("cpus_each", "cpus_each"),
    ("max_tokens", "max_tokens"),
    ("max_iterations", "max_iterations"),
    ("icode_version", "iCode"),
)


def shard_status(shard: dict) -> list[str]:
    """Why a shard has no trials, and which questions that leaves unrun."""
    names = shard.get("not_run") or []
    count = len(names) or shard.get("not_run_count") or 0
    result = shard.get("result") or ""
    parts = []
    if result and count:
        parts.append(f"{result} before any trial")
    elif result and result != "SUCCESS":
        parts.append(result)
    plural = "s" if count != 1 else ""
    if names:
        parts.append(f"{count} question{plural} not run: {', '.join(names)}")
    elif count:
        parts.append(f"{count} question{plural} at offset {_cell(shard.get('not_run_offset'))} not run")
    return parts


def shard_worker_lines(view: dict) -> list[str]:
    """One plain-text `Worker (shard N ...)` line per shard, then a line when the shards differ."""
    lines = []
    for shard in view.get("shards") or []:
        parts = []
        if shard.get("has_data", True):
            parts = [f"{name} {_cell(shard[key])}" for key, name in SHARD_WORKER_FIELDS]
            if shard.get("os"):
                parts.append(f"os {shard['os']}")
            if shard.get("containers"):
                parts.append(f"containers {shard['containers']}")
            if shard.get("egress"):
                parts.append(f"egress probe {shard['egress']}")
            if shard.get("trial_network"):
                parts.append(f"trial networks {shard['trial_network']}")
        parts.extend(shard_status(shard))
        lines.append(f"Worker ({shard['label']}): {' · '.join(parts) or '-'}")
    if view.get("model_params_status") == "mixed":
        detail = "; ".join(f"{k} {v}" for k, v in view.get("model_params_mismatch", {}).items())
        lines.append(f"Shards differ: {detail or 'model params'}. Not one measurement of one iCode setup.")
    return lines


def coverage_line(coverage: object) -> str:
    """How much of the dispatcher's plan a merged report covers; empty without a plan."""
    if not isinstance(coverage, dict) or not isinstance(coverage.get("planned"), int):
        return ""
    planned = coverage["planned"]
    not_run = coverage.get("not_run") if isinstance(coverage.get("not_run"), list) else []
    not_named = coverage.get("not_named") if isinstance(coverage.get("not_named"), int) else 0
    text = f"Coverage: {_cell(coverage.get('with_trials'))} of {planned} planned questions have trials"
    if not_run or not_named:
        text += f"; {len(not_run) + not_named} not run"
        shards = [str(s) for s in coverage.get("shards_not_run") or []]
        if shards:
            text += f" ({', '.join(shards)})"
    if not_named:
        text += f"; {not_named} not named, so Pass@k covers {_cell(coverage.get('in_metrics'))} questions"
    return text


def provenance_markdown(protocol: dict | None) -> list[str]:
    view = provenance_view(protocol)
    if view is None:
        return []
    lines = ["", "### Provenance", ""]
    lines.append(f"- Harbor: `{_cell(view['harbor_version'])}`")
    count = _cell(view["task_count"])
    lines.append(
        f"- Benchmark: `{_cell(view['benchmark_url'])}` @ `{_cell(view['benchmark_sha'])}` ({count} tasks)"
    )
    applied = _cell(view["overlay_applied"])
    lines.append(
        f"- Grader overlay: `{_cell(view['overlay_marker'])}` `{_cell(view['overlay_sha256'])}` applied `{applied}`"
    )
    pipeline = f"- Pipeline: `{_cell(view['commit'])}` dirty `{_cell(view['dirty'])}`"
    if view["pipeline_source"]:
        pipeline += f" from `{_cell(view['pipeline_source'])}`"
    if view["pipeline_hash"]:
        pipeline += f" hash `{_cell(view['pipeline_hash'])}`"
    lines.append(pipeline)
    if view["shards"]:
        lines.extend(f"- {line}" for line in shard_worker_lines(view))
    else:
        worker = (
            "- Worker: "
            f"node `{_cell(view['node'])}` · "
            f"nproc `{_cell(view['nproc'])}` · "
            f"memory_kb `{_cell(view['memory_kb'])}` · "
            f"docker `{_cell(view['docker_version'])}` · "
            f"kernel `{_cell(view['kernel'])}` · "
            f"cpu `{_cell(view['cpu_model'])}`"
        )
        if view["os"]:
            worker += f" · os `{view['os']}`"
        if view["containers"]:
            worker += f" · containers `{view['containers']}`"
        lines.append(worker)
    lines.append(f"- Requester: `{_cell(view['user'])}` `{_cell(view['build_url'])}`")
    if view["transcripts"]:
        lines.append(f"- {view['transcripts']}")
    for line in isolation_lines(view["isolation"]):
        lines.append(f"- {line}")
    icode = protocol.get("icode") if isinstance(protocol, dict) and isinstance(protocol.get("icode"), dict) else {}
    release = icode.get("release") if isinstance(icode.get("release"), dict) else None
    if icode.get("mode") == "release" and release and str(release.get("sha256") or ""):
        lines.append(
            f"- iCode release: `{_cell(release.get('filename'))}` `{_cell(release.get('sha256'))}`"
        )
    if view["tasks"]:
        lines.append("")
        lines.append("| Task | Image id | Platform | task.toml sha256 | tests list sha256 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in view["tasks"]:
            lines.append(
                "| {task} | {image} | {platform} | {toml} | {tests} |".format(
                    task=row["task"],
                    image=row["image_id"] or "-",
                    platform=row["platform"] or "-",
                    toml=row["task_toml_sha256"] or "-",
                    tests=row["tests_list_sha256"] or "-",
                )
            )
    return lines


PROVENANCE_FILE = "provenance.md"
ENV_CHECK_HEADERS = (
    "Task",
    "Worker",
    "Source",
    "Platform",
    "Image",
    "oracle F2P",
    "oracle P2P",
    "nop F2P",
    "nop P2P",
    "Result",
    "Checked in",
    "Reused",
)


def _ratio(result: object, passed: str, total: str) -> str:
    if not isinstance(result, dict) or _int_or_none(result.get(total)) is None:
        return "-"
    return f"{_cell(result.get(passed))}/{result[total]}"


def _env_check_row(tid: str, entry: dict, worker: str) -> dict:
    oracle, nop = entry.get("oracle"), entry.get("nop")
    return {
        "task": tid,
        "worker": worker,
        "source": str(entry.get("source") or ""),
        "platform": str(entry.get("platform") or ""),
        "image": str(entry.get("image_id") or entry.get("image") or ""),
        "oracle_f2p": _ratio(oracle, "f2p_passed", "f2p_total"),
        "oracle_p2p": _ratio(oracle, "p2p_passed", "p2p_total"),
        "nop_f2p": _ratio(nop, "f2p_passed", "f2p_total"),
        "nop_p2p": _ratio(nop, "p2p_passed", "p2p_total"),
        "pass": entry.get("pass") is True,
        "reason": str(entry.get("reason") or ""),
        "checked_in": str(entry.get("checked_in") or ""),
        "reused": entry.get("reused") is True,
        "attempts": [a for a in entry.get("attempts") or [] if isinstance(a, dict)],
    }


def env_check_rows(protocol: dict) -> list[dict]:
    """One row per worker and question: a merged run shows each shard's own check."""
    shards = [s for s in protocol.get("shards") or [] if isinstance(s, dict) and isinstance(s.get("env_check"), dict)]
    rows = []
    if shards:
        for shard in shards:
            worker = shard.get("worker") if isinstance(shard.get("worker"), dict) else {}
            node = str(worker.get("node") or f"shard {shard.get('shard')}")
            for tid, entry in sorted(shard["env_check"].items()):
                if isinstance(entry, dict):
                    rows.append(_env_check_row(str(tid), entry, node))
        return rows
    worker = protocol.get("worker") if isinstance(protocol.get("worker"), dict) else {}
    checks = protocol.get("env_check") if isinstance(protocol.get("env_check"), dict) else {}
    for tid, entry in sorted(checks.items()):
        if isinstance(entry, dict):
            rows.append(_env_check_row(str(tid), entry, str(worker.get("node") or "")))
    return rows


def env_check_line(rows: list[dict]) -> str:
    """`Environment check: N of T passed (M reused, K kept out)`; empty when no question was checked."""
    if not rows:
        return ""
    passed = [row for row in rows if row["pass"]]
    reused = sum(1 for row in passed if row["reused"])
    return (
        f"Environment check: {len(passed)} of {len(rows)} passed "
        f"({reused} reused, {len(rows) - len(passed)} kept out)"
    )


def _env_check_result(row: dict) -> str:
    if row["pass"]:
        return "pass"
    return "kept out: " + (row["reason"].removeprefix("environment check failed: ") or "failed")


def env_check_markdown(rows: list[dict]) -> list[str]:
    """The Environment check section of provenance.md."""
    if not rows:
        return []
    lines = [
        "## Environment check",
        "",
        "Before any rollout, each worker proves the image it will use for a question: Harbor's oracle agent "
        "applies the reference solution (every F2P and P2P test must pass), and its nop agent changes nothing "
        "(some F2P test must fail, every P2P test must pass). The answer is stored on that worker and reused "
        "while the image and the task files stay the same. A question that fails is kept out of Harbor.",
        "",
        f"- {env_check_line(rows)}",
        "",
        "| " + " | ".join(ENV_CHECK_HEADERS) + " |",
        "| " + " | ".join("---" for _ in ENV_CHECK_HEADERS) + " |",
    ]
    for row in rows:
        cells = [
            row["task"],
            row["worker"] or "-",
            row["source"] or "-",
            row["platform"] or "-",
            row["image"] or "-",
            row["oracle_f2p"],
            row["oracle_p2p"],
            row["nop_f2p"],
            row["nop_p2p"],
            _env_check_result(row),
            row["checked_in"] or "-",
            "yes" if row["reused"] else "no",
        ]
        lines.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    earlier = [
        f"- {row['task']} on {row['worker'] or '-'}: {_cell(a.get('source'))} {_cell(a.get('platform'))} "
        f"{'passed' if a.get('pass') is True else 'failed'} first"
        + (f" ({str(a['reason']).removeprefix('environment check failed: ')})" if a.get("reason") else "")
        for row in rows
        for a in row["attempts"]
    ]
    if earlier:
        lines.extend(["", "Earlier attempts in the same build:", "", *earlier])
    return lines


def provenance_brief(protocol: dict | None) -> list[str]:
    """The short Provenance block of summary.md and report.html; the rest is in provenance.md."""
    view = provenance_view(protocol)
    if view is None:
        return []
    pipeline = f"Pipeline {_cell(view['commit'])}"
    if view["dirty"] is True:
        pipeline += " (dirty)"
    lines = [pipeline]
    if view["shards"]:
        for shard in view["shards"]:
            parts = []
            if shard.get("has_data", True):
                parts = [f"os {_cell(shard['os'])}", f"containers {_cell(shard['containers'])}"]
            parts.extend(shard_status(shard))
            node = f" {shard['node']}" if shard.get("node") else ""
            lines.append(f"Worker{node} ({shard['label']}): {' · '.join(parts) or '-'}")
        lines.extend(line for line in shard_worker_lines(view) if line.startswith("Shards differ"))
    else:
        lines.append(f"Worker {_cell(view['node'])}: os {_cell(view['os'])} · containers {_cell(view['containers'])}")
    check = env_check_line(view["env_check"])
    if check:
        lines.append(check)
    iso = view["isolation"]
    if iso:
        hits = ", ".join(iso["leak_hits"])
        lines.append(
            f"Isolation: mode {_cell(iso['mode'])} · canary {_cell(iso['canary_status'])} · "
            f"leak scan {'hits ' + hits if hits else 'no hits' if iso['leak_scanner'] else '-'}"
        )
    lines.append(f"Full provenance: {PROVENANCE_FILE}")
    return lines


def provenance_document(doc: dict) -> str:
    """provenance.md: every provenance fact of the run, the environment check, and the questions kept out."""
    protocol = doc.get("eval_protocol") if isinstance(doc.get("eval_protocol"), dict) else None
    view = provenance_view(protocol)
    if view is None:
        return ""
    suite = str(doc.get("suite") or "-")
    lines = [f"# Provenance — {suite}", ""]
    lines.append(f"- Run: `{_cell(doc.get('run_label') or doc.get('run_id'))}`")
    lines.append("- Machine-readable: `eval_protocol` in `artifact.json`")
    lines.extend(["", "## Run", ""])
    lines.extend(provenance_markdown(protocol)[3:])
    check = env_check_markdown(view["env_check"])
    if check:
        lines.extend(["", *check])
    skipped = [str(item) for item in doc.get("skipped_questions") or []]
    if skipped:
        lines.extend(["", "## Kept out of Harbor", ""])
        lines.extend(f"- {item}" for item in skipped)
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark pin checks and eval provenance")
    sub = parser.add_subparsers(dest="cmd", required=True)

    count = sub.add_parser("assert-count")
    count.add_argument("tasks_dir")
    count.add_argument("expected", type=int)

    write = sub.add_parser("write-inputs")
    write.add_argument("--out", required=True)
    write.add_argument("--repo-dir", required=True)
    write.add_argument("--tasks-dir", required=True)
    write.add_argument("--selected", required=True)
    write.add_argument("--overlay", required=True)
    write.add_argument("--applied", default="0")
    write.add_argument("--workdir", required=True)
    write.add_argument("--pipeline-root", default=os.environ.get("MAC_K3D_ROOT", ""))
    write.add_argument("--icode-root", default="", help="iCode host tree mounted at /opt/icode-host")
    write.add_argument("--mounts", default="", help="JSON list passed to harbor --mounts")

    image = sub.add_parser("record-image")
    image.add_argument("--inputs", required=True)
    image.add_argument("--task-id", required=True)
    image.add_argument("--task-toml", required=True)

    leak = sub.add_parser("record-leakscan")
    leak.add_argument("--inputs", required=True)
    leak.add_argument("--report", required=True)

    reuse = sub.add_parser("record-canary-reuse")
    reuse.add_argument("--inputs", required=True)
    reuse.add_argument("--source", required=True)
    canary = sub.add_parser("record-canary")
    canary.add_argument("--inputs", required=True)
    canary.add_argument("--summary", required=True)

    egress = sub.add_parser("record-egress-probe")
    egress.add_argument("--inputs", required=True)
    egress.add_argument("--probe", required=True)

    network = sub.add_parser("record-trial-network")
    network.add_argument("--inputs", required=True)
    network.add_argument("--record", required=True)

    res = sub.add_parser("record-resources")
    res.add_argument("--inputs", required=True)
    res.add_argument("--plan", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "assert-count":
        assert_task_count(Path(args.tasks_dir), args.expected)
        return 0
    if args.cmd == "write-inputs":
        root = Path(args.pipeline_root) if args.pipeline_root else Path(args.workdir)
        try:
            mounts = json.loads(args.mounts) if args.mounts else []
        except json.JSONDecodeError as exc:
            print(f"ERROR: --mounts is not JSON: {exc}", file=sys.stderr)
            return 1
        write_protocol_inputs(
            out=Path(args.out),
            repo_dir=Path(args.repo_dir),
            tasks_dir=Path(args.tasks_dir),
            selected_file=Path(args.selected),
            overlay=Path(args.overlay),
            applied=str(args.applied).strip().lower() in {"1", "true", "yes", "on"},
            workdir=Path(args.workdir),
            pipeline_root=root,
            icode_root=Path(args.icode_root) if args.icode_root else None,
            mounts=mounts,
        )
        return 0
    if args.cmd == "record-leakscan":
        record_leakscan(Path(args.inputs), Path(args.report))
        return 0
    if args.cmd == "record-canary":
        record_canary(Path(args.inputs), Path(args.summary))
        return 0
    if args.cmd == "record-canary-reuse":
        record_canary_reuse(Path(args.inputs), args.source)
        return 0
    if args.cmd == "record-egress-probe":
        record_egress_probe(Path(args.inputs), Path(args.probe))
        return 0
    if args.cmd == "record-trial-network":
        record_trial_network(Path(args.inputs), Path(args.record))
        return 0
    if args.cmd == "record-resources":
        record_resources(Path(args.inputs), Path(args.plan))
        return 0
    record_task_image(Path(args.inputs), args.task_id, Path(args.task_toml))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
