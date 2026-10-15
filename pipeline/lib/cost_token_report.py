#!/usr/bin/env python3
"""Write cost-token-report.md for a completed eval run.

The archive phase (stages/archive/analysis.sh) writes it into every run folder
before the backup. Run it by hand for an older run folder or tarball.
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# USD per 1M tokens — DeepSeek-V4.1-Flash list price.
# https://api-docs.deepseek.com/quick_start/pricing
FLASH_PRICES = {
    "label": "DeepSeek-V4.1-Flash",
    "off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60},
    "peak": {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
}


def catalog_id(model: str | None) -> str:
    text = str(model or "").strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lower()


def prices_for_model(model: str | None) -> tuple[dict, str]:
    cid = catalog_id(model)
    if cid in ("deepseek-flash",) or "flash" in cid:
        return FLASH_PRICES, ""
    note = (
        f"Model `{model}` is not deepseek-flash; using DeepSeek-V4.1-Flash list rates "
        "for the estimate. Check [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) "
        "if this catalog has different rates."
    )
    return FLASH_PRICES, note


HIT_SHARES = (0.0, 0.25, 0.50, 0.75, 1.0)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def span_label(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "-"
    seconds = int(round(float(value)))
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        hours, minutes = divmod(seconds, 3600)
        minutes //= 60
        if minutes == 0:
            return f"{hours}h"
        return f"{hours}h {minutes}m"
    days, hours = divmod(seconds, 86400)
    hours //= 3600
    if hours == 0:
        return f"{days}d"
    return f"{days}d {hours}h"


def fmt_millions(n: float | int) -> str:
    return f"{float(n) / 1_000_000:.2f}M"


def fmt_count(n: float | int) -> str:
    return f"{int(round(float(n))):,}"


def blended_input_rate(prices: dict, hit_share: float) -> float:
    hit = float(prices["cache_hit"])
    miss = float(prices["cache_miss"])
    share = min(1.0, max(0.0, float(hit_share)))
    return share * hit + (1.0 - share) * miss


def estimate_cost(
    *,
    input_tokens: float,
    output_tokens: float,
    band: dict,
    hit_share: float,
) -> dict:
    in_m = float(input_tokens) / 1_000_000.0
    out_m = float(output_tokens) / 1_000_000.0
    in_rate = blended_input_rate(band, hit_share)
    out_rate = float(band["output"])
    in_cost = in_m * in_rate
    out_cost = out_m * out_rate
    return {
        "input": in_cost,
        "output": out_cost,
        "total": in_cost + out_cost,
        "input_rate": in_rate,
        "output_rate": out_rate,
    }


def money(value: float) -> str:
    return f"${value:,.2f}"


def money_tilde(value: float) -> str:
    if value >= 100:
        return f"~${value:,.0f}"
    return f"~${value:,.2f}"


def rate_money(value: float) -> str:
    return f"${value:.2f}" if float(value) >= 0.01 or value == 0 else f"${value:.3f}"


def heaviest_tasks(tasks: list, limit: int = 5) -> list[dict]:
    rows: list[dict] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tok_in = task.get("tok_in")
        tok_out = task.get("tok_out")
        if not isinstance(tok_in, (int, float)) or isinstance(tok_in, bool):
            continue
        if not isinstance(tok_out, (int, float)) or isinstance(tok_out, bool):
            tok_out = 0
        rows.append(
            {
                "id": str(task.get("id") or ""),
                "in": float(tok_in),
                "out": float(tok_out),
                "total": float(tok_in) + float(tok_out),
            }
        )
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows[:limit]


def find_artifact_in_tar(tar_path: Path) -> tuple[dict, str]:
    with tarfile.open(tar_path, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.endswith("/artifact.json") or m.name == "artifact.json"]
        if not members:
            raise FileNotFoundError(f"no artifact.json in {tar_path}")
        members.sort(key=lambda m: m.name.count("/"))
        member = members[0]
        raw = tar.extractfile(member)
        if raw is None:
            raise FileNotFoundError(f"cannot read {member.name} from {tar_path}")
        doc = json.loads(raw.read().decode("utf-8"))
        folder = Path(member.name).parent.name if "/" in member.name else tar_path.stem
        return doc, folder


def candidate_roots(repo: Path) -> list[Path]:
    return [repo / "output", repo / "eval-runs" / "output"]


def resolve_run_dir(
    *,
    repo: Path,
    run_dir: str | None,
    suite: str | None,
    build: str | None,
    run: str | None,
    tar: str | None,
) -> tuple[Path | None, Path | None, dict]:
    """Return (run_dir_or_None, tar_or_None, artifact_doc)."""
    if tar:
        tar_path = Path(tar).expanduser()
        if not tar_path.is_file():
            tar_path = (repo / tar).resolve()
        if not tar_path.is_file():
            raise FileNotFoundError(f"tar not found: {tar}")
        doc, _folder = find_artifact_in_tar(tar_path)
        return None, tar_path, doc

    if run_dir:
        path = Path(run_dir).expanduser()
        if not path.is_dir():
            path = (repo / run_dir).resolve()
        art = path / "artifact.json"
        if not art.is_file():
            raise FileNotFoundError(f"missing artifact.json under {path}")
        return path, None, load_json(art)

    if not suite:
        raise ValueError("provide --run-dir, --tar, or --suite with --build/--run")

    suite = suite.strip().lower()
    if run:
        stem = run.strip()
        if stem.endswith(".tar.gz"):
            stem = stem[: -len(".tar.gz")]
        for root in candidate_roots(repo):
            folder = root / suite / stem
            if (folder / "artifact.json").is_file():
                return folder, None, load_json(folder / "artifact.json")
            tar_path = root / suite / f"{stem}.tar.gz"
            if tar_path.is_file():
                doc, _ = find_artifact_in_tar(tar_path)
                return None, tar_path, doc
        raise FileNotFoundError(f"no run {stem!r} under output/{suite} or eval-runs/output/{suite}")

    if build is None or str(build).strip() == "":
        raise ValueError("--suite requires --build or --run (or use --run-dir / --tar)")

    build_s = str(build).strip()
    if build_s.isdigit():
        pattern = f"jenkins-{int(build_s)}-*"
    else:
        pattern = build_s if "*" in build_s else f"*{build_s}*"

    matches: list[Path] = []
    tar_matches: list[Path] = []
    for root in candidate_roots(repo):
        base = root / suite
        if not base.is_dir():
            continue
        matches.extend(sorted(base.glob(pattern)))
        tar_matches.extend(sorted(base.glob(f"{pattern}.tar.gz")))

    dirs = [p for p in matches if p.is_dir() and (p / "artifact.json").is_file()]
    if len(dirs) == 1:
        return dirs[0], None, load_json(dirs[0] / "artifact.json")
    if len(dirs) > 1:
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        # Prefer newest; still OK when multiple UTC folders for same build.
        chosen = dirs[0]
        return chosen, None, load_json(chosen / "artifact.json")

    tars = [p for p in tar_matches if p.is_file()]
    if len(tars) == 1:
        doc, _ = find_artifact_in_tar(tars[0])
        return None, tars[0], doc
    if len(tars) > 1:
        tars.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        doc, _ = find_artifact_in_tar(tars[0])
        return None, tars[0], doc

    raise FileNotFoundError(
        f"no run matching suite={suite!r} build/pattern={build_s!r} under "
        f"output/{suite} or eval-runs/output/{suite}"
    )


def render_report(doc: dict, *, folder_label: str) -> str:
    arm = doc.get("icode") if isinstance(doc.get("icode"), dict) else {}
    tokens = arm.get("tokens") if isinstance(arm.get("tokens"), dict) else {}
    timing = arm.get("timing") if isinstance(arm.get("timing"), dict) else {}
    tasks = arm.get("tasks") if isinstance(arm.get("tasks"), list) else []

    tok_in = float(tokens.get("in") or 0)
    tok_out = float(tokens.get("out") or 0)
    tok_total = float(tokens.get("total") or (tok_in + tok_out))
    avg = tokens.get("avg_total_per_task")
    if not isinstance(avg, (int, float)) or isinstance(avg, bool):
        n_data = tokens.get("tasks_with_data") or doc.get("n_tasks") or 0
        avg = (tok_total / n_data) if isinstance(n_data, int) and n_data > 0 else 0
    tasks_with = tokens.get("tasks_with_data")
    n_tasks = doc.get("n_tasks")
    n_rollouts = int(doc.get("n_rollouts") or 1)
    scored = n_tasks * n_rollouts if isinstance(n_tasks, int) and not isinstance(n_tasks, bool) else "-"

    model = doc.get("model")
    price_table, price_note = prices_for_model(str(model) if model else "")
    label = price_table["label"]
    cid = catalog_id(str(model) if model else "") or "deepseek-flash"

    wall_s = timing.get("wall_seconds")
    wall_txt = span_label(wall_s)
    if wall_txt != "-":
        wall_txt = f"~{wall_txt} (complete)" if isinstance(wall_s, (int, float)) else wall_txt

    run_label = doc.get("run_label") or doc.get("run_id") or folder_label
    suite = doc.get("suite") or "-"

    heavy = heaviest_tasks(tasks, 5)
    off = price_table["off_peak"]
    peak = price_table["peak"]

    lines: list[str] = []
    lines.append(f"# Cost and token usage — {run_label}")
    lines.append("")
    lines.append("On-demand estimate from `artifact.json`. Not produced by the eval pipeline.")
    lines.append("")
    lines.append("## Run")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Run | `{run_label}` |")
    lines.append(f"| Folder | `{folder_label}` |")
    lines.append(f"| Model (artifact) | `{model}` ({label}) |")
    lines.append(f"| Suite | {n_tasks} tasks × {n_rollouts} rollouts = {scored} scored rollouts |")
    lines.append(f"| Wall | {wall_txt} |")
    lines.append("| Source tokens | `artifact.json` → `icode.tokens` |")
    lines.append("")
    lines.append("## Token usage")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("| --- | --- |")
    lines.append(f"| Input | {fmt_count(tok_in)} ({fmt_millions(tok_in)}) |")
    lines.append(f"| Output | {fmt_count(tok_out)} ({fmt_millions(tok_out)}) |")
    lines.append(f"| Total | {fmt_count(tok_total)} ({fmt_millions(tok_total)}) |")
    lines.append(f"| Avg total / task | {fmt_millions(avg)} |")
    lines.append(f"| Tasks with token data | {tasks_with} / {n_tasks} |")
    lines.append("")
    if tok_total > 0 and tok_in / tok_total >= 0.9:
        lines.append(f"Input is ~{tok_in / tok_total * 100:.0f}% of billed volume.")
        lines.append("")
    if heavy:
        lines.append("Heaviest tasks by total tokens (in + out):")
        lines.append("")
        lines.append("| Task | Input | Output | Total |")
        lines.append("| --- | --- | --- | --- |")
        for row in heavy:
            lines.append(
                f"| {row['id']} | {fmt_millions(row['in'])} | {fmt_millions(row['out'])} | {fmt_millions(row['total'])} |"
            )
        lines.append("")

    lines.append(f"## {label} list price")
    lines.append("")
    lines.append(
        f"Official API id: `{cid}`. Prices in USD per 1M tokens from "
        "[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing) "
        "(built into this script; verify if rates change):"
    )
    if price_note:
        lines.append("")
        lines.append(f"Note: {price_note}")
    lines.append("")
    lines.append("| | Off-peak | Peak (2×) |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| Input cache hit | {rate_money(off['cache_hit'])} | {rate_money(peak['cache_hit'])} |")
    lines.append(f"| Input cache miss | {rate_money(off['cache_miss'])} | {rate_money(peak['cache_miss'])} |")
    lines.append(f"| Output | {rate_money(off['output'])} | {rate_money(peak['output'])} |")
    lines.append("")
    lines.append(
        "Peak is hourly on weekdays only (not a multi-day “season”). In **Hong Kong time (UTC+8)**:"
    )
    lines.append("")
    lines.append("| Peak window (HKT, Mon–Fri) | Same window (UTC) |")
    lines.append("| --- | --- |")
    lines.append("| **09:00–12:00** | 01:00–04:00 |")
    lines.append("| **14:00–18:00** | 06:00–10:00 |")
    lines.append("")
    lines.append(
        "All other HKT hours, plus Saturday/Sunday and Chinese public holidays, are off-peak. "
        "This artifact does **not** record cache hit/miss or peak share, so cost is a range, "
        "not an invoice."
    )
    lines.append("")
    lines.append("## Estimated API cost for this run")
    lines.append("")
    lines.append(
        "`cost = (input_M × blended_input_$/M) + (output_M × output_$/M)`  "
    )
    lines.append(
        "where `blended_input_$/M = hit_share × cache_hit_rate + (1 − hit_share) × cache_miss_rate`."
    )
    lines.append("")
    lines.append("### Upper-bound style (all input billed as cache miss)")
    lines.append("")
    lines.append("| Scenario | Input $ | Output $ | Total | $/task | $/rollout |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    n_task_num = int(n_tasks) if isinstance(n_tasks, int) and not isinstance(n_tasks, bool) and n_tasks else 0
    n_roll_num = n_task_num * n_rollouts if n_task_num else 0
    for name, band in (("Off-peak, 0% cache hit", off), ("Peak, 0% cache hit", peak)):
        est = estimate_cost(input_tokens=tok_in, output_tokens=tok_out, band=band, hit_share=0.0)
        per_task = est["total"] / n_task_num if n_task_num else 0.0
        per_roll = est["total"] / n_roll_num if n_roll_num else 0.0
        lines.append(
            f"| {name} | {money(est['input'])} | {money(est['output'])} | "
            f"**{money_tilde(est['total'])}** | {money_tilde(per_task) if n_task_num else '-'} | "
            f"{money_tilde(per_roll) if n_roll_num else '-'} |"
        )
    lines.append("")
    lines.append("### Assumed input cache-hit share (off-peak vs peak)")
    lines.append("")
    lines.append("| Assumed cache-hit share on input | Off-peak total | Peak total |")
    lines.append("| --- | --- | --- |")
    for share in HIT_SHARES:
        off_est = estimate_cost(input_tokens=tok_in, output_tokens=tok_out, band=off, hit_share=share)
        peak_est = estimate_cost(input_tokens=tok_in, output_tokens=tok_out, band=peak, hit_share=share)
        pct = f"{int(share * 100)}%"
        suffix = " (unrealistic for long agents)" if share >= 1.0 else ""
        lines.append(
            f"| {pct}{suffix} | {money_tilde(off_est['total'])} | {money_tilde(peak_est['total'])} |"
        )
    lines.append("")
    lines.append("## Practical takeaway")
    lines.append("")
    off0 = estimate_cost(input_tokens=tok_in, output_tokens=tok_out, band=off, hit_share=0.0)
    peak0 = estimate_cost(input_tokens=tok_in, output_tokens=tok_out, band=peak, hit_share=0.0)
    lines.append(
        f"Budget about **{money_tilde(off0['total'])}–{money_tilde(peak0['total'])}** for this "
        f"`{suite}` run on {label} if almost all input is cache-miss (off-peak vs peak). "
        "With higher cache-hit share, cost drops quickly (see table above). "
        f"Output alone is about **{money_tilde(off0['output'])}** off-peak / "
        f"**{money_tilde(peak0['output'])}** peak."
    )
    lines.append("")
    lines.append(
        "This is list-price arithmetic from Harbor/iCode token counters, not the DeepSeek "
        "billing dashboard. Retries, tooling outside those counters, or different cache "
        "behavior can change the real charge."
    )
    lines.append("")
    return "\n".join(lines)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def default_out_path(run_dir: Path | None, tar_path: Path | None, folder_label: str, repo: Path) -> Path:
    if run_dir is not None:
        return run_dir / "cost-token-report.md"
    if tar_path is not None:
        # foo/bar/jenkins-37-....tar.gz → foo/bar/jenkins-37-..../cost-token-report.md
        name = tar_path.name
        if name.endswith(".tar.gz"):
            stem = name[: -len(".tar.gz")]
        else:
            stem = tar_path.stem
        stem_dir = tar_path.parent / stem
        stem_dir.mkdir(parents=True, exist_ok=True)
        return stem_dir / "cost-token-report.md"
    return repo / "output" / folder_label / "cost-token-report.md"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", default="", help="Path to run folder containing artifact.json")
    ap.add_argument("--suite", default="", help="deepswe | lolbench | swebenchpro")
    ap.add_argument("--build", default="", help="Jenkins build number (e.g. 37)")
    ap.add_argument("--run", default="", help="Folder stem e.g. jenkins-37-20260927T153132Z")
    ap.add_argument("--tar", default="", help="Path to output/<suite>/<run>.tar.gz")
    ap.add_argument("--out", default="", help="Output markdown path (default: <run-dir>/cost-token-report.md)")
    ap.add_argument("--repo", default=str(REPO_ROOT), help="mac-k3d checkout root")
    args = ap.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    try:
        run_dir, tar_path, doc = resolve_run_dir(
            repo=repo,
            run_dir=args.run_dir or None,
            suite=args.suite or None,
            build=args.build or None,
            run=args.run or None,
            tar=args.tar or None,
        )
    except (FileNotFoundError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if run_dir is not None:
        folder_label = str(run_dir.relative_to(repo)) if _is_relative_to(run_dir, repo) else str(run_dir)
    elif tar_path is not None:
        folder_label = str(tar_path.relative_to(repo)) if _is_relative_to(tar_path, repo) else str(tar_path)
    else:
        folder_label = str(doc.get("run_dir") or doc.get("run_id") or "run")

    text = render_report(doc, folder_label=folder_label)
    out = Path(args.out).expanduser() if args.out else default_out_path(run_dir, tar_path, folder_label, repo)
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
