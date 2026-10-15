#!/usr/bin/env python3
"""P5/P6 SWE-bench Pro: iCode in Scale Docker image, then swe_bench_pro_eval.py."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from swebenchpro_tasks import as_eval_list_str, parse_task_list


def get_dockerhub_image_uri(uid: str, dockerhub_username: str, repo_name: str = "") -> str:
    """Match scaleapi/SWE-bench_Pro-os helper_code/image_uri.py."""
    repo_base, repo_name_only = repo_name.lower().split("/")
    hsh = uid.replace("instance_", "")
    if uid == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name_only = "element-web"
    elif "element-hq" in repo_name.lower() and "element-web" in repo_name.lower():
        repo_name_only = "element"
        if hsh.endswith("-vnan"):
            hsh = hsh[:-5]
    elif hsh.endswith("-vnan"):
        hsh = hsh[:-5]
    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]
    return f"{dockerhub_username}/sweap-images:{tag}"


def dockerhub_image(meta: dict) -> str:
    user = str(meta.get("dockerhub_username") or "jefzda").strip() or "jefzda"
    uid = str(meta.get("instance_id") or "").strip()
    repo = str(meta.get("repo") or "").strip()
    raw_name = str(meta.get("image_name") or "").strip()
    prefix = f"{user}/sweap-images:"
    if raw_name.startswith(prefix):
        return raw_name
    tag = str(meta.get("dockerhub_tag") or "").strip()
    if "." in tag and ":" not in tag:
        return f"{prefix}{tag}"
    if "/" not in repo:
        raise RuntimeError(f"cannot resolve Docker Hub image for {uid!r} repo={repo!r}")
    return get_dockerhub_image_uri(uid, user, repo)


INNER_SH = r"""
set -euo pipefail
export PATH="/opt/icode-host:/opt/icode-host/.venv/bin:${PATH:-/usr/bin:/bin}"
WORKDIR=""
for cand in /testbed /workspace /repo /app /src "$PWD"; do
  if [ -d "$cand/.git" ]; then
    WORKDIR="$cand"
    break
  fi
done
if [ -z "$WORKDIR" ]; then
  WORKDIR="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
[ -n "$WORKDIR" ] && [ -d "$WORKDIR" ] || { echo "no git repo in sweap image" >&2; exit 2; }
cd "$WORKDIR"
if [ -n "${BASE_COMMIT:-}" ]; then
  git reset --hard "$BASE_COMMIT" || true
  git clean -fd || true
fi
cp /task/instruction.md /tmp/instruction.md
ICODE_BIN="${ICODE_BIN_IN_SANDBOX:-icode}"
if [ -x /opt/icode-host/.venv/bin/icode ]; then
  ICODE_BIN=/opt/icode-host/.venv/bin/icode
elif [ -x "/opt/icode-host/${ICODE_BIN_IN_SANDBOX:-icode}" ]; then
  ICODE_BIN="/opt/icode-host/${ICODE_BIN_IN_SANDBOX:-icode}"
fi
"$ICODE_BIN" run -t /tmp/instruction.md -C "$WORKDIR" -a code --json || true
git diff > /out/agent.patch || true
"""


def load_meta(task_dir: Path) -> dict:
    path = task_dir / "meta.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"instance_id": task_dir.name}


def selected_ids(tasks_dir: Path, task_file: str, n_tasks: int) -> list[str]:
    if task_file:
        raw = Path(task_file).read_text(encoding="utf-8")
        names = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return [n for n in names if (tasks_dir / n).is_dir()]
    dirs = sorted([p.name for p in tasks_dir.iterdir() if p.is_dir()])
    return dirs[: max(1, n_tasks)]


def docker_platform() -> str:
    return os.environ.get("SWEBENCHPRO_DOCKER_PLATFORM", "linux/amd64")


def find_eval_script(src_dir: Path) -> Path | None:
    for cand in (
        src_dir / "src" / "swe_bench_pro_eval.py",
        src_dir / "swe_bench_pro_eval.py",
        src_dir / "src" / "sweap_pro_eval_modal.py",
    ):
        if cand.is_file():
            return cand
    for path in src_dir.rglob("swe_bench_pro_eval.py"):
        return path
    return None


def find_scripts_dir(src_dir: Path) -> Path | None:
    for cand in (src_dir / "src" / "run_scripts", src_dir / "run_scripts", src_dir / "scripts" / "run_scripts"):
        if cand.is_dir():
            return cand
    for path in src_dir.rglob("run_scripts"):
        if path.is_dir():
            return path
    return None


def run_icode_docker(
    task_dir: Path,
    out_dir: Path,
    host_icode: Path,
    icode_bin: str,
    skip_docker: bool,
) -> tuple[Path, int]:
    meta = load_meta(task_dir)
    tid = meta.get("instance_id") or task_dir.name
    dest = out_dir / tid
    dest.mkdir(parents=True, exist_ok=True)
    patch_path = dest / "agent.patch"
    if skip_docker:
        if not patch_path.is_file():
            patch_path.write_text("", encoding="utf-8")
        return patch_path, 0
    image = dockerhub_image(meta)
    icode_name = Path(icode_bin).name if icode_bin else "icode"
    env = {
        **os.environ,
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        "ICODE_MODEL": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        "ICODE_API_BASE": "https://api.deepseek.com/v1",
        "ICODE_PROVIDER": os.environ.get("ICODE_PROVIDER") or "DeepSeek",
        "ICODE_REASONING_EFFORT": os.environ.get("ICODE_REASONING_EFFORT") or "high",
        "BASE_COMMIT": str(meta.get("base_commit") or ""),
        "ICODE_BIN_IN_SANDBOX": icode_name,
    }
    cmd = [
        "docker",
        "run",
        "--rm",
        "--platform",
        docker_platform(),
        "-v",
        f"{host_icode}:/opt/icode-host",
        "-v",
        f"{task_dir}:/task:ro",
        "-v",
        f"{dest}:/out",
        "-e",
        "DEEPSEEK_API_KEY",
        "-e",
        "DEEPSEEK_MODEL",
        "-e",
        "ICODE_MODEL",
        "-e",
        "ICODE_API_BASE",
        "-e",
        "ICODE_PROVIDER",
        "-e",
        "ICODE_REASONING_EFFORT",
        "-e",
        "BASE_COMMIT",
        "-e",
        "ICODE_BIN_IN_SANDBOX",
        image,
        "-lc",
        INNER_SH,
    ]
    # Scale sweap images set ENTRYPOINT ["/bin/bash"]; do not pass bash again (exit 126).
    print(f"P5 swebenchpro docker image={image} task={tid}", flush=True)
    proc = subprocess.run(cmd, check=False, env=env)
    if proc.returncode != 0:
        print(f"WARNING: docker run exited {proc.returncode} for {tid}", file=sys.stderr)
    if not patch_path.is_file():
        patch_path.write_text("", encoding="utf-8")
    (dest / "docker_exit.txt").write_text(str(proc.returncode), encoding="utf-8")
    return patch_path, proc.returncode


def collect_baseline_patches(baseline_dir: Path, ids: list[str]) -> dict[str, str]:
    patches: dict[str, str] = {}
    for tid in ids:
        path = baseline_dir / tid / "agent.patch"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "diff --git" in text or text.lstrip().startswith("--- "):
            patches[tid] = text
        else:
            patches[tid] = ""
    return patches


def write_patches_json(path: Path, patches: dict[str, str]) -> None:
    payload = [
        {"instance_id": tid, "patch": text or "", "prefix": ""}
        for tid, text in patches.items()
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_eval_output(out_dir: Path) -> dict[str, bool | None]:
    resolved: dict[str, bool | None] = {}
    for path in out_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        if path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        objs = data if isinstance(data, list) else [data]
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, bool) and path.name == "eval_results.json":
                    resolved[str(key)] = val
                elif isinstance(val, dict) and ("resolved" in val or "success" in val or "passed" in val):
                    objs.append({**val, "instance_id": key})
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            iid = str(obj.get("instance_id") or obj.get("id") or "").strip()
            if not iid:
                continue
            flag = obj.get("resolved")
            if flag is None:
                flag = obj.get("success")
            if flag is None:
                flag = obj.get("passed")
            if isinstance(flag, bool):
                resolved[iid] = flag
            elif isinstance(flag, (int, float)):
                resolved[iid] = float(flag) >= 1.0 - 1e-9
    return resolved


def passed_test_names(eval_dir: Path, tid: str) -> set[str]:
    for cand in (eval_dir / tid / "_output.json", eval_dir / tid / "workspace" / "output.json"):
        if not cand.is_file():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tests = data.get("tests") if isinstance(data, dict) else None
        if not isinstance(tests, list):
            continue
        return {
            str(item.get("name"))
            for item in tests
            if isinstance(item, dict) and item.get("status") == "PASSED" and item.get("name")
        }
    return set()


def scale_suite_rates(meta: dict, passed: set[str]) -> dict:
    f2p = set(ast.literal_eval(as_eval_list_str(meta.get("fail_to_pass"))))
    p2p = set(ast.literal_eval(as_eval_list_str(meta.get("pass_to_pass"))))
    f2p_pass = len(f2p & passed)
    p2p_pass = len(p2p & passed)
    return {
        "f2p": (f2p_pass / len(f2p)) if f2p else None,
        "f2p_pass": f2p_pass if f2p else None,
        "f2p_total": len(f2p) if f2p else None,
        "p2p": (p2p_pass / len(p2p)) if p2p else None,
        "p2p_pass": p2p_pass if p2p else None,
        "p2p_total": len(p2p) if p2p else None,
    }


def run_scale_eval(
    src_dir: Path,
    out_dir: Path,
    tasks_dir: Path,
    ids: list[str],
    patches: dict[str, str],
    skip_eval: bool,
) -> dict[str, bool | None]:
    eval_dir = out_dir / "scale_eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    raw_path = eval_dir / "selected.jsonl"
    with raw_path.open("w", encoding="utf-8") as fh:
        for tid in ids:
            meta = load_meta(tasks_dir / tid)
            fh.write(json.dumps(meta) + "\n")
    patch_path = eval_dir / "patches.json"
    write_patches_json(patch_path, patches)
    if skip_eval:
        resolved: dict[str, bool | None] = {}
        for tid in ids:
            resolved[tid] = False
            dest = out_dir / tid
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "eval.json").write_text(
                json.dumps({"instance_id": tid, "resolved": False}, indent=2),
                encoding="utf-8",
            )
        return resolved

    script = find_eval_script(src_dir)
    scripts_dir = find_scripts_dir(src_dir)
    if script is None:
        print("WARNING: swe_bench_pro_eval.py not found; skipping Scale eval", file=sys.stderr)
        return {tid: False for tid in ids}
    eval_root = script.parent
    for tid in ids:
        df = eval_root / "dockerfiles" / "base_dockerfile" / tid / "Dockerfile"
        if not df.is_file():
            raise RuntimeError(f"missing Scale dockerfile {df}")
    cmd = [
        sys.executable,
        str(script),
        f"--raw_sample_path={raw_path.resolve()}",
        f"--patch_path={patch_path.resolve()}",
        f"--output_dir={eval_dir.resolve()}",
        "--dockerhub_username=jefzda",
        "--use_local_docker",
        "--num_workers=1",
        f"--docker_platform={docker_platform()}",
        "--redo",
    ]
    if scripts_dir is not None:
        cmd.append(f"--scripts_dir={scripts_dir.resolve()}")
    print("P5 swebenchpro eval:", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, check=False, cwd=str(eval_root))
    if proc.returncode != 0:
        print(f"ERROR: swe_bench_pro_eval.py exited {proc.returncode}", file=sys.stderr)
        raise RuntimeError(f"swe_bench_pro_eval.py exited {proc.returncode}")
    resolved = parse_eval_output(eval_dir)
    for tid in ids:
        resolved.setdefault(tid, False if patches.get(tid) else False)
        dest = out_dir / tid
        dest.mkdir(parents=True, exist_ok=True)
        rates = scale_suite_rates(load_meta(tasks_dir / tid), passed_test_names(eval_dir, tid))
        payload = {"instance_id": tid, "resolved": resolved.get(tid), **rates}
        (dest / "eval.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return resolved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("harness", "baseline"), required=True)
    ap.add_argument("--tasks-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--src-dir", default=os.environ.get("SWEBENCHPRO_DIR", ""))
    ap.add_argument("--task-file", default="")
    ap.add_argument("--n-tasks", type=int, default=1)
    ap.add_argument("--icode-host", default="")
    ap.add_argument("--icode-bin", default="icode")
    ap.add_argument("--skip-docker", action="store_true")
    ap.add_argument("--skip-eval", action="store_true")
    args = ap.parse_args()

    tasks_dir = Path(args.tasks_dir)
    out_dir = Path(args.out_dir)
    src_dir = Path(args.src_dir) if args.src_dir else tasks_dir.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = selected_ids(tasks_dir, args.task_file, args.n_tasks)
    if not ids:
        print("ERROR: no SWE-bench Pro tasks selected", file=sys.stderr)
        return 1

    extra = parse_task_list(os.environ.get("TASKS", ""))
    if extra:
        ids = extra

    patches: dict[str, str] = {}
    docker_failed = False
    if args.mode == "harness":
        host = Path(args.icode_host) if args.icode_host else None
        if host is None or not host.is_dir():
            print("ERROR: --icode-host is required for harness mode", file=sys.stderr)
            return 1
        for tid in ids:
            patch_path, docker_rc = run_icode_docker(
                tasks_dir / tid,
                out_dir,
                host,
                args.icode_bin,
                args.skip_docker,
            )
            if not args.skip_docker and docker_rc != 0:
                docker_failed = True
            patches[tid] = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else ""
    else:
        patches = collect_baseline_patches(out_dir, ids)

    skip_eval = args.skip_eval or args.skip_docker
    try:
        resolved = run_scale_eval(src_dir, out_dir, tasks_dir, ids, patches, skip_eval)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    summary = [{"id": tid, "resolved": resolved.get(tid), "has_patch": bool(patches.get(tid))} for tid in ids]
    (out_dir / "scale_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"OK swebenchpro {args.mode} n={len(ids)}")
    if docker_failed:
        print("ERROR: docker run failed for at least one SWE-bench Pro task", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
