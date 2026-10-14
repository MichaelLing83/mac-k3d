"""JiuwenSwarm's headless result: the `type: result` line ending jiuwenswarm.jsonl.

  {"type": "result", "status": "completed|failed|cancelled|timed_out",
   "exit_code": N, "output": "...", "error": {"code": ..., "message": ...},
   "usage": {"input_tokens": N, "output_tokens": N, "total_tokens": N,
             "cache_tokens": N, "model_calls": N, ...}}

icode_usage.py reads the usage, exit code and log through the same helpers it
uses for iCode, so the report, score and run steps need no harness switch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LOG = "jiuwenswarm.jsonl"
EXIT = "jiuwenswarm-exit.txt"
# The result line without its answer text (jiuwenswarm_trial.py usage).
USAGE = "jiuwenswarm-usage.json"
TAIL_BYTES = 4 * 1024 * 1024


def read_result(path: Path, tail_bytes: int = TAIL_BYTES) -> dict[str, Any] | None:
    """The last `type: result` object in a jiuwenswarm.jsonl, or None."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            raw = fh.read()
    except OSError:
        return None
    for line in reversed(raw.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line.startswith("{") or '"result"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def result_error(result: dict[str, Any] | None) -> str:
    """`CODE: message` of a result's error, '' when it has none."""
    error = result.get("error") if isinstance(result, dict) else None
    if not isinstance(error, dict):
        return ""
    code = str(error.get("code") or "").strip()
    message = str(error.get("message") or "").strip()
    return ": ".join(part for part in (code, message) if part)
