"""The Connections tab: a switch per client, Update for an old command,
changes through the backend in a worker. The backend is a fake; nothing
here touches a real client's config.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from tests.contracts.test_tui import SIZE, FakeBackend, settle  # noqa: E402
from textual.widgets import Button, Switch, TabbedContent  # noqa: E402


class ClientsBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.states = {"claude-code": "connected", "hermes": "outdated", "opencode": "not connected",
                       "oh-my-pi": "not installed"}
        self.changes: list[tuple[str, bool]] = []

    def clients(self):
        return [{"client": c, "name": c.title(), "state": s, "command": ["/old/python", "-m", "x"],
                 "config": f"/home/u/.{c}/config", "note": "", "setup_default": c == "claude-code"}
                for c, s in self.states.items()]

    def set_connection(self, client, connect):
        self.changes.append((client, connect))
        self.states[client] = "connected" if connect else "not connected"
        return "ok", "done"


def run_app(test, backend, body) -> None:
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=SIZE) as pilot:
            await settle(app, pilot)
            await pilot.press("4")
            await settle(app, pilot)
            await pilot.pause(0.2)
            test.assertEqual(app.query_one(TabbedContent).active, "connections")
            await body(app, pilot)

    asyncio.run(go())


class ConnectionsTabTests(unittest.TestCase):
    def test_the_switches_show_each_state(self) -> None:
        async def body(app, pilot):
            self.assertTrue(app.query_one("#conn-switch-claude-code", Switch).value)
            self.assertTrue(app.query_one("#conn-switch-hermes", Switch).value)  # outdated still works
            self.assertFalse(app.query_one("#conn-switch-opencode", Switch).value)
            self.assertTrue(app.query_one("#conn-switch-oh-my-pi", Switch).disabled)
            self.assertTrue(app.query_one("#conn-update-hermes", Button).display)
            self.assertFalse(app.query_one("#conn-update-claude-code", Button).display)
            self.assertIn("2 of 4 connected", str(app.query_one("#conn-status").render()))

        run_app(self, ClientsBackend(), body)

    def test_a_click_connects_and_disconnects(self) -> None:
        backend = ClientsBackend()

        async def body(app, pilot):
            await pilot.click("#conn-switch-opencode")
            await settle(app, pilot)
            await pilot.pause(0.2)
            self.assertEqual(backend.changes, [("opencode", True)])
            self.assertTrue(app.query_one("#conn-switch-opencode", Switch).value)
            await pilot.click("#conn-switch-claude-code")
            await settle(app, pilot)
            await pilot.pause(0.2)
            self.assertEqual(backend.changes[-1], ("claude-code", False))
            self.assertFalse(app.query_one("#conn-switch-claude-code", Switch).value)
            # Refreshing the list moves switches by code; that must not count as a click.
            self.assertEqual(len(backend.changes), 2)

        run_app(self, backend, body)

    def test_update_points_an_old_entry_at_this_pcbridge(self) -> None:
        backend = ClientsBackend()

        async def body(app, pilot):
            await pilot.click("#conn-update-hermes")
            await settle(app, pilot)
            await pilot.pause(0.2)
            self.assertEqual(backend.changes, [("hermes", True)])
            self.assertFalse(app.query_one("#conn-update-hermes", Button).display)

        run_app(self, backend, body)


if __name__ == "__main__":
    unittest.main()
