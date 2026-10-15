#!/usr/bin/env python3
"""Native DeepSWE builds on an arm64 worker, and the check that proves them.

`mac-k3d eval prepare --benchmark deepswe` builds each question's Dockerfile
natively and proves the build once: Harbor's oracle agent applies the reference
solution (every fail-to-pass (F2P) and pass-to-pass (P2P) test must pass), and
its nop agent changes nothing (some F2P test must fail, every P2P test must
pass). No model is called. The result is stored per machine and reused while
its key matches:

    $MAC_K3D_SHARE/env-check/<benchmark>/<task>.json

An eval (tasks/images) runs a question natively only on a build with a stored
pass, and every other question on the published linux/amd64 image under
emulation, with the reason in the build's env_check.json. x86-64 workers run
the published images as they are and have no env_check.json entries.

  image     the official image name a task.toml declares
  key       the cache key of one native build (JSON)
  cached    the stored entry when its key matches: exit 0 for a pass, 2 for a
            fail; exit 1 when nothing matches
  judge     the pass rule applied to the oracle and nop jobs dirs (JSON
            verdict; exit 0 on pass, 1 on fail; --oracle-only judges the
            oracle half)
  record    store a verdict and make it the build's native entry; exit 1 on fail
  emulated  the build's entry for a question on the published image, and why
  settle    the task-container platform of the selection: native only when
            every selected question is
  platform  the task-container platform env_check.json settles for the selection

A verdict with no grader result (Harbor stopped before the verifier wrote
reward.json or reward.txt: compose or a timeout) or with a crashed verifier
(reward.txt -1) is not stored, so the next prepare checks again.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from icode_sanitize import tree_sha256
from provenance import docker_image_name, sha256_file, task_fingerprints

SCHEMA = "mac-k3d-env-check-v1"
SOURCES = ("published", "built")
COUNTS = ("f2p_total", "f2p_passed", "p2p_total", "p2p_passed")
EMULATED_PLATFORM = "linux/amd64"
PREPARE_HINT = "run mac-k3d eval prepare --benchmark deepswe"


def _load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _ids(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def make_key(
    task_dir: Path,
    *,
    benchmark: str,
    source: str,
    platform: str,
    image_id: str,
    harbor_version: str,
    base_image: str = "",
) -> dict:
    """What a stored verdict depends on. Any change means the check runs again."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)} (got {source!r})")
    key = {
        "schema": SCHEMA,
        "benchmark": benchmark,
        "source": source,
        "platform": platform,
        "image_id": image_id,
        "harbor_version": harbor_version,
        **task_fingerprints(task_dir),
        "tests_sha256": tree_sha256(task_dir / "tests") if (task_dir / "tests").is_dir() else "",
        "solution_sha256": tree_sha256(task_dir / "solution") if (task_dir / "solution").is_dir() else "",
    }
    if source == "built":
        dockerfile = task_dir / "environment" / "Dockerfile"
        key["dockerfile_sha256"] = sha256_file(dockerfile) if dockerfile.is_file() else ""
        key["base_image"] = base_image
    return key


def _slot(key: dict) -> str:
    return f"{key.get('source')} {key.get('platform')}"


def store_path(store: Path, task: str) -> Path:
    return store / f"{task}.json"


def cached(store: Path, task: str, key: dict) -> dict | None:
    """The stored entry for this image when every key part still matches."""
    doc = _load(store_path(store, task))
    results = doc.get("results") if isinstance(doc.get("results"), dict) else {}
    entry = results.get(_slot(key))
    if not isinstance(entry, dict) or entry.get("key") != key:
        return None
    if not isinstance(entry.get("verdict"), dict):
        return None
    return entry


def _exception(jobs: Path) -> str:
    for path in sorted(jobs.glob("*/*/result.json")):
        info = _load(path).get("exception_info")
        if isinstance(info, dict) and info.get("exception_type"):
            return str(info["exception_type"])
    return ""


def trial_result(jobs: Path | None) -> dict | None:
    """The grader's result for the one trial under a jobs dir; None when it wrote none.

    reward.json carries the counts (and apply_failed when the patch did not
    apply); reward.txt alone is test.sh's crash sentinel (-1).
    """
    if jobs is None or not jobs.is_dir():
        return None
    rewards = sorted(jobs.rglob("verifier/reward.json"), key=lambda p: p.stat().st_mtime)
    if rewards:
        doc = _load(rewards[-1])
        out = {name: _int(doc.get(name)) for name in COUNTS}
        out["reward"] = doc.get("reward")
        out["apply_failed"] = bool(doc.get("apply_failed"))
        out["crash"] = False
        return out
    texts = sorted(jobs.rglob("verifier/reward.txt"), key=lambda p: p.stat().st_mtime)
    if texts:
        raw = texts[-1].read_text(encoding="utf-8", errors="replace").strip()
        try:
            value = float(raw)
        except ValueError:
            value = -1.0
        return {**{name: None for name in COUNTS}, "reward": value, "apply_failed": False, "crash": value < 0}
    return None


def _why(agent: str, result: dict | None, jobs: Path | None) -> str:
    """Why one agent's run fails its half of the rule; empty when it passes."""
    if result is None:
        if jobs is None:
            return f"{agent} not run"
        exc = _exception(jobs)
        return f"{agent} left no reward.json" + (f" ({exc})" if exc else "")
    if result["crash"]:
        return f"{agent} verifier crashed (reward {result['reward']})"
    if result["apply_failed"]:
        return f"{agent} patch did not apply"
    if any(result[name] is None for name in COUNTS):
        return f"{agent} reward.json has no F2P/P2P counts"
    f2p, f2p_total = result["f2p_passed"], result["f2p_total"]
    p2p, p2p_total = result["p2p_passed"], result["p2p_total"]
    parts = []
    if agent == "oracle":
        if f2p_total == 0:
            parts.append("oracle f2p 0/0 (no fail-to-pass tests)")
        elif f2p != f2p_total:
            parts.append(f"oracle f2p {f2p}/{f2p_total}")
    elif f2p >= f2p_total:
        parts.append(f"nop f2p {f2p}/{f2p_total} (passes without the fix)")
    if p2p != p2p_total:
        parts.append(f"{agent} p2p {p2p}/{p2p_total}")
    return "; ".join(parts)


def oracle_passes(oracle_jobs: Path) -> bool:
    return not _why("oracle", trial_result(oracle_jobs), oracle_jobs)


def judge(oracle_jobs: Path | None, nop_jobs: Path | None, *, source: str, platform: str) -> dict:
    """The pass rule. oracle: f2p_passed == f2p_total > 0 and p2p_passed == p2p_total.
    nop: f2p_passed < f2p_total and p2p_passed == p2p_total. Anything else fails."""
    oracle = trial_result(oracle_jobs)
    nop = trial_result(nop_jobs) if nop_jobs is not None and nop_jobs.is_dir() else None
    oracle_why = _why("oracle", oracle, oracle_jobs)
    nop_why = _why("nop", nop, nop_jobs if nop_jobs is not None and nop_jobs.is_dir() else None)
    parts = [oracle_why] if oracle_why else [oracle_why, nop_why]
    parts = [p for p in parts if p]
    passed = not parts
    # A fail is stored only when it is the grader's answer, not Harbor's: the
    # oracle's own result, or the nop's after a passing oracle. A crashed
    # verifier (-1) may be the machine (out of memory), so it is checked again.
    stored = (
        oracle is not None
        and not oracle["crash"]
        and (bool(oracle_why) or (nop is not None and not nop["crash"]))
    )
    return {
        "pass": passed,
        "stored": stored,
        "source": source,
        "platform": platform,
        "oracle": oracle,
        "nop": nop,
        "reason": "" if passed else f"environment check failed: {'; '.join(parts)} on {source} {platform}",
    }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_label() -> str:
    return f"{os.environ.get('JOB_NAME') or 'local'}#{os.environ.get('BUILD_NUMBER') or '0'}"


def _summary(entry: dict) -> str:
    if entry.get("mode") == "emulated":
        line = f"emulated on published {entry.get('platform')}: {entry.get('reason') or '-'}"
        return line + (f"; {entry['hint']}" if entry.get("hint") else "")

    def counts(name: str) -> str:
        r = entry.get(name)
        if not isinstance(r, dict) or r.get("f2p_total") is None:
            return f"{name} -"
        return f"{name} f2p {r['f2p_passed']}/{r['f2p_total']} p2p {r['p2p_passed']}/{r['p2p_total']}"

    where = f"{entry.get('source')} {entry.get('platform')}"
    if entry.get("pass"):
        line = f"pass on {where} ({counts('oracle')}; {counts('nop')})"
    else:
        detail = str(entry.get("reason") or "").removeprefix("environment check failed: ").removesuffix(f" on {where}")
        line = f"fail on {where}" + (f" ({detail})" if detail else "")
    if entry.get("reused"):
        line += f"; reused from {entry.get('checked_in') or '-'}"
    return line


def record(
    *,
    store: Path,
    task: str,
    key: dict,
    verdict: dict,
    build_record: Path,
    image: str,
    reused: bool,
    checked_in: str = "",
    checked_at: str = "",
) -> dict:
    """Store a fresh verdict, and make it this build's native entry for the task."""
    if not reused:
        checked_in = checked_in or _build_label()
        checked_at = checked_at or _now()
        if verdict.get("stored"):
            path = store_path(store, task)
            doc = _load(path)
            results = doc.get("results") if isinstance(doc.get("results"), dict) else {}
            results[_slot(key)] = {
                "key": key,
                "verdict": verdict,
                "checked_in": checked_in,
                "checked_at": checked_at,
                "node": os.environ.get("NODE_NAME") or socket.gethostname(),
            }
            _write(path, {"schema": SCHEMA, "task": task, "results": results})
    entry = {
        "mode": "native",
        "source": key.get("source"),
        "platform": key.get("platform"),
        "image": image,
        "image_id": key.get("image_id"),
        "pass": verdict.get("pass") is True,
        "reason": str(verdict.get("reason") or ""),
        "oracle": verdict.get("oracle"),
        "nop": verdict.get("nop"),
        "checked_in": checked_in,
        "checked_at": checked_at,
        "reused": reused,
        "dockerfile_sha256": key.get("dockerfile_sha256") or "",
        "base_image": key.get("base_image") or "",
    }
    _set_entry(build_record, task, entry)
    return entry


def _set_entry(build_record: Path, task: str, entry: dict) -> None:
    doc = _load(build_record)
    tasks = doc.get("tasks") if isinstance(doc.get("tasks"), dict) else {}
    tasks[task] = entry
    doc.update({"schema": SCHEMA, "tasks": tasks})
    _write(build_record, doc)


def native_failure(verdict: dict) -> tuple[str, bool]:
    """Why a native build's check failed, and whether another prepare could change it.

    ``verdict`` is a bare verdict (judge) or a stored entry (cached). A stored
    fail is the grader's answer; one that was not stored (a crash, a timeout)
    may pass next time.
    """
    inner = verdict.get("verdict") if isinstance(verdict.get("verdict"), dict) else verdict
    where = f"{inner.get('source')} {inner.get('platform')}"
    detail = str(inner.get("reason") or "").removeprefix("environment check failed: ").removesuffix(f" on {where}")
    return f"native check failed: {detail or 'no result'}", not inner.get("stored")


def emulated(*, build_record: Path, task: str, image: str, reason: str, hint: bool) -> dict:
    """This build runs ``task`` on the published linux/amd64 image, under emulation, because ``reason``."""
    entry = {
        "mode": "emulated",
        "source": "published",
        "platform": EMULATED_PLATFORM,
        "image": image,
        "reason": reason,
        "hint": PREPARE_HINT if hint else "",
    }
    _set_entry(build_record, task, entry)
    return entry


def settle(*, build_record: Path, selected: Path, benchmark: str, host_platform: str) -> dict:
    """The task-container platform of the selection: native only when every selected question is."""
    ids = _ids(selected)
    doc = _load(build_record)
    tasks = doc.get("tasks") if isinstance(doc.get("tasks"), dict) else {}
    missing = [tid for tid in ids if not isinstance(tasks.get(tid), dict)]
    if missing:
        raise SystemExit(f"ERROR: no image decision for {', '.join(missing)}")
    native = [tid for tid in ids if tasks[tid].get("mode") == "native" and tasks[tid].get("pass") is True]
    emulated_ids = [tid for tid in ids if tasks[tid].get("mode") == "emulated"]
    if native and emulated_ids:
        raise SystemExit(
            f"ERROR: one run mixes native ({', '.join(native)}) and emulated ({', '.join(emulated_ids)}) questions"
        )
    if native and len(native) == len(ids):
        plat = host_platform
    elif len(emulated_ids) == len(ids):
        plat = EMULATED_PLATFORM
    else:
        plat = ""
    doc.update(
        {
            "schema": SCHEMA,
            "benchmark": benchmark,
            "host_platform": host_platform,
            "platform": plat,
            "selected": ids,
            "tasks": tasks,
        }
    )
    _write(build_record, doc)
    return {"native": native, "emulated": emulated_ids, "platform": plat}


def platform(build_record: Path, selected: Path) -> str:
    doc = _load(build_record)
    if doc.get("selected") != _ids(selected):
        return ""
    return str(doc.get("platform") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepSWE image decisions on an arm64 host: native builds and their check, or emulated")
    sub = parser.add_subparsers(dest="cmd", required=True)

    img = sub.add_parser("image")
    img.add_argument("--task-dir", required=True)

    key = sub.add_parser("key")
    key.add_argument("--task-dir", required=True)
    key.add_argument("--benchmark", default=os.environ.get("BENCHMARK") or "deepswe")
    key.add_argument("--source", required=True, choices=SOURCES)
    key.add_argument("--platform", required=True)
    key.add_argument("--image-id", required=True)
    key.add_argument("--harbor-version", default=os.environ.get("HARBOR_VERSION") or "")
    key.add_argument("--base-image", default="")
    key.add_argument("--out", required=True)

    hit = sub.add_parser("cached")
    hit.add_argument("--store", required=True)
    hit.add_argument("--task", required=True)
    hit.add_argument("--key", required=True)
    hit.add_argument("--out", required=True)

    jud = sub.add_parser("judge")
    jud.add_argument("--oracle", default="")
    jud.add_argument("--nop", default="")
    jud.add_argument("--oracle-only", action="store_true")
    jud.add_argument("--key", required=True)
    jud.add_argument("--out", default="")

    rec = sub.add_parser("record")
    rec.add_argument("--store", required=True)
    rec.add_argument("--task", required=True)
    rec.add_argument("--key", required=True)
    rec.add_argument("--verdict", required=True)
    rec.add_argument("--build-record", required=True)
    rec.add_argument("--image", default="")
    rec.add_argument("--reused", action="store_true")

    emu = sub.add_parser("emulated")
    emu.add_argument("--build-record", required=True)
    emu.add_argument("--task", required=True)
    emu.add_argument("--image", default="")
    emu.add_argument("--reason", default="", help="why the question cannot run natively")
    emu.add_argument("--verdict", default="", help="the native check that failed (judge or cached output)")
    emu.add_argument("--hint", action="store_true", help="mac-k3d eval prepare would fix it")

    fin = sub.add_parser("settle")
    fin.add_argument("--build-record", required=True)
    fin.add_argument("--selected", required=True)
    fin.add_argument("--benchmark", default=os.environ.get("BENCHMARK") or "deepswe")
    fin.add_argument("--host-platform", default="")

    plat = sub.add_parser("platform")
    plat.add_argument("--record", required=True)
    plat.add_argument("--selected", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "image":
        name = docker_image_name(Path(args.task_dir) / "task.toml")
        if not name:
            print(f"ERROR: no docker_image in {args.task_dir}/task.toml", file=sys.stderr)
            return 1
        print(name)
        return 0
    if args.cmd == "key":
        _write(
            Path(args.out),
            make_key(
                Path(args.task_dir),
                benchmark=args.benchmark,
                source=args.source,
                platform=args.platform,
                image_id=args.image_id,
                harbor_version=args.harbor_version,
                base_image=args.base_image,
            ),
        )
        return 0
    if args.cmd == "cached":
        entry = cached(Path(args.store), args.task, _load(Path(args.key)))
        if entry is None:
            return 1
        _write(Path(args.out), entry)
        return 0 if entry["verdict"].get("pass") is True else 2
    if args.cmd == "judge":
        if args.oracle_only:
            return 0 if args.oracle and oracle_passes(Path(args.oracle)) else 1
        want = _load(Path(args.key))
        verdict = judge(
            Path(args.oracle) if args.oracle else None,
            Path(args.nop) if args.nop else None,
            source=str(want.get("source") or ""),
            platform=str(want.get("platform") or ""),
        )
        if not args.out:
            print("ERROR: judge needs --out", file=sys.stderr)
            return 2
        _write(Path(args.out), verdict)
        return 0 if verdict["pass"] else 1
    if args.cmd == "record":
        raw = _load(Path(args.verdict))
        # `cached` writes the stored entry; `judge` writes a bare verdict.
        stored = raw if isinstance(raw.get("verdict"), dict) else None
        entry = record(
            store=Path(args.store),
            task=args.task,
            key=_load(Path(args.key)),
            verdict=stored["verdict"] if stored else raw,
            build_record=Path(args.build_record),
            image=args.image,
            reused=args.reused,
            checked_in=str(stored.get("checked_in") or "") if stored else "",
            checked_at=str(stored.get("checked_at") or "") if stored else "",
        )
        print(f"env check: {args.task} {_summary(entry)}")
        return 0 if entry["pass"] else 1
    if args.cmd == "emulated":
        reason, hint = args.reason, args.hint
        if args.verdict:
            reason, hint = native_failure(_load(Path(args.verdict)))
        if not reason:
            print("ERROR: emulated needs --reason or --verdict", file=sys.stderr)
            return 2
        entry = emulated(build_record=Path(args.build_record), task=args.task, image=args.image,
                         reason=reason, hint=hint)
        print(f"images: {args.task} {_summary(entry)}")
        return 0
    if args.cmd == "settle":
        got = settle(
            build_record=Path(args.build_record),
            selected=Path(args.selected),
            benchmark=args.benchmark,
            host_platform=args.host_platform,
        )
        print(
            f"images: {len(got['native'])} native, {len(got['emulated'])} emulated"
            + (f"; task containers {got['platform']}" if got["platform"] else "")
        )
        return 0
    print(platform(Path(args.record), Path(args.selected)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
