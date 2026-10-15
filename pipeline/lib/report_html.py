"""Self-contained HTML report for one harness arm."""

from __future__ import annotations

import html

from eval_metrics import mean_ci

CANDIDATE = "icode"
DASH = "—"

_SUITE_TITLES = {
    "deepswe": "DeepSWE",
    "swebenchpro": "SWE-bench Pro",
    "lolbench": "LOLBench",
}


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_rate(value) -> float | None:
    number = _num(value)
    if number is None:
        return None
    if number > 1:
        return number / 100.0
    return number


def harness_arm(doc: dict) -> dict:
    icode = doc.get("icode")
    if isinstance(icode, dict):
        return icode
    if any(key in doc for key in ("tasks", "macro_pass@1", "macro_pass_at_1")):
        return doc
    return {}


def _root(doc: dict, arm: dict, key: str):
    if key in doc and doc.get(key) is not None:
        return doc.get(key)
    if key in arm and arm.get(key) is not None:
        return arm.get(key)
    return None


def _tasks(arm: dict) -> list[dict]:
    rows = arm.get("tasks")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _pass_k(arm: dict, k: int) -> float | None:
    direct = arm.get(f"pass@{k}")
    if direct is not None:
        return _as_rate(direct)
    nested = arm.get("pass_at")
    if isinstance(nested, dict):
        if str(k) in nested:
            return _as_rate(nested[str(k)])
        if k in nested:
            return _as_rate(nested[k])
    return None


def _pass_k_scored(arm: dict, k: int) -> float | None:
    return _as_rate(arm.get(f"pass@{k}_scored"))


def _macro_from_tasks(tasks: list[dict]) -> tuple[float | None, float | None]:
    vals = []
    for task in tasks:
        frac = task.get("pass_frac")
        if isinstance(frac, (int, float)) and not isinstance(frac, bool):
            vals.append(float(frac))
            continue
        c = task.get("c")
        n = task.get("n")
        if isinstance(c, int) and not isinstance(c, bool) and isinstance(n, int) and n > 0:
            vals.append(c / n)
    return mean_ci(vals)


def _scored_ci_from_tasks(tasks: list[dict]) -> float | None:
    vals = [
        float(task["pass_frac_scored"])
        for task in tasks
        if task.get("n_scored") and isinstance(task.get("pass_frac_scored"), (int, float)) and not isinstance(task.get("pass_frac_scored"), bool)
    ]
    _mean, half = mean_ci(vals)
    return half


def _fmt_pct(rate: float | None) -> str:
    if rate is None:
        return DASH
    return f"{rate * 100:.1f}%"


def _fmt_rate3(rate: float | None) -> str:
    if rate is None:
        return DASH
    return f"{rate:.3f}"


def _fmt_token(value) -> str:
    number = _num(value)
    if number is None:
        return DASH
    sign = "-" if number < 0 else ""
    n = abs(number)
    if n >= 1_000_000_000:
        return sign + f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return sign + f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return sign + f"{n / 1_000:.1f}k"
    if n == int(n):
        return sign + str(int(n))
    return sign + f"{n:.1f}"


def _fmt_minutes(seconds) -> str:
    number = _num(seconds)
    if number is None:
        return DASH
    if abs(number) < 60:
        text = f"{number:.0f}" if number == int(number) else f"{number:.1f}"
        return f"{text} s"
    return f"{number / 60:.1f} min"


def _kpi_minutes(seconds) -> str:
    number = _num(seconds)
    if number is None:
        return DASH
    return f"{number / 60:.1f} min"


def _fmt_wall(seconds) -> str:
    number = _num(seconds)
    if number is None:
        return DASH
    hours = number / 3600.0
    if hours < 48:
        return f"~{hours:.1f}h"
    days = int(hours // 24)
    rest = int(round(hours - days * 24))
    return f"~{days}d {rest}h"


def _suite_title(raw) -> str:
    text = "" if raw is None else str(raw)
    return _SUITE_TITLES.get(text.strip().lower(), text or DASH)


def _catalog_id(model) -> str:
    text = "" if model is None else str(model).strip()
    if text.startswith("openai/"):
        text = text[len("openai/") :]
    return text


def _svg_bars(
    chart_title: str,
    labels: list[str],
    values: list[float | None],
    y_max: float,
    tick,
    height: int = 280,
    label_size: int = 12,
    series_name: str = CANDIDATE,
) -> str:
    width = 860
    left, right, top, bottom = 48, 16, 28, 40
    plot_w = width - left - right
    plot_h = height - top - bottom
    ceiling = y_max if y_max > 0 else 1.0
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img">',
        f"<title>{_esc(chart_title)}. {_esc(series_name)}</title>",
    ]
    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = top + plot_h * (1 - frac)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e6e6e4"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="{label_size}" fill="#6b6b6b">{_esc(tick(ceiling * frac))}</text>'
        )
    slot = plot_w / max(len(labels), 1)
    for index, label in enumerate(labels):
        value = values[index] if index < len(values) else None
        cx = left + slot * index + slot / 2
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 10}" text-anchor="middle" font-size="{label_size}" fill="#6b6b6b">{_esc(label)}</text>'
        )
        if value is None:
            continue
        height_px = plot_h * max(0.0, min(value, ceiling)) / ceiling
        y = top + plot_h - height_px
        parts.append(
            f'<rect x="{cx - 14:.1f}" y="{y:.1f}" width="28" height="{max(height_px, 0):.1f}" rx="2" fill="#1f8a4c"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{max(y - 6, 14):.1f}" text-anchor="middle" font-size="{label_size}" fill="#1c1c1c">{_esc(tick(value))}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _svg_grouped_bars(
    chart_title: str,
    labels: list[str],
    series: list[tuple[str, str, list[float | None]]],
    y_max: float,
    tick,
    height: int = 280,
    label_size: int = 12,
    series_name: str = CANDIDATE,
) -> str:
    width = 860
    left, right, top, bottom = 48, 16, 28, 40
    plot_w = width - left - right
    plot_h = height - top - bottom
    ceiling = y_max if y_max > 0 else 1.0
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img">',
        f"<title>{_esc(chart_title)}. {_esc(series_name)}</title>",
    ]
    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = top + plot_h * (1 - frac)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e6e6e4"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="{label_size}" fill="#6b6b6b">{_esc(tick(ceiling * frac))}</text>'
        )
    slot = plot_w / max(len(labels), 1)
    n_series = max(len(series), 1)
    bar_w = min(16.0, (slot * 0.62) / n_series)
    gap = 2.0
    group_w = n_series * bar_w + (n_series - 1) * gap
    for index, label in enumerate(labels):
        cx = left + slot * index + slot / 2
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 10}" text-anchor="middle" font-size="{label_size}" fill="#6b6b6b">{_esc(label)}</text>'
        )
        start = cx - group_w / 2
        for s_i, (_name, color, values) in enumerate(series):
            value = values[index] if index < len(values) else None
            if value is None:
                continue
            x = start + s_i * (bar_w + gap)
            height_px = plot_h * max(0.0, min(value, ceiling)) / ceiling
            y = top + plot_h - height_px
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(height_px, 0):.1f}" rx="2" fill="{_esc(color)}"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _svg_donut(solved: int, unsolved: int) -> str:
    total = solved + unsolved
    if total <= 0:
        return ""
    radius = 42.0
    circ = 2.0 * 3.1415926535 * radius
    solved_len = circ * solved / total
    pct = f"{solved / total * 100:.0f}%"
    return (
        f'<svg class="donut" viewBox="0 0 120 120" role="img" aria-label="Solved {solved}, unsolved {unsolved}">'
        f'<circle cx="60" cy="60" r="{radius:.0f}" fill="none" stroke="#e6a23c" stroke-width="16"/>'
        f'<circle cx="60" cy="60" r="{radius:.0f}" fill="none" stroke="#146336" stroke-width="16" '
        f'stroke-dasharray="{solved_len:.2f} {circ - solved_len:.2f}" transform="rotate(-90 60 60)"/>'
        f'<text x="60" y="64" text-anchor="middle" font-size="16" fill="#146336">{pct}</text>'
        "</svg>"
    )


def _legend(series_name: str = CANDIDATE) -> str:
    return f'<p class="legend"><span class="dot"></span>{_esc(series_name)}</p>'


def _pass_legend() -> str:
    return (
        '<p class="legend">'
        '<span class="dot"></span>Padded (missing=fail) '
        '<span class="dot scored"></span>Scored-only (missing omitted)'
        "</p>"
    )


def _table(headers: list[str], rows: list[list[str]], kind: str = "") -> str:
    klass = f' class="{_esc(kind)}"' if kind else ""
    head = "".join(f"<th>{_esc(cell)}</th>" for cell in headers)
    body = []
    for row in rows:
        row_kind = ""
        if kind == "outcomes" and row:
            label = row[0].strip().lower()
            if label == "solved":
                row_kind = ' class="solved"'
            elif label == "unsolved":
                row_kind = ' class="unsolved"'
        body.append(f"<tr{row_kind}>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>")
    return f"<table{klass}><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _scroll(table_html: str, n_rows: int) -> str:
    if n_rows <= 12:
        return table_html
    return f'<div class="scroll">{table_html}</div>'


def _pct_tick(value: float) -> str:
    return f"{value:.1f}%"


def _count_tick(value: float) -> str:
    return str(int(round(value)))


def _minute_tick(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def _empty_patch_html(arm: dict) -> str:
    """The empty-patch section; empty for an artifact written before rollouts recorded it."""
    from eval_metrics import EMPTY_PATCH_HEADERS, EMPTY_PATCH_INTRO, EMPTY_PATCH_TITLE
    from render_report import empty_patch_cells

    items = arm.get("empty_patches")
    if not isinstance(items, list):
        return ""
    rows = [empty_patch_cells(item) for item in items if isinstance(item, dict)]
    body = _scroll(_table(EMPTY_PATCH_HEADERS, rows, "unscored"), len(rows)) if rows else "<p>None</p>"
    return f"<h2>{_esc(EMPTY_PATCH_TITLE)}</h2>\n<p class=\"source\">{_esc(EMPTY_PATCH_INTRO)}</p>\n{body}"


def _provenance_html(doc: dict) -> str:
    """The short Provenance list; everything else is in provenance.md next to this page."""
    from provenance import PROVENANCE_FILE, provenance_brief

    protocol = doc.get("eval_protocol") if isinstance(doc.get("eval_protocol"), dict) else None
    brief = provenance_brief(protocol)
    if not brief:
        return ""
    items = [_esc(line) for line in brief[:-1]]
    link = f'<a href="{PROVENANCE_FILE}">{PROVENANCE_FILE}</a>'
    items.append(_esc(brief[-1]).replace(PROVENANCE_FILE, link))
    return "<h2>Provenance</h2><ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def report_html(doc: dict) -> str:
    arm = harness_arm(doc)
    tasks = _tasks(arm)
    suite = _suite_title(_root(doc, arm, "suite"))
    n_tasks = _root(doc, arm, "n_tasks")
    if not isinstance(n_tasks, int) or isinstance(n_tasks, bool):
        n_tasks = len(tasks)
    n_rollouts = _root(doc, arm, "n_rollouts")
    if not isinstance(n_rollouts, int) or isinstance(n_rollouts, bool):
        widths = [task.get("n") for task in tasks if isinstance(task.get("n"), int) and not isinstance(task.get("n"), bool)]
        n_rollouts = max(widths) if widths else 1
    n_rollouts = max(int(n_rollouts), 1)
    concurrency = _root(doc, arm, "concurrency")
    cpus_each = _root(doc, arm, "cpus_each")
    model = _root(doc, arm, "model")
    catalog = _catalog_id(model)
    protocol_harness = (doc.get("eval_protocol") or {}).get("harness") if isinstance(doc.get("eval_protocol"), dict) else None
    name = str(protocol_harness.get("name") or CANDIDATE) if isinstance(protocol_harness, dict) else CANDIDATE
    candidate = f"{name} + {catalog}" if catalog else name
    api_base = _root(doc, arm, "api_base")
    run_id = _root(doc, arm, "run_label") or _root(doc, arm, "run_id")

    macro = _as_rate(arm.get("macro_pass@1"))
    if macro is None:
        macro = _as_rate(arm.get("macro_pass_at_1"))
    ci = _as_rate(arm.get("macro_pass@1_ci"))
    if ci is None:
        ci = _as_rate(arm.get("macro_pass_at_1_ci"))
    task_macro, task_ci = _macro_from_tasks(tasks)
    if macro is None:
        macro = task_macro
    if ci is None:
        ci = task_ci

    first = _as_rate(arm.get("first_pass_at_1"))
    if first is None:
        first = _pass_k(arm, 1)
    first_hits = sum(1 for task in tasks if task.get("first") is True)
    if first is None and n_tasks:
        first = first_hits / n_tasks
    elif not any("first" in task for task in tasks) and first is not None and n_tasks:
        first_hits = int(round(first * n_tasks))

    solved_rows = [
        task
        for task in tasks
        if task.get("best") is True or (isinstance(task.get("c"), int) and not isinstance(task.get("c"), bool) and task.get("c") > 0)
    ]
    any_pass = _as_rate(arm.get("any_pass"))
    if any_pass is None:
        any_pass = _pass_k(arm, n_rollouts)
    hits = arm.get("any_pass_hits")
    if hits is None:
        hits = arm.get("best_attempt_pass")
    if not isinstance(hits, int) or isinstance(hits, bool):
        hits = len(solved_rows)
    if any_pass is None and n_tasks:
        any_pass = hits / n_tasks

    timing = arm.get("timing") if isinstance(arm.get("timing"), dict) else {}
    tokens = arm.get("tokens") if isinstance(arm.get("tokens"), dict) else {}
    macro_rates = arm.get("macro") if isinstance(arm.get("macro"), dict) else {}
    micro = arm.get("micro") if isinstance(arm.get("micro"), dict) else {}
    median_s = _num(timing.get("median_best_attempt_s"))
    mean_s = _num(timing.get("mean_best_attempt_s"))
    token_tasks = tokens.get("tasks_with_data")
    token_total = _num(tokens.get("total"))
    mean_tokens = None
    if token_total is not None and isinstance(token_tasks, int) and not isinstance(token_tasks, bool) and token_tasks > 0:
        mean_tokens = token_total / token_tasks

    title = f"{suite}: {candidate}"
    protocol = doc.get("eval_protocol") if isinstance(doc.get("eval_protocol"), dict) else None
    from render_report import harness_display

    agent = harness_display(protocol)
    run_clause = f"Harness {agent}, {n_tasks} tasks × {n_rollouts} rollouts"
    if concurrency is not None:
        run_clause += f", concurrency {concurrency}"
    if cpus_each is not None:
        run_clause += f", CPUs each {cpus_each}"
    clauses = [run_clause + "."]
    if model is not None:
        clauses.append(f"Model {model}.")
    if api_base is not None:
        clauses.append(f"API {api_base}.")
    if protocol:
        params = protocol.get("model_params") if isinstance(protocol.get("model_params"), dict) else {}
        thinking = params.get("thinking") if isinstance(params.get("thinking"), dict) else {}
        from render_report import icode_under_test

        clauses.append(
            f"{agent} under test: {icode_under_test(protocol)}. "
            f"Provider {params.get('provider') or '-'}. "
            f"reasoning_effort {params.get('reasoning_effort') or '-'}. "
            f"thinking.type {thinking.get('type') or '-'}. "
            f"max_tokens {params.get('max_tokens') or '-'}. "
            f"max_iterations {params.get('max_iterations') or '-'}."
        )
    empty_items = arm.get("empty_patches")
    if isinstance(empty_items, list):
        from eval_metrics import empty_patch_cause_counts

        counts = empty_patch_cause_counts([item for item in empty_items if isinstance(item, dict)])
        clauses.append(
            f"Empty model.patch {len(empty_items)} of {n_tasks * n_rollouts} rollouts "
            f"(infra {counts['infra']}, cut_off {counts['cut_off']}, no_edit {counts['no_edit']})."
        )
    if isinstance(arm.get("cut_off_rollouts"), int) and not isinstance(arm.get("cut_off_rollouts"), bool):
        clauses.append(f"Cut-off replies (last reply hit max_tokens) {arm['cut_off_rollouts']}.")
    oom = arm.get("oom_killed_rollouts")
    if isinstance(oom, int) and not isinstance(oom, bool) and oom > 0:
        clauses.append(f"OOM-killed rollouts {oom}.")
    anticheat = doc.get("anticheat") if isinstance(doc.get("anticheat"), dict) else None
    if anticheat and anticheat.get("status") == "ok":
        counts = anticheat.get("counts") if isinstance(anticheat.get("counts"), dict) else {}
        clauses.append(
            f"Anti-cheat {anticheat.get('version') or '-'}: {counts.get('clean', 0)} clean, "
            f"{counts.get('flagged', 0)} flagged, {counts.get('rejected', 0)} rejected, "
            f"{counts.get('not_run', 0)} not run. "
            f"Macro Pass@1 raw {_fmt_pct(_as_rate(anticheat.get('macro_pass@1_raw')))}, "
            f"official {_fmt_pct(_as_rate(anticheat.get('macro_pass@1_official')))} (rejected counted unresolved)."
        )
    elif anticheat:
        clauses.append("Anti-cheat not run.")
    if run_id is not None:
        clauses.append(f"Run {run_id}.")

    chips = [f"{n_tasks} tasks", f"{n_rollouts} rollouts"]
    if concurrency is not None:
        chips.append(f"concurrency {concurrency}")
    if model is not None:
        chips.append(f"model {model}")
    if run_id is not None:
        chips.append(f"run {run_id}")

    macro_scored = _as_rate(arm.get("macro_pass@1_scored"))
    ci_scored = _as_rate(arm.get("macro_pass@1_scored_ci"))
    if ci_scored is None:
        ci_scored = _scored_ci_from_tasks(tasks)
    headline_bits = []
    if macro is not None:
        bit = f"macro Pass@1 (padded, missing=fail) {_fmt_pct(macro)}"
        if ci is not None:
            bit += f" ±{ci * 100:.1f}% (CI)"
        headline_bits.append(bit)
    if macro_scored is not None:
        bit = f"macro Pass@1 (scored-only, missing omitted) {_fmt_pct(macro_scored)}"
        if ci_scored is not None:
            bit += f" ±{ci_scored * 100:.1f}% (CI)"
        headline_bits.append(bit)
    if any_pass is not None:
        headline_bits.append(f"Pass@{n_rollouts} / any-pass (padded, missing=fail) {_fmt_pct(any_pass)} ({hits}/{n_tasks})")
    if median_s is not None:
        headline_bits.append(f"Median best-attempt duration {_fmt_minutes(median_s)}")
    if mean_tokens is not None:
        headline_bits.append(f"Mean tokens per task {_fmt_token(mean_tokens)}")
    headline = f"{candidate} on {suite}"
    if headline_bits:
        headline += ": " + ". ".join(headline_bits) + "."

    ci_caption = "Macro Pass@1 (padded, missing=fail)" if ci is None else f"Macro Pass@1 (padded, missing=fail) ±{ci * 100:.1f}% (CI)"
    kpis = [
        (_fmt_pct(macro), ci_caption),
        (_fmt_pct(any_pass), f"Pass@{n_rollouts} / any-pass (padded, missing=fail) ({hits}/{n_tasks})"),
        (_kpi_minutes(median_s), "Median best-attempt"),
        (_fmt_token(mean_tokens) if mean_tokens is not None else DASH, "Mean tokens / task"),
    ]

    has_scored_ladder = any(_pass_k_scored(arm, k) is not None for k in range(1, n_rollouts + 1)) or macro_scored is not None

    ladder_labels = [f"Pass@{k}" for k in range(1, n_rollouts + 1)]
    padded_values: list[float | None] = []
    scored_values: list[float | None] = []
    for k in range(1, n_rollouts + 1):
        rate = _pass_k(arm, k)
        padded_values.append(None if rate is None else rate * 100)
        scored_rate = _pass_k_scored(arm, k)
        scored_values.append(None if scored_rate is None else scored_rate * 100)

    def _macro_cell(rate: float | None, spread: float | None) -> str:
        if rate is None:
            return DASH
        cell = _fmt_pct(rate)
        if spread is not None:
            cell = f"{cell} ±{spread * 100:.1f}% (CI)"
        return cell

    metric_headers = [
        "Metric",
        "Pass@1..k (padded, missing=fail)",
        "Pass@1..k (scored-only, missing omitted)",
    ]
    metric_rows = [
        ["Macro Pass@1 (±CI)", _macro_cell(macro, ci), _macro_cell(macro_scored, ci_scored)],
    ]
    for k in range(1, n_rollouts + 1):
        # Pass@1 is the first attempt alone, not an average over the rollouts.
        # Macro Pass@1 one row above is the average; saying so here stops the
        # two being read as the same number.
        label = "Pass@1 (first rollout only)" if k == 1 else f"Pass@{k}"
        metric_rows.append([label, _fmt_pct(_pass_k(arm, k)), _fmt_pct(_pass_k_scored(arm, k))])
    unscored_rollouts = arm.get("unscored_rollouts")
    all_scored = isinstance(unscored_rollouts, int) and not isinstance(unscored_rollouts, bool) and unscored_rollouts == 0
    any_cell = DASH if any_pass is None else f"{_fmt_pct(any_pass)} ({hits}/{n_tasks})"
    scored_first = _fmt_pct(first) if all_scored else DASH
    scored_any = any_cell if all_scored else DASH
    macro_f2p = _fmt_rate3(_as_rate(macro_rates.get("f2p")))
    micro_f2p = _fmt_rate3(_as_rate(micro.get("f2p")))
    metric_rows.append(["First-rollout Pass@1", _fmt_pct(first), scored_first])
    metric_rows.append([f"Pass@{n_rollouts} / any-pass", any_cell, scored_any])
    metric_rows.append(["Macro F2P (best rollout per question)", macro_f2p, macro_f2p if all_scored else DASH])
    metric_rows.append(["Micro F2P (best rollout per question)", micro_f2p, micro_f2p if all_scored else DASH])
    if isinstance(arm.get("micro_all"), dict):
        micro_all_f2p = _fmt_rate3(_as_rate(arm["micro_all"].get("f2p")))
        metric_rows.append(["Micro F2P (all scored rollouts)", micro_all_f2p if all_scored else DASH, micro_all_f2p])

    if median_s is None and mean_s is None:
        latency = "<p>No duration data</p>"
    else:
        latency = _svg_bars(
            "Latency (best-attempt duration)",
            ["Median", "Mean"],
            [None if median_s is None else median_s / 60.0, None if mean_s is None else mean_s / 60.0],
            max((median_s or 0) / 60.0, (mean_s or 0) / 60.0, 1.0),
            _minute_tick,
            height=280,
            label_size=12,
            series_name=candidate,
        )
        latency += '<p class="source">Minutes per task · best attempt</p>'
        latency += _legend(candidate)

    total_cell = _fmt_token(tokens.get("total"))
    if isinstance(token_tasks, int) and not isinstance(token_tasks, bool):
        total_cell = f"{total_cell} ({token_tasks} tasks)"
    wall_label = f"Wall (conc={concurrency})" if concurrency is not None else "Wall"
    token_rows = [
        ["Total tokens", total_cell],
        ["Input", _fmt_token(tokens.get("in"))],
        ["Output", _fmt_token(tokens.get("out"))],
        [wall_label, _fmt_wall(timing.get("wall_seconds"))],
        ["Eval model", DASH if model is None else str(model)],
    ]
    token_in = _num(tokens.get("in"))
    token_out = _num(tokens.get("out"))
    footnote = ""
    if token_in is not None and token_out is not None:
        side = "input" if token_in >= token_out else "output"
        footnote = (
            f'<p class="source">Output is {_fmt_token(token_out)} against {_fmt_token(token_in)} input; '
            f"token mass is almost all {side}.</p>"
        )

    bins = [0 for _ in range(n_rollouts + 1)]
    for task in tasks:
        count = task.get("c")
        if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= n_rollouts:
            bins[count] += 1
    hist_labels = [f"{count}/{n_rollouts}" for count in range(n_rollouts, -1, -1)]
    hist_values = [float(bins[count]) for count in range(n_rollouts, -1, -1)]
    hist_max = max(hist_values + [1.0]) * 1.15

    unsolved_rows = [task for task in tasks if task not in solved_rows]
    outcome_rows = []
    for label, count in (("Solved", len(solved_rows)), ("Unsolved", len(unsolved_rows))):
        share = DASH if not n_tasks else f"{count / n_tasks * 100:.1f}%"
        outcome_rows.append([label, str(count), share])

    solved_sorted = sorted(
        solved_rows,
        key=lambda task: (-(_num(task.get("pass_frac")) or 0.0), str(task.get("id"))),
    )
    unsolved_sorted = sorted(unsolved_rows, key=lambda task: str(task.get("id")))

    def cn(task: dict) -> str:
        return f"{task.get('c')}/{task.get('n')}"

    def sort_c(task: dict, reverse: bool) -> tuple:
        count = task.get("c")
        number = count if isinstance(count, int) and not isinstance(count, bool) else (-1 if reverse else 10**9)
        return ((-number if reverse else number), str(task.get("id")))

    low = sorted(tasks, key=lambda task: sort_c(task, False))
    outcome_tasks = solved_sorted + unsolved_sorted

    def task_table(rows: list[dict], columns: list[str], kind: str = "") -> str:
        if not rows:
            return "<p>None</p>"
        body = []
        for task in rows:
            cells = []
            for column in columns:
                if column == "Task":
                    cells.append(str(task.get("id") or ""))
                elif column == "c/n":
                    cells.append(cn(task))
                elif column == "set":
                    cells.append("solved" if task in solved_rows else "unsolved")
                elif column == "Notes":
                    notes = task.get("notes")
                    cells.append("" if notes in (None, "", "-") else str(notes))
            body.append(cells)
        return _table(columns, body, kind)

    if has_scored_ladder:
        pass_chart = _svg_grouped_bars(
            "Pass@k ladder",
            ladder_labels,
            [
                ("padded", "#1f8a4c", padded_values),
                ("scored", "#1d4e89", scored_values),
            ],
            100,
            _pct_tick,
            series_name=candidate,
        )
        pass_legend = _pass_legend()
    else:
        pass_chart = _svg_bars("Pass@k ladder", ladder_labels, padded_values, 100, _pct_tick, series_name=candidate)
        pass_legend = _legend(candidate)

    unscored_tasks = arm.get("unscored_tasks") if isinstance(arm.get("unscored_tasks"), list) else []
    unscored_tasks = [row for row in unscored_tasks if isinstance(row, dict) and int(row.get("unscored") or 0) > 0]
    if not unscored_tasks:
        unscored_html = ""
    else:
        from render_report import questions_without_score, skipped_reasons, unscored_notes

        skipped = skipped_reasons(doc)
        u_rows = []
        for row in unscored_tasks:
            notes = unscored_notes(row, skipped)
            u_rows.append(
                [
                    str(row.get("id") or ""),
                    f"{row.get('unscored')}/{row.get('n')}",
                    "" if notes == "-" else notes,
                ]
            )
        no_score = questions_without_score(doc)
        no_score_html = ""
        if no_score:
            names = " · ".join(f"{tid} ({why})" for tid, why in no_score)
            no_score_html = f'<p class="source">Questions without a score: {len(no_score)} ({_esc(names)})</p>'
        unscored_html = (
            "<h2>Unscored / no-response (not in Pass@k as a scored fail)</h2>"
            "<p class=\"source\">Tasks with at least one attempt that returned no reward. High rates here can move padded Pass@k.</p>"
            + no_score_html
            + _scroll(_table(["Task", "unscored/n", "notes"], u_rows, "unscored"), len(u_rows))
        )

    empty_html = _empty_patch_html(arm)
    provenance_html = _provenance_html(doc)
    from provenance import coverage_line

    coverage = coverage_line(doc.get("coverage"))
    coverage_html = f'<p class="source">{_esc(coverage)}</p>' if coverage else ""
    chip_html = "".join(f'<span class="chip">{_esc(chip)}</span>' for chip in chips)
    kpi_html = "".join(
        f'<div class="kpi"><div class="num">{_esc(number)}</div><div class="cap">{_esc(caption)}</div></div>'
        for number, caption in kpis
    )
    legend_html = _legend(candidate)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
body {{ margin: 0; background: #f4f4f2; color: #1c1c1c; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size: 17px; }}
.wrap {{ max-width: 920px; margin: 0 auto; padding: 32px 20px 64px; }}
h1 {{ font-size: 30px; font-weight: 700; margin: 0 0 8px; }}
h2 {{ font-size: 22px; font-weight: 650; margin: 36px 0 12px; }}
h3 {{ font-size: 18px; margin: 0 0 8px; }}
.sub, .source {{ color: #6b6b6b; font-size: 16px; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }}
.chip {{ display: inline-block; border: 1px solid #e0e0dc; border-radius: 999px; padding: 4px 10px; font-size: 15px; }}
.kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
.kpi {{ background: #fff; border: 1px solid #e6e6e4; border-radius: 12px; padding: 16px; }}
.kpi .num {{ font-size: 34px; font-weight: 700; color: #1f8a4c; }}
.kpi .cap {{ font-size: 15px; color: #6b6b6b; margin-top: 4px; }}
.card {{ background: #fff; border: 1px solid #e6e6e4; border-radius: 12px; padding: 16px; margin-top: 12px; }}
.pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.scroll {{ max-height: 420px; overflow-y: auto; border: 1px solid #e6e6e4; border-radius: 12px; background: #fff; }}
.scroll table {{ margin-top: 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 16px; background: #fff; margin-top: 12px; }}
th {{ text-align: left; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid #e6e6e4; }}
table.rates th {{ background: #e5f4eb; color: #146336; }}
table.tokens th {{ background: #e7f0fa; color: #1d4e89; }}
table.solved th, table.solved td {{ background: #f3fbf6; }}
table.solved th {{ background: #d9f2e4; color: #146336; }}
table.unsolved th, table.weak th {{ background: #fdf3e3; color: #8a5a12; }}
table.unsolved td, table.weak td {{ background: #fffaf3; }}
table.outcomes tr.solved td {{ background: #e8f6ee; }}
table.outcomes tr.unsolved td {{ background: #fdf3e3; }}
table.outcomes th {{ background: #f3f3f1; }}
table.unscored th {{ background: #f8e8e8; color: #8a1f1f; }}
table.unscored td {{ background: #fff7f7; }}
.legend {{ font-size: 15px; color: #6b6b6b; }}
.dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 999px; background: #1f8a4c; margin-right: 6px; }}
.dot.unsolved {{ background: #e6a23c; }}
.dot.scored {{ background: #1d4e89; }}
svg {{ width: 100%; height: auto; }}
svg.donut {{ width: 160px; height: 160px; display: block; margin: 0 auto 8px; }}
.pair > .card {{ margin-top: 0; }}
table.outcomes {{ width: 100%; }}
table.outcomes th, table.outcomes td {{ white-space: nowrap; }}
@media (max-width: 720px) {{
  .kpis {{ grid-template-columns: 1fr 1fr; }}
  .pair {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<main class="wrap">
<h1>{_esc(title)}</h1>
<p class="sub">{_esc(" ".join(clauses))}</p>
<div class="chips">{chip_html}</div>
<p>{_esc(headline)}</p>
<div class="kpis">{kpi_html}</div>
{coverage_html}
<h2>Pass@k ladder</h2>
<p class="source">Pass@1..k (padded, missing=fail): missing attempt counts as not resolved; n is N_ROLLOUTS. Pass@1..k (scored-only, missing omitted): only attempts with reward.json. Pass@1 is the first rollout of each question on its own; Macro Pass@1 = mean of c/n (padded) or c_scored/n_scored, which averages every rollout and is the number to compare across runs.</p>
<div class="card">
{pass_chart}
{pass_legend}
</div>
{_table(metric_headers, metric_rows, "rates")}
<h2>Efficiency</h2>
<div class="pair">
<div class="card latency"><h3>Latency (best-attempt duration)</h3>{latency}</div>
<div class="card"><h3>Tokens &amp; wall clock</h3>
<div class="kpi"><div class="num">{_esc(_fmt_token(mean_tokens) if mean_tokens is not None else DASH)}</div><div class="cap">Mean tokens / task</div></div>
{_table(["", candidate], token_rows, "tokens")}
{footnote}
</div>
</div>
<h2>Pass-count histogram (c/{n_rollouts})</h2>
<div class="card">
{_svg_bars("Pass-count histogram", hist_labels, hist_values, hist_max, _count_tick, height=280, label_size=12, series_name=candidate)}
{legend_html}
<p class="source">Task counts by how many rollouts passed.</p>
</div>
<h2>Task outcomes (any-pass)</h2>
<div class="card">
{_svg_donut(len(solved_rows), len(unsolved_rows))}
{_table(["Set", "Count", f"% of {n_tasks}"], outcome_rows, "outcomes")}
<p class="source">First rollout: pass {first_hits} · miss {n_tasks - first_hits}.</p>
{_scroll(task_table(outcome_tasks, ["Task", "set", "c/n", "Notes"], "outcomes"), len(outcome_tasks))}
</div>
<h2>Pass fraction</h2>
<div class="card"><h3>Lowest pass fraction</h3>
{_scroll(task_table(low, ["Task", "c/n"], "weak"), len(low))}
</div>
{empty_html}
{unscored_html}
{provenance_html}
</main>
</body>
</html>
"""
