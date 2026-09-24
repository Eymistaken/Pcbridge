"""`pcbridge/cli/grant.py`: the grant as `status`, `lock`, `unlock` and the
terminal UI see it. No real runtime is created; the gate is a stub.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli import grant  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402


def cfg(state: Path, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(state_dir=state, desktop=SimpleNamespace(enabled=enabled),
                           source_path=state / "config.toml")


class FakeRuntime:
    def __init__(self) -> None:
        self.closed = False
        self.gate = mock.Mock()
        self.gate.lock.return_value = "Desktop control closed."
        self.gate.unlock.return_value = "Desktop control open for 5 min."
        self.capture_provider = mock.Mock()
        self.capture_provider.kill_helpers.return_value = 1

    def close(self) -> None:
        self.closed = True


class GrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_state_file_reads_as_locked(self) -> None:
        st = grant.read_state(cfg(self.state))
        self.assertFalse(st.open)
        self.assertEqual(st.seconds_left, 0)
        self.assertEqual(st.describe(), "locked")

    def test_an_open_grant_counts_down(self) -> None:
        now = time.time()
        LeaseStore(self.state).grant(until=now + 125, reason="t", granted=now, granted_by="pcbridge unlock")
        st = grant.read_state(cfg(self.state), now=now)
        self.assertTrue(st.open)
        self.assertEqual(st.seconds_left, 125)
        self.assertEqual(st.granted_by, "pcbridge unlock")
        self.assertEqual(st.describe(), "open, 2:05 left")
        self.assertFalse(grant.read_state(cfg(self.state), now=now + 130).open)

    def test_a_sliding_grant_shows_its_ceiling(self) -> None:
        now = time.time()
        store = LeaseStore(self.state)
        store.grant(until=now + 900, reason="t", granted=now, granted_by="x")
        store.touch(store.snapshot().token(now), idle_seconds=90, now=now)
        st = grant.read_state(cfg(self.state), now=now)
        self.assertEqual((st.seconds_left, st.hard_seconds_left), (90, 900))
        self.assertTrue(st.sliding)
        self.assertEqual(st.describe(), "open, 1:30 left unless an agent acts again (at most 15:00)")

    def test_a_revoked_grant_is_locked(self) -> None:
        now = time.time()
        store = LeaseStore(self.state)
        store.grant(until=now + 600, reason="t", granted=now, granted_by="x")
        store.revoke(now=now)
        self.assertFalse(grant.read_state(cfg(self.state), now=now).open)

    def test_disabled_in_config_is_said_and_unlock_refuses(self) -> None:
        c = cfg(self.state, enabled=False)
        self.assertEqual(grant.read_state(c).describe(), "disabled in config")
        with mock.patch("pcbridge.cli.runtime_of") as runtime_of:
            with self.assertRaisesRegex(grant.GrantError, r"\[desktop\] enabled = false"):
                grant.unlock(c, 5)
            runtime_of.assert_not_called()

    def test_lock_and_unlock_use_the_gate_and_close_the_runtime(self) -> None:
        rt = FakeRuntime()
        with mock.patch("pcbridge.cli.runtime_of", return_value=rt):
            out = grant.lock(cfg(self.state))
        self.assertIn("Desktop control closed.", out)
        self.assertIn("1 screen share(s) stopped", out)
        self.assertTrue(rt.closed)

        rt = FakeRuntime()
        with mock.patch("pcbridge.cli.runtime_of", return_value=rt):
            out = grant.unlock(cfg(self.state), 5, "why", granted_by="pcbridge ui")
        rt.gate.unlock.assert_called_once_with(5, "why", granted_by="pcbridge ui")
        self.assertTrue(rt.closed)

    def test_durations(self) -> None:
        self.assertEqual(grant.format_duration(0), "0:00")
        self.assertEqual(grant.format_duration(59), "0:59")
        self.assertEqual(grant.format_duration(3725), "1:02:05")


if __name__ == "__main__":
    unittest.main()
