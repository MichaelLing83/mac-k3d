#!/usr/bin/env python3
"""The hosts an agent container may reach during a Harbor run.

`pipeline/config/network-allowlist-v1.json` is the only list. Harbor gets one
`--allow-agent-host` per entry; every other host is blocked for the agent.

  hosts                 print one host per line (for --allow-agent-host)
  check --api-base URL  fail when the model API host is not on the list
  record --inputs FILE  write the version and hosts into eval_protocol_inputs.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "network-allowlist-v1.json"


def load(path: Path = DEFAULT_CONFIG) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    hosts = doc.get("agent_hosts")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) and h for h in hosts):
        raise ValueError(f"{path}: agent_hosts must be a non-empty list of host names")
    return doc


def hosts(path: Path = DEFAULT_CONFIG) -> list[str]:
    return [h.strip().lower() for h in load(path)["agent_hosts"]]


def api_host(api_base: str) -> str:
    return (urlparse(api_base).hostname or "").lower()


def check(api_base: str, path: Path = DEFAULT_CONFIG) -> str | None:
    """Problem text, or None when the model API host is allowed."""
    host = api_host(api_base)
    if not host:
        return f"cannot read a host from api base {api_base!r}"
    allowed = hosts(path)
    if host not in allowed:
        return (
            f"model API host {host} is not on the agent allowlist ({', '.join(allowed)}); "
            f"the agent could not reach it. Add it to {path.name} or change ICODE_API_BASE"
        )
    return None


def record(inputs: Path, path: Path = DEFAULT_CONFIG) -> dict:
    doc = json.loads(inputs.read_text(encoding="utf-8")) if inputs.is_file() else {}
    isolation = doc.get("isolation") if isinstance(doc.get("isolation"), dict) else {}
    isolation["network_allowlist"] = {
        "version": str(load(path).get("version") or ""),
        "agent_hosts": hosts(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    doc["isolation"] = isolation
    inputs.parent.mkdir(parents=True, exist_ok=True)
    inputs.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hosts")
    chk = sub.add_parser("check")
    chk.add_argument("--api-base", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--inputs", required=True)
    args = parser.parse_args(argv)
    path = Path(args.config)

    if args.cmd == "hosts":
        print("\n".join(hosts(path)))
        return 0
    if args.cmd == "check":
        problem = check(args.api_base, path)
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        print(f"network allowlist: {api_host(args.api_base)} allowed ({', '.join(hosts(path))})")
        return 0
    record(Path(args.inputs), path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
