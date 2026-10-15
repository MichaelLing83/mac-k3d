#!/usr/bin/env python3
"""What each task declares it needs, and how many of them fit on this worker.

Harbor applies ``[verifier.environment]`` cpus/memory_mb/storage_mb from task.toml
unless something overrides them. Up to PF.3 mac-k3d overrode both with
``CPU_LOCK_QTY / EVAL_SLOTS``, which handed a task declaring 2 CPUs and 8 GB a
single CPU and whatever memory was left. Now the declared numbers stand and this
module only decides how many trials run at once.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from pathlib import Path

# Leave this much RAM to the host, the Jenkins agent and Docker itself.
HOST_RESERVE_GB = 2
# Below this much free space Docker image pulls fail mid-run.
MIN_DISK_GB = 40

_CPUS = re.compile(r"^\s*cpus\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$", re.M)
_MEMORY_MB = re.compile(r"^\s*memory_mb\s*=\s*([0-9]+)\s*$", re.M)
_STORAGE_MB = re.compile(r"^\s*storage_mb\s*=\s*([0-9]+)\s*$", re.M)


def _meminfo_kb(key: str) -> int | None:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith(f"{key}:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def mem_available_kb() -> int | None:
    """Free right now. Decides how many trials start, not whether any can."""
    return _meminfo_kb("MemAvailable")


def mem_total_kb() -> int | None:
    """Installed RAM. Decides whether this worker can host the suite at all."""
    return _meminfo_kb("MemTotal")


def cpu_count(value) -> int | float | None:
    """A CPU count as task.toml means it: ``cpus = 2.0`` is 2, ``0.5`` stays fractional.

    task.toml writes whole counts as floats, and ``2.0`` is not an int to argparse
    or to a shell ``case`` pattern.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return int(num) if num.is_integer() else num


def disk_free_gb(path: str | Path) -> float | None:
    try:
        return shutil.disk_usage(str(path)).free / (1024**3)
    except OSError:
        return None


def declared_for_task(task_toml: Path) -> dict:
    """Largest cpus / memory_mb / storage_mb any environment block in this task asks for.

    A task.toml carries the same keys under ``[verifier.environment]`` and
    ``[environment]``; the agent and the verifier both have to fit, so take the max.
    """
    try:
        text = task_toml.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict = {}
    cpus = [float(m) for m in _CPUS.findall(text)]
    mem = [int(m) for m in _MEMORY_MB.findall(text)]
    storage = [int(m) for m in _STORAGE_MB.findall(text)]
    if cpus:
        out["cpus"] = max(cpus)
    if mem:
        out["memory_mb"] = max(mem)
    if storage:
        out["storage_mb"] = max(storage)
    return out


def declared_for_tasks(tasks_dir: Path, task_ids: list[str]) -> dict:
    """Per-task declarations plus the peak across them all."""
    per_task: dict[str, dict] = {}
    for tid in task_ids:
        got = declared_for_task(tasks_dir / tid / "task.toml")
        if got:
            per_task[tid] = got
    peak = {
        "cpus": max((d.get("cpus", 0) for d in per_task.values()), default=0),
        "memory_mb": max((d.get("memory_mb", 0) for d in per_task.values()), default=0),
        "storage_mb": max((d.get("storage_mb", 0) for d in per_task.values()), default=0),
    }
    return {"per_task": per_task, "peak": peak, "tasks": len(task_ids), "declared_tasks": len(per_task)}


def cpus_to_lock(cpus: float) -> int:
    """Whole cores the Jenkins lock takes. ``2.0`` is 2; a fraction rounds up."""
    if cpus <= 0:
        return 1
    return max(1, math.ceil(cpus))


def plan_slots(
    peak: dict,
    cpu_lock_qty: int,
    trials: int,
    mem_available_kb_: int | None,
    mem_total_kb_: int | None,
    free_disk_gb: float | None,
    measured_peak_gb: float | None = None,
    jenkins: bool = False,
) -> dict:
    """How many trials run at once, from CPUs only.

    RAM is not an admission limit and does not change the slot count. A Jenkins
    build is one container (``jenkins``), and it does not fit when the cores it
    locked are below what the task declares. A local run divides ``cpu_lock_qty``
    by the declared CPUs.
    """
    del mem_available_kb_, mem_total_kb_, measured_peak_gb
    cpus = float(peak.get("cpus") or 0)
    mem_mb = int(peak.get("memory_mb") or 0)
    need = cpus_to_lock(cpus) if cpus > 0 else 1
    reasons: list[str] = []
    blockers: list[str] = []

    if cpus > 0 and cpu_lock_qty < need:
        blockers.append(f"each trial declares {cpus:g} CPUs but this build locked only {cpu_lock_qty}")
    if jenkins or cpus <= 0:
        slots = 1
        reasons.append(f"one container; lock {need} CPUs" if jenkins else "no declared CPUs; one at a time")
    else:
        by_cpu = int(cpu_lock_qty // cpus) if cpus > 0 else 1
        slots = max(1, by_cpu)
        reasons.append(f"cpu {cpu_lock_qty}/{cpus:g} = {by_cpu}")
        if trials > 0:
            slots = min(slots, trials)
    if free_disk_gb is not None and free_disk_gb < MIN_DISK_GB and not jenkins:
        slots = min(slots, 1)
        reasons.append(f"free disk {free_disk_gb:.0f}GB below {MIN_DISK_GB}GB, one at a time")
    return {
        "slots": slots,
        "fits": not blockers,
        "blockers": blockers,
        "cpus_each": cpu_count(cpus) if cpus > 0 else None,
        "cpus_lock": need,
        "memory_mb_each": mem_mb or None,
        "per_trial_gb": None,
        "reasons": reasons,
    }


def _shell(name: str, value) -> str:
    return f"{name}={value}"


def sample_peak(harness_dir: Path, slots: int = 0) -> float | None:
    """Append one docker-stats snapshot to the build's memory trace.

    One ``harbor run`` has no single active question, so the trace is per build:
    ``render_report`` takes the largest ``peak_gb`` it finds, and the next build
    feeds that back in as ``--measured-peak-gb``.
    """
    from eval_slots import measure_container_gb

    gb = measure_container_gb()
    if gb is None or gb <= 0:
        return None
    harness_dir.mkdir(parents=True, exist_ok=True)
    row = {"at": int(time.time()), "peak_gb": round(gb, 4), "slots": slots}
    with (harness_dir / "container_mem.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    peak_file = harness_dir / "container_mem_peak_gb"
    prev = 0.0
    if peak_file.is_file():
        try:
            prev = float(peak_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            prev = 0.0
    peak = max(prev, gb)
    peak_file.write_text(f"{peak:.6f}\n", encoding="utf-8")
    return peak


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    sample_p = sub.add_parser("sample", help="record one docker-stats memory snapshot")
    sample_p.add_argument("--harness-dir", required=True)
    sample_p.add_argument("--slots", type=int, default=0)

    plan_p = sub.add_parser("plan", help="declared resources and how many trials fit")
    plan_p.add_argument("--tasks-dir", required=True)
    plan_p.add_argument("--selected", required=True, help="newline-separated task dir names")
    plan_p.add_argument("--cpu", type=int, required=True, help="CPU_LOCK_QTY for this build")
    plan_p.add_argument("--jenkins", action="store_true", help="one container; the lock is the declared CPUs")
    plan_p.add_argument("--n-rollouts", type=int, default=1)
    plan_p.add_argument("--workdir", default=".")
    plan_p.add_argument(
        "--measured-peak-gb", type=float, default=0.0, help="observed container peak, when a prior run measured one"
    )
    plan_p.add_argument("--out", default="", help="also write the plan as JSON here")

    declare_p = sub.add_parser("declare", help="whole CPUs to lock for the selected tasks")
    declare_p.add_argument("--tasks-dir", required=True)
    declare_p.add_argument("--selected", required=True)
    declare_p.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd is None:
        ap.error("need a subcommand (plan or sample)")

    if args.cmd == "sample":
        sample_peak(Path(args.harness_dir), args.slots)
        return 0

    if args.cmd == "declare":
        tasks_dir = Path(args.tasks_dir)
        ids = [ln.strip() for ln in Path(args.selected).read_text(encoding="utf-8").splitlines() if ln.strip()]
        need = cpus_to_lock(float(declared_for_tasks(tasks_dir, ids)["peak"].get("cpus") or 0))
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{need}\n", encoding="utf-8")
        print(need)
        return 0

    tasks_dir = Path(args.tasks_dir)
    ids = [ln.strip() for ln in Path(args.selected).read_text(encoding="utf-8").splitlines() if ln.strip()]
    declared = declared_for_tasks(tasks_dir, ids)
    peak = declared["peak"]
    trials = len(ids) * max(1, args.n_rollouts)
    avail_kb = mem_available_kb()
    total_kb = mem_total_kb()
    free_disk = disk_free_gb(args.workdir)
    plan = plan_slots(
        peak,
        max(1, args.cpu),
        trials,
        avail_kb,
        total_kb,
        free_disk,
        args.measured_peak_gb or None,
        jenkins=args.jenkins,
    )

    if not plan["fits"]:
        print(
            "ERROR: this worker cannot run the selected tasks as declared: "
            + "; ".join(plan["blockers"])
            + ". This worker's CPU limit is below what the task declares.",
            file=sys.stderr,
        )
        return 1

    doc = {
        "declared": declared,
        "applied": {
            "slots": plan["slots"],
            "cpus_each": plan["cpus_each"],
            "cpus_lock": plan["cpus_lock"],
            "memory_mb_each": plan["memory_mb_each"],
            "per_trial_gb": plan["per_trial_gb"],
            "cpu_lock_qty": args.cpu,
            "source": "task.toml",
        },
        "host": {
            "mem_total_gb": round(total_kb / 1024 / 1024, 1) if total_kb else None,
            "mem_available_gb": round(avail_kb / 1024 / 1024, 1) if avail_kb else None,
            "free_disk_gb": round(free_disk, 1) if free_disk is not None else None,
        },
        "trials": trials,
        "reasons": plan["reasons"],
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    why = "; ".join(plan["reasons"]) or "no declared resources found; one at a time"
    print(
        f"slots: declared cpus={peak['cpus'] or '?'} memory_mb={peak['memory_mb'] or '?'} "
        f"per trial, {plan['slots']} at a time ({why})",
        file=sys.stderr,
    )
    print(_shell("EVAL_SLOTS", plan["slots"]))
    print(_shell("DECLARED_CPUS", peak["cpus"] or 0))
    print(_shell("DECLARED_MEMORY_MB", peak["memory_mb"] or 0))
    print(_shell("DECLARED_STORAGE_MB", peak["storage_mb"] or 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
