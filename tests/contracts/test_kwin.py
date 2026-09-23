"""KDE Plasma: the helper's KWin screenshot authorization and capture report."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge.desktop import kwin  # noqa: E402


class HelperEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.binary = self.root / "pcbridge-native"
        self.binary.write_text("#!/bin/sh\n", encoding="utf-8")
        self.binary.chmod(0o755)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_entry_names_the_helper_and_only_the_screenshot_interface(self) -> None:
        text = kwin.helper_entry(self.binary)
        self.assertIn(f"Exec={self.binary.resolve()}\n", text)
        self.assertIn("X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2\n", text)
        self.assertIn("NoDisplay=true\n", text)

    def test_install_is_idempotent_and_found_by_the_same_search_kwin_runs(self) -> None:
        apps = self.root / "data" / "applications"
        path, changed = kwin.install_helper_entry(self.binary, dest_dir=apps)
        self.assertTrue(changed)
        self.assertEqual(kwin.install_helper_entry(self.binary, dest_dir=apps), (path, False))
        self.assertEqual(kwin.helper_authorized(self.binary, dirs=[self.root / "data"]), path)

    def test_an_entry_for_another_program_or_interface_does_not_count(self) -> None:
        apps = self.root / "data" / "applications"
        apps.mkdir(parents=True)
        other = self.root / "other"
        other.write_text("", encoding="utf-8")
        (apps / "pcbridge-a.desktop").write_text(kwin.helper_entry(other), encoding="utf-8")
        (apps / "pcbridge-b.desktop").write_text(
            kwin.helper_entry(self.binary).replace("ScreenShot2", "Nothing"), encoding="utf-8")
        # Only pcbridge*.desktop is read, so an authorizing file named
        # otherwise is not found (the native helper searches the same way).
        (apps / "else.desktop").write_text(kwin.helper_entry(self.binary), encoding="utf-8")
        self.assertIsNone(kwin.helper_authorized(self.binary, dirs=[self.root / "data"]))

    def test_the_search_order_is_data_home_then_data_dirs(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/h", "XDG_DATA_DIRS": "/a:/b"}):
            self.assertEqual(kwin.data_dirs(), [Path("/h"), Path("/a"), Path("/b")])


if __name__ == "__main__":
    unittest.main()
