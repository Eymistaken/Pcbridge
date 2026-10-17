#!/usr/bin/env python3
"""The browser keeper and the manager's pure parts, without a browser: a
fake one speaks the pipe protocol (tests/fixtures/browser/fake_browser.py).

The keeper must relay whole messages only, survive its client going away
(that is why it exists: measured, the pipe closing ends the browser), and
close the browser with Browser.close on SIGTERM.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.browser import manager as M  # noqa: E402
from pcbridge.browser.cdp import Connection  # noqa: E402
from pcbridge.config import BrowserSpec  # noqa: E402

FAKE = ROOT / "tests/fixtures/browser/fake_browser.py"


def runtime_sock(tag: str) -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    return base / f"pcbridge-test-{tag}-{os.getpid()}.sock"


class KeeperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sock = runtime_sock(self.id().rsplit(".", 1)[-1][:20])
        self.info = self.dir / "keeper.json"
        self.marker = self.dir / "closed"
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(M.KEEPER), "--socket", str(self.sock), "--info", str(self.info),
             "--", sys.executable, str(FAKE), str(self.marker)], start_new_session=True)
        for _ in range(100):
            if self.sock.exists() and self.info.exists():
                break
            time.sleep(0.05)

    def tearDown(self) -> None:
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(5)
        self.sock.unlink(missing_ok=True)
        self.tmp.cleanup()

    async def one_call(self, method: str) -> dict:
        conn = await Connection.open(str(self.sock))
        try:
            return await conn.call(method, timeout=5)
        finally:
            await conn.close()

    def test_the_record_names_the_keeper_and_the_browser(self) -> None:
        info = json.loads(self.info.read_text())
        self.assertEqual(info["keeper_pid"], self.proc.pid)
        self.assertEqual(info["socket"], str(self.sock))
        self.assertTrue(Path(f"/proc/{info['browser_pid']}").exists())
        self.assertEqual(self.sock.stat().st_mode & 0o777, 0o600)

    def test_a_client_can_leave_and_another_come_back(self) -> None:
        self.assertEqual(asyncio.run(self.one_call("A.first")), {"method": "A.first"})
        self.assertEqual(asyncio.run(self.one_call("A.second")), {"method": "A.second"})
        self.assertIsNone(self.proc.poll(), "the keeper must outlive its client")

    def test_half_a_message_from_a_client_that_left_never_reaches_the_browser(self) -> None:
        async def half() -> None:
            _r, w = await asyncio.open_unix_connection(str(self.sock))
            w.write(b'{"id": 5, "method": "Half.mess')
            await w.drain()
            w.close()
        asyncio.run(half())
        time.sleep(0.2)
        # The browser would have died on broken JSON; it answers instead.
        self.assertEqual(asyncio.run(self.one_call("After.half")), {"method": "After.half"})

    def test_sigterm_closes_the_browser_with_browser_close(self) -> None:
        browser = json.loads(self.info.read_text())["browser_pid"]
        os.kill(self.proc.pid, signal.SIGTERM)
        self.assertEqual(self.proc.wait(15), 0)
        self.assertEqual(self.marker.read_text(), "closed cleanly")
        self.assertFalse(self.sock.exists())
        self.assertFalse(self.info.exists())
        time.sleep(0.2)
        status = Path(f"/proc/{browser}/status")
        self.assertTrue(not status.exists() or "zombie" in status.read_text().lower())

    def test_the_keeper_ends_when_the_browser_does(self) -> None:
        browser = json.loads(self.info.read_text())["browser_pid"]
        os.kill(browser, signal.SIGKILL)
        self.proc.wait(10)
        self.assertFalse(self.info.exists())


class ManagerPartsTests(unittest.TestCase):
    def cfg(self, **browser) -> SimpleNamespace:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return SimpleNamespace(state_dir=Path(self.tmp.name), browser=BrowserSpec(**browser))

    def test_urls(self) -> None:
        self.assertEqual(M.normalize_url("192.168.1.1"), "http://192.168.1.1")
        self.assertEqual(M.normalize_url("https://example.org/a?b"), "https://example.org/a?b")
        self.assertEqual(M.normalize_url("about:blank"), "about:blank")
        for bad in ("file:///etc/passwd", "javascript:alert(1)", "chrome://settings", "data:text/html,x"):
            with self.assertRaises(M.BrowserError, msg=bad):
                M.normalize_url(bad)

    def test_the_audit_gets_the_host_only(self) -> None:
        self.assertEqual(M.host_of("https://bank.example/login?token=abc#x"), "bank.example")
        self.assertEqual(M.host_of("http://192.168.1.1:8080/cgi?pw=1"), "192.168.1.1")

    def test_refs(self) -> None:
        self.assertEqual(M.REF.match("e12").group(1), "12")
        self.assertEqual(M.REF.match("[e7]").group(1), "7")
        self.assertIsNone(M.REF.match("12"))

    def test_auto_prefers_chrome_then_brave_then_chromium(self) -> None:
        m = M.BrowserManager(self.cfg())
        have = {"brave-browser": "/usr/bin/brave-browser", "chromium": "/usr/bin/chromium"}
        with mock.patch.object(M.shutil, "which", side_effect=have.get):
            self.assertEqual(m.executable(), "/usr/bin/brave-browser")
        have["google-chrome"] = "/usr/bin/google-chrome"
        with mock.patch.object(M.shutil, "which", side_effect=have.get):
            self.assertEqual(m.executable(), "/usr/bin/google-chrome")
        with mock.patch.object(M.shutil, "which", return_value=None):
            with self.assertRaises(M.BrowserError):
                m.executable()

    def test_a_named_browser_and_a_path(self) -> None:
        with mock.patch.object(M.shutil, "which", side_effect={"chromium": "/x/chromium"}.get):
            self.assertEqual(M.BrowserManager(self.cfg(executable="chromium")).executable(), "/x/chromium")
        self.assertEqual(M.BrowserManager(self.cfg(executable=sys.executable)).executable(), sys.executable)
        with self.assertRaises(M.BrowserError):
            M.BrowserManager(self.cfg(executable="/nonexistent/chrome")).executable()

    def test_a_stale_record_is_not_a_running_browser(self) -> None:
        m = M.BrowserManager(self.cfg())
        m.dir.mkdir(parents=True)
        m.info_path.write_text(json.dumps({"keeper_pid": os.getpid(), "socket": "/nonexistent"}))
        self.assertIsNone(m.status())
        self.assertFalse(m.info_path.exists())

    def test_the_profile_lives_under_the_state_directory_unless_set(self) -> None:
        m = M.BrowserManager(self.cfg())
        self.assertEqual(m.profile, Path(self.tmp.name) / "browser" / "profile")
        m = M.BrowserManager(self.cfg(profile_dir="/srv/p"))
        self.assertEqual(m.profile, Path("/srv/p"))

    def test_launch_flags_keep_a_hidden_window_working(self) -> None:
        for flag in ("--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                     "--disable-background-timer-throttling"):
            self.assertIn(flag, M.LAUNCH_FLAGS)


if __name__ == "__main__":
    unittest.main()
