#!/usr/bin/env python3
"""Baseline arm: send DeepSWE instruction.md to DeepSeek chat API (no iCode)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def list_tasks(tasks_dir: Path, n: int, names: list[str] | None = None) -> list[Path]:
    if names:
        out: list[Path] = []
        for name in names:
            p = tasks_dir / name
            if p.is_dir():
                out.append(p)
        return out
    dirs = sorted([p for p in tasks_dir.iterdir() if p.is_dir()])
    return dirs[:n]


def read_instruction(task_dir: Path) -> str:
    for name in (
        "instruction.md",
        "Instruction.md",
        "instruction.txt",
        "prompt.md",
        "problem.md",
        "task.md",
    ):
        p = task_dir / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    nested = next(task_dir.rglob("instruction.md"), None)
    if nested is not None and nested.is_file():
        return nested.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no instruction.md (or Harbor prompt file) under {task_dir}")


def usage_from_body(data: dict) -> dict:
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    return {"prompt": prompt, "completion": completion, "total": total}


def chat_deepseek(prompt: str, api_key: str, model: str) -> dict:
    url = "https://api.deepseek.com/chat/completions"
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a coding agent. Solve the software engineering task. "
                    "Reply with a unified diff patch only when possible, otherwise explain briefly."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            header_map = {k.lower(): v for k, v in resp.headers.items()}
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            err_body = ""
        print(f"  API HTTP {e.code}: {err_body}", file=sys.stderr)
        raise urllib.error.HTTPError(
            e.url, e.code, f"{e.reason}: {err_body}", e.headers, None
        )
    elapsed = round(time.perf_counter() - started, 3)
    content = data["choices"][0]["message"]["content"]
    version = (
        header_map.get("x-ds-model-version")
        or header_map.get("x-model-version")
        or data.get("system_fingerprint")
        or None
    )
    return {
        "content": content,
        "model_served": data.get("model"),
        "usage": usage_from_body(data),
        "duration_seconds": elapsed,
        "llm_version": version,
    }


def clear_stale_grades(out_dir: Path, task_out: Path) -> None:
    """Drop grades left by an older runner so this baseline stands alone."""
    for name in ("eval.json", "scale_summary.json", "reward.json"):
        for path in (task_out / name, out_dir / name):
            if path.is_file():
                path.unlink()
    scale = out_dir / "scale_eval"
    if scale.is_dir():
        shutil.rmtree(scale)


def extract_patch(text: str) -> str:
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            chunk = part.strip()
            if chunk.startswith("diff") or chunk.startswith("---") or "\n@@" in chunk:
                if chunk.startswith("diff") or chunk.startswith("---"):
                    return chunk
                lines = chunk.splitlines()
                if lines and not lines[0].startswith("---") and not lines[0].startswith("diff"):
                    return "\n".join(lines[1:])
                return chunk
    if "diff --git" in text or text.lstrip().startswith("--- "):
        return text
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", required=True)
    ap.add_argument("--n-tasks", type=int, default=1)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"))
    ap.add_argument("--task-file", default="", help="newline-separated task dir names")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("DEEPSEEK_API_KEY missing", file=sys.stderr)
        return 1

    tasks_dir = Path(args.tasks_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    names: list[str] | None = None
    if args.task_file:
        raw = Path(args.task_file).read_text(encoding="utf-8")
        names = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    tasks = list_tasks(tasks_dir, args.n_tasks, names)
    n_rollouts = int(os.environ.get("N_ROLLOUTS") or "1")
    if n_rollouts < 1:
        print("N_ROLLOUTS must be an integer >= 1", file=sys.stderr)
        return 1
    summary = []
    wall0 = time.perf_counter()
    served = None
    version = None
    for task in tasks:
        tid = task.name
        print(f"baseline task={tid} model={args.model} rollouts={n_rollouts}")
        prompt = read_instruction(task)
        task_out = out_dir / tid
        task_out.mkdir(parents=True, exist_ok=True)
        clear_stale_grades(out_dir, task_out)
        for old in task_out.glob("attempt-*"):
            if old.is_dir():
                shutil.rmtree(old)
        prompt_tokens = completion_tokens = total_tokens = 0
        duration = 0.0
        wrote = 0
        for i in range(1, n_rollouts + 1):
            dest = task_out / f"attempt-{i:02d}"
            dest.mkdir(parents=True, exist_ok=True)
            try:
                result = chat_deepseek(prompt, api_key, args.model)
                served = result.get("model_served") or served
                version = result.get("llm_version") or version
                patch = extract_patch(result["content"])
                (dest / "response.txt").write_text(result["content"], encoding="utf-8")
                (dest / "agent.patch").write_text(patch, encoding="utf-8")
                (dest / "usage.json").write_text(
                    json.dumps(
                        {
                            "usage": result["usage"],
                            "duration_seconds": result["duration_seconds"],
                            "model_served": result.get("model_served"),
                            "llm_version": result.get("llm_version"),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                usage = result["usage"]
                prompt_tokens += int(usage.get("prompt") or 0)
                completion_tokens += int(usage.get("completion") or 0)
                total_tokens += int(usage.get("total") or 0)
                duration += float(result["duration_seconds"] or 0)
                wrote += 1
                print(f"  wrote {dest / 'agent.patch'}")
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as e:
                (dest / "agent.patch").write_text("", encoding="utf-8")
                (dest / "notes.txt").write_text(str(e), encoding="utf-8")
                print(f"  ERROR attempt {i}: {e}", file=sys.stderr)
        first = task_out / "attempt-01" / "agent.patch"
        if first.is_file():
            shutil.copyfile(first, task_out / "agent.patch")
        summary.append(
            {
                "id": tid,
                "ok": wrote > 0,
                "patch_path": str(task_out / "agent.patch"),
                "n_rollouts": n_rollouts,
                "token_usage": {
                    "prompt": prompt_tokens,
                    "completion": completion_tokens,
                    "total": total_tokens,
                },
                "duration_seconds": round(duration, 3),
                "llm_model_served": served,
                "llm_version": version,
            }
        )

    wall = round(time.perf_counter() - wall0, 3)
    meta = {
        "access_date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "llm_model_id": args.model,
        "llm_model_served": served,
        "llm_version": version,
        "duration_seconds": wall,
        "token_usage": {
            "prompt": sum(int((x.get("token_usage") or {}).get("prompt") or 0) for x in summary),
            "completion": sum(
                int((x.get("token_usage") or {}).get("completion") or 0) for x in summary
            ),
            "total": sum(int((x.get("token_usage") or {}).get("total") or 0) for x in summary),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return 0 if all(x.get("ok") for x in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
