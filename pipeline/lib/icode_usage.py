"""Parse the agent's token usage, exit code and log: iCode `--json` (icode.txt), or
JiuwenSwarm's headless JSONL (jiuwenswarm.jsonl, jiuwenswarm_usage.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jiuwenswarm_usage

# iCode's is_fatal_provider_error markers, plus the OpenAI SDK's connection and
# timeout messages. Matched as whole words so "401" inside a number does not count.
PROVIDER_ERROR_MARKERS = (
    "insufficient balance",
    "invalid api key",
    "incorrect api key",
    "authentication",
    "401",
    "403 forbidden",
    "billing",
    "quota exceeded",
    "exceeded your current quota",
    "rate limit",
    "connection error",
    "request timed out",
    "apiconnectionerror",
    "apitimeouterror",
)
ERROR_LINES = (
    re.compile(r"caught tool/runtime error; continuing \(\d+/\d+\): (.*)"),
    re.compile(r"fatal provider error with deliverable worktree; finishing turn: (.*)"),
)
ERROR_MESSAGE_CHARS = 300


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_present(usage: dict, keys: tuple[str, ...], extra: Any = None) -> Any:
    """First defined value, including 0. A missing key is not the same as zero."""
    for key in keys:
        if key in usage and usage[key] is not None:
            return usage[key]
    return extra


def usage_from_obj(obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else None
    if usage is None and any(
        k in obj for k in ("input_tokens", "prompt_tokens", "prompt", "token_usage")
    ):
        usage = obj.get("token_usage") if isinstance(obj.get("token_usage"), dict) else obj
    if not isinstance(usage, dict):
        return None
    inp = usage.get("input_tokens")
    if inp is None:
        inp = usage.get("prompt_tokens", usage.get("prompt"))
    out = usage.get("output_tokens")
    if out is None:
        out = usage.get("completion_tokens", usage.get("completion"))
    tot = usage.get("total_tokens", usage.get("total"))
    if inp is None and out is None:
        return None
    try:
        prompt = int(inp or 0)
        completion = int(out or 0)
    except (TypeError, ValueError):
        return None
    try:
        total = int(tot) if tot is not None else prompt + completion
    except (TypeError, ValueError):
        total = prompt + completion
    model = obj.get("model") or obj.get("llm_model_id")
    details = usage.get("prompt_tokens_details")
    nested_cached = details.get("cached_tokens") if isinstance(details, dict) else None
    cache = _first_present(
        usage,
        ("prompt_cache_hit_tokens", "cache_hit_tokens", "cached_tokens", "cache_tokens", "cache"),
        nested_cached,
    )
    try:
        cache_hit = int(cache) if cache is not None else None
    except (TypeError, ValueError):
        cache_hit = None
    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "model": model if isinstance(model, str) else None,
        "cache_hit": cache_hit,
        "model_calls": _int_or_none(usage.get("model_calls")),
        "last_output_tokens": _int_or_none(usage.get("last_output_tokens")),
    }


def parse_icode_usage_text(text: str) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    decoder = json.JSONDecoder()
    i = 0
    n = len(text)
    while i < n:
        start = text.find("{", i)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            i = start + 1
            continue
        got = usage_from_obj(obj)
        if got is not None:
            last = got
        i = end if end > start else start + 1
    return last


def parse_icode_usage_file(path: Path, tail_bytes: int = 262144) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > tail_bytes:
        raw = raw[-tail_bytes:]
    return parse_icode_usage_text(raw.decode("utf-8", errors="replace"))


def _marker_in(message: str) -> str | None:
    """First provider-error marker in an error message, or None.

    An OS error is a tool failing on the filesystem, and its text quotes the
    model's own path or file content, so it never counts.
    """
    text = message.strip()
    if text.startswith("[Errno"):
        return None
    text = text[:ERROR_MESSAGE_CHARS].lower()
    for marker in PROVIDER_ERROR_MARKERS:
        if re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text):
            return marker
    return None


def provider_error_marker(text: str) -> str | None:
    """The provider-error marker in iCode's error lines or its JSON `error`, else None.

    The final `result` is the model's own words, so it is never searched.
    """
    for line in text.splitlines():
        for pattern in ERROR_LINES:
            match = pattern.search(line)
            got = _marker_in(match.group(1)) if match else None
            if got:
                return got
        stripped = line.strip()
        if stripped.startswith("{") and '"error"' in stripped:
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            error = obj.get("error") if isinstance(obj, dict) else None
            if isinstance(error, dict):
                # JiuwenSwarm's result: {"code": ..., "message": ...}.
                error = error.get("message")
            if isinstance(error, str):
                got = _marker_in(error)
                if got:
                    return got
    return None


def icode_log_text(trial: Path, tail_bytes: int = 1048576) -> str:
    """The tail of the agent's log: icode.txt, else jiuwenswarm.jsonl."""
    for name in ("icode.txt", jiuwenswarm_usage.LOG):
        try:
            raw = (trial / "agent" / name).read_bytes()
        except OSError:
            continue
        return raw[-tail_bytes:].decode("utf-8", errors="replace")
    return ""


def icode_exit_code(trial: Path) -> int | None:
    """The agent's exit status (agent/icode-exit.txt or jiuwenswarm-exit.txt), or None when not written."""
    for name in ("icode-exit.txt", jiuwenswarm_usage.EXIT):
        try:
            text = (trial / "agent" / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        return int(text) if text.lstrip("-").isdigit() else None
    return None


def find_icode_usage(root: Path) -> dict[str, Any] | None:
    """Newest icode-usage.json or jiuwenswarm-usage.json, else last usage object in icode.txt/json or jiuwenswarm.jsonl."""
    if not root.is_dir():
        return None
    newest_usage: dict[str, Any] | None = None
    newest_mtime = -1.0
    for path in [*root.rglob("icode-usage.json"), *root.rglob(jiuwenswarm_usage.USAGE)]:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        got = parse_icode_usage_file(path)
        if got is None:
            continue
        if mtime >= newest_mtime:
            newest_mtime = mtime
            newest_usage = got
    if newest_usage is not None:
        return newest_usage
    last: dict[str, Any] | None = None
    last_mtime = -1.0
    for name in ("icode.json", "icode.txt", jiuwenswarm_usage.LOG, "timing.json"):
        for path in root.rglob(name):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            got = parse_icode_usage_file(path)
            if got is None:
                continue
            if mtime >= last_mtime:
                last_mtime = mtime
                last = got
    return last
