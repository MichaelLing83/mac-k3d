#!/usr/bin/env python3
"""evaluate/harbor_run: work-unit progress.json and the 60s heartbeat line."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ETA_FORMULA = "ETA ≈ remaining × mean_rollout / slots"


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def format_span(seconds) -> str:
    number = _num(seconds)
    if number is None:
        return ""
    total = int(round(number))
    if total < 0:
        total = 0
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        hours, minutes = divmod(total, 3600)
        minutes //= 60
        if minutes == 0:
            return f"{hours}h"
        return f"{hours}h {minutes}m"
    days, hours = divmod(total, 86400)
    hours //= 3600
    if hours == 0:
        return f"{days}d"
    return f"{days}d {hours}h"


def elapsed_since(started_at) -> int | None:
    if not isinstance(started_at, str) or not started_at.strip():
        return None
    text = started_at.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        start = datetime.fromisoformat(text)
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return max(0, int(round((datetime.now(timezone.utc) - start).total_seconds())))


def progress_doc(
    *,
    done: int,
    needed: int,
    inflight: int,
    slots: int,
    mean_rollout_s: float | None,
    started_at: str,
) -> dict:
    done = max(0, int(done))
    needed = max(0, int(needed))
    inflight = max(0, int(inflight))
    slots = max(1, int(slots))
    remaining = max(0, needed - done)
    mean = _num(mean_rollout_s)
    if mean is not None and mean <= 0:
        mean = None
    eta_s = None
    eta_note = None
    if mean is not None and remaining > 0:
        eta_s = remaining * mean / slots
        eta_note = ETA_FORMULA
    return {
        "done": done,
        "needed": needed,
        "inflight": inflight,
        "slots": slots,
        "mean_rollout_s": mean,
        "eta_s": eta_s,
        "started_at": started_at,
        **({"eta_note": eta_note} if eta_note else {}),
    }


def write_progress(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def p5_progress_pct(done: int, needed: int) -> int:
    """Map rollout completion onto the evaluate band (55–70)."""
    if needed <= 0:
        return 55
    frac = min(1.0, max(0.0, done / needed))
    return 55 + int(round(15 * frac))


def heartbeat_text(doc: dict, elapsed_s: int | None = None) -> str:
    done = int(doc.get("done") or 0)
    needed = int(doc.get("needed") or 0)
    inflight = int(doc.get("inflight") or 0)
    slots = int(doc.get("slots") or 1)
    if elapsed_s is None:
        elapsed_s = elapsed_since(doc.get("started_at"))
    if elapsed_s is None:
        elapsed_s = 0
    frac = (done / needed * 100.0) if needed else 0.0
    line = (
        f"harbor heartbeat {elapsed_s}s {done}/{needed} ({frac:.1f}%) "
        f"inflight={inflight} slots={slots}"
    )
    pct = p5_progress_pct(done, needed)
    progress = f"PROGRESS {pct}% evaluate {done}/{needed} rollouts"
    return f"{line}\n{progress}\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    write_p = sub.add_parser("write")
    write_p.add_argument("--out", required=True)
    write_p.add_argument("--done", type=int, required=True)
    write_p.add_argument("--needed", type=int, required=True)
    write_p.add_argument("--inflight", type=int, required=True)
    write_p.add_argument("--slots", type=int, required=True)
    write_p.add_argument("--mean", default="")
    write_p.add_argument("--started-at", required=True)
    beat = sub.add_parser("heartbeat")
    beat.add_argument("--progress", required=True)
    beat.add_argument("--elapsed", type=int, default=-1)
    args = ap.parse_args()
    if args.cmd == "write":
        mean = None
        if str(args.mean).strip():
            mean = float(args.mean)
        doc = progress_doc(
            done=args.done,
            needed=args.needed,
            inflight=args.inflight,
            slots=args.slots,
            mean_rollout_s=mean,
            started_at=args.started_at,
        )
        write_progress(Path(args.out), doc)
        return 0
    path = Path(args.progress)
    if not path.is_file():
        elapsed = args.elapsed if args.elapsed >= 0 else 0
        print(f"harbor heartbeat {elapsed}s")
        return 0
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(doc, dict):
        return 0
    elapsed = args.elapsed if args.elapsed >= 0 else None
    print(heartbeat_text(doc, elapsed), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
