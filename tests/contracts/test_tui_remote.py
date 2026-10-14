"""The Remote tab: the remote apps with Revoke, Revoke all, and the offer to
revoke after a new password. The backend is a fake; nothing here touches
an OAuth database.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui.app import Confirm, PcbridgeApp  # noqa: E402
from pcbridge.tui.remote_pane import row_id  # noqa: E402
from tests.contracts.test_tui import SIZE, FakeBackend, settle  # noqa: E402
from textual.widgets import Button, TabbedContent  # noqa: E402


class RemoteBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.apps = {"ChatGPT": 1, "Gemini [phone]": 3}
        self.revoked: list[str | None] = []

    def remote_clients(self, cfg):
        return {"clients": [{"name": n, "connected": c, "registrations": c, "expires": None,
                             "last": "2026-10-14 10:00"} for n, c in self.apps.items()],
                "registrations": sum(self.apps.values()), "static_token": True,
                "remote_profile": "readonly", "remote_profile_set": True}

    def connected_remote(self, cfg):
        return sum(self.apps.values())

    def revoke_remote(self, cfg, name):
        self.revoked.append(name)
        if name is None:
            self.apps.clear()
        else:
            self.apps.pop(name)
        return f"Revoked {name or 'everything'}."


def run_app(test, backend, body) -> None:
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=SIZE) as pilot:
            await settle(app, pilot)
            await pilot.press("5")
            await settle(app, pilot)
            await pilot.pause(0.2)
            test.assertEqual(app.query_one(TabbedContent).active, "remote")
            await body(app, pilot)

    asyncio.run(go())


def status(app) -> str:
    return str(app.query_one("#remote-status").render())


class RemoteTabTests(unittest.TestCase):
    def test_the_apps_and_the_profile_are_listed(self) -> None:
        async def body(app, pilot):
            self.assertIn("2 app(s) connected", status(app))
            self.assertIn("Tools for remote clients: readonly", status(app))
            self.assertIn("static token", status(app))
            # Any character in a name still makes a valid row.
            self.assertTrue(app.query_one(f"#remote-revoke-{row_id('Gemini [phone]')}", Button))
            self.assertFalse(app.query_one("#remote-revoke-all", Button).disabled)

        run_app(self, RemoteBackend(), body)

    def test_revoke_asks_first_and_cancel_does_nothing(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await pilot.click(f"#remote-revoke-{row_id('ChatGPT')}")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, Confirm)
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertEqual(backend.revoked, [])

            await pilot.click(f"#remote-revoke-{row_id('ChatGPT')}")
            await settle(app, pilot)
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            await pilot.pause(0.2)
            self.assertEqual(backend.revoked, ["ChatGPT"])
            self.assertIn("1 app(s) connected", status(app))

        run_app(self, backend, body)

    def test_revoke_all(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            await pilot.click("#remote-revoke-all")
            await settle(app, pilot)
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            await pilot.pause(0.2)
            self.assertEqual(backend.revoked, [None])
            self.assertIn("No remote app holds a token", status(app))
            self.assertTrue(app.query_one("#remote-revoke-all", Button).disabled)

        run_app(self, backend, body)

    def test_a_new_password_offers_to_revoke_everything(self) -> None:
        backend = RemoteBackend()

        async def body(app, pilot):
            app.offer_revoke_after_password()
            await settle(app, pilot)
            self.assertIsInstance(app.screen, Confirm)
            self.assertIn("does not end the sessions", str(app.screen.question))
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            self.assertEqual(backend.revoked, [None])

        run_app(self, backend, body)

    def test_no_offer_without_connected_apps(self) -> None:
        backend = RemoteBackend()
        backend.apps.clear()

        async def body(app, pilot):
            app.offer_revoke_after_password()
            await settle(app, pilot)
            self.assertNotIsInstance(app.screen, Confirm)

        run_app(self, backend, body)


if __name__ == "__main__":
    unittest.main()
