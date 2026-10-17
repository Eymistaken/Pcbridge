#!/usr/bin/env python3
"""`pcbridge setup` restarts a daemon whose code differs from the installed
code, even when the version string is the same.

Unreleased builds keep the last release's version, so a version-only check
said "already running" after a wheel from a newer commit was installed, and
setup's readiness check then asked the old process (2026-10-17, twice).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import __version__  # noqa: E402
from pcbridge import build as buildlib  # noqa: E402
from pcbridge.cli import ops  # noqa: E402


class FingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sub").mkdir()
        (self.root / "a.py").write_text("A = 1\n")
        (self.root / "sub" / "b.py").write_text("B = 2\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_identical_reinstall_matches(self) -> None:
        before = buildlib.fingerprint(self.root)
        time.sleep(0.01)
        # pip --force-reinstall rewrites every file: new mtimes, same bytes.
        (self.root / "a.py").write_text("A = 1\n")
        os.utime(self.root / "sub" / "b.py")
        self.assertEqual(buildlib.fingerprint(self.root), before)

    def test_changed_added_or_renamed_files_do_not(self) -> None:
        base = buildlib.fingerprint(self.root)
        (self.root / "a.py").write_text("A = 2\n")
        changed = buildlib.fingerprint(self.root)
        self.assertNotEqual(changed, base)
        (self.root / "c.py").write_text("")
        added = buildlib.fingerprint(self.root)
        self.assertNotEqual(added, changed)
        (self.root / "c.py").rename(self.root / "d.py")
        self.assertNotEqual(buildlib.fingerprint(self.root), added)

    def test_bytecode_caches_are_ignored(self) -> None:
        base = buildlib.fingerprint(self.root)
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00")
        self.assertEqual(buildlib.fingerprint(self.root), base)


class SetupRestartDecisionTests(unittest.TestCase):
    def test_same_version_but_other_code_is_restarted(self) -> None:
        with patch.object(buildlib, "fingerprint", return_value="new"):
            self.assertFalse(ops.runs_installed_code({"version": __version__, "build": "old"}))
            self.assertTrue(ops.runs_installed_code({"version": __version__, "build": "new"}))

    def test_a_daemon_without_a_build_is_restarted(self) -> None:
        self.assertFalse(ops.runs_installed_code({"version": __version__}))

    def test_other_version_or_no_daemon_is_restarted(self) -> None:
        fp = buildlib.fingerprint()
        self.assertFalse(ops.runs_installed_code({"version": "0.0.0", "build": fp}))
        self.assertFalse(ops.runs_installed_code("connection refused"))
        self.assertTrue(ops.runs_installed_code({"version": __version__, "build": fp}))



class StampTests(unittest.TestCase):
    def test_stamp_carries_the_build(self) -> None:
        from pcbridge import daemon
        from pcbridge.cli import install

        with tempfile.TemporaryDirectory() as raw, \
                patch.object(install, "version_stamp_path", return_value=Path(raw) / "s"):
            install.write_version_stamp()
            text = (Path(raw) / "s").read_text()
        self.assertEqual(daemon.stamp_build(text), buildlib.fingerprint())
        self.assertIsNone(daemon.stamp_build("2.5.0 user 1790799767"))
        self.assertIsNone(daemon.stamp_build("2.0.0 new"))


if __name__ == "__main__":
    unittest.main()
