"""Classify Pier job result.json (no network)."""

from __future__ import annotations

from typing import Any


def hollow_job_reason(doc: dict[str, Any]) -> str | None:
    """Return a failure reason if Pier did not complete any real trial.

    A job with 0 eval trials (compose/CLI abort) is a hollow pass.
    A job that ran trials and then errored is not hollow.
    """
    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    n_eval_trials = 0
    evals = stats.get("evals") if isinstance(stats.get("evals"), dict) else {}
    for ev in evals.values():
        if isinstance(ev, dict):
            n_eval_trials += int(ev.get("n_trials") or 0)
    n_err = int(stats.get("n_errored_trials") or 0)
    if n_eval_trials == 0:
        if n_err > 0:
            return (
                f"pier completed 0 trials with {n_err} error(s) "
                "(environment/CLI failed before a trial ran)"
            )
        return "pier completed 0 trials"
    return None
