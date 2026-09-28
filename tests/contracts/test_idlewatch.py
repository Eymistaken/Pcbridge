"""Idle observations require a live writer in the same compositor session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge.desktop import idlewatch  # noqa: E402


class IdleRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "idle.json"
        # A process whose command line holds "idle-watch", like the watcher.
        self.writer = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", idlewatch.WATCH_ARGUMENT])
        self.environment = mock.patch.dict(os.environ, {
            "WAYLAND_DISPLAY": "wayland-test", "HYPRLAND_INSTANCE_SIGNATURE": "test-session",
        })
        self.environment.start()

    def tearDown(self) -> None:
        self.writer.kill()
        self.writer.wait()
        self.environment.stop()
        self.dir.cleanup()

    def write(self, **fields) -> None:
        record = {"version": 2, "pid": self.writer.pid, "timeout_ms": 1000,
                  "idle": True, "since_unix_ms": 10_000, "confirmed_unix_ms": 74_000,
                  "writer_start_ticks": idlewatch._writer_start_ticks(self.writer.pid),
                  "wayland_display": "wayland-test", "hyprland_instance": "test-session"}
        record.update(fields)
        self.path.write_text(json.dumps(record), encoding="utf-8")

    def test_idle_counts_from_the_last_input(self) -> None:
        self.write()
        self.assertEqual(idlewatch.read_idle_ms(self.path, now_ms=75_000), 65_000)

    def test_active_reads_as_zero(self) -> None:
        self.write(idle=False, since_unix_ms=70_000)
        self.assertEqual(idlewatch.read_idle_ms(self.path, now_ms=75_000), 0)

    def test_a_record_without_a_live_watcher_is_unknown(self) -> None:
        import os

        self.write(pid=os.getpid())  # alive, but not a watcher
        self.assertIsNone(idlewatch.read_idle_ms(self.path, now_ms=75_000))
        self.writer.kill()
        self.writer.wait()
        self.write()
        self.assertIsNone(idlewatch.read_idle_ms(self.path, now_ms=75_000))

    def test_missing_or_malformed_records_are_unknown(self) -> None:
        self.assertIsNone(idlewatch.read_idle_ms(self.path, now_ms=1))
        self.path.write_text("not json", encoding="utf-8")
        self.assertIsNone(idlewatch.read_idle_ms(self.path, now_ms=1))
        self.write(version=1)
        self.assertIsNone(idlewatch.read_idle_ms(self.path, now_ms=1))
        self.write(since_unix_ms="10")
        self.assertIsNone(idlewatch.read_idle_ms(self.path, now_ms=1))

    def test_stale_future_reused_pid_and_foreign_session_records_are_unknown(self) -> None:
        for fields in (
            {"confirmed_unix_ms": 70_000}, {"confirmed_unix_ms": 76_000},
            {"writer_start_ticks": 1}, {"wayland_display": "wayland-other"},
            {"hyprland_instance": "previous"}, {"idle": "true"}, {"pid": True},
            {"since_unix_ms": 76_000}, {"timeout_ms": 0},
        ):
            with self.subTest(fields=fields):
                self.write(**fields)
                self.assertIsNone(idlewatch.read_idle_ms(self.path, now_ms=75_000))


if __name__ == "__main__":
    unittest.main()
