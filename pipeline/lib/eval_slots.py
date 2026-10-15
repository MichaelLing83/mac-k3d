"""What the eval containers are actually using, read from `docker stats`.

Up to PF.1 this module also scheduled the run: it handed out one question at a
time, tracked in-flight rollouts and guessed a Docker memory cap. Harbor does
all of that now (`-k`, `-n`, and the cpus/memory each task.toml declares), so
what is left is the measurement `task_resources.py` feeds back as the observed
per-trial peak.
"""

from __future__ import annotations

import re
import subprocess

# Ignore startup noise / mis-parses below ~10 MiB so they cannot become peak 0.00.
MIN_SAMPLE_GB = 0.01
PEAK_NAME = "container_mem_peak_gb"
JSONL_NAME = "container_mem.jsonl"

_MEM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]i?B)", re.IGNORECASE)
_MAIN_RE = re.compile(r"-main-\d+")


def parse_mem_gb(text: str) -> float | None:
    match = _MEM_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower().replace("i", "")
    scale = {"kb": 1 / (1024 * 1024), "mb": 1 / 1024, "gb": 1.0, "tb": 1024.0}
    factor = scale.get(unit)
    if factor is None:
        return None
    return value * factor


def usable_sample_gb(sample_gb: float | None) -> float | None:
    """Drop readings below MIN_SAMPLE_GB so noise cannot become the recorded peak."""
    if sample_gb is None or sample_gb < MIN_SAMPLE_GB:
        return None
    return sample_gb


def _is_eval_container(line: str) -> bool:
    # Harbor job names contain _icode_. Older trials use __env-main-1; current
    # Harbor often names the main service task__hash-main-1. Egress sidecars
    # also contain __ and must not set the memory peak.
    name = line.split("\t", 1)[0]
    low = name.lower()
    if "egress" in low or "sidecar" in low:
        return False
    if "_icode_" in name or "__env-main-" in name:
        return True
    return "__" in name and _MAIN_RE.search(name) is not None


def max_icode_mem_gb(stats_text: str) -> float | None:
    best: float | None = None
    for line in stats_text.splitlines():
        if not _is_eval_container(line):
            continue
        gb = usable_sample_gb(parse_mem_gb(line))
        if gb is None:
            continue
        best = gb if best is None else max(best, gb)
    return best


def measure_container_gb() -> float | None:
    """Largest eval container right now, in GB. None when Docker says nothing useful."""
    try:
        proc = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return max_icode_mem_gb(proc.stdout)
