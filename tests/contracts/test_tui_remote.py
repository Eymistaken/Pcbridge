"""The Remote tab: the tunnel, the address, access (the password dialogs
and the remote tool profile), and the connected apps with Revoke, Revoke
all and the stale clean-up. The backend is a fake; nothing here touches
Tailscale or an OAuth database.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import passphrase  # noqa: E402
from pcbridge import settings as S  # noqa: E402
from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.dialogs import Confirm, ShowOnce  # noqa: E402
from pcbridge.tui.remote_pane import LoginScreen, PasswordScreen, row_id  # noqa: E402
from tests.contracts.test_settings import example_config  # noqa: E402
from tests.contracts.test_tui import (NARROW, SIZE, FakeBackend, flat, screen_text, settle,  # noqa: E402
                                     words_of)
from textual.widgets import Button, DataTable, Input, Switch, TabbedContent  # noqa: E402

LOGIN = "the-users-login-password"


async def login(app, pilot, password: str = LOGIN) -> None:
    """Open the remote password dialog and get past the login-password screen."""
    await pilot.click("#password-replace")
    await settle(app, pilot)
    assert isinstance(app.screen, LoginScreen), app.screen
    app.screen.query_one("#login-pw", Input).value = password
    app.screen.query_one("#login-ok", Button).press()
    await settle(app, pilot)
    await pilot.pause(0.1)


class RemoteBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.apps = {"ChatGPT": 1, "Gemini [phone]": 3}
        self.revoked: list[str | None] = []
        self.has_password = True
        self.saved: list[str] = []
        self.logins: list[str] = []
        self.tunnel = False
        self.tunnel_calls: list[bool] = []
        self.cleanups = 0
        self.copied: list[str] = []
        self.stale = 15
        self.cfg.mcp_url = "https://box.example.ts.net/mcp"

    def login_ok(self, password):
        self.logins.append(password)
        return password == LOGIN

    def set_remote_password(self, cfg, password, login_password):
        assert login_password == LOGIN
        self.saved.append(password)
        self.has_password = True

    def remote_clients(self, cfg):
        return {"clients": [{"name": n, "connected": c, "registrations": c + 1, "stale": 1,
                             "expires": None, "last": "2026-10-14 10:00"} for n, c in self.apps.items()],
                "registrations": sum(self.apps.values()), "static_token": True,
                "stale": self.stale, "has_password": self.has_password,
                "remote_profile": "readonly", "remote_profile_setting": "readonly",
                "remote_profile_set": True, "profile_counts": {"full": 40, "core": 24, "desktop": 23,
                                                               "readonly": 13},
                "url": "https://box.example.ts.net/mcp", "tunnel": self.tunnel}

    def connected_remote(self, cfg):
        return sum(self.apps.values())

    def revoke_remote(self, cfg, name):
        self.revoked.append(name)
        if name is None:
            self.apps.clear()
        else:
            self.apps.pop(name)
        return f"Revoked {name or 'everything'}."

    def set_tunnel(self, cfg, on):
        self.tunnel_calls.append(on)
        self.tunnel = on
        return True, "remote access open" if on else "remote access closed"

    def cleanup_stale(self, cfg):
        self.cleanups += 1
        self.stale = 0
        return "Removed 15 stale registration(s); live sessions stay."

    def copy_text(self, text):
        self.copied.append(text)
        return True


def run_app(test, backend, body, size=SIZE) -> None:
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await pilot.press("5")
            await settle(app, pilot)
            await pilot.pause(0.2)
            test.assertEqual(app.query_one(TabbedContent).active, "remote")
            await body(app, pilot)

    asyncio.run(go())


def right(app) -> str:
    return words_of(app.query_one("#remote-apps"))


def left(app) -> str:
    return words_of(app.query_one("#remote-left")) + " " + words_of(app.query_one("#remote-right"))


def apps(app) -> list[str]:
    return [str(k.value) for k in app.query_one("#apps-table", DataTable).rows]


class RemoteTabTests(unittest.TestCase):
    def test_the_four_sections(self) -> None:
        async def body(app, pilot):
            text = left(app)
            self.assertIn("○ Closed", text)
            self.assertIn("Remote apps cannot reach pcbridge. Local clients are not affected.", text)
            self.assertIn("The tunnel never starts at login", text)
            self.assertIn("https://box.example.ts.net/mcp Copy y", text)
            self.assertIn("In claude.ai: Settings › Connectors › Add custom connector", text)
            self.assertIn("✓ set · stored as a hash Replace… p", text)
            self.assertIn("readonly: only tools that change nothing, 13 tools. Applies to the static "
                          "token too.", text)
            self.assertEqual(app.query_one("#remote-profile").value, "readonly")
            self.assertIn("2 apps hold tokens", flat(app))
            self.assertEqual(apps(app), [row_id("ChatGPT"), row_id("Gemini [phone]")])  # any name works
            self.assertIn("(1 stale)", flat(app))
            self.assertIn("Clean up 15 stale registrations", flat(app))
            self.assertIn("static token", right(app))
            footer = screen_text(app)[-1]
            self.assertIn("t Tunnel on/off  y Copy address  p Password  x Revoke", footer)

        run_app(self, RemoteBackend(), body)

    def test_the_tab_reads_the_config_the_app_loads(self) -> None:
        # The tab mounts before the app loads the config; it used to show
        # "the config does not load" until Refresh.
        backend = RemoteBackend()
        real = backend.remote_clients

        def strict(cfg):
            if cfg is None:
                raise RuntimeError("the config does not load")
            return real(cfg)

        backend.remote_clients = strict

        async def body(app, pilot):
            self.assertNotIn("could not be read", right(app))
            self.assertIn("2 apps hold tokens", flat(app))

        run_app(self, backend, body)

    def test_opening_the_tunnel_asks_and_closing_does_not(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await pilot.click("#tunnel-switch")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            await pilot.click("#confirm-no")
            await settle(app, pilot)
            self.assertEqual(backend.tunnel_calls, [])
            self.assertFalse(app.query_one("#tunnel-switch", Switch).value)
            await pilot.press("t")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            self.assertEqual(backend.tunnel_calls, [True])
            self.assertIn("◉ Open", left(app))
            await pilot.press("t")
            await settle(app, pilot)
            self.assertNotIsInstance(app.screen, Confirm)
            self.assertEqual(backend.tunnel_calls, [True, False])

        run_app(self, backend, body)

    def test_no_password_creates_one_before_the_tunnel(self) -> None:
        backend = RemoteBackend()
        backend.has_password = False

        async def body(app, pilot):
            self.assertIn("not set: remote access is off", left(app))
            self.assertIn("Create… p", left(app))
            await pilot.press("t")
            await pilot.pause()
            self.assertIsInstance(app.screen, LoginScreen)
            self.assertEqual(backend.tunnel_calls, [])

        run_app(self, backend, body)

    def test_copy_the_address(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await pilot.press("y")
            await pilot.pause()
            self.assertEqual(backend.copied, ["https://box.example.ts.net/mcp"])
            self.assertIn("✓ copied with wl-copy", left(app))

        run_app(self, backend, body)

    def test_revoke_asks_inline_and_keep_does_nothing(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await pilot.press("x")
            await pilot.pause()
            self.assertIn("ChatGPT: sign it out?", right(app))
            await pilot.click("#apps-confirm-no")
            await settle(app, pilot)
            self.assertEqual(backend.revoked, [])
            await pilot.press("x")
            await pilot.pause()
            await pilot.click("#apps-confirm-yes")
            await settle(app, pilot)
            self.assertEqual(backend.revoked, ["ChatGPT"])
            self.assertIn("1 app holds tokens", flat(app))

        run_app(self, backend, body)

    def test_revoke_every_app_asks_with_a_danger_frame(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await pilot.click("#remote-revoke-all")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            self.assertEqual(app.screen.kind, "destroys")
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            self.assertEqual(backend.revoked, [None])
            self.assertIn("No remote app holds a token", right(app))
            self.assertTrue(app.query_one("#remote-revoke-all", Button).disabled)

        run_app(self, backend, body)

    def test_clean_up_stale_registrations(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await pilot.click("#stale-cleanup")
            await settle(app, pilot)
            self.assertEqual(backend.cleanups, 1)
            self.assertTrue(app.query_one("#stale-cleanup", Button).disabled)

        run_app(self, backend, body)

    def test_creating_the_password_generates_and_shows_once(self) -> None:
        backend = RemoteBackend()
        backend.has_password = False
        backend.apps = {}

        async def body(app, pilot):
            await login(app, pilot)
            self.assertIsInstance(app.screen, PasswordScreen)
            self.assertFalse(app.screen.query("#pw-signout"))   # nothing to sign out
            await pilot.press("g")
            await settle(app, pilot)
            self.assertEqual(len(backend.saved), 1)
            secret = backend.saved[0]
            words = secret.split("-")
            self.assertEqual(len(words), 6)
            self.assertTrue(set(words) <= set(passphrase.words()))
            self.assertIsInstance(app.screen, ShowOnce)
            self.assertEqual(app.screen.secret, secret)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ShowOnce)       # no Escape out of it
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ShowOnce)       # Done waits for the box
            await pilot.click("#once-saved")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertNotIsInstance(app.screen, ShowOnce)
            self.assertNotIn(secret, app.export_screenshot())

        run_app(self, backend, body)

    def test_a_typed_password_has_live_checks_and_is_not_shown(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await login(app, pilot)
            screen = app.screen
            first, second = screen.query_one("#pw-first", Input), screen.query_one("#pw-second", Input)
            first.value = "short"
            await pilot.pause()
            self.assertIn("5 of 12", str(screen.query_one("#pw-first-check").render()))
            self.assertTrue(screen.query_one("#pw-typed", Button).disabled)
            first.value = "a-typed-long-password"
            second.value = "a-typed-long-passwore"
            await pilot.pause()
            self.assertIn("✓ 21 chars", str(screen.query_one("#pw-first-check").render()))
            self.assertIn("✕ differs", str(screen.query_one("#pw-second-check").render()))
            self.assertTrue(screen.query_one("#pw-typed", Button).disabled)
            second.value = "a-typed-long-password"
            await pilot.pause()
            self.assertIn("✓ same", str(screen.query_one("#pw-second-check").render()))
            self.assertFalse(screen.query_one("#pw-typed", Button).disabled)
            screen.query_one("#pw-signout").value = False          # keep the apps signed in
            await pilot.click("#pw-typed")
            await settle(app, pilot)
            self.assertEqual(backend.saved, ["a-typed-long-password"])
            self.assertEqual(backend.revoked, [])
            self.assertNotIsInstance(app.screen, ShowOnce)
            self.assertNotIn("a-typed-long-password", app.export_screenshot())

        run_app(self, backend, body)

    def test_replacing_signs_the_apps_out_by_default(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await login(app, pilot)
            self.assertIn("Also sign out the 2 apps that hold tokens", words_of(app.screen))
            self.assertTrue(app.screen.query_one("#pw-signout").value)
            await pilot.press("g")
            await settle(app, pilot)
            self.assertEqual(backend.revoked, [None])
            self.assertIsInstance(app.screen, ShowOnce)

        run_app(self, backend, body)

    def test_a_wrong_login_password_stops_at_the_first_screen(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await login(app, pilot, "not-it")
            self.assertIsInstance(app.screen, LoginScreen)
            self.assertIn("not the login password", str(app.screen.query_one("#pw-error").render()))
            self.assertEqual(app.screen.query_one("#login-pw", Input).value, "")
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, LoginScreen)
            self.assertEqual(backend.saved, [])
            self.assertEqual(backend.logins, ["not-it"])

        run_app(self, backend, body)

    def test_cancel_changes_nothing(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await login(app, pilot)
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertNotIsInstance(app.screen, PasswordScreen)
            self.assertEqual(backend.saved, [])
            self.assertEqual(backend.revoked, [])

        run_app(self, backend, body)

    def test_the_remote_profile_saves_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(example_config(Path(tmp) / "state"))
            path.chmod(0o600)

            class Backend(RemoteBackend):
                def editor(self):
                    return S.ConfigEditor(path)

            async def body(app, pilot):
                app.query_one("#remote-profile").value = "core"
                await settle(app, pilot)
                self.assertEqual(tomllib.loads(path.read_text())["tools"]["remote_profile"], "core")
                self.assertEqual(app.restart_due, 1)

            with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                              if not k.startswith("PCBRIDGE_")}, clear=True):
                run_app(self, Backend(), body)

    def test_the_tab_at_80_columns(self) -> None:
        async def body(app, pilot):
            self.assertTrue(all(len(r) == 80 for r in screen_text(app)))
            self.assertIn("1 TUNNEL", flat(app))

        run_app(self, RemoteBackend(), body, size=NARROW)


if __name__ == "__main__":
    unittest.main()
