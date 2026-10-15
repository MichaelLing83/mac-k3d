#!/usr/bin/env python3
"""OpenAI-compatible GET /models (DeepSeek uses the same JSON as OpenAI).

Used by env/model_api and `mac-k3d set --check-models` so catalog ids are provider Chat
Completions ids (e.g. deepseek-flash), not product names (deepseek-v4.1-flash).
Does not print API keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_BASE = "https://api.deepseek.com"


def api_base() -> str:
    raw = (
        os.environ.get("ICODE_API_BASE")
        or os.environ.get("DEEPSEEK_API_BASE")
        or DEFAULT_BASE
    ).strip()
    return raw.rstrip("/")


def models_base(base: str | None = None) -> str:
    """Catalog check stays on GET https://api.deepseek.com/models.

    iCode chat uses https://api.deepseek.com/v1. That suffix is not part of the
    models list URL.
    """
    raw = (base if base is not None else api_base()).rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[: -len("/v1")]
    return raw


def parse_model_ids(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or []
    ids: list[str] = []
    if not isinstance(data, list):
        return []
    for item in data:
        if isinstance(item, dict):
            mid = item.get("id")
            if isinstance(mid, str) and mid.strip():
                ids.append(mid.strip())
    return ids


def fetch_model_ids(api_key: str, base: str | None = None) -> list[str]:
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    url = f"{models_base(base)}/models"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            body = ""
        raise RuntimeError(f"GET /models HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GET /models failed: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GET /models returned non-JSON: {e}") from e
    ids = parse_model_ids(payload)
    if not ids:
        raise RuntimeError("GET /models returned no data[].id")
    return ids


def model_in_ids(model: str, ids: list[str]) -> bool:
    want = (model or "").strip()
    if not want:
        return False
    if want in ids:
        return True
    return want.lower() in {i.lower() for i in ids}


def missing_model_message(model: str, ids: list[str]) -> str:
    shown = ", ".join(ids)
    return f"model '{model}' is not returned by GET /models (allowed from API: {shown})"


def require_model_on_api(model: str, api_key: str, base: str | None = None) -> list[str]:
    want = (model or "").strip()
    if not want:
        raise RuntimeError("model id is empty")
    ids = fetch_model_ids(api_key, base)
    if not model_in_ids(want, ids):
        raise RuntimeError(missing_model_message(want, ids))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenAI-compatible GET /models")
    ap.add_argument("--list", action="store_true", help="print provider model ids")
    ap.add_argument("--check-model", default="", help="fail if this id is not on GET /models")
    args = ap.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("MAC_K3D_DEEPSEEK_API_KEY") or ""
    try:
        if args.check_model:
            ids = require_model_on_api(args.check_model, key)
            print(f"OK GET /models includes {args.check_model.strip()} ({len(ids)} ids)")
            return 0
        if args.list:
            for mid in fetch_model_ids(key):
                print(mid)
            return 0
        ap.print_help()
        return 2
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
