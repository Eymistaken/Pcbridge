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


class SetupEntryTests(unittest.TestCase):
    """`pcbridge setup` on Plasma: the helper's entry and the lock entry."""

    def test_setup_installs_both_and_uninstall_moves_both_aside(self) -> None:
        from pcbridge.cli import install as inst

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            binary = root / "pcbridge-native"
            binary.write_text("", encoding="utf-8")
            apps = root / "applications"
            with mock.patch.object(inst, "applications_dir", return_value=apps), \
                    mock.patch("pcbridge.native.discover_native_binary", return_value=binary), \
                    mock.patch.object(inst, "launcher_path", return_value=Path("/usr/bin/pcbridge")):
                first = inst.install_kde_entries(object())
                again = inst.install_kde_entries(object())
                self.assertTrue(all("installed" in line for line in first), first)
                self.assertTrue(all("up to date" in line for line in again), again)
                self.assertEqual(kwin.helper_authorized(binary, dirs=[root]),
                                 apps / kwin.HELPER_ENTRY)
                lock = (apps / inst.LOCK_ENTRY).read_text(encoding="utf-8")
                self.assertIn("Exec=/usr/bin/pcbridge lock\n", lock)
                self.assertNotIn("X-KDE-Shortcuts", lock, "no shortcut is set for the user")

                backup = mock.Mock()
                inst.remove_kde_entries(backup)
                moved = sorted(call.args[0].name for call in backup.move.call_args_list)
                self.assertEqual(moved, sorted([kwin.HELPER_ENTRY, inst.LOCK_ENTRY]))

    def test_a_missing_helper_is_said_not_raised(self) -> None:
        from pcbridge.cli import install as inst
        from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode

        missing = DesktopError(code=ErrorCode.NATIVE_NOT_FOUND, message="x",
                               category=ErrorCategory.IPC, retryable=False,
                               suggested_action="configure_native_binary")
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.object(inst, "applications_dir", return_value=Path(raw)), \
                mock.patch("pcbridge.native.discover_native_binary", side_effect=missing):
            lines = inst.install_kde_entries(object())
        self.assertTrue(lines[0].startswith("native helper not found"))


if __name__ == "__main__":
    unittest.main()
