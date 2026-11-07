"""The daemon closes a browser that pcbridge 2.6.0 left running, and only that.

2.6.0's browser ran under a keeper process in its own systemd scope, so it
outlives an upgrade; 2.7.0 removed the browser tools, so the daemon closes it
once at start-up. The profile, with the user's sign-ins, is left alone.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pcbridge import config as configlib
from pcbridge import daemon


class RetiredBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.browser = self.state / "browser"
        (self.browser / "profile").mkdir(parents=True)
        (self.browser / "profile" / "Cookies").write_text("kept")
        self.record = self.browser / "keeper.json"

    def spawn(self, *argv: str) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *argv])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        self.record.write_text(json.dumps({"keeper_pid": proc.pid, "browser_pid": 1}))
        return proc

    def test_a_running_keeper_is_closed_and_the_profile_stays(self) -> None:
        proc = self.spawn("/old/venv/pcbridge/browser/keeper.py", "--socket", "s")
        self.assertTrue(daemon.close_retired_browser(self.state))
        self.assertEqual(proc.wait(timeout=5), -15)
        self.assertFalse(self.record.exists())
        self.assertEqual((self.browser / "profile" / "Cookies").read_text(), "kept")

    def test_a_reused_pid_that_is_not_a_keeper_is_left_alone(self) -> None:
        proc = self.spawn("something-else")
        self.assertFalse(daemon.close_retired_browser(self.state))
        self.assertIsNone(proc.poll())

    def test_nothing_to_do_without_a_record(self) -> None:
        self.assertFalse(daemon.close_retired_browser(self.state))
        self.record.write_text("not json")
        self.assertFalse(daemon.close_retired_browser(self.state))

    def test_a_2_6_config_with_a_browser_section_still_loads(self) -> None:
        path = self.state / "config.toml"
        path.write_text(
            'public_url = "http://localhost:8765"\n'
            '[auth]\npassword = "a-long-enough-test-password"\n'
            f'[paths]\nstate_dir = "{self.state}"\n'
            '[agents.claude]\ncommand = ["true"]\n'
            '[browser]\nexecutable = "chrome"\nallow_password_fields = true\n'
        )
        cfg = configlib.load_config(str(path), check_state=False)
        self.assertFalse(hasattr(cfg, "browser"))
        self.assertTrue(any("removed the browser tools" in w for w in cfg.warnings), cfg.warnings)


if __name__ == "__main__":
    unittest.main()
