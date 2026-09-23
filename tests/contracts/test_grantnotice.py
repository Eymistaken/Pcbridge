"""The grant notification: a courtesy on GNOME, the grant's signal on Plasma."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge import paths as pathslib  # noqa: E402
from pcbridge.desktop import compositor, grantnotice  # noqa: E402


class FakeProc:
    def __init__(self, lines: str) -> None:
        self.stdout = io.StringIO(lines)
        self.pid = 4242

    def wait(self) -> int:
        return 0


class GrantNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(pathslib, "runtime_base", return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = Path(self.tmp.name) / "pcbridge" / grantnotice.STATE_FILE

    def test_gnome_shows_a_plain_notification_and_waits_for_nothing(self) -> None:
        with mock.patch.object(compositor, "is_kde", return_value=False), \
                mock.patch.object(grantnotice.subprocess, "run") as run, \
                mock.patch.object(grantnotice.subprocess, "Popen") as popen:
            grantnotice.show("Desktop control granted", "10 min")
        popen.assert_not_called()
        argv = run.call_args.args[0]
        self.assertNotIn("-w", argv)
        self.assertNotIn("-A", argv)

    def test_plasma_keeps_it_open_with_a_lock_button_that_runs_the_kill_switch(self) -> None:
        locked = []
        with mock.patch.object(compositor, "is_kde", return_value=True), \
                mock.patch.object(grantnotice, "_lock_now", side_effect=lambda: locked.append(1)), \
                mock.patch.object(grantnotice.subprocess, "Popen",
                                  return_value=FakeProc("7\nlock\n")) as popen:
            grantnotice.show("Desktop control granted", "10 min")
            for _ in range(100):
                if locked:
                    break
                time.sleep(0.01)
        argv = popen.call_args.args[0]
        self.assertIn("-w", argv)
        self.assertIn("lock=Lock now", argv)
        self.assertEqual(locked, [1])
        self.assertEqual(json.loads(self.state.read_text()), {"id": 7, "pid": 4242})

    def test_close_closes_the_notification_by_id_from_any_process(self) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        # A pid that is not notify-send must not be signalled.
        self.state.write_text(json.dumps({"id": 9, "pid": 1}), encoding="utf-8")
        with mock.patch.object(grantnotice.subprocess, "run") as run, \
                mock.patch.object(grantnotice.os, "kill") as kill:
            self.assertTrue(grantnotice.close())
        self.assertEqual(run.call_args.args[0][-3:], ["CloseNotification", "u", "9"])
        kill.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertFalse(grantnotice.close(), "nothing open, nothing to close")

    def test_a_notify_send_process_is_ended(self) -> None:
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.kill)
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps({"id": 3, "pid": proc.pid}), encoding="utf-8")
        with mock.patch.object(grantnotice.subprocess, "run"), \
                mock.patch.object(grantnotice.Path, "read_bytes",
                                  return_value=b"/usr/bin/notify-send\0-w\0"), \
                mock.patch.object(grantnotice.os, "kill") as kill:
            grantnotice.close()
        kill.assert_called_once_with(proc.pid, grantnotice.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
