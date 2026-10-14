#!/usr/bin/env python3
"""Strip task deliverables from the iCode runtime Harbor mounts at /opt/icode-host.

icode_input.sh calls this after icode_embed_sandbox_cpython. Only the packaged
runtime under .venv changes; iCode's tracked files stay as shipped.

- tomllib, test/ and idlelib/idle_test leave the sandbox stdlib.
- tomllib comes back as a .pyc-only stub: alembic (iCode's memory store) imports
  it at startup on Python 3.11+, but nothing on iCode's run path parses TOML.
  The stub imports and raises TOMLDecodeError on load/loads.
- zoneinfo stays importable (pydantic imports it at startup) but as .pyc only.
- Vendored tomli and backports.zoneinfo leave every site-packages under .venv.
- --sourceless compiles the sandbox stdlib to legacy .pyc and deletes its .py
  files, so the agent cannot read typing.py, ast.py and their neighbors.

The manifest records two fingerprints of .venv:
- tree_sha256: exact bytes; proves nothing changed the tree after sanitizing.
- runtime_sha256: the same for every clone of one iCode commit on one worker
  image, whatever the workspace path or file times. Compare it across runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_mounts import ICODE_TARGET

SANITIZER_VERSION = "mac-k3d-icode-sanitize-v3"
MANIFEST_NAME = "SANITIZED_MANIFEST.json"
EXIT_STALE = 3
STDLIB_REMOVE = ("tomllib", "test", "idlelib/idle_test")
STDLIB_BYTECODE_ONLY = ("zoneinfo",)
STUB_MARKER = "mac-k3d sanitizer stub"
# No line here may equal a line of the cpython_5 gold patch (the real tomllib).
STDLIB_STUBS = {
    "tomllib": f'''"""{STUB_MARKER}: the real tomllib is a task deliverable and is not mounted."""

TOMLDecodeError = type("TOMLDecodeError", (ValueError,), {{"__module__": "tomllib"}})


def _unavailable(*_args, **_kwargs):
    raise TOMLDecodeError("TOML parsing is not available in the mounted iCode runtime ({STUB_MARKER})")


load = loads = _unavailable
''',
}
_SITE_PACKAGES_RX = r"[/\\]site-packages([/\\]|$)"


def sandbox_python(sandbox: Path) -> Path | None:
    for name in ("python3.13", "python3", "python"):
        path = sandbox / "bin" / name
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def stdlib_dirs(sandbox: Path) -> list[Path]:
    lib = sandbox / "lib"
    if not lib.is_dir():
        return []
    return sorted(p for p in lib.glob("python3.*") if p.is_dir() and not p.is_symlink())


def site_packages_dirs(venv: Path) -> list[Path]:
    found: list[Path] = []
    for root, dirs, _files in os.walk(venv, followlinks=False):
        if "site-packages" in dirs:
            path = Path(root) / "site-packages"
            if not path.is_symlink():
                found.append(path)
            dirs.remove("site-packages")
    return sorted(found)


def _is_tomli(name: str) -> bool:
    """tomli and its dist-info; tomli_w (a writer) stays."""
    return name == "tomli" or name.startswith("tomli-")


def vendored_targets(site: Path) -> list[Path]:
    hits: list[Path] = []
    for root, dirs, _files in os.walk(site, followlinks=False):
        if Path(root).name == "_vendor":
            for name in [d for d in dirs if _is_tomli(d)]:
                hits.append(Path(root) / name)
                dirs.remove(name)
    for entry in site.iterdir():
        if _is_tomli(entry.name):
            hits.append(entry)
    backports = site / "backports"
    if backports.is_dir() and not backports.is_symlink():
        for entry in backports.iterdir():
            if entry.name.startswith("zoneinfo"):
                hits.append(entry)
    return sorted(set(hits))


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _stub_wanted(stdlib: Path) -> bool:
    """tomllib joined the stdlib in 3.11; older runtimes import tomli instead."""
    try:
        minor = int(stdlib.name.split(".", 1)[1])
    except (IndexError, ValueError):
        return False
    return minor >= 11


def is_stub(pkg: Path) -> bool:
    """pkg holds only the compiled sanitizer stub."""
    if not pkg.is_dir() or pkg.is_symlink():
        return False
    names = sorted(p.name for p in pkg.iterdir())
    if names != ["__init__.pyc"]:
        return False
    return STUB_MARKER.encode("utf-8") in (pkg / "__init__.pyc").read_bytes()


class StaleManifestError(RuntimeError):
    """The tree was sanitized by another sanitizer version; its sources are already gone."""


def _compile(python: Path, tree: Path, target: Path, exclude_site_packages: bool) -> None:
    # unchecked-hash keeps file times out of the .pyc; -s/-p record container paths, not the workspace.
    cmd = [
        str(python), "-I", "-m", "compileall", "-b", "-q",
        "--invalidation-mode", "unchecked-hash",
        "-s", str(tree), "-p", ICODE_TARGET,
    ]
    if exclude_site_packages:
        cmd += ["-x", _SITE_PACKAGES_RX]
    cmd.append(str(target))
    # compileall prints errors on stdout; tasks/harness captures stdout as the icode path.
    subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr, check=False)


def _in_site_packages(path: Path, stdlib: Path) -> bool:
    return "site-packages" in path.relative_to(stdlib).parts


def _drop_compiled_sources(root: Path, stdlib: Path) -> tuple[list[Path], list[Path]]:
    """Delete each .py under root that has a legacy .pyc beside it."""
    removed: list[Path] = []
    kept: list[Path] = []
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        here = Path(dirpath)
        if "site-packages" in dirs and here == stdlib:
            dirs.remove("site-packages")
        for name in files:
            if not name.endswith(".py"):
                continue
            src = here / name
            if src.is_symlink() or _in_site_packages(src, stdlib):
                continue
            if src.with_suffix(".pyc").is_file():
                src.unlink()
                removed.append(src)
            else:
                kept.append(src)
    return removed, kept


def tree_sha256(venv: Path) -> str:
    """sha256 over relative paths and contents of every file under .venv (manifest excluded)."""
    rows: list[str] = []
    for dirpath, dirs, files in os.walk(venv, followlinks=False):
        here = Path(dirpath)
        for name in dirs:
            path = here / name
            if path.is_symlink():
                rows.append(f"L {path.relative_to(venv).as_posix()} -> {os.readlink(path)}")
        for name in files:
            path = here / name
            rel = path.relative_to(venv).as_posix()
            if rel == MANIFEST_NAME:
                continue
            if path.is_symlink():
                rows.append(f"L {rel} -> {os.readlink(path)}")
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            rows.append(f"F {rel} {digest.hexdigest()}")
    rows.sort()
    return hashlib.sha256(("\n".join(rows) + "\n").encode("utf-8")).hexdigest()


def _runtime_link(path: Path, tree: Path) -> str:
    target = os.readlink(path)
    if not os.path.isabs(target):
        return target
    if target == str(tree) or target.startswith(str(tree) + os.sep):
        return ICODE_TARGET + target[len(str(tree)):]
    return "<outside-tree>"


# Install bookkeeping that differs per workspace: RECORD hashes the bin/ scripts
# (whose shebang holds the clone path) and uv_cache.json holds a build time.
# Every file a RECORD lists is hashed on its own anyway.
_RUNTIME_SKIP_NAMES = ("RECORD", "uv_cache.json")


def _install_roots(tree: Path) -> list[bytes]:
    """The clone path now, plus the one uv sync wrote into .venv/bin (differs if the tree was copied)."""
    roots = {str(tree)}
    launcher = tree / ".venv" / "bin" / "icode"
    try:
        first = launcher.read_bytes().split(b"\n", 1)[0].decode("utf-8")
    except (OSError, UnicodeDecodeError):
        first = ""
    if first.startswith("#!") and "/.venv/bin/" in first:
        roots.add(first[2:].split("/.venv/bin/", 1)[0].strip())
    return sorted((root.encode("utf-8") for root in roots if root), key=len, reverse=True)


def runtime_sha256(tree: Path) -> str:
    """Like tree_sha256, but the clone path reads as /opt/icode-host.

    pyvenv.cfg, __pycache__, install bookkeeping and links that leave the tree
    are host details the container never uses, so they are left out.
    """
    tree = tree.resolve()
    venv = tree / ".venv"
    roots = _install_roots(tree)
    mounted = ICODE_TARGET.encode("utf-8")
    rows: list[str] = []
    for dirpath, dirs, files in os.walk(venv, followlinks=False):
        here = Path(dirpath)
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in dirs:
            path = here / name
            if path.is_symlink():
                rows.append(f"L {path.relative_to(venv).as_posix()} -> {_runtime_link(path, tree)}")
        for name in files:
            path = here / name
            rel = path.relative_to(venv).as_posix()
            if rel in (MANIFEST_NAME, "pyvenv.cfg") or name in _RUNTIME_SKIP_NAMES:
                continue
            if path.is_symlink():
                rows.append(f"L {rel} -> {_runtime_link(path, tree)}")
                continue
            data = path.read_bytes()
            for root in roots:
                data = data.replace(root, mounted)
            rows.append(f"F {rel} {hashlib.sha256(data).hexdigest()}")
    rows.sort()
    return hashlib.sha256(("\n".join(rows) + "\n").encode("utf-8")).hexdigest()


def _load_manifest(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def sanitize_tree(
    tree: Path, *, sourceless: bool = False, python: Path | None = None, refresh: bool = False
) -> dict | None:
    """Sanitize tree/.venv in place. Returns the manifest, or None when there is no sandbox CPython.

    refresh: the sandbox CPython was just copied again after a stale manifest; keep the
    old removed list (paths outside the sandbox stay gone) but restart the sourceless counts.
    """
    tree = tree.resolve()
    venv = tree / ".venv"
    sandbox = venv / "sandbox-cpython"
    if not sandbox.is_dir():
        return None
    manifest_path = venv / MANIFEST_NAME
    prev = _load_manifest(manifest_path)
    if refresh:
        prev = {"removed": prev.get("removed")} if prev else {}
    elif prev and prev.get("sanitizer") != SANITIZER_VERSION:
        raise StaleManifestError(
            f"{manifest_path} was written by {prev.get('sanitizer') or 'an unknown sanitizer'}, not {SANITIZER_VERSION}; "
            "re-embed the sandbox CPython and sanitize again"
        )
    interp = python or sandbox_python(sandbox)
    if interp is None:
        raise RuntimeError(f"no sandbox python under {sandbox}/bin")
    removed: list[Path] = []
    stubbed: list[Path] = []
    sourceless_removed: list[Path] = []
    kept: list[Path] = []

    stdlibs = stdlib_dirs(sandbox)
    for stdlib in stdlibs:
        for rel in STDLIB_REMOVE:
            path = stdlib / rel
            if rel in STDLIB_STUBS and is_stub(path):
                continue
            if path.exists() or path.is_symlink():
                _remove(path)
                removed.append(path)
        for rel, text in STDLIB_STUBS.items():
            pkg = stdlib / rel
            if not _stub_wanted(stdlib):
                continue
            if not is_stub(pkg):
                pkg.mkdir(parents=True, exist_ok=True)
                (pkg / "__init__.py").write_text(text, encoding="utf-8")
                _compile(interp, tree, pkg, exclude_site_packages=False)
                _dropped, left = _drop_compiled_sources(pkg, stdlib)
                if left or not is_stub(pkg):
                    raise RuntimeError(f"could not compile the {rel} stub in {pkg}")
            stubbed.append(pkg)
        for rel in STDLIB_BYTECODE_ONLY:
            pkg = stdlib / rel
            if not pkg.is_dir():
                continue
            _compile(interp, tree, pkg, exclude_site_packages=False)
            dropped, left = _drop_compiled_sources(pkg, stdlib)
            if left:
                names = ", ".join(p.relative_to(tree).as_posix() for p in left)
                raise RuntimeError(f"could not compile {names}; refusing to leave readable source")
            removed.extend(dropped)
    for site in site_packages_dirs(venv):
        for path in vendored_targets(site):
            _remove(path)
            removed.append(path)
    if sourceless:
        for stdlib in stdlibs:
            _compile(interp, tree, stdlib, exclude_site_packages=True)
            dropped, left = _drop_compiled_sources(stdlib, stdlib)
            sourceless_removed.extend(dropped)
            kept.extend(left)

    prev_removed = prev.get("removed") if isinstance(prev.get("removed"), list) else []
    rels = {p.relative_to(tree).as_posix() for p in removed}
    prev_count = prev.get("sourceless_removed")
    prev_count = prev_count if isinstance(prev_count, int) and not isinstance(prev_count, bool) else 0
    prev_kept = prev.get("sourceless_kept") if isinstance(prev.get("sourceless_kept"), list) else []
    doc = {
        "sanitizer": SANITIZER_VERSION,
        "removed": sorted(rels | {str(item) for item in prev_removed}),
        "stubbed": sorted(p.relative_to(tree).as_posix() for p in stubbed),
        "sourceless": bool(sourceless or prev.get("sourceless") is True),
        "sourceless_removed": prev_count + len(sourceless_removed),
        "sourceless_kept": (
            sorted(p.relative_to(tree).as_posix() for p in kept) if sourceless else sorted(str(i) for i in prev_kept)
        ),
        "tree_sha256": tree_sha256(venv),
        "runtime_sha256": runtime_sha256(tree),
    }
    text = json.dumps(doc, indent=2) + "\n"
    if not manifest_path.is_file() or manifest_path.read_text(encoding="utf-8") != text:
        manifest_path.write_text(text, encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strip task deliverables from the mounted iCode runtime")
    parser.add_argument("--tree", required=True, help="iCode host tree (contains .venv/sandbox-cpython)")
    parser.add_argument("--sourceless", action="store_true", help="compile the stdlib and delete its .py files")
    parser.add_argument("--python", default="", help="interpreter for compileall (default: the sandbox one)")
    parser.add_argument("--refresh", action="store_true", help="the sandbox CPython was re-embedded after exit 3")
    args = parser.parse_args(argv)
    try:
        doc = sanitize_tree(
            Path(args.tree),
            sourceless=args.sourceless,
            python=Path(args.python) if args.python else None,
            refresh=args.refresh,
        )
    except StaleManifestError as exc:
        print(f"icode sanitize: {exc}", file=sys.stderr)
        return EXIT_STALE
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: icode sanitize: {exc}", file=sys.stderr)
        return 1
    if doc is None:
        print(f"icode sanitize: no .venv/sandbox-cpython under {args.tree}; nothing to do", file=sys.stderr)
        return 0
    print(
        "OK icode sanitize removed={n} sourceless={s} sourceless_removed={c} kept={k} "
        "tree_sha256={h} runtime_sha256={r}".format(
            n=len(doc["removed"]),
            s=str(doc["sourceless"]).lower(),
            c=doc["sourceless_removed"],
            k=len(doc["sourceless_kept"]),
            h=doc["tree_sha256"],
            r=doc["runtime_sha256"],
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
