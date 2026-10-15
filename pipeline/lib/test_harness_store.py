#!/usr/bin/env python3
"""The harness store: a bundle is unpacked once, found again only by its sha256
(or the input it was built from), checked against its digest on every use, and
evicted only when it has not been used for a day."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

import harness_store as hs  # noqa: E402


def make_tree(root: Path) -> Path:
    tree = root / "src"
    (tree / "venv" / "bin").mkdir(parents=True)
    (tree / "venv" / "lib").mkdir()
    (tree / "venv" / "lib" / "mod.py").write_text("x = 1\n")
    tool = tree / "venv" / "bin" / "tool"
    tool.write_text("#!/bin/sh\necho hi\n")
    tool.chmod(0o755)
    os.symlink("/opt/jiuwenswarm-host/python/bin/python3.11", tree / "venv" / "bin" / "python")
    os.symlink("lib", tree / "venv" / "lib64")
    os.link(tree / "venv" / "lib" / "mod.py", tree / "venv" / "lib" / "same.py")
    return tree


def meta(**extra) -> dict:
    doc = {
        "harness": "jiuwenswarm",
        "identity": {"mode": "git", "sha": "a" * 40},
        "platform": "linux/amd64",
        "recipe": "jiuwenswarm-v1",
    }
    doc.update(extra)
    return doc


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tree = make_tree(self.tmp)

    def tearDown(self):
        hs._remove(self.tmp)

    def test_pack_is_reproducible_and_records_meta(self):
        a = hs.pack(self.tree, {**meta(), "schema": hs.SCHEMA}, self.tmp / "a" / "bundle.tar.gz")
        b = hs.pack(self.tree, {**meta(), "schema": hs.SCHEMA}, self.tmp / "b" / "other-name.tar.gz")
        self.assertEqual(a, b)
        self.assertEqual(hs.read_meta(self.tmp / "a" / "bundle.tar.gz")["harness"], "jiuwenswarm")

    def test_tree_digest_sees_content_mode_and_links(self):
        base = hs.tree_digest(self.tree)
        (self.tree / "venv" / "lib" / "mod.py").write_text("x = 2\n")
        changed = hs.tree_digest(self.tree)
        self.assertNotEqual(base, changed)
        (self.tree / "venv" / "lib" / "mod.py").write_text("x = 1\n")
        self.assertEqual(hs.tree_digest(self.tree), base)
        (self.tree / "venv" / "bin" / "tool").chmod(0o644)
        self.assertNotEqual(hs.tree_digest(self.tree), base)

    def test_read_meta_refuses_a_plain_tarball(self):
        plain = self.tmp / "plain.tar.gz"
        subprocess.run(["tar", "-czf", str(plain), "-C", str(self.tree), "."], check=True)
        with self.assertRaises(hs.StoreError):
            hs.read_meta(plain)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        self.tree = make_tree(self.tmp)
        self.bundle = self.tmp / "bundle.tar.gz"
        self.sha = hs.pack(self.tree, {**meta(), "schema": hs.SCHEMA}, self.bundle)

    def tearDown(self):
        hs._remove(self.tmp)

    def test_put_unpacks_once_read_only_and_get_finds_it(self):
        entry = hs.put(self.store, "jiuwenswarm", self.bundle, expect_sha=self.sha)
        self.assertEqual(entry.name, self.sha)
        tree = entry / hs.TREE
        self.assertEqual(os.readlink(tree / "venv" / "bin" / "python"), "/opt/jiuwenswarm-host/python/bin/python3.11")
        self.assertTrue(os.access(tree / "venv" / "bin" / "tool", os.X_OK))
        self.assertFalse(stat.S_IMODE((tree / "venv" / "lib" / "mod.py").stat().st_mode) & 0o222)
        self.assertFalse(stat.S_IMODE((tree / "venv").stat().st_mode) & 0o222)
        self.assertEqual(hs.tree_digest(tree), hs.tree_digest(self.tree))
        before = (entry / hs.ENTRY).stat().st_mtime_ns
        again = hs.put(self.store, "jiuwenswarm", self.bundle)
        self.assertEqual(again, entry)
        self.assertEqual((entry / hs.ENTRY).stat().st_mtime_ns, before, "a second put unpacked again")
        self.assertEqual(hs.get(self.store, "jiuwenswarm", sha=self.sha), entry)

    def test_wrong_sha_and_wrong_harness_are_refused(self):
        with self.assertRaises(hs.StoreError) as err:
            hs.put(self.store, "jiuwenswarm", self.bundle, expect_sha="b" * 64)
        self.assertEqual(err.exception.code, 2)
        with self.assertRaises(hs.StoreError):
            hs.put(self.store, "icode", self.bundle)
        self.assertFalse((self.store / "jiuwenswarm" / self.sha).exists())

    def test_get_misses_another_sha(self):
        hs.put(self.store, "jiuwenswarm", self.bundle)
        with self.assertRaises(hs.StoreError) as err:
            hs.get(self.store, "jiuwenswarm", sha="c" * 64)
        self.assertEqual(err.exception.code, 1)
        with self.assertRaises(hs.StoreError):
            hs.get(self.store, "jiuwenswarm", sha="../../etc")

    def test_by_input_key_points_at_the_bundle(self):
        key = hs.input_key("jiuwenswarm", meta()["identity"], "linux/amd64", "jiuwenswarm-v1")
        other = hs.input_key("jiuwenswarm", {"mode": "git", "sha": "b" * 40}, "linux/amd64", "jiuwenswarm-v1")
        arm = hs.input_key("jiuwenswarm", meta()["identity"], "linux/arm64", "jiuwenswarm-v1")
        self.assertNotEqual(key, other)
        self.assertNotEqual(key, arm)
        with self.assertRaises(hs.StoreError) as err:
            hs.get(self.store, "jiuwenswarm", key=key)
        self.assertEqual(err.exception.code, 1)
        entry = hs.put(self.store, "jiuwenswarm", self.bundle, key=key)
        self.assertEqual(hs.get(self.store, "jiuwenswarm", key=key), entry)
        with self.assertRaises(hs.StoreError):
            hs.get(self.store, "jiuwenswarm", key=other)

    def test_a_changed_tree_is_removed_not_used(self):
        entry = hs.put(self.store, "jiuwenswarm", self.bundle)
        target = entry / hs.TREE / "venv" / "lib" / "mod.py"
        target.parent.chmod(0o755)
        target.chmod(0o644)
        target.write_text("x = 'tampered'\n")
        with self.assertRaises(hs.StoreError) as err:
            hs.get(self.store, "jiuwenswarm", sha=self.sha)
        self.assertEqual(err.exception.code, 3)
        self.assertFalse(entry.exists())
        again = hs.put(self.store, "jiuwenswarm", self.bundle)
        self.assertEqual(hs.get(self.store, "jiuwenswarm", sha=self.sha), again)

    def test_only_a_dispatcher_put_keeps_the_tarball(self):
        entry = hs.put(self.store, "jiuwenswarm", self.bundle)
        self.assertFalse((entry / hs.BUNDLE).exists(), "a shard's store keeps only the tree")
        again = hs.put(self.store, "jiuwenswarm", self.bundle, keep_bundle=True)
        self.assertEqual(again, entry)
        kept = entry / hs.BUNDLE
        self.assertEqual(hs.file_sha256(kept), self.sha)
        self.assertFalse(stat.S_IMODE(kept.stat().st_mode) & 0o222)
        self.assertEqual(hs.get(self.store, "jiuwenswarm", sha=self.sha), entry)

    def test_put_repairs_a_tampered_entry(self):
        entry = hs.put(self.store, "jiuwenswarm", self.bundle)
        target = entry / hs.TREE / "venv" / "bin" / "tool"
        target.chmod(0o644)
        again = hs.put(self.store, "jiuwenswarm", self.bundle)
        self.assertTrue(os.access(again / hs.TREE / "venv" / "bin" / "tool", os.X_OK))

    def test_an_unfinished_entry_is_never_used(self):
        entry = self.store / "jiuwenswarm" / self.sha
        (entry / hs.TREE).mkdir(parents=True)
        with self.assertRaises(hs.StoreError) as err:
            hs.get(self.store, "jiuwenswarm", sha=self.sha)
        self.assertEqual(err.exception.code, 1)
        self.assertEqual(hs.put(self.store, "jiuwenswarm", self.bundle), entry)
        self.assertTrue((entry / hs.READY).is_file())

    def test_concurrent_puts_unpack_once(self):
        results: list[Path] = []
        errors: list[BaseException] = []

        def worker():
            try:
                results.append(hs.put(self.store, "jiuwenswarm", self.bundle))
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        leftovers = [p.name for p in (self.store / "jiuwenswarm").iterdir() if p.name.startswith(".") and p.name != hs.LOCK]
        self.assertEqual(leftovers, [])

    def test_evict_keeps_recent_and_never_one_used_today(self):
        now = time.time()
        entries = []
        for n in range(4):
            tree = self.tmp / f"t{n}"
            tree.mkdir()
            (tree / "f").write_text(str(n))
            bundle = self.tmp / f"b{n}.tar.gz"
            hs.pack(tree, {**meta(), "schema": hs.SCHEMA, "n": n}, bundle)
            key = hs.input_key("jiuwenswarm", {"n": n}, "linux/amd64", "r")
            entries.append((hs.put(self.store, "jiuwenswarm", bundle, key=key), key))
        for n, (entry, _) in enumerate(entries):
            age = 48 * 3600 if n < 3 else 3600
            (entry / hs.LAST_USED).write_text(f"{now - age - n:.0f}\n")
        removed = hs.evict(self.store, "jiuwenswarm", keep=1, idle_hours=24, now=now)
        self.assertEqual(sorted(removed), sorted(e.name for e, _ in entries[:3]))
        self.assertTrue(entries[3][0].is_dir())
        self.assertFalse((self.store / "jiuwenswarm" / hs.BY_INPUT / entries[0][1]).exists())
        self.assertTrue((self.store / "jiuwenswarm" / hs.BY_INPUT / entries[3][1]).exists())
        (entries[3][0] / hs.LAST_USED).write_text(f"{now - 60:.0f}\n")
        self.assertEqual(hs.evict(self.store, "jiuwenswarm", keep=0, idle_hours=24, now=now), [])

    def test_cli_round_trip(self):
        script = str(LIB / "harness_store.py")
        meta_file = self.tmp / "meta.json"
        meta_file.write_text(json.dumps(meta()))
        out = self.tmp / "cli.tar.gz"
        sha = subprocess.run(
            [sys.executable, script, "pack", "--tree", str(self.tree), "--meta", str(meta_file), "--out", str(out)],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(sha, self.sha)
        put = subprocess.run(
            [sys.executable, script, "put", "--store", str(self.store), "--harness", "jiuwenswarm",
             "--bundle", str(out), "--expect-sha", sha],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(Path(put).name, sha)
        miss = subprocess.run(
            [sys.executable, script, "get", "--store", str(self.store), "--harness", "jiuwenswarm", "--sha", "d" * 64],
            capture_output=True, text=True,
        )
        self.assertEqual(miss.returncode, 1)
        bad = subprocess.run(
            [sys.executable, script, "put", "--store", str(self.store), "--harness", "jiuwenswarm",
             "--bundle", str(out), "--expect-sha", "e" * 64],
            capture_output=True, text=True,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertIn("not the", bad.stderr)

    def test_the_default_store_is_the_one_the_stages_use(self):
        script = str(LIB / "harness_store.py")
        share = self.store.parent
        hs.put(share / "harness-store", "jiuwenswarm", self.bundle)
        env = {k: v for k, v in os.environ.items() if k not in ("MAC_K3D_HARNESS_STORE", "MAC_K3D_SHARE")}
        # The Jenkins shard's store check passes no --store.
        hit = subprocess.run(
            [sys.executable, script, "get", "--harness", "jiuwenswarm", "--sha", self.sha],
            env={**env, "MAC_K3D_SHARE": str(share)}, capture_output=True, text=True,
        )
        self.assertEqual(hit.returncode, 0, hit.stderr)
        env_file = self.tmp / "empty.env"
        env_file.write_text("")
        env_file.chmod(0o600)
        stages = subprocess.run(
            ["bash", "-c", 'source "$1/pipeline/stages/_common.sh" >/dev/null && printf "%s\\n" "$MAC_K3D_SHARE/harness-store"',
             "_", str(LIB.parent.parent)],
            env={**env, "HOME": str(self.tmp), "XDG_DATA_HOME": str(self.tmp / "data"),
                 "MAC_K3D_EVAL_WORKDIR": str(self.tmp / "wd"), "MAC_K3D_ENV_FILE": str(env_file)},
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp), "XDG_DATA_HOME": str(self.tmp / "data")}, clear=False):
            for key in ("MAC_K3D_HARNESS_STORE", "MAC_K3D_SHARE"):
                os.environ.pop(key, None)
            self.assertEqual(str(hs.default_store()), stages)
            os.environ["MAC_K3D_HARNESS_STORE"] = str(self.store)
            self.assertEqual(hs.default_store(), self.store)


if __name__ == "__main__":
    unittest.main()
