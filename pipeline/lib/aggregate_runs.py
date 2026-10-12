#!/usr/bin/env python3
"""Merge the shards of one suite run into a single report.

A full-suite build splits its tasks across N ``<suite>_some_task`` shards, each
holding its own worker's CPU lock. Every shard archives ``eval-runs/`` and the
aggregator copies those archives here. This script stitches them back into one
harness tree and then runs the ordinary report path over it, so an aggregated
run and a single-worker run are scored by the same code.

Only tasks are merged. Anti-cheat verdicts stay exactly as the shard decided
them (the anticheat phase is local and needs the gold patch), so this re-adds up the counts
rather than re-judging anything.

With ``--plan`` (the dispatcher's ``shards/plan.txt``) every shard the dispatcher
started gets a row, including one that archived nothing, and a shard's files
count only when its own build wrote them: a workspace is reused, so a build that
stopped early still holds the previous build's inputs and verdicts.

The dispatcher copies the trials without the iCode transcripts; the report's
Provenance names the shard builds that keep them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from render_report import (  # noqa: E402
    SKIPPED_TASKS,
    _thinking_type,
    build_artifact,
    container_mem_peak,
    eval_model_label,
    icode_identity,
    read_skipped_tasks,
    write_report,
)
from score_results import load_json  # noqa: E402
from task_resources import cpu_count  # noqa: E402

VERDICTS = ("clean", "flagged", "rejected", "not_run")
# What has to match for the shards to be one measurement of one iCode setup.
COMPARED_MODEL_PARAMS = ("model", "api_base", "provider", "reasoning_effort", "max_tokens", "max_iterations")
INPUT_PROTOCOL_KEYS = ("harbor", "benchmark", "grader_overlay", "images", "worker", "requester", "pipeline", "isolation")


def shard_report_protocol(shard: Path, builds: list[str], own_only: bool = False) -> dict | None:
    """The eval_protocol the shard's own report phase wrote on its worker; None when it did not run.

    A reused workspace can hold older runs' reports, so the shard's own build wins.
    With ``own_only`` another build's report never stands in for it.
    """
    found = []
    for path in (shard / "output").glob("*/*/artifact.json"):
        doc = load_json(path)
        if isinstance(doc, dict) and isinstance(doc.get("eval_protocol"), dict):
            own = str(doc.get("run_id") or "") in builds
            if own or not own_only:
                found.append((own, path.stat().st_mtime, str(path), doc["eval_protocol"]))
    return max(found, key=lambda item: item[:3])[3] if found else None


def read_plan(path: Path) -> list[dict]:
    """The dispatcher's ``shards/plan.txt``.

    One ``index|build|result|offset|count|tasks`` line per shard, or
    ``...|rollout|attempts`` when each line is one question and one rollout.
    """
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("|")
        if len(parts) not in (6, 8):
            continue
        index, build, result, offset, count, tasks = parts[:6]
        rollout, attempts = (parts[6], parts[7]) if len(parts) == 8 else ("1", "1")
        try:
            rows.append(
                {
                    "shard": int(index),
                    "build": str(int(build)),
                    "result": result.strip() if result.strip() not in ("", "null") else "UNKNOWN",
                    "offset": int(offset),
                    "count": int(count),
                    "tasks": [t.strip() for t in tasks.split(",") if t.strip()],
                    "rollout": int(rollout),
                    "attempts": int(attempts),
                }
            )
        except ValueError:
            continue
    return rows


def planned_questions(plan: list[dict]) -> int:
    """Distinct questions. A per-rollout plan has ``count`` 1 on every line;
    an older shard plan sums ``count`` because one line covers several questions.
    """
    if plan and all(int(entry.get("count") or 0) == 1 for entry in plan):
        named = {tid for entry in plan for tid in entry.get("tasks") or []}
        if named:
            return len(named)
        return len({entry.get("offset") for entry in plan})
    return sum(int(entry.get("count") or 0) for entry in plan)


def built_by(inputs: dict, build: str) -> bool:
    """Whether Jenkins build ``build`` wrote this ``eval_protocol_inputs.json``."""
    requester = inputs.get("requester") if isinstance(inputs.get("requester"), dict) else {}
    return str(requester.get("build_url") or "").rstrip("/").endswith(f"/{build}")


def planned_shards(shards: list[Path], plan: list[dict]) -> list[tuple[int, Path | None, dict | None]]:
    """``(index, eval-runs dir or None, plan line)`` per shard; without a plan, every dir found."""
    if not plan:
        return [(index, shard, None) for index, shard in enumerate(shards, start=1)]
    by_build = {shard.parent.name: shard for shard in shards}
    out = [(entry["shard"], by_build.pop(entry["build"], None), entry) for entry in plan]
    for name in sorted(by_build):
        print(f"WARNING: {by_build[name]} is not in the dispatcher's plan; ignored", file=sys.stderr)
    return out


def inputs_protocol(inputs: dict) -> dict:
    """A protocol from a shard's raw inputs alone. A value the shard did not record stays None."""
    out = {key: inputs[key] for key in INPUT_PROTOCOL_KEYS if key in inputs}
    effort = inputs.get("reasoning_effort")
    out["model_params"] = {
        "model": inputs.get("model"),
        "api_base": inputs.get("api_base"),
        "provider": inputs.get("provider"),
        "reasoning_effort": effort,
        "thinking": {"type": _thinking_type(effort)} if effort else {},
        "max_tokens": inputs.get("max_tokens") or None,
        "max_iterations": inputs.get("max_iterations") or None,
        "n_rollouts": inputs.get("n_rollouts"),
    }
    out["resources"] = {
        "cpu_lock_qty": inputs.get("cpu_lock_qty"),
        "concurrency": inputs.get("concurrency"),
        "cpus_each": inputs.get("cpus_each"),
    }
    return out


def egress_probe_of(protocol: dict) -> dict | None:
    isolation = protocol.get("isolation") if isinstance(protocol.get("isolation"), dict) else {}
    probe = isolation.get("egress_probe")
    return probe if isinstance(probe, dict) and probe.get("image") else None


def trial_network_of(protocol: dict) -> dict | None:
    isolation = protocol.get("isolation") if isinstance(protocol.get("isolation"), dict) else {}
    record = isolation.get("trial_network")
    return record if isinstance(record, dict) and record.get("mode") else None


def isolation_record_of(protocol: dict, key: str) -> dict | None:
    isolation = protocol.get("isolation") if isinstance(protocol.get("isolation"), dict) else {}
    record = isolation.get(key)
    return record if isinstance(record, dict) else None


def node_of(protocol: dict | None, index: int) -> str:
    worker = (protocol or {}).get("worker") if isinstance((protocol or {}).get("worker"), dict) else {}
    return str(worker.get("node") or f"shard {index}")


def shard_row(index: int, builds: list[str], protocol: dict) -> dict:
    icode = protocol.get("icode") if isinstance(protocol.get("icode"), dict) else {}
    row = {
        "shard": index,
        "builds": builds,
        "worker": protocol.get("worker") if isinstance(protocol.get("worker"), dict) else {},
        "resources": protocol.get("resources") if isinstance(protocol.get("resources"), dict) else {},
        "model_params": protocol.get("model_params") if isinstance(protocol.get("model_params"), dict) else {},
        "icode_version": icode_identity(icode) or None,
    }
    probe = egress_probe_of(protocol)
    if probe is not None:
        row["egress_probe"] = {"image": str(probe.get("image")), "substituted": probe.get("substituted") is True}
    network = trial_network_of(protocol)
    if network is not None:
        row["trial_network"] = {key: network.get(key) for key in ("mode", "pool", "prefix", "free", "total", "need")}
    leak = isolation_record_of(protocol, "leak_scan")
    if leak is not None and leak.get("report_sha256"):
        row["leak_scan_sha256"] = str(leak["report_sha256"])
    canary = isolation_record_of(protocol, "canary")
    if canary is not None:
        row["canary"] = {"status": str(canary.get("status") or ""), "summary_sha256": str(canary.get("summary_sha256") or "")}
    return row


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def merge_leak_scans(records: list[dict]) -> dict:
    """The shards' leak scans as one: each shard scanned only its own questions."""
    statuses: dict[str, int] = {}
    for record in records:
        for name, num in (record.get("statuses") if isinstance(record.get("statuses"), dict) else {}).items():
            statuses[str(name)] = statuses.get(str(name), 0) + _count(num)
    return {
        "scanner": ", ".join(sorted({str(r.get("scanner")) for r in records if r.get("scanner")})),
        "hit_tasks": sorted({str(t) for r in records for t in r.get("hit_tasks") or []}),
        "statuses": dict(sorted(statuses.items())),
        "shards": len(records),
    }


def merge_canaries(ran: list[dict | None], stopped: list[dict]) -> dict | None:
    """The shards' canaries as one.

    ``ran`` holds the record (or None) of every shard that ran Harbor, ``stopped``
    the records of shards that stopped before it. Any failed canary fails the
    merge; a shard that ran Harbor without a canary makes it ``missing``.
    """
    records = [r for r in ran if r] + stopped
    if not records:
        return None
    statuses = {str(r.get("status") or "") for r in records}
    if "fail" in statuses:
        status = "fail"
    elif len([r for r in ran if r]) < len(ran) or statuses != {"pass"}:
        status = "missing"
    else:
        status = "pass"
    counts: dict[str, int] = {}
    for record in records:
        for name, num in (record.get("counts") if isinstance(record.get("counts"), dict) else {}).items():
            counts[str(name)] = counts.get(str(name), 0) + _count(num)
    out = {
        "version": ", ".join(sorted({str(r.get("version")) for r in records if r.get("version")})),
        "status": status,
        "nodes": sorted({str(r.get("node")) for r in records if r.get("node")}),
        "tasks": sorted({str(t) for r in records for t in r.get("tasks") or []}),
        "failed_tasks": sorted({str(t) for r in records for t in r.get("failed_tasks") or []}),
        "warn_tasks": sorted({str(t) for r in records for t in r.get("warn_tasks") or []}),
        "counts": counts,
        "shards": len(records),
    }
    fallback = [item for r in records for item in r.get("fallback_from") or [] if isinstance(item, dict)]
    if fallback:
        out["fallback_from"] = fallback
    return out


def merge_trial_networks(records: list[dict]) -> dict:
    """Shards on one subnet pool: the smallest free count and every shard's need."""
    out = dict(records[0])
    frees = [r["free"] for r in records if isinstance(r.get("free"), int) and not isinstance(r.get("free"), bool)]
    if frees:
        out["free"] = min(frees)
    needs = [r.get("need") for r in records]
    if len(records) > 1:
        out["need_by_shard"] = needs
        out["need"] = max((n for n in needs if isinstance(n, int)), default=None)
    out.pop("checked_at", None)
    return out


def node_resources(entries: list[tuple[str, dict]]) -> dict:
    """protocol.resources per node. One node runs its shards one after another, so
    it counts once, with its largest plan; different nodes run side by side."""
    by_node: dict[str, dict] = {}
    for node, res in entries:
        prev = by_node.get(node)
        if prev is None or _count(res.get("concurrency")) > _count(prev.get("concurrency")):
            by_node[node] = {key: res.get(key) for key in ("cpu_lock_qty", "concurrency", "cpus_each")}
    return by_node


def merge_resources(base: dict, entries: list[tuple[str, dict]]) -> dict:
    by_node = node_resources(entries)
    if not by_node:
        return base
    out = dict(base)
    out["concurrency"] = sum(_count(r.get("concurrency")) for r in by_node.values()) or base.get("concurrency")
    out["cpu_lock_qty"] = sum(_count(r.get("cpu_lock_qty")) for r in by_node.values()) or base.get("cpu_lock_qty")
    cpus = {cpu_count(r.get("cpus_each")) for r in by_node.values() if r.get("cpus_each")}
    out["cpus_each"] = cpus.pop() if len(cpus) == 1 else None
    if len(by_node) > 1:
        out["by_node"] = by_node
    return out


def merge_resource_plans(plans: list[tuple[str, dict]]) -> dict | None:
    """The shards' eval_resources.json as one: declared per question, applied per node."""
    if not plans:
        return None
    if len(plans) == 1:
        return plans[0][1]
    per_task: dict = {}
    peak: dict = {}
    declared_count = 0
    for _, doc in plans:
        declared = doc.get("declared") if isinstance(doc.get("declared"), dict) else {}
        if isinstance(declared.get("per_task"), dict):
            per_task.update(declared["per_task"])
        for key, value in (declared.get("peak") if isinstance(declared.get("peak"), dict) else {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                peak[key] = max(peak.get(key, value), value)
        declared_count += _count(declared.get("tasks"))
    applied_by_node: dict[str, dict] = {}
    host_by_node: dict[str, dict] = {}
    for node, doc in plans:
        applied = doc.get("applied") if isinstance(doc.get("applied"), dict) else {}
        prev = applied_by_node.get(node)
        if prev is None or _count(applied.get("slots")) > _count(prev.get("slots")):
            applied_by_node[node] = applied
            if isinstance(doc.get("host"), dict):
                host_by_node[node] = doc["host"]
    out = json.loads(json.dumps(plans[0][1]))
    out["declared"] = {
        **(out.get("declared") if isinstance(out.get("declared"), dict) else {}),
        "per_task": per_task,
        "peak": peak,
        "tasks": declared_count,
        "declared_tasks": len(per_task),
    }
    applied = dict(out.get("applied") if isinstance(out.get("applied"), dict) else {})
    applied["slots"] = sum(_count(a.get("slots")) for a in applied_by_node.values())
    applied["cpu_lock_qty"] = sum(_count(a.get("cpu_lock_qty")) for a in applied_by_node.values())
    cpus = {cpu_count(a.get("cpus_each")) for a in applied_by_node.values() if a.get("cpus_each")}
    applied["cpus_each"] = cpus.pop() if len(cpus) == 1 else None
    out["applied"] = applied
    out["by_node"] = {node: {"applied": a, "host": host_by_node.get(node, {})} for node, a in applied_by_node.items()}
    out["reasons"] = [reason for _, doc in plans for reason in doc.get("reasons") or []]
    return out


def merge_protocols(
    protocols: list[dict],
    ran: list[tuple[str, dict]] | None = None,
    stopped: list[dict] | None = None,
) -> dict:
    """The first shard's protocol, with every shard's per-task image and benchmark pins.

    ``ran`` lists ``(node, protocol)`` for the shards whose own build ran Harbor,
    ``stopped`` the protocols of shards that stopped before it. Their leak scans,
    canaries, trial networks and resources are merged into one record each.
    When the shards' egress probes or trial network pools differ, the merged
    protocol names none of them; each shard's row keeps its own.
    """
    if not protocols:
        return {}
    out = json.loads(json.dumps(protocols[0]))
    ran = ran if ran is not None else [(node_of(p, i), p) for i, p in enumerate(protocols, start=1)]
    isolation = out.get("isolation") if isinstance(out.get("isolation"), dict) else None
    probes = {
        (str(p.get("image")), p.get("substituted") is True)
        for p in (egress_probe_of(protocol) for protocol in protocols)
        if p is not None
    }
    if len(probes) > 1 and isolation is not None:
        isolation.pop("egress_probe", None)
    networks = [n for n in (trial_network_of(protocol) for protocol in protocols) if n is not None]
    pools = {(str(n.get("mode")), str(n.get("pool") or ""), n.get("prefix")) for n in networks}
    if len(pools) > 1 and isolation is not None:
        isolation.pop("trial_network", None)
    elif networks and isolation is not None:
        isolation["trial_network"] = merge_trial_networks(networks)
    if isolation is not None and len(protocols) > 1:
        leaks = [r for r in (isolation_record_of(p, "leak_scan") for _, p in ran) if r is not None]
        if leaks:
            isolation["leak_scan"] = merge_leak_scans(leaks)
        else:
            isolation.pop("leak_scan", None)
        canary = merge_canaries(
            [isolation_record_of(p, "canary") for _, p in ran],
            [r for r in (isolation_record_of(p, "canary") for p in stopped or []) if r is not None],
        )
        if canary is not None:
            isolation["canary"] = canary
        else:
            isolation.pop("canary", None)
    if len(protocols) > 1:
        out["resources"] = merge_resources(
            out.get("resources") if isinstance(out.get("resources"), dict) else {},
            [(node, p["resources"]) for node, p in ran if isinstance(p.get("resources"), dict)],
        )
    images = out.get("images") if isinstance(out.get("images"), dict) else {}
    bench = out.get("benchmark") if isinstance(out.get("benchmark"), dict) else None
    pins = bench.get("tasks") if bench is not None and isinstance(bench.get("tasks"), dict) else None
    for other in protocols[1:]:
        if isinstance(other.get("images"), dict):
            images.update({k: v for k, v in other["images"].items() if k not in images})
        other_bench = other.get("benchmark") if isinstance(other.get("benchmark"), dict) else {}
        if pins is not None and isinstance(other_bench.get("tasks"), dict):
            pins.update({k: v for k, v in other_bench["tasks"].items() if k not in pins})
    if images:
        out["images"] = images
    return out


def shard_mismatches(rows: list[dict]) -> dict:
    """Model params and iCode versions that differ between shards, by key.

    A value a shard did not record (it stopped early) is unknown, not different.
    """
    found: dict[str, list] = {}
    for key in COMPARED_MODEL_PARAMS:
        values = {json.dumps(row["model_params"].get(key)) for row in rows if row["model_params"].get(key) is not None}
        if len(values) > 1:
            found[key] = sorted(json.loads(v) for v in values)
    versions = {row.get("icode_version") for row in rows if row.get("icode_version")}
    if len(versions) > 1:
        found["icode_version"] = sorted(str(v) for v in versions)
    return found


def shard_dirs(root: Path) -> list[Path]:
    """Every ``eval-runs`` directory under the copied artifacts, in a stable order.

    copyArtifacts keeps each build's paths, so the layout is
    ``shards/<build>/eval-runs/...`` — but a hand-assembled ``shards/eval-runs``
    has to work too. A shard that stopped before Harbor has no ``harness`` but
    may still hold its inputs.
    """
    marks = ("harness", "eval_protocol_inputs.json", "selected_tasks.txt")
    found = sorted({p.resolve() for p in root.rglob("eval-runs") if any((p / m).exists() for m in marks)})
    if not found and (root / "harness").is_dir():
        found = [root]
    return found


def name_offset_shards(rows: list[dict], suites: list[list[str]], ran_tasks: set[str]) -> list[str]:
    """Name the questions of offset shards that ran nothing; returns the names found.

    Every shard that reached the tasks phase wrote the same byte-ordered
    ``suite_tasks.txt``, and an offset shard's slice is ``suite[offset:offset+count]``.
    Lists that disagree name nothing, and the shard stays counted only.
    """
    unnamed = [row for row in rows if row.get("not_run_count")]
    if not unnamed or not suites:
        return []
    if any(suite != suites[0] for suite in suites[1:]):
        print(
            "WARNING: the shards listed different suites (benchmark or pipeline differ), "
            "so the questions of a shard that ran nothing stay unnamed",
            file=sys.stderr,
        )
        return []
    suite = suites[0]
    found: list[str] = []
    for row in unnamed:
        offset, count = int(row.get("not_run_offset") or 0), int(row["not_run_count"])
        names = suite[offset : offset + count]
        if len(names) != count:
            print(
                f"WARNING: shard {row['shard']} {row['builds'][0]}: offset {offset} + {count} runs past "
                f"the suite's {len(suite)} questions; left unnamed",
                file=sys.stderr,
            )
            continue
        overlap = sorted(set(names) & ran_tasks)
        if overlap:
            print(
                f"WARNING: shard {row['shard']} {row['builds'][0]}'s slice overlaps questions another shard ran "
                f"({', '.join(overlap)}); the shards cut different slices of the suite",
                file=sys.stderr,
            )
        row["not_run"] = names
        row.pop("not_run_count", None)
        row.pop("not_run_offset", None)
        found.extend(names)
    return found


def coverage_record(rows: list[dict], plan: list[dict], harness: Path, tasks: list[str]) -> dict:
    """How many of the dispatcher's planned questions the merged report has trials for."""
    from score_results import harbor_task_trials

    not_run = [tid for row in rows for tid in row.get("not_run") or []]
    failed = [
        f"shard {row['shard']} {row['builds'][0]} {row.get('result') or 'UNKNOWN'}"
        for row in rows
        if row.get("not_run") or row.get("not_run_count")
    ]
    return {
        "planned": planned_questions(plan),
        "with_trials": sum(1 for tid in tasks if harbor_task_trials(harness, tid)),
        "not_run": not_run,
        "not_named": sum(int(row.get("not_run_count") or 0) for row in rows),
        "shards_not_run": failed,
        "in_metrics": len(tasks),
    }


def merge_harness(shards: list[Path], dest: Path, plan: list[dict] | None = None) -> dict:
    """Copy every shard's trials and anti-cheat verdicts under one harness dir.

    Shard directories are named per build, so trials cannot collide. A task that
    ran in several builds keeps every trial, ordered by the rollout number
    written next to that build's jobs dir.

    With a ``plan``, a shard's inputs, selection and resources count only when
    its own build wrote them, its verdicts only when its own build has trials,
    and a shard without trials keeps a row naming the questions it did not run.
    """
    harness = dest / "harness"
    (harness / "harbor_runs").mkdir(parents=True, exist_ok=True)
    (harness / "anticheat").mkdir(parents=True, exist_ok=True)
    tasks: list[str] = []
    summaries: list[dict] = []
    pipelines: list[dict] = []
    protocols: list[dict] = []
    rows: list[dict] = []
    suites: list[list[str]] = []
    ran_tasks: set[str] = set()
    first_inputs: dict | None = None
    first_resources: dict | None = None
    ran_protocols: list[tuple[str, dict]] = []
    stopped_protocols: list[dict] = []
    resource_plans: list[tuple[str, dict]] = []
    memory: list[dict] = []
    skipped: dict[str, str] = {}
    transcript_builds: list[str] = []
    for index, shard, entry in planned_shards(shards, plan or []):
        build = entry["build"] if entry else None
        own_run = f"jenkins-{build}" if build else None
        src = shard / "harness" / "harbor_runs" if shard else None
        builds = sorted(p for p in src.iterdir() if p.is_dir()) if src and src.is_dir() else []
        for run in builds:
            target = harness / "harbor_runs" / f"shard{index}-{run.name}"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(run, target, symlinks=True)
        names = [run.name for run in builds]
        ran = own_run in names if own_run else bool(names)
        if ran:
            transcript_builds.extend([build] if build else [n.removeprefix("jenkins-") for n in names])
        got = load_json(shard / "eval_protocol_inputs.json") if shard else None
        inputs = got if isinstance(got, dict) else {}
        trusted = shard is not None and (build is None or built_by(inputs, build))
        if shard is not None and inputs and not trusted:
            requester = inputs.get("requester") if isinstance(inputs.get("requester"), dict) else {}
            print(
                f"WARNING: shard {index} #{build} holds files from {requester.get('build_url') or 'another build'}; ignored",
                file=sys.stderr,
            )
        if not trusted:
            inputs = {}
        if shard is not None and (ran or build is None):
            for name in ("anticheat.jsonl", "summary.json"):
                got = shard / "harness" / "anticheat" / name
                if not got.is_file():
                    continue
                if name == "summary.json":
                    doc = load_json(got)
                    if isinstance(doc, dict):
                        summaries.append(doc)
                else:
                    with (harness / "anticheat" / name).open("a", encoding="utf-8") as out:
                        out.write(got.read_text(encoding="utf-8"))
        shard_tasks: list[str] = []
        selected = shard / "selected_tasks.txt" if shard else None
        if trusted and selected is not None and selected.is_file():
            shard_tasks = [ln.strip() for ln in selected.read_text(encoding="utf-8").splitlines() if ln.strip()]
        elif entry is not None:
            shard_tasks = list(entry["tasks"])
        for tid in shard_tasks:
            if tid not in tasks:
                tasks.append(tid)
        if ran:
            ran_tasks.update(shard_tasks)
        suite_file = shard / "suite_tasks.txt" if shard else None
        if trusted and suite_file is not None and suite_file.is_file():
            suites.append([ln.strip() for ln in suite_file.read_text(encoding="utf-8").splitlines() if ln.strip()])
        if inputs and first_inputs is None:
            first_inputs = inputs
        if isinstance(inputs.get("pipeline"), dict):
            pipelines.append(inputs["pipeline"])
        protocol = None
        if trusted:
            protocol = shard_report_protocol(shard, names, own_only=build is not None) or (
                inputs_protocol(inputs) if inputs else None
            )
        if protocol is not None:
            protocols.append(protocol)
            if ran:
                ran_protocols.append((node_of(protocol, index), protocol))
            else:
                stopped_protocols.append(protocol)
        row_builds = [own_run] if own_run else names
        if protocol is not None or entry is not None:
            row = shard_row(index, row_builds, protocol or {})
            if entry is not None:
                row["result"] = entry["result"]
                if not ran:
                    if shard_tasks:
                        row["not_run"] = shard_tasks
                    elif entry["count"]:
                        row["not_run_count"] = entry["count"]
                        row["not_run_offset"] = entry["offset"]
            rows.append(row)
        if trusted:
            got = load_json(shard / "eval_resources.json")
            if isinstance(got, dict):
                first_resources = first_resources or got
                if ran:
                    resource_plans.append((node_of(protocol, index), got))
        if trusted and ran:
            mem_file = shard / "harness" / "container_mem.jsonl"
            if container_mem_peak(mem_file) is not None:
                for line in mem_file.read_text(encoding="utf-8").splitlines():
                    try:
                        got = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(got, dict):
                        memory.append({**got, "shard": index})
            for tid, why in read_skipped_tasks(shard / SKIPPED_TASKS).items():
                skipped.setdefault(tid, why)
    for tid in name_offset_shards(rows, suites, ran_tasks):
        if tid not in tasks:
            tasks.append(tid)
    merged = merge_anticheat(summaries)
    if merged is not None:
        (harness / "anticheat" / "summary.json").write_text(
            json.dumps(merged, indent=2) + "\n", encoding="utf-8"
        )
    return {
        "harness": harness,
        "tasks": sorted(tasks),
        "protocol": merge_protocols(protocols, ran_protocols, stopped_protocols),
        "inputs": first_inputs or {},
        "shard_rows": rows,
        "resources": merge_resource_plans(resource_plans) or first_resources,
        "anticheat": merged,
        "pipelines": pipelines,
        "shards": len(plan) if plan else len(shards),
        "memory": memory,
        "skipped": skipped,
        "transcript_builds": sorted(set(transcript_builds), key=lambda b: (not b.isdigit(), int(b) if b.isdigit() else 0, b)),
    }


def pipeline_status(pipelines: list[dict]) -> dict | None:
    """Whether every shard ran the same pipeline build.

    Each worker runs the pipeline embedded in its own mac-k3d binary, so a worker
    that missed a redeploy shows up here as a second commit or pipeline hash.
    """
    found = sorted(
        {(str(p.get("commit") or ""), str(p.get("pipeline_hash") or "")) for p in pipelines}
    )
    if not found:
        return None
    if len(found) == 1:
        return {"pipeline_status": "same"}
    return {
        "pipeline_status": "mixed",
        "pipelines": [{"commit": commit, "pipeline_hash": digest} for commit, digest in found],
    }


def merge_anticheat(summaries: list[dict]) -> dict | None:
    """Add up the shards' verdict counts. A shard that did not run one wins nothing."""
    if not summaries:
        return None
    out = dict(summaries[0])
    counts = {v: 0 for v in VERDICTS}
    attempts = 0
    no_transcript = 0
    overrides = 0
    rejected: list = []
    flagged: list = []
    for summ in summaries:
        got = summ.get("counts") or {}
        for verdict in VERDICTS:
            counts[verdict] += int(got.get(verdict) or 0)
        attempts += int(summ.get("attempts") or 0)
        no_transcript += int(summ.get("no_transcript") or 0)
        overrides += int(summ.get("overrides") or 0)
        rejected.extend(summ.get("rejected") or [])
        flagged.extend(summ.get("flagged") or [])
    out.update(
        {
            "counts": counts,
            "attempts": attempts,
            "no_transcript": no_transcript,
            "overrides": overrides,
            "rejected": rejected,
            "flagged": flagged,
            "shards": len(summaries),
        }
    )
    versions = {str(s.get("version")) for s in summaries if s.get("version")}
    if len(versions) > 1:
        # Different shards ran different pipelines; the merged number is not comparable.
        out["status"] = "mixed_versions"
        out["versions"] = sorted(versions)
    return out


def _print_icode_under_test(doc: dict) -> None:
    """Name the iCode the merged shards graded, or MIXED when they disagree."""
    protocol = doc.get("eval_protocol") if isinstance(doc.get("eval_protocol"), dict) else {}
    shards = [s for s in protocol.get("shards") or [] if isinstance(s, dict) and s.get("icode_version")]
    versions = sorted({str(s["icode_version"]) for s in shards})
    icode = protocol.get("icode") if isinstance(protocol.get("icode"), dict) else {}
    n = len(shards) or 1
    if len(versions) > 1:
        print(f"iCode under test ({n} shards): MIXED: {', '.join(versions)}")
        return
    shown = versions[0] if versions else (icode.get("version") or "-")
    print(f"iCode under test ({n} shards): {shown}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", required=True, help="dir holding the copied shard artifacts")
    ap.add_argument("--benchmark", required=True)
    ap.add_argument("--run-group", required=True)
    ap.add_argument("--n-rollouts", type=int, default=4)
    ap.add_argument("--plan", default="", help="the dispatcher's shards/plan.txt (index|build|result|offset|count|tasks)")
    ap.add_argument("--build-url", default="", help="the dispatcher build's URL, the merged report's requester")
    ap.add_argument("--requester-user", default="", help="the Jenkins user who started the dispatcher build")
    ap.add_argument("--shard-job", default="", help="the shard job, whose builds keep the iCode transcripts")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    root = Path(args.shards)
    shards = shard_dirs(root)
    plan = read_plan(Path(args.plan)) if args.plan else []
    if not shards and not plan:
        print(
            f"ERROR: no shard archives under {root}. The shards archive eval-runs/ only when "
            "RUN_GROUP is set, so check that the dispatcher passed it.",
            file=sys.stderr,
        )
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    merged = merge_harness(shards, out, plan)
    tasks = merged["tasks"]
    if not tasks:
        print(f"ERROR: shards under {root} name no tasks (no selected_tasks.txt)", file=sys.stderr)
        return 1

    protocol = merged["protocol"] or {}
    inputs = merged["inputs"]
    params = protocol.get("model_params") if isinstance(protocol.get("model_params"), dict) else {}
    rows = merged["shard_rows"]
    for row in rows:
        if row.get("not_run"):
            print(
                f"WARNING: shard {row['shard']} {row['builds'][0]} ({row.get('result')}) ran no trials; "
                f"{len(row['not_run'])} questions count as missing",
                file=sys.stderr,
            )
        elif row.get("not_run_count"):
            print(
                f"WARNING: shard {row['shard']} {row['builds'][0]} ({row.get('result')}) ran no trials; "
                f"its {row['not_run_count']} questions at offset {row.get('not_run_offset')} are not named, "
                "so the report does not count them",
                file=sys.stderr,
            )
    if isinstance(protocol.get("images"), dict):
        # A shard on an older pipeline still lists its workspace's earlier tasks.
        protocol["images"] = {tid: image for tid, image in protocol["images"].items() if tid in tasks}
    requester = protocol.get("requester") if isinstance(protocol.get("requester"), dict) else {}
    if args.build_url:
        protocol["requester"] = requester = {**requester, "build_url": args.build_url}
    if args.requester_user.strip():
        protocol["requester"] = {**requester, "user": args.requester_user.strip()}
    if merged["transcript_builds"]:
        protocol["transcripts"] = {"job": args.shard_job.strip(), "builds": merged["transcript_builds"]}
    if rows:
        protocol["shards"] = rows
        mismatched = shard_mismatches(rows)
        protocol["model_params_status"] = "mixed" if mismatched else "same"
        if mismatched:
            protocol["model_params_mismatch"] = mismatched
            detail = "; ".join(f"{key} {values}" for key, values in mismatched.items())
            print(
                f"WARNING: shards ran different model params or iCode ({detail}). "
                "This report is not one measurement of one iCode setup.",
                file=sys.stderr,
            )
    applied = (merged["resources"] or {}).get("applied") or {}
    model = str(params.get("model") or inputs.get("model") or "")
    doc = build_artifact(
        suite=args.benchmark,
        model=eval_model_label(model) if model else "",
        api_base=str(params.get("api_base") or inputs.get("api_base") or ""),
        task_ids=tasks,
        harness_dir=merged["harness"],
        baseline_dir=out / "baseline",
        n_rollouts=max(1, args.n_rollouts),
        concurrency=int(applied.get("slots") or 0),
        cpus_each=cpu_count(applied.get("cpus_each")) or 0,
        run_id=args.run_group,
        eval_protocol=protocol or None,
    )
    doc["run_group"] = args.run_group
    doc["shards"] = merged["shards"]
    if merged["resources"]:
        doc["resources"] = merged["resources"]
    peaks = [float(row["peak_gb"]) for row in merged["memory"] if isinstance(row.get("peak_gb"), (int, float))]
    doc["container_mem_max_gb"] = max((gb for gb in peaks if gb > 0), default=None)
    doc["skipped_questions"] = [f"{tid}: {why}" for tid, why in sorted(merged["skipped"].items())]
    if merged["memory"]:
        (out / "container_mem.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in merged["memory"]), encoding="utf-8"
        )
    if doc["skipped_questions"]:
        (out / "skipped_questions.txt").write_text(
            "".join(f"{item}\n" for item in doc["skipped_questions"]), encoding="utf-8"
        )
    status = pipeline_status(merged["pipelines"])
    if status:
        doc.update(status)
        if status["pipeline_status"] == "mixed":
            names = ", ".join(p["commit"][:12] or "unknown" for p in status["pipelines"])
            print(
                f"WARNING: shards ran different pipelines ({names}). "
                "A worker missed a redeploy; this report is not one run of one pipeline.",
                file=sys.stderr,
            )
    if plan:
        coverage = coverage_record(rows, plan, merged["harness"], tasks)
        doc["coverage"] = coverage
        requeues = [
            {
                "tasks": entry["tasks"],
                "offset": entry["offset"],
                "rollout": entry.get("rollout") or 1,
                "attempts": entry.get("attempts") or 1,
            }
            for entry in plan
            if (entry.get("attempts") or 1) > 1
        ]
        if requeues:
            protocol["oom_requeues"] = requeues
            doc["eval_protocol"] = protocol
        if coverage["planned"] != len(tasks):
            print(
                f"WARNING: the dispatcher planned {coverage['planned']} questions but Pass@k covers {len(tasks)}; "
                "the metrics are not over the whole selection",
                file=sys.stderr,
            )
    write_report(doc, out)
    _print_icode_under_test(doc)
    # archive_run.py packs these tasks' trials into the combined .tar.gz.
    (out / "selected_tasks.txt").write_text("".join(f"{tid}\n" for tid in tasks), encoding="utf-8")
    print(
        f"aggregate: {merged['shards']} shards, {len(tasks)} tasks, "
        f"{args.n_rollouts} rollouts -> {out / 'artifact.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
