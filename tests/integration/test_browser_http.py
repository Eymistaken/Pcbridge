"""The browser tools' "Done when" against a real daemon over HTTP with OAuth,
the path a remote client takes (ROADMAP, "Browser tools for the user's
existing session").

A throwaway config, state directory, socket, port and browser profile, as
in `test_remote_access.py`, whose harness this borrows; the browser runs
headless on its own profile and the "router" is a local fake
(tests/fixtures/browser/router_site.py). It never touches the user's
daemon, browser or desktop: desktop control is checked only in states where
the grant is closed (never `desktop_unlock`), and the expired grant is a
lease file written into the throwaway state directory.

Skipped when no Chrome, Brave or Chromium is installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.browser.manager import CANDIDATES  # noqa: E402
from tests.fixtures.browser.router_site import RouterSite  # noqa: E402
from tests.integration import test_remote_access as remote  # noqa: E402

_HARNESS = ("setUp", "_write_config", "_start_daemon", "_stop_daemon", "_rpc", "_session",
            "_oauth", "_socket_tools")
HAVE_BROWSER = any(shutil.which(n) for names in CANDIDATES.values() for n in names)
BROWSER = '[browser]\nextra_args = ["--headless=new"]\n'


def ref(text: str, label: str) -> str:
    m = re.search(r'"' + re.escape(label) + r'" \[(e\d+)\]', text)
    if not m:
        raise AssertionError(f"no ref for {label!r} in:\n{text}")
    return m.group(1)


@unittest.skipUnless(HAVE_BROWSER, "no Chrome, Brave or Chromium installed")
class BrowserOverHttp(unittest.TestCase):
    locals().update({name: getattr(remote.RemoteAccessTests, name) for name in _HARNESS})

    def setUp(self) -> None:  # type: ignore[override]
        remote.RemoteAccessTests.setUp(self)  # type: ignore[arg-type]
        # The Claude app's systemd environment would make the throwaway daemon
        # put its browser into a scope of its own; keep it a plain child.
        self.env.pop("INVOCATION_ID", None)
        self.site = RouterSite()
        self.addCleanup(self.site.close)
        self.addCleanup(self._stop_browser)

    def _stop_browser(self) -> None:
        info = self.tmp / "state" / "browser" / "keeper.json"
        try:
            pid = json.loads(info.read_text())["keeper_pid"]
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            return
        for _ in range(100):
            if not info.exists():
                return
            time.sleep(0.1)

    def _daemon(self, tools: str = 'profile = "full"', desktop: bool = False,
                browser: str = BROWSER) -> None:
        self._stop_daemon()
        self._write_config(tools + "\n\n" + browser)
        if desktop:
            text = self.cfg.read_text().replace("[desktop]\nenabled = false", "[desktop]\nenabled = true")
            self.assertIn("[desktop]\nenabled = true", text)
            self.cfg.write_text(text)
        self._start_daemon()
        self.token = self._oauth("browser-test")["access_token"]
        self.sid = self._session(self.token)

    def call(self, name: str, args: dict | None = None) -> dict:
        r, data = self._rpc(self.token, "tools/call", {"name": name, "arguments": args or {}},
                            sid=self.sid)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("result", data, data)
        return data["result"]

    def ok(self, name: str, args: dict | None = None) -> str:
        res = self.call(name, args)
        text = res["content"][0]["text"]
        self.assertFalse(res.get("isError"), f"{name} failed: {text}")
        return text

    def refused(self, name: str, args: dict | None = None) -> str:
        res = self.call(name, args)
        self.assertTrue(res.get("isError"), f"{name} was expected to fail")
        return res["content"][0]["text"]

    def audit(self) -> str:
        return (self.tmp / "state" / "audit.log").read_text()

    def desktop_is_closed(self) -> None:
        """The desktop grant is not open: a screen-reading tool is refused."""
        res = self.call("ui_dump", {})
        self.assertTrue(res.get("isError") or "desktop_unlock" in res["content"][0]["text"]
                        or "disabled" in res["content"][0]["text"], res)

    # ------------------------------------------------------------------------
    def test_a_router_panel_through_every_closed_grant_state(self) -> None:
        site = self.site
        # 1. [desktop] enabled = false: the grant cannot even exist.
        self._daemon(browser=BROWSER + "allow_password_fields = true\n")
        self.desktop_is_closed()
        self.assertIn("not running", self.ok("browser_tabs"))
        page = self.ok("browser_navigate", {"url": site.url + "/"})
        self.assertIn("Router - Sign in", page)
        self.assertIn('password "Password"', page)
        self.ok("browser_act", {"action": "type", "ref": ref(page, "Username"), "text": "admin",
                                "max_chars": 0})
        self.ok("browser_act", {"action": "type", "ref": ref(page, "Password"),
                                "text": site.password, "max_chars": 0})
        masked = self.ok("browser_read", {"find": "password"})
        self.assertNotIn(site.password, masked)
        status = self.ok("browser_act", {"action": "click", "ref": ref(page, "Sign in")})
        self.assertIn("Router - Status", status)
        self.assertIn(site.firmware, status, "the status table inside the iframe")
        self.assertIn("| laptop | 192.168.1.20 | -48 dBm |", status)
        tab = re.search(r"Tab (\w{8})", status).group(1)
        shot = self.call("browser_screenshot")
        self.assertIn("/shot/", shot["content"][0]["text"])
        self.assertEqual([c["type"] for c in shot["content"]], ["text", "image"])

        # 2. desktop enabled, after desktop_lock. The browser outlived the
        # daemon restart: the same tab, still signed in.
        self._daemon(desktop=True)
        self.ok("desktop_lock")
        self.desktop_is_closed()
        self.assertIn(tab, self.ok("browser_tabs"))
        wifi = self.ok("browser_navigate", {"url": site.url + "/wifi"})
        self.assertIn('textbox "Network name"', wifi)
        self.ok("browser_act", {"action": "type", "ref": ref(wifi, "Network name"),
                                "text": "lab-net-7", "max_chars": 0})
        self.ok("browser_act", {"action": "select", "ref": ref(wifi, "Channel"),
                                "text": "Channel 11", "max_chars": 0})
        self.ok("browser_act", {"action": "click", "ref": ref(wifi, "5 GHz"), "max_chars": 0})
        saved = self.ok("browser_act", {"action": "click", "ref": ref(wifi, "Save")})
        # Verified against fresh state: the outline after the action, and the site.
        self.assertIn('"Settings saved"', saved)
        self.assertIn('= "lab-net-7"', saved)
        self.assertIn('"Channel 11"*', saved)
        self.assertEqual(site.state["ssid"], "lab-net-7")
        self.assertEqual(site.state["channel"], "11")
        self.assertEqual(site.state["band"], "5")
        gone = self.refused("browser_act", {"action": "click", "ref": ref(wifi, "Save")})
        self.assertIn("is gone", gone)

        # 3. after the grant expired: a lease past its hard limit.
        past = time.time() - 600
        (self.tmp / "state" / "desktop_unlock.json").write_text(json.dumps({
            "schema_version": 1, "grant_id": "expired-test-grant", "revoke_epoch": 0,
            "until": past, "hard_until": past}))
        self.desktop_is_closed()
        self.assertIn("lab-net-7", self.ok("browser_read", {"mode": "text", "find": "lab-net"})
                      + self.ok("browser_read", {"find": "Network name"}))
        self.ok("browser_navigate", {"action": "reload", "max_chars": 0})

        # 4. A sign-in survives the browser itself: close it, start it again.
        self._stop_browser()
        again = self.ok("browser_navigate", {"url": site.url + "/"})
        self.assertIn("Router - Status", again, "the persistent session cookie was kept")

        # The audit log says what was done, never what was typed or read.
        log = self.audit()
        for secret in (site.password, "lab-net-7", site.firmware, "192.168.1.20", "/wifi"):
            self.assertNotIn(secret, log)
        for event in ('"browser_navigate"', '"browser_act"', '"browser_read"', '"browser_screenshot"'):
            self.assertIn(event, log)
        self.assertIn('"host": "127.0.0.1"', log)

    def test_password_fields_are_refused_by_default(self) -> None:
        self._daemon()
        page = self.ok("browser_navigate", {"url": self.site.url + "/login.html"})
        text = self.refused("browser_act", {"action": "type", "ref": ref(page, "Password"),
                                            "text": "guess"})
        self.assertIn("allow_password_fields", text)
        self.assertNotIn("/login", self.site.posts)
        self.assertIn("Only http and https", self.refused("browser_navigate", {"url": "file:///etc/passwd"}))

    def test_a_readonly_phone_reads_but_cannot_act(self) -> None:
        self._daemon('profile = "full"\nremote_profile = "readonly"')
        _r, data = self._rpc(self.token, "tools/list", sid=self.sid)
        names = {t["name"] for t in data["result"]["tools"]}
        self.assertLessEqual({"browser_tabs", "browser_read", "browser_screenshot"}, names)
        self.assertTrue(names.isdisjoint({"browser_navigate", "browser_act"}))
        # A local client (full profile) opens the page; the phone reads it.
        local, _tools = self._socket_tools()
        local.call("tools/call", {"name": "browser_navigate",
                                  "arguments": {"url": self.site.url + "/login.html"}})
        page = self.ok("browser_read")
        self.assertIn("Router sign in", page)
        for name, args in (("browser_act", {"action": "click", "ref": ref(page, "Sign in")}),
                           ("browser_navigate", {"url": self.site.url + "/"})):
            self.assertIn("not offered", self.refused(name, args))
        self.assertEqual(self.site.posts, [])



if __name__ == "__main__":
    unittest.main()
