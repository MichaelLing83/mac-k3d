#!/usr/bin/env python3
"""The worker's store of prepared harness trees (iCode, JiuwenSwarm).

A bundle is one gzipped tar: `bundle.json` (which harness, the input it was
built from, the platform, the recipe) and `tree/`, the prepared tree that a
trial mounts read-only. Its name in the store is the sha256 of the tarball, so
a build that is told which sha256 it needs can never pick up another one.

    $MAC_K3D_SHARE/harness-store/<harness>/<bundle sha256>/
        bundle.json  tree/  entry.json  READY  last_used  [bundle.tar.gz]
    $MAC_K3D_SHARE/harness-store/<harness>/by-input/<input key>   (a bundle sha256)

bundle.tar.gz is kept only where a bundle is handed on (`put --keep-bundle`,
the dispatcher's prepare); a shard keeps the tree alone.

entry.json adds the bundle's sha256 and a digest of the unpacked tree; `get`
recomputes that digest, so a tree that changed on disk is never mounted. The
tree is made read-only once unpacked.

    harness_store.py pack --tree DIR --meta FILE --out FILE        prints the sha256
    harness_store.py put [--store DIR] --harness NAME --bundle FILE [--expect-sha S] [--key K] [--keep-bundle]
    harness_store.py get [--store DIR] --harness NAME (--sha S | --key K) [--no-verify]
    harness_store.py key --harness NAME --identity FILE --platform P --recipe R
    harness_store.py sha256 FILE
    harness_store.py evict [--store DIR] --harness NAME [--keep N] [--idle-hours H]

--store defaults to default_store(): $MAC_K3D_HARNESS_STORE, else
$MAC_K3D_SHARE/harness-store with _common.sh's default share.

`put` and `get` print the entry directory. Exit codes: 0 ok, 1 not in the store,
2 a sha256 or layout mismatch, 3 a stored tree that no longer matches its digest
(the entry is removed).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import time
from pathlib import Path

SCHEMA = "mac-k3d-harness-bundle-v1"
META = "bundle.json"
TREE = "tree"
ENTRY = "entry.json"
BUNDLE = "bundle.tar.gz"
READY = "READY"
LAST_USED = "last_used"
BY_INPUT = "by-input"
LOCK = ".lock"
KEEP = 6
IDLE_HOURS = 24.0
CHUNK = 1024 * 1024


class StoreError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root: Path):
    """Every path under root, sorted, without following links."""
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        here = Path(dirpath)
        names = sorted(dirs + files)
        for name in names:
            yield here / name


def tree_digest(root: Path) -> str:
    """sha256 over each path's type, executable bit, and content or link target."""
    digest = hashlib.sha256()
    for path in _walk(root):
        rel = path.relative_to(root).as_posix()
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            row = f"L {rel} {os.readlink(path)}\n".encode()
            digest.update(row)
        elif stat.S_ISDIR(st.st_mode):
            digest.update(f"D {rel}\n".encode())
        elif stat.S_ISREG(st.st_mode):
            exe = "x" if st.st_mode & 0o111 else "-"
            digest.update(f"F {rel} {exe} {st.st_size}\n".encode())
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def pack(tree: Path, meta: dict, out: Path) -> str:
    """bundle.json and tree/ in a gzipped tar: sorted, owner 0, no gzip timestamp.

    File times are kept so the bytecode a build compiled still matches its source.
    """
    if not tree.is_dir():
        raise StoreError(f"{tree} is not a directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")

    def normal(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tmp.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=1, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            body = (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode()
            info = tarfile.TarInfo(META)
            info.size = len(body)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(body))
            tar.add(tree, arcname=TREE, recursive=False, filter=normal)
            for path in _walk(tree):
                arc = f"{TREE}/{path.relative_to(tree).as_posix()}"
                tar.add(path, arcname=arc, recursive=False, filter=normal)
    tmp.replace(out)
    return file_sha256(out)


def read_meta(bundle: Path) -> dict:
    """bundle.json of a bundle tarball, or StoreError when it is not one."""
    try:
        with tarfile.open(bundle, mode="r:gz") as tar:
            member = tar.getmember(META)
            fh = tar.extractfile(member)
            doc = json.loads(fh.read().decode("utf-8")) if fh else {}
    except (tarfile.TarError, KeyError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StoreError(f"{bundle} is not a harness bundle ({exc})") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise StoreError(f"{bundle} is not a {SCHEMA} bundle")
    return doc


def _extract(bundle: Path, dest: Path) -> None:
    with tarfile.open(bundle, mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if name != META and name != TREE and not name.startswith(TREE + "/"):
                raise StoreError(f"{bundle}: unexpected member {name}")
        if hasattr(tarfile, "tar_filter"):
            tar.extractall(dest, filter="tar")
        else:  # pragma: no cover - Python without extraction filters
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise StoreError(f"{bundle}: unsafe member {member.name}")
            tar.extractall(dest)


def _read_only(root: Path) -> None:
    for path in [root, *_walk(root)]:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            continue
        os.chmod(path, stat.S_IMODE(st.st_mode) & ~0o222)


def _writable(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *_walk(root)]:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            continue
        os.chmod(path, stat.S_IMODE(st.st_mode) | 0o200)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        _writable(path)
        shutil.rmtree(path)


@contextlib.contextmanager
def _locked(harness_dir: Path):
    harness_dir.mkdir(parents=True, exist_ok=True)
    with (harness_dir / LOCK).open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _hex64(value: str) -> str:
    value = value.strip().lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise StoreError(f"not a sha256: {value!r}")
    return value


def _entry(store: Path, harness: str, sha: str) -> Path:
    if not harness or "/" in harness or harness.startswith("."):
        raise StoreError(f"not a harness name: {harness!r}")
    return store / harness / _hex64(sha)


def _load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _touch(entry: Path) -> None:
    (entry / LAST_USED).write_text(f"{time.time():.0f}\n", encoding="utf-8")


def _index(store: Path, harness: str, key: str, sha: str) -> None:
    index = store / harness / BY_INPUT
    index.mkdir(parents=True, exist_ok=True)
    tmp = index / f".{key}.{os.getpid()}"
    tmp.write_text(sha + "\n", encoding="utf-8")
    tmp.replace(index / key)


def _keep(bundle: Path, entry: Path) -> None:
    """The tarball next to its tree, for a dispatcher that hands it to shards."""
    dest = entry / BUNDLE
    if dest.is_file():
        return
    tmp = entry / f".{BUNDLE}.{os.getpid()}"
    try:
        os.link(bundle, tmp)
    except OSError:
        shutil.copyfile(bundle, tmp)
    os.chmod(tmp, 0o444)
    tmp.replace(dest)


def put(store: Path, harness: str, bundle: Path, expect_sha: str = "", key: str = "", keep_bundle: bool = False) -> Path:
    """Unpack bundle into the store (once) and return its entry directory."""
    sha = file_sha256(bundle)
    expect = _hex64(expect_sha) if expect_sha.strip() else ""
    if expect and sha != expect:
        raise StoreError(f"bundle {bundle} is sha256 {sha}, not the {expect} this build needs")
    meta = read_meta(bundle)
    if meta.get("harness") != harness:
        raise StoreError(f"bundle {bundle} is for harness {meta.get('harness')!r}, not {harness!r}")
    if key:
        key = _hex64(key)
    harness_dir = store / harness
    entry = _entry(store, harness, sha)
    if (entry / READY).is_file():
        try:
            get(store, harness, sha)
        except StoreError as exc:
            if exc.code != 3:
                raise
            print(f"harness store: {exc}; unpacking again", file=sys.stderr)
    with _locked(harness_dir):
        if (entry / READY).is_file():
            _touch(entry)
            if keep_bundle:
                _keep(bundle, entry)
        else:
            _remove(entry)
            tmp = Path(tempfile.mkdtemp(prefix=f".{sha[:12]}.", dir=harness_dir))
            os.chmod(tmp, 0o755)
            try:
                _extract(bundle, tmp)
                tree = tmp / TREE
                if not tree.is_dir():
                    raise StoreError(f"bundle {bundle} has no {TREE}/")
                doc = {
                    "bundle_sha256": sha,
                    "tree_sha256": tree_digest(tree),
                    "harness": harness,
                    "platform": meta.get("platform", ""),
                    "stored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "stored_by": f"{os.environ.get('JOB_NAME') or 'local'}#{os.environ.get('BUILD_NUMBER') or '0'}",
                }
                (tmp / ENTRY).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
                _read_only(tree)
                if keep_bundle:
                    _keep(bundle, tmp)
                (tmp / READY).write_text(sha + "\n", encoding="utf-8")
                _touch(tmp)
                tmp.rename(entry)
            except BaseException:
                _remove(tmp)
                raise
        if key:
            _index(store, harness, key, sha)
    return entry


def get(store: Path, harness: str, sha: str = "", key: str = "", verify: bool = True) -> Path:
    """The READY entry for sha (or for the bundle an input key points at)."""
    if key and not sha:
        ref = store / harness / BY_INPUT / _hex64(key)
        sha = ref.read_text(encoding="utf-8").strip() if ref.is_file() else ""
        if not sha:
            raise StoreError(f"no {harness} bundle for input {key[:16]}", code=1)
    entry = _entry(store, harness, sha)
    sha = entry.name
    with _locked(store / harness):
        if not (entry / READY).is_file():
            raise StoreError(f"{harness} bundle {sha[:16]} is not in {store}", code=1)
        doc = _load(entry / ENTRY)
        if doc.get("bundle_sha256") != sha or (entry / READY).read_text().strip() != sha:
            _remove(entry)
            raise StoreError(f"{entry} records another bundle; removed", code=3)
        _touch(entry)
    if verify:
        got = tree_digest(entry / TREE)
        if got != doc.get("tree_sha256"):
            with _locked(store / harness):
                _remove(entry)
            raise StoreError(
                f"{entry}/{TREE} changed on disk (digest {got[:16]}, stored {str(doc.get('tree_sha256'))[:16]}); removed",
                code=3,
            )
    return entry


def input_key(harness: str, identity: dict, platform: str, recipe: str) -> str:
    raw = json.dumps(
        {"harness": harness, "identity": identity, "platform": platform, "recipe": recipe},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def evict(store: Path, harness: str, keep: int = KEEP, idle_hours: float = IDLE_HOURS, now: float | None = None) -> list[str]:
    """Remove the least recently used entries beyond `keep`; never one used within idle_hours."""
    harness_dir = store / harness
    if not harness_dir.is_dir():
        return []
    now = time.time() if now is None else now
    removed: list[str] = []
    with _locked(harness_dir):
        entries = []
        for path in harness_dir.iterdir():
            if path.name.startswith(".") or path.name == BY_INPUT or not path.is_dir():
                continue
            try:
                used = float((path / LAST_USED).read_text().strip())
            except (OSError, ValueError):
                used = path.stat().st_mtime
            entries.append((used, path))
        entries.sort(reverse=True)
        for used, path in entries[keep:]:
            if now - used < idle_hours * 3600:
                continue
            _remove(path)
            removed.append(path.name)
        index = harness_dir / BY_INPUT
        if index.is_dir():
            for ref in index.iterdir():
                try:
                    target = ref.read_text().strip()
                except OSError:
                    continue
                if not (harness_dir / target / READY).is_file():
                    ref.unlink(missing_ok=True)
    return removed


def default_store() -> Path:
    """The store _common.sh names: $MAC_K3D_HARNESS_STORE, else $MAC_K3D_SHARE/harness-store."""
    if os.environ.get("MAC_K3D_HARNESS_STORE"):
        return Path(os.environ["MAC_K3D_HARNESS_STORE"])
    share = os.environ.get("MAC_K3D_SHARE")
    if not share:
        data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        share = str(Path(data) / "mac-k3d")
    return Path(share) / "harness-store"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The worker's store of prepared harness trees")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--tree", required=True)
    p.add_argument("--meta", required=True, help="JSON file: harness, identity, platform, recipe, ...")
    p.add_argument("--out", required=True)
    p = sub.add_parser("put")
    p.add_argument("--store", type=Path, default=None)
    p.add_argument("--harness", required=True)
    p.add_argument("--bundle", required=True)
    p.add_argument("--expect-sha", default="")
    p.add_argument("--key", default="")
    p.add_argument("--keep-bundle", action="store_true", help="keep the tarball in the entry as bundle.tar.gz")
    p = sub.add_parser("get")
    p.add_argument("--store", type=Path, default=None)
    p.add_argument("--harness", required=True)
    p.add_argument("--sha", default="")
    p.add_argument("--key", default="")
    p.add_argument("--no-verify", action="store_true")
    p = sub.add_parser("key")
    p.add_argument("--harness", required=True)
    p.add_argument("--identity", required=True, help="JSON file")
    p.add_argument("--platform", required=True)
    p.add_argument("--recipe", required=True)
    p = sub.add_parser("meta")
    p.add_argument("bundle")
    p = sub.add_parser("sha256")
    p.add_argument("file")
    p = sub.add_parser("evict")
    p.add_argument("--store", type=Path, default=None)
    p.add_argument("--harness", required=True)
    p.add_argument("--keep", type=int, default=int(os.environ.get("MAC_K3D_HARNESS_STORE_KEEP") or KEEP))
    p.add_argument("--idle-hours", type=float, default=IDLE_HOURS)
    args = parser.parse_args(argv)
    if hasattr(args, "store") and args.store is None:
        args.store = default_store()
    try:
        if args.cmd == "pack":
            meta = _load(Path(args.meta))
            if not meta.get("harness"):
                raise StoreError(f"{args.meta} names no harness")
            meta["schema"] = SCHEMA
            print(pack(Path(args.tree), meta, Path(args.out)))
        elif args.cmd == "put":
            print(put(args.store, args.harness, Path(args.bundle), args.expect_sha, args.key, args.keep_bundle))
        elif args.cmd == "get":
            if not args.sha and not args.key:
                raise StoreError("get needs --sha or --key")
            print(get(args.store, args.harness, args.sha, args.key, verify=not args.no_verify))
        elif args.cmd == "key":
            identity = _load(Path(args.identity))
            if not identity:
                raise StoreError(f"{args.identity} is empty")
            print(input_key(args.harness, identity, args.platform, args.recipe))
        elif args.cmd == "meta":
            print(json.dumps(read_meta(Path(args.bundle)), indent=2, sort_keys=True))
        elif args.cmd == "sha256":
            print(file_sha256(Path(args.file)))
        elif args.cmd == "evict":
            for name in evict(args.store, args.harness, args.keep, args.idle_hours):
                print(f"harness store: removed {args.harness} bundle {name[:16]}", file=sys.stderr)
    except StoreError as exc:
        print(f"harness store: {exc}", file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
