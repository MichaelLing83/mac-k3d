#!/usr/bin/env python3
"""Back up one run into the repo and pack it as ``<folder>.tar.gz`` (archive phase).

The backup holds the report files, the anti-cheat verdicts and, per trial,
the files listed below. Transcripts are gzipped with key-shaped values and
this process's secret env values masked; ``.harbor-env`` is never copied.

``archive_run.py compress --jobs-dir D`` does the same gzip and masking in
place for one build's trials, before a shard archives them.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path

REPORT_FILES = (
    "artifact.json",
    "summary.md",
    "report.html",
    "provenance.md",
    "cost-token-report.md",
    "container_mem.jsonl",
    "skipped_questions.txt",
)

_TRIAL_FILES = {
    "result.json",
    "reward.json",
    "agent_report.json",
    "usage.json",
    "notes.txt",
    "icode-usage.json",
    "icode.json",
    "icode.txt",
    "timing.json",
    "agent.patch",
    "model.patch",
    "capture.json",
    "capture_flags.json",
    "base_sha.txt",
    "anticheat.json",
    "trial.log",
    "jiuwenswarm-usage.json",
    "jiuwenswarm-exit.txt",
}
_GZIP_TRIAL_FILES = {"events.jsonl", "jiuwenswarm.jsonl"}

_KEY_SHAPED = re.compile(r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,}")
_SECRET_ENV = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ICODE_API_KEY", "GITCODE_TOKEN", "GITHUB_TOKEN")


def default_backup_root() -> Path:
    for key in ("MAC_K3D_BACKUP_ROOT", "MAC_K3D_OUTPUT_ROOT"):
        override = os.environ.get(key)
        if override:
            return Path(override)
    root = os.environ.get("MAC_K3D_ROOT")
    if root:
        return Path(root) / "output"
    return Path(__file__).resolve().parents[2] / "output"


def archive_transcript(src: Path, target: Path) -> None:
    """Gzip a transcript, masking key-shaped values and this process's secret env values."""
    secrets = [v for v in (os.environ.get(k) or "" for k in _SECRET_ENV) if len(v) >= 8]
    with src.open("r", encoding="utf-8", errors="replace") as fh, gzip.open(target, "wt", encoding="utf-8") as out:
        for line in fh:
            for value in secrets:
                line = line.replace(value, "***")
            out.write(_KEY_SHAPED.sub("***", line))


def compress_transcripts(jobs_dir: Path) -> tuple[int, list[str]]:
    """Gzip and mask every transcript under one build's jobs dir, in place.

    A shard archives its trials for the dispatcher; the transcript is most of a
    trial's size. Returns how many were packed and the ones that could not be.
    """
    packed = 0
    failed: list[str] = []
    if not jobs_dir.is_dir():
        return packed, failed
    for path in sorted(jobs_dir.rglob("*")):
        if path.name not in _GZIP_TRIAL_FILES or not path.is_file():
            continue
        tmp = path.with_name(f".{path.name}.gz.tmp")
        try:
            archive_transcript(path, tmp)
            tmp.replace(path.with_name(path.name + ".gz"))
            path.unlink()
            packed += 1
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            failed.append(f"{path.relative_to(jobs_dir)}: {exc.strerror or exc}")
    return packed, failed


def _copy_trial_files(trial: Path, dest: Path) -> None:
    if not trial.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    gzipped = {f"{name}.gz" for name in _GZIP_TRIAL_FILES}
    for path in trial.rglob("*"):
        if not path.is_file():
            continue
        if path.name == ".harbor-env" or ".harbor-env" in path.parts:
            continue
        target = dest / path.relative_to(trial)
        if path.name in _GZIP_TRIAL_FILES:
            target = target.with_name(target.name + ".gz")
            target.parent.mkdir(parents=True, exist_ok=True)
            archive_transcript(path, target)
            continue
        if path.name in gzipped:
            # Already masked and packed by `compress` (archive/backup).
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            continue
        if path.name not in _TRIAL_FILES and path.suffix != ".patch":
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def backup_run(
    *,
    backup_root: Path,
    suite: str,
    task_ids: list[str],
    report_dir: Path,
    run_folder: str,
    harness_dir: Path,
) -> Path:
    dest = backup_root / suite / run_folder
    dest.mkdir(parents=True, exist_ok=True)
    for name in REPORT_FILES:
        src = report_dir / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    (dest / "tasks.txt").write_text("".join(f"{tid}\n" for tid in task_ids), encoding="utf-8")
    verdicts = harness_dir / "anticheat"
    if verdicts.is_dir():
        shutil.copytree(verdicts, dest / "anticheat", dirs_exist_ok=True)
    from score_results import harbor_task_trials

    for tid in task_ids:
        for index, trial in enumerate(harbor_task_trials(harness_dir, tid), start=1):
            _copy_trial_files(trial, dest / "icode" / tid / f"attempt-{index:02d}")
    return dest


def compress_backup(dest: Path) -> Path:
    """Pack a run folder into ``<folder>.tar.gz`` and remove the loose directory.

    The archive's top entry is the folder name, so extracting recreates the
    same layout. A failed pack deletes the temporary archive and leaves the
    folder in place.
    """
    archive = dest.parent / f"{dest.name}.tar.gz"
    tmp = dest.parent / f".{dest.name}.tar.gz.tmp"
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            tar.add(dest, arcname=dest.name)
        tmp.replace(archive)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    shutil.rmtree(dest)
    return archive


def compress_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="archive_run.py compress", description=compress_transcripts.__doc__)
    ap.add_argument("--jobs-dir", required=True, help="this build's harbor_runs/jenkins-<N>")
    args = ap.parse_args(argv)
    packed, failed = compress_transcripts(Path(args.jobs_dir))
    for item in failed:
        print(f"WARNING: transcript left as it is: {item}")
    print(f"compress: {packed} transcripts gzipped and masked under {args.jobs_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["compress"]:
        return compress_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report-dir", required=True, help="run folder the report phase wrote")
    ap.add_argument("--harness-dir", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--suite", default=os.environ.get("BENCHMARK", "deepswe"))
    ap.add_argument("--backup-root", default="")
    ap.add_argument(
        "--run-folder",
        default="",
        help="name of the backup folder and <name>.tar.gz (default: the report folder's name)",
    )
    args = ap.parse_args(argv)
    report_dir = Path(args.report_dir)
    if not (report_dir / "artifact.json").is_file():
        ap.error(f"no artifact.json in {report_dir}; run the report phase first")
    run_folder = args.run_folder.strip() or report_dir.name
    if "/" in run_folder or run_folder in {".", ".."}:
        ap.error(f"--run-folder must be a plain folder name (got {run_folder!r})")
    ids = [ln.strip() for ln in Path(args.task_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    dest = backup_run(
        backup_root=Path(args.backup_root) if args.backup_root else default_backup_root(),
        suite=args.suite,
        task_ids=ids,
        report_dir=report_dir,
        run_folder=run_folder,
        harness_dir=Path(args.harness_dir),
    )
    print(f"backup {compress_backup(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
