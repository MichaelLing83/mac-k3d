#!/usr/bin/env python3
"""Materialize SWE-bench Pro instance dirs (instruction.md + meta.json) from Scale jsonl/csv."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
from pathlib import Path


def first_present(obj: dict, *keys):
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return None


def as_eval_list_str(raw) -> str:
    """Scale swe_bench_pro_eval.py does set(eval(sample['fail_to_pass']))."""
    if raw is None:
        return "[]"
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return "[]"
        try:
            val = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return json.dumps([text])
        if isinstance(val, (list, tuple, set)):
            return text
        return json.dumps([val])
    if isinstance(raw, (list, tuple, set)):
        return json.dumps(list(raw))
    return json.dumps([raw])


def parse_task_list(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n]", raw or "") if p.strip()]


def find_dataset_files(src_dir: Path) -> list[Path]:
    hits: list[Path] = []
    for pattern in ("**/*.jsonl", "**/*.csv"):
        for path in src_dir.glob(pattern):
            name = path.name.lower()
            if path.stat().st_size == 0:
                continue
            if "swe" in name or "eval" in name or "pro" in name or "sample" in name:
                hits.append(path)
    if hits:
        return sorted(hits, key=lambda p: p.stat().st_size, reverse=True)
    for path in src_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".jsonl", ".csv"} and path.stat().st_size > 0:
            hits.append(path)
    return sorted(hits, key=lambda p: p.stat().st_size, reverse=True)


def row_from_obj(obj: dict) -> dict | None:
    iid = str(obj.get("instance_id") or obj.get("id") or "").strip()
    if not iid:
        return None
    statement = (
        obj.get("problem_statement")
        or obj.get("problem")
        or obj.get("instruction")
        or obj.get("issue")
        or ""
    )
    tag = str(obj.get("dockerhub_tag") or obj.get("docker_tag") or "").strip()
    repo = str(obj.get("repo") or "").strip()
    if not tag and repo:
        tag = repo.replace("/", "_").replace(":", "_")
    image_name = str(obj.get("image_name") or "").strip()
    f2p = as_eval_list_str(first_present(obj, "FAIL_TO_PASS", "fail_to_pass"))
    p2p = as_eval_list_str(first_present(obj, "PASS_TO_PASS", "pass_to_pass"))
    tests = first_present(obj, "selected_test_files_to_run")
    if isinstance(tests, (list, tuple)):
        tests = json.dumps(list(tests))
    return {
        "instance_id": iid,
        "problem_statement": str(statement),
        "dockerhub_tag": tag,
        "image_name": image_name,
        "repo": repo,
        "base_commit": str(obj.get("base_commit") or "").strip(),
        "fail_to_pass": f2p,
        "pass_to_pass": p2p,
        "before_repo_set_cmd": obj.get("before_repo_set_cmd"),
        "selected_test_files_to_run": tests,
        "dockerhub_username": str(obj.get("dockerhub_username") or "jefzda"),
    }


def iter_rows(path: Path):
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    row = row_from_obj(obj)
                    if row:
                        yield row
        return
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for obj in reader:
            row = row_from_obj(obj)
            if row:
                yield row


def literal_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    text = str(raw).strip()
    if not text:
        return []
    try:
        val = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return [text]
    if isinstance(val, (list, tuple, set)):
        return [str(item) for item in val]
    return [str(val)]


def last_shell_line(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return text.splitlines()[-1].strip()


def suite_reward(passed: set[str], fail_to_pass, pass_to_pass) -> dict:
    """Same rule as Scale: resolved iff every FAIL_TO_PASS and PASS_TO_PASS name passed."""
    f2p = set(literal_list(fail_to_pass))
    p2p = set(literal_list(pass_to_pass))
    f2p_pass = len(f2p & passed)
    p2p_pass = len(p2p & passed)
    required = f2p | p2p
    resolved = bool(required) and required <= passed
    return {
        "reward": 1.0 if resolved else 0.0,
        "resolved": resolved,
        "f2p": (f2p_pass / len(f2p)) if f2p else None,
        "f2p_pass": f2p_pass,
        "f2p_total": len(f2p),
        "p2p": (p2p_pass / len(p2p)) if p2p else None,
        "p2p_pass": p2p_pass,
        "p2p_total": len(p2p),
    }


def find_instance_scripts(src_dir: Path, tid: str) -> tuple[Path | None, Path | None]:
    for base in (src_dir, src_dir / "src"):
        run = base / "run_scripts" / tid / "run_script.sh"
        parser = base / "run_scripts" / tid / "parser.py"
        if run.is_file() and parser.is_file():
            return run, parser
    return None, None


GRADE_PY = r'''#!/usr/bin/env python3
"""Score Scale parser output.json against FAIL_TO_PASS / PASS_TO_PASS."""
import json
from pathlib import Path

spec = json.loads(Path("/tests/task_eval.json").read_text(encoding="utf-8"))
out = Path("/logs/verifier/output.json")
passed = set()
if out.is_file():
    data = json.loads(out.read_text(encoding="utf-8"))
    for item in data.get("tests") or []:
        if isinstance(item, dict) and item.get("status") == "PASSED" and item.get("name"):
            passed.add(str(item["name"]))
f2p = set(spec.get("fail_to_pass") or [])
p2p = set(spec.get("pass_to_pass") or [])
f2p_pass = len(f2p & passed)
p2p_pass = len(p2p & passed)
required = f2p | p2p
resolved = bool(required) and required <= passed
reward = {
    "reward": 1.0 if resolved else 0.0,
    "resolved": resolved,
    "f2p": (f2p_pass / len(f2p)) if f2p else None,
    "f2p_pass": f2p_pass,
    "f2p_total": len(f2p),
    "p2p": (p2p_pass / len(p2p)) if p2p else None,
    "p2p_pass": p2p_pass,
    "p2p_total": len(p2p),
}
Path("/logs/verifier").mkdir(parents=True, exist_ok=True)
Path("/logs/verifier/reward.json").write_text(json.dumps(reward, indent=2) + "\n", encoding="utf-8")
Path("/logs/verifier/reward.txt").write_text(("1" if resolved else "0") + "\n", encoding="utf-8")
print(json.dumps(reward))
'''

TEST_SH = r'''#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier /logs/artifacts
cd /app || { python3 /tests/grade.py; exit 0; }
git config --global --add safe.directory /app || true
python3 - <<'PY'
import json, subprocess
from pathlib import Path
spec = json.loads(Path("/tests/task_eval.json").read_text(encoding="utf-8"))
base = str(spec.get("base_commit") or "").strip()
if base:
    subprocess.run(["git", "reset", "--hard", base], check=False)
    subprocess.run(["git", "clean", "-fd"], check=False)
patch = None
for cand in ("/logs/artifacts/model.patch", "/logs/agent/agent.patch"):
    path = Path(cand)
    if path.is_file() and path.stat().st_size:
        patch = path
        break
if patch is not None:
    subprocess.run(["git", "apply", "-v", str(patch)], check=False)
before = Path("/tests/before_repo.sh")
if before.is_file() and before.stat().st_size:
    subprocess.run(["bash", str(before)], check=False)
files = spec.get("test_files") or []
arg = ",".join(str(item) for item in files)
run = Path("/tests/run_script.sh")
if run.is_file():
    subprocess.run(
        f"bash /tests/run_script.sh {arg} > /logs/verifier/stdout.log 2> /logs/verifier/stderr.log",
        shell=True,
        check=False,
    )
parser = Path("/tests/parser.py")
if parser.is_file():
    subprocess.run(
        ["python3", str(parser), "/logs/verifier/stdout.log", "/logs/verifier/stderr.log", "/logs/verifier/output.json"],
        check=False,
    )
PY
python3 /tests/grade.py
'''


def write_pier_task(dest: Path, row: dict, src_dir: Path) -> None:
    """Harbor task.toml layout (same shape as DeepSWE) using the official sweap image."""
    from swebenchpro_run import dockerhub_image

    tid = row["instance_id"]
    image = dockerhub_image(row)
    base = str(row.get("base_commit") or "").strip()
    before = last_shell_line(row.get("before_repo_set_cmd"))
    tests = literal_list(row.get("selected_test_files_to_run"))
    spec = {
        "instance_id": tid,
        "base_commit": base,
        "test_files": tests,
        "fail_to_pass": literal_list(row.get("fail_to_pass")),
        "pass_to_pass": literal_list(row.get("pass_to_pass")),
    }
    (dest / "tests").mkdir(parents=True, exist_ok=True)
    (dest / "environment").mkdir(parents=True, exist_ok=True)
    (dest / "tests" / "task_eval.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    (dest / "tests" / "before_repo.sh").write_text(
        ("#!/bin/bash\nset -uo pipefail\n" + before + "\n") if before else "",
        encoding="utf-8",
    )
    (dest / "tests" / "grade.py").write_text(GRADE_PY, encoding="utf-8")
    (dest / "tests" / "test.sh").write_text(TEST_SH, encoding="utf-8")
    (dest / "tests" / "test.sh").chmod(0o755)
    run_src, parser_src = find_instance_scripts(src_dir, tid)
    if run_src is not None and parser_src is not None:
        (dest / "tests" / "run_script.sh").write_bytes(run_src.read_bytes())
        (dest / "tests" / "parser.py").write_bytes(parser_src.read_bytes())
        (dest / "tests" / "run_script.sh").chmod(0o755)
    (dest / "environment" / "Dockerfile").write_text(
        f"FROM {image}\n"
        "WORKDIR /app\n"
        "# sweap ENTRYPOINT is /bin/bash. Harbor's keepalive is command\n"
        '# ["sh","-c","sleep infinity"]. Left in place, bash treats sh as a\n'
        "# script and the container exits 126 before a trial starts.\n"
        "# Harbor builds this Dockerfile only with --force-build. The live\n"
        "# path uses [environment].docker_image and docker-compose.yaml.\n"
        "ENTRYPOINT []\n",
        encoding="utf-8",
    )
    # Harbor's prebuilt compose sets command [sh,-c,sleep infinity] and does
    # not clear the image ENTRYPOINT. The agent env merges environment/docker-compose.yaml.
    # The separate verifier uses tests/ as its project directory and would
    # otherwise start the same image and exit 126 before reward.json is written.
    keepalive = (
        "services:\n"
        "  main:\n"
        '    entrypoint: ["sh", "-c", "sleep infinity"]\n'
    )
    (dest / "environment" / "docker-compose.yaml").write_text(keepalive, encoding="utf-8")
    (dest / "tests" / "docker-compose.yaml").write_text(keepalive, encoding="utf-8")
    collect = (
        "cd /app && mkdir -p /logs/artifacts && git config --global --add safe.directory /app && "
        "git add -A && git diff --cached --binary"
        + (f" {base}" if base else "")
        + " > /logs/artifacts/model.patch"
    )
    toml = f'''schema_version = "1.3"

[task]
name = "swebenchpro/{tid}"
description = "SWE-bench Pro instance scored with the official FAIL_TO_PASS and PASS_TO_PASS lists."

[metadata]
instance_id = "{tid}"
repo = "{row.get("repo") or ""}"
base_commit = "{base}"
docker_image = "{image}"

[environment]
docker_image = "{image}"
os = "linux"
cpus = 4
memory_mb = 8192
storage_mb = 20480
build_timeout_sec = 1800.0

[agent]
timeout_sec = 10800.0
network_mode = "no-network"

[verifier]
timeout_sec = 3600.0
# shared: Harbor uploads tests/test.sh into the agent container. separate
# skips that upload and expects /tests/test.sh to already be in the image.
# The sweap image does not contain it, so a separate verifier exits before
# grade.py can write reward.json.
environment_mode = "shared"
network_mode = "no-network"

[[verifier.collect]]
command = "{collect}"
timeout_sec = 300.0
'''
    (dest / "task.toml").write_text(toml, encoding="utf-8")


def write_task(out_dir: Path, row: dict, src_dir: Path) -> None:
    tid = row["instance_id"]
    dest = out_dir / tid
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "instruction.md").write_text(row.get("problem_statement") or tid, encoding="utf-8")
    (dest / "meta.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
    write_pier_task(dest, row, src_dir)


def materialize(
    src_dir: Path,
    out_dir: Path,
    task: str = "",
    tasks: str = "",
    n_tasks: int = 1,
) -> list[str]:
    files = find_dataset_files(src_dir)
    if not files:
        raise FileNotFoundError(f"no SWE-bench Pro jsonl/csv under {src_dir}")
    wanted = parse_task_list(tasks) or parse_task_list(task)
    written: list[str] = []
    seen: set[str] = set()
    limit = max(1, n_tasks) if not wanted else 10**9
    for path in files:
        for row in iter_rows(path):
            iid = row["instance_id"]
            if iid in seen:
                continue
            if wanted and iid not in wanted:
                continue
            write_task(out_dir, row, src_dir)
            seen.add(iid)
            written.append(iid)
            if not wanted and len(written) >= limit:
                return written
        if written and not wanted:
            return written
        if wanted and all(w in seen for w in wanted):
            return written
    if wanted:
        missing = [w for w in wanted if w not in seen]
        if missing:
            raise FileNotFoundError(f"instance_id not in dataset: {', '.join(missing)}")
    if not written:
        raise FileNotFoundError(f"no instances materialized from {src_dir}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--task", default="")
    ap.add_argument("--tasks", default="")
    ap.add_argument("--n-tasks", type=int, default=1)
    args = ap.parse_args()
    src = Path(args.src_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids = materialize(src, out, args.task, args.tasks, args.n_tasks)
    print(f"OK swebenchpro tasks={len(ids)} dir={out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
