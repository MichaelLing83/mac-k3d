#!/usr/bin/env python3
"""Isolation canary verdicts (report P0.6): did the sandbox block what it should?

canary_probe.sh records facts inside the task container; this module turns them
into pass, warn, fail or skip per check, and pass or fail per task. Rules live in
pipeline/config/canary-v1.json. Stdlib only, so the unit tests need no Harbor.

Spec for one task (host lists, gold file names, and content hashes, never gold text):
    canary_verdict.py spec --task-dir T --benchmark B --out spec.json

Verdict of every canary trial under a jobs dir (<jobs>/<task>/<job>/<trial>/agent):
    canary_verdict.py summarize --jobs-dir D --out-dir O [--fallback-file F]

Whether one task's canary trial ran the probe at all (exit 3 and the reason if not):
    canary_verdict.py started --jobs-dir D --task T

Exit codes: 0 pass, 2 a task failed, 1 error. Output names hosts, paths and
variable names only, never values.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import socket
import sys
import tomllib
from collections import Counter
from pathlib import Path

from anticheat_leakscan import is_test_path
from anticheat_similarity import generated_globs, is_generated, parse_patch
from anticheat_similarity import load_config as load_anticheat_config
import harness as harness_table

CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "canary-v1.json"
SCHEMA = "mac-k3d-canary-v1"
CHECKS = (
    "network",
    "model_api",
    "declared_hosts",
    "filesystem",
    "mount",
    "history",
    "env_names",
    "python_env",
    "runtime",
    "icode_home",
    "tool_list",
)
FACT_FILES = {
    "probe": "canary.json",
    "root": "canary_root.json",
    "home_pre": "canary_home_pre.json",
    "home_post": "canary_home_post.json",
    "host": "canary_host.json",
}


def load_config(path: Path | None = None) -> dict:
    return json.loads(Path(path or CONFIG_FILE).read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _gold_text(task_dir: Path) -> str:
    sol = task_dir / "solution"
    path = sol / "solution.patch"
    if not path.is_file():
        found = sorted(sol.glob("*.patch")) if sol.is_dir() else []
        if not found:
            return ""
        path = found[0]
    return path.read_text(encoding="utf-8", errors="replace")


def _declared_hosts(task_dir: Path) -> list[str]:
    try:
        doc = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    agent = doc.get("agent") if isinstance(doc.get("agent"), dict) else {}
    hosts = agent.get("allowed_hosts")
    return [str(h).strip().lower() for h in hosts if str(h).strip()] if isinstance(hosts, list) else []


def content_fingerprint(text: str) -> str:
    """sha256 of the non-empty stripped lines, joined by newlines.

    The same rule runs in canary_probe.sh. An empty result means there is
    nothing to match, so a blank file is not a copy of the answer.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def gold_content_hashes(gold_text: str, globs: list[str]) -> list[str]:
    """Hashes of the answer, never the answer text.

    One hash for each new non-test file the patch creates, plus one for the
    patch text itself so a copied solution.patch still matches.
    """
    hashes: set[str] = set()
    whole = content_fingerprint(gold_text)
    if whole:
        hashes.add(whole)
    for path, info in parse_patch(gold_text).items():
        if not info["new"] or is_test_path(path) or is_generated(path, globs):
            continue
        digest = content_fingerprint("\n".join(info["added"]))
        if digest:
            hashes.add(digest)
    return sorted(hashes)


def gold_file_patterns(gold_text: str, globs: list[str]) -> tuple[list[str], list[str]]:
    """find -name / -path patterns for files the gold patch creates (last two path parts)."""
    names: set[str] = set()
    paths: set[str] = set()
    for path, info in parse_patch(gold_text).items():
        if not info["new"] or is_test_path(path) or is_generated(path, globs):
            continue
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            paths.add("*/" + "/".join(parts[-2:]))
        elif parts:
            names.add(parts[0])
    return sorted(names), sorted(paths)


def build_spec(task_dir: Path, benchmark: str, config: dict | None = None, allow_host: str = "") -> dict:
    """allow_host: CANARY_ALLOW_HOST, a host opened on purpose; it is probed as a source host."""
    config = config or load_config()
    model = [h.lower() for h in config.get("model_hosts") or []]
    skip = set(model) | {h.lower() for h in config.get("model_host_aliases") or []}
    sources = [h.lower() for h in config.get("source_hosts") or []]
    allow_host = allow_host.strip().lower()
    if allow_host and allow_host not in sources:
        sources.insert(0, allow_host)
    hosts = [{"kind": "source", "host": h} for h in sources]
    hosts += [{"kind": "model", "host": h} for h in model]
    for host in _declared_hosts(task_dir):
        if host not in skip and host not in sources and "*" not in host:
            hosts.append({"kind": "declared", "host": host})
    globs = generated_globs(load_anticheat_config(), benchmark)
    gold = _gold_text(task_dir)
    gold_names, gold_paths = gold_file_patterns(gold, globs)
    return {
        "version": config.get("version", ""),
        "task": task_dir.name,
        "benchmark": benchmark,
        "hosts": hosts,
        "names": sorted(set(config.get("gold_names") or []) | set(gold_names)),
        "paths": sorted(set(config.get("gold_paths") or []) | set(gold_paths)),
        "hashes": gold_content_hashes(gold, globs),
        "prune": list(config.get("prune") or []),
        "mount": harness_table.current().mount,
        "timeouts": dict(config.get("timeouts") or {}),
        "model_attempts": int(config.get("model_attempts") or 1),
        "allow_host": allow_host or None,
    }


def render_spec(spec: dict) -> str:
    """canary_spec.txt for canary_probe.sh: one "<key> <value...>" per line."""
    lines = [f"host {h['kind']} {h['host']}" for h in spec.get("hosts") or []]
    lines += [f"name {n}" for n in spec.get("names") or []]
    lines += [f"path {p}" for p in spec.get("paths") or []]
    lines += [f"hash {h}" for h in spec.get("hashes") or []]
    lines += [f"prune {p}" for p in spec.get("prune") or []]
    lines += [f"timeout {k} {int(v)}" for k, v in sorted((spec.get("timeouts") or {}).items())]
    lines.append(f"attempts model {int(spec.get('model_attempts') or 1)}")
    return "\n".join(lines) + "\n"


_SECRET_RX = re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|_PAT)[A-Z0-9_]*=)\S+")
_KEY_RX = re.compile(r"\b(sk-|ghp_|gho_|glpat-)[A-Za-z0-9_\-]{6,}")
_REASON_RX = re.compile(r"not found|No module named|No such file|Permission denied|Error:", re.I)


def trial_error(trial: Path) -> str:
    """Why a Harbor trial stopped, in one line with secret-like values masked; '' if it did not."""
    result = _load_json(trial / "result.json") or {}
    info = result.get("exception_info") if isinstance(result.get("exception_info"), dict) else {}
    kind = str(info.get("exception_type") or "")
    if not kind:
        return ""
    message = str(info.get("exception_message") or "")
    parts = [kind]
    code = re.search(r"\(exit (\d+)\)", message)
    if code:
        parts.append(f"exit {code.group(1)}")
    reason = next((ln.strip() for ln in message.splitlines()[1:] if _REASON_RX.search(ln)), "")
    if reason:
        parts.append(reason[:200])
    text = ": ".join(parts)
    return _KEY_RX.sub(r"\1***", _SECRET_RX.sub(r"\1***", text))


def load_facts(agent_dir: Path) -> dict:
    facts = {key: _load_json(agent_dir / name) for key, name in FACT_FILES.items()}
    facts["runtime"] = {
        path.stem.removeprefix("canary_runtime_"): _load_json(path)
        for path in sorted(agent_dir.glob("canary_runtime_*.json"))
    }
    facts["trial_error"] = trial_error(agent_dir.parent)
    return facts


def _harness(host: dict | None) -> harness_table.Harness:
    """The harness the canary ran as; iCode for host records older than the field."""
    return harness_table.from_record({"name": host.get("harness")} if isinstance(host, dict) else None)


def _mount(host: dict | None, config: dict) -> str:
    mount = host.get("mount") if isinstance(host, dict) else None
    if isinstance(mount, str) and mount:
        return mount
    return str(config.get("mount") or harness_table.get("icode").mount)


def _check(status: str, detail: str = "") -> dict:
    return {"status": status, "detail": detail}


def _reached(entry: dict) -> bool:
    code = str(entry.get("http_code") or "").strip()
    return bool(code) and code != "000"


def _blocked_detail(entry: dict) -> str:
    code = entry.get("http_code")
    return f"{entry.get('host')} reached (HTTP {code})"


def _unreached_detail(entry: dict) -> str:
    tries = entry.get("attempts")
    tries = f" after {tries} attempts" if isinstance(tries, int) and tries > 1 else ""
    return f"{entry.get('host')} unreachable{tries} (curl exit {entry.get('curl_exit')}: {entry.get('error') or '-'})"


def check_network(probe: dict, allow_host: str | None = None) -> tuple[dict, dict, dict]:
    """allow_host: CANARY_ALLOW_HOST. The run must fail either way; the detail says whether it proved anything."""
    if probe.get("curl") is not True:
        missing = _check("fail", "curl is missing in the container, so egress was not tested")
        return missing, missing, _check("skip", "curl missing")
    entries = [e for e in probe.get("network") or [] if isinstance(e, dict)]
    by_kind: dict[str, list[dict]] = {}
    for entry in entries:
        by_kind.setdefault(str(entry.get("kind") or ""), []).append(entry)
    sources = by_kind.get("source", [])
    leaked = [e for e in sources if _reached(e)]
    opened = next((e for e in sources if allow_host and str(e.get("host")) == allow_host), None)
    if not sources:
        network = _check("fail", "no source host was probed")
    elif leaked:
        network = _check("fail", "; ".join(_blocked_detail(e) for e in leaked))
    elif allow_host:
        why = f" ({_unreached_detail(opened)})" if opened else " (not probed)"
        network = _check(
            "fail",
            f"negative test inconclusive: CANARY_ALLOW_HOST={allow_host} was opened but did not answer{why}; "
            "use a host this worker can reach",
        )
    else:
        network = _check("pass", f"{len(sources)} source hosts blocked")
    models = by_kind.get("model", [])
    down = [e for e in models if not _reached(e)]
    if not models:
        model = _check("fail", "no model host was probed")
    elif down:
        model = _check(
            "fail",
            "; ".join(_unreached_detail(e) for e in down)
            + ". iCode would not reach the model either; check HTTPS from a plain container first"
            " (a VPN tunnel with a smaller MTU than Docker's bridge stalls every TLS handshake)",
        )
    else:
        model = _check("pass", ", ".join(f"{e.get('host')} HTTP {e.get('http_code')}" for e in models))
    declared = by_kind.get("declared", [])
    open_declared = [e for e in declared if _reached(e)]
    if not declared:
        extra = _check("pass", "task allowlist declares no other hosts")
    elif open_declared:
        extra = _check(
            "warn",
            "task allowlist lets the agent reach " + ", ".join(str(e.get("host")) for e in open_declared),
        )
    else:
        extra = _check("pass", f"{len(declared)} declared hosts blocked")
    return network, model, extra


def check_filesystem(probe: dict, config: dict) -> dict:
    fs = probe.get("filesystem") if isinstance(probe.get("filesystem"), dict) else None
    if fs is None:
        return _check("fail", "no filesystem search recorded")
    if fs.get("timed_out") is True:
        return _check("fail", "filesystem search timed out")
    ignore = list(config.get("filesystem_ignore") or [])
    hits = [str(h) for h in fs.get("hits") or [] if not any(fnmatch.fnmatchcase(str(h), g) for g in ignore)]
    if hits:
        more = " (list capped)" if len(fs.get("hits") or []) >= int(fs.get("max_hits") or 0) > 0 else ""
        return _check("fail", "gold-like files outside the repo: " + ", ".join(hits[:10]) + more)
    return _check("pass", f"{fs.get('patterns', 0)} patterns, nothing found outside the repo")


def check_mount(probe: dict, root: dict | None) -> dict:
    mount = probe.get("mount") if isinstance(probe.get("mount"), dict) else None
    if mount is None or mount.get("present") is not True:
        return _check("fail", "the iCode mount is missing")
    problems = []
    if mount.get("read_only") is not True:
        problems.append(f"mount options {mount.get('options') or 'unknown'} are not ro")
    if mount.get("touch_exit") == 0:
        problems.append("the agent user wrote into the mount")
    if root is None:
        problems.append("no root write test recorded")
    elif root.get("touch_exit") == 0:
        problems.append("root wrote into the mount")
    elif root.get("read_only_error") is not True:
        problems.append("root write failed without a read-only error")
    if problems:
        return _check("fail", "; ".join(problems))
    return _check("pass", f"{mount.get('path')} is read-only")


def check_history(probe: dict) -> dict:
    repo = probe.get("repo") if isinstance(probe.get("repo"), dict) else None
    if repo is None:
        return _check("fail", "no repo history recorded")
    if repo.get("error"):
        return _check("fail", str(repo.get("error")))
    problems = []
    all_count, head_count = repo.get("all_count"), repo.get("head_count")
    if not isinstance(all_count, int) or not isinstance(head_count, int):
        problems.append("commit counts unavailable")
    elif all_count != head_count:
        problems.append(f"{all_count - head_count} commits reachable from refs but not from HEAD")
    if repo.get("fsck_timed_out") is True:
        problems.append("git fsck timed out")
    if repo.get("unreachable"):
        problems.append(f"{repo.get('unreachable')} unreachable objects")
    if repo.get("remotes"):
        problems.append("remotes " + ", ".join(str(r) for r in repo.get("remotes")))
    if repo.get("stash"):
        problems.append(f"{repo.get('stash')} stash entries")
    if problems:
        return _check("fail", f"{repo.get('path')}: " + "; ".join(problems))
    return _check("pass", f"{repo.get('path')}: {head_count} commits, all reachable from HEAD")


def secret_like(name: str, rules: dict) -> bool:
    upper = name.upper()
    if any(part in upper for part in rules.get("contains") or []):
        return True
    return any(upper.endswith(suffix) for suffix in rules.get("suffix") or [])


def check_env(probe: dict, config: dict, host: dict | None = None) -> dict:
    names = probe.get("env_names")
    if not isinstance(names, list):
        return _check("fail", "no environment names recorded")
    rules = config.get("secret_names") or {}
    allowed = set(rules.get("allowed") or []) | set(_harness(host).key_names)
    forbidden = set(rules.get("forbidden") or [])
    bad = sorted({str(n) for n in names if str(n) in forbidden or (secret_like(str(n), rules) and str(n) not in allowed)})
    if bad:
        return _check("fail", "secret-like variables in the agent environment: " + ", ".join(bad))
    return _check("pass", f"{len(names)} names, only the model key is secret-like")


def check_python_env(probe: dict, mount: str) -> dict:
    env = probe.get("python_env") if isinstance(probe.get("python_env"), dict) else None
    if env is None:
        return _check("fail", "no PYTHONPATH / VIRTUAL_ENV recorded")
    bad = [key for key in ("PYTHONPATH", "VIRTUAL_ENV") if mount in str(env.get(key) or "")]
    if bad:
        return _check("fail", " and ".join(bad) + f" point into {mount}")
    return _check("pass", f"neither PYTHONPATH nor VIRTUAL_ENV points into {mount}")


def check_runtime(runtime: dict | None, host: dict | None) -> dict:
    """The harness's own commands, run once in the task image (canary_probe.sh runtime)."""
    expected = host.get("runtime_probes") if isinstance(host, dict) else None
    if not isinstance(expected, list) or not expected:
        return _check("skip", f"{_harness(host).display}'s install starts it before the probe")
    runtime = runtime if isinstance(runtime, dict) else {}
    problems = []
    for name in expected:
        fact = runtime.get(str(name))
        if not isinstance(fact, dict):
            problems.append(f"{name}: not recorded")
        elif fact.get("exit") != 0:
            problems.append(f"{name}: exit {fact.get('exit')} ({fact.get('error') or '-'})")
    if problems:
        return _check("fail", "; ".join(problems))
    return _check("pass", ", ".join(str(n) for n in expected) + " ran in the task image")


def check_icode_home(pre: dict | None, post: dict | None, host: dict | None) -> dict:
    if _harness(host).name != "icode":
        servers = host.get("task_mcp_servers") if isinstance(host, dict) else None
        if isinstance(servers, int) and servers > 0:
            return _check("fail", f"the task declares {servers} MCP servers")
        return _check("skip", f"iCode-only (~/.icode, research subagent); {_harness(host).display} declares no task MCP servers")
    if pre is None or post is None:
        return _check("fail", "iCode home state not recorded before and after install")
    problems = []
    if pre.get("mcp_json") or post.get("mcp_json"):
        problems.append(f"{post.get('icode_home') or pre.get('icode_home')}/mcp.json exists")
    if pre.get("settings_json"):
        problems.append("the image ships an iCode settings.json")
    if pre.get("agents_home_exists"):
        problems.append(f"the image ships {pre.get('agents_home')}")
    if pre.get("research_subagent") or post.get("research_subagent"):
        problems.append("OPENJIUWEN_CLI_RESEARCH_SUBAGENT is on")
    servers = host.get("task_mcp_servers") if isinstance(host, dict) else None
    if isinstance(servers, int) and servers > 0:
        problems.append(f"the task declares {servers} MCP servers")
    if problems:
        return _check("fail", "; ".join(problems))
    return _check("pass", f"no MCP config; {pre.get('icode_home')} is fresh")


def check_tool_list(host: dict | None) -> dict:
    tools = host.get("tool_list") if isinstance(host, dict) and isinstance(host.get("tool_list"), dict) else {}
    status = str(tools.get("status") or "")
    if status == "ok":
        enabled = [str(t) for t in tools.get("web_tools") or []]
        if enabled:
            return _check("fail", "web tools enabled: " + ", ".join(enabled))
        return _check("pass", "no web search or fetch tool enabled")
    return _check("skip", str(tools.get("reason") or "iCode tool list unavailable; the P0.5 transcript scan covers web tools"))


def verdict(facts: dict, config: dict | None = None) -> dict:
    config = config or load_config()
    probe = facts.get("probe")
    if not isinstance(probe, dict):
        why = "canary.json missing or unreadable"
        if facts.get("trial_error"):
            why += f"; the trial stopped before the probe: {facts['trial_error']}"
        checks = {name: _check("fail", why) for name in CHECKS}
        checks["tool_list"] = check_tool_list(facts.get("host"))
        return {"status": "fail", "checks": checks}
    host = facts.get("host") if isinstance(facts.get("host"), dict) else {}
    network, model, declared = check_network(probe, str(host.get("allow_host") or "") or None)
    checks = {
        "network": network,
        "model_api": model,
        "declared_hosts": declared,
        "filesystem": check_filesystem(probe, config),
        "mount": check_mount(probe, facts.get("root")),
        "history": check_history(probe),
        "env_names": check_env(probe, config, host),
        "python_env": check_python_env(probe, _mount(host, config)),
        "runtime": check_runtime(facts.get("runtime"), host),
        "icode_home": check_icode_home(facts.get("home_pre"), facts.get("home_post"), facts.get("host")),
        "tool_list": check_tool_list(facts.get("host")),
    }
    status = "fail" if any(c["status"] == "fail" for c in checks.values()) else "pass"
    return {"status": status, "checks": checks}


def canary_trials(task_dir: Path) -> list[Path]:
    """Harbor trial dirs under <jobs>/<task>: <job>/<trial>/agent, oldest first."""
    found = [p.parent for p in task_dir.glob("*/*/agent") if p.is_dir()]
    return sorted(found, key=lambda p: p.stat().st_mtime)


def probe_started(task_dir: Path) -> tuple[bool, str]:
    """Whether the newest canary trial under <jobs>/<task> ran the probe; if not, why.

    A trial that stopped before the probe (image pull, compose) proves nothing
    about isolation either way, so evaluate/canary moves on to the next question.
    """
    trials = canary_trials(task_dir)
    if not trials:
        return False, "no canary trial"
    facts = load_facts(trials[-1] / "agent")
    if isinstance(facts.get("probe"), dict):
        return True, ""
    return False, facts.get("trial_error") or "canary.json missing or unreadable"


def read_fallback(path: Path | None) -> list[dict]:
    """evaluate/canary's `<task>\\t<reason>` lines for questions whose trial never started."""
    if path is None or not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        tid, _, why = line.partition("\t")
        if tid.strip():
            out.append({"task": tid.strip(), "reason": why.strip() or "trial did not start"})
    return out


def summarize(
    jobs_dir: Path, config: dict | None = None, tasks: list[str] | None = None, fallback: list[dict] | None = None
) -> dict:
    config = config or load_config()
    ids = tasks if tasks else sorted(p.name for p in jobs_dir.iterdir() if p.is_dir()) if jobs_dir.is_dir() else []
    results: dict[str, dict] = {}
    for tid in ids:
        trials = canary_trials(jobs_dir / tid)
        if not trials:
            checks = {name: _check("fail", "no canary trial") for name in CHECKS}
            results[tid] = {"status": "fail", "trial": None, "checks": checks}
            continue
        trial = trials[-1]
        result = verdict(load_facts(trial / "agent"), config)
        results[tid] = {"status": result["status"], "trial": str(trial), "checks": result["checks"]}
    counts = Counter(r["status"] for r in results.values())
    warned = sorted(t for t, r in results.items() if any(c["status"] == "warn" for c in r["checks"].values()))
    summary = {
        "schema": SCHEMA,
        "config_version": config.get("version", ""),
        "node": socket.gethostname(),
        "jobs_dir": str(jobs_dir),
        "status": "pass" if ids and not counts["fail"] else "fail",
        "counts": {"tasks": len(ids), "pass": counts["pass"], "fail": counts["fail"]},
        "failed_tasks": sorted(t for t, r in results.items() if r["status"] == "fail"),
        "warn_tasks": warned,
        "tasks": results,
    }
    skipped = [item for item in fallback or [] if item.get("task") not in results]
    if skipped:
        summary["fallback_from"] = skipped
    return summary


def report_markdown(summary: dict) -> str:
    counts = summary.get("counts") or {}
    lines = [
        "# Isolation canary",
        "",
        f"- Rules: `{summary.get('config_version')}` · node `{summary.get('node')}`",
        f"- Status: **{summary.get('status')}** · tasks {counts.get('tasks', 0)} · "
        f"pass {counts.get('pass', 0)} · fail {counts.get('fail', 0)}",
        "",
        "| Task | Status | " + " | ".join(CHECKS) + " |",
        "| --- | --- | " + " | ".join("---" for _ in CHECKS) + " |",
    ]
    for tid, result in sorted((summary.get("tasks") or {}).items()):
        cells = [result["checks"].get(name, {}).get("status", "-") for name in CHECKS]
        lines.append(f"| {tid} | {result['status']} | " + " | ".join(cells) + " |")
    notes = []
    for tid, result in sorted((summary.get("tasks") or {}).items()):
        for name in CHECKS:
            check = result["checks"].get(name) or {}
            if check.get("status") in ("fail", "warn"):
                notes.append(f"- `{tid}` {name} **{check['status']}**: {check.get('detail') or '-'}")
    if notes:
        lines += ["", "## Failures and warnings", "", *notes]
    fallback = summary.get("fallback_from") or []
    if fallback:
        lines += ["", "## Questions whose canary trial did not start", ""]
        lines += [f"- `{item.get('task')}`: {item.get('reason') or '-'}" for item in fallback]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolation canary spec and verdicts")
    sub = parser.add_subparsers(dest="cmd", required=True)
    spec = sub.add_parser("spec")
    spec.add_argument("--task-dir", required=True)
    spec.add_argument("--benchmark", default="deepswe")
    spec.add_argument("--out", required=True)
    spec.add_argument("--allow-host", default="", help="CANARY_ALLOW_HOST (test only)")
    spec.add_argument("--config", default="")
    summ = sub.add_parser("summarize")
    summ.add_argument("--jobs-dir", required=True)
    summ.add_argument("--out-dir", required=True)
    summ.add_argument("--task", action="append", default=[], help="expected task id (default: every dir)")
    summ.add_argument("--fallback-file", default="", help="<task>\\t<reason> per question whose trial never started")
    summ.add_argument("--config", default="")
    started = sub.add_parser("started", help="exit 0 when the task's canary trial ran the probe, 3 when not")
    started.add_argument("--jobs-dir", required=True)
    started.add_argument("--task", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "started":
        ok, why = probe_started(Path(args.jobs_dir) / args.task)
        if not ok:
            print(why)
        return 0 if ok else 3
    try:
        config = load_config(Path(args.config) if args.config else None)
        if args.cmd == "spec":
            task_dir = Path(args.task_dir)
            if not task_dir.is_dir():
                raise FileNotFoundError(f"missing task dir {task_dir}")
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            doc = build_spec(task_dir, args.benchmark, config, args.allow_host)
            out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            return 0
        fallback = read_fallback(Path(args.fallback_file) if args.fallback_file else None)
        summary = summarize(Path(args.jobs_dir), config, args.task or None, fallback)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        (out_dir / "report.md").write_text(report_markdown(summary), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"ERROR: canary: {exc}", file=sys.stderr)
        return 1
    for tid, result in sorted(summary["tasks"].items()):
        print(f"canary: {tid} {result['status']}")
        noted = [(n, c) for n, c in ((n, result["checks"][n]) for n in CHECKS) if c["status"] in ("fail", "warn")]
        if len(noted) > 1 and len({(c["status"], c["detail"]) for _, c in noted}) == 1:
            noted = [("all", noted[0][1])]
        for name, check in noted:
            print(f"CANARY {check['status'].upper()} task={tid} check={name}: {check['detail']}")
    for item in summary.get("fallback_from") or []:
        print(f"canary: {item['task']} did not start ({item['reason']}); the next question took its place")
    counts = summary["counts"]
    print(
        f"canary {summary['status']}: tasks={counts['tasks']} pass={counts['pass']} fail={counts['fail']} "
        f"report={Path(args.out_dir) / 'report.md'}"
    )
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
