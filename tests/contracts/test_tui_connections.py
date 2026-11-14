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
from tests.contracts.test_tui import (NARROW, SIZE, FakeBackend, flat, screen_text, settle,  # noqa: E402
                                     words_of)
from textual.widgets import Button, DataTable, TabbedContent  # noqa: E402


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
        return "ok", "done (backup in /home/u/.local/state/pcbridge/backups/1)"

    def connection_preview(self, client, connect):
        self.previews = getattr(self, "previews", []) + [(client, connect)]
        if self.states[client] == "connected" and connect:
            return "ok", "already runs /home/u/.local/bin/pcbridge stdio"
        if connect:
            before = "/old/python -m x" if self.states[client] == "outdated" else "(not registered)"
            return "plan", f"{before} -> /home/u/.local/bin/pcbridge stdio  [/home/u/.{client}/config]"
        return "plan", f"/home/u/.local/bin/pcbridge stdio -> off  [/home/u/.{client}/config]"


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


def keys(app) -> list[str]:
    return [str(k.value) for k in app.query_one("#conn-table", DataTable).rows]


async def select(app, pilot, cid: str) -> None:
    app.query_one("#conn-table", DataTable).move_cursor(row=keys(app).index(cid))
    await settle(app, pilot)


class ConnectionsTabTests(unittest.TestCase):
    def test_the_table_groups_the_clients_and_shows_each_state(self) -> None:
        async def body(app, pilot):
            self.assertEqual(keys(app), ["group:on this computer", "claude-code", "hermes", "opencode",
                                         "group:not found", "oh-my-pi"])
            text = flat(app)
            self.assertIn("2 of 4 local clients use pcbridge through pcbridge stdio, a relay to the "
                          "daemon. No network, no password.", text)
            self.assertIn("● connected", text)
            self.assertIn("▲ old command", text)
            self.assertIn("○ off", text)
            self.assertIn("not installed", text)
            self.assertIn("Every file is backed up before it changes.", text)

        run_app(self, ClientsBackend(), body)

    def test_the_selected_client_shows_what_a_change_would_write(self) -> None:
        backend = ClientsBackend()

        async def body(app, pilot):
            await select(app, pilot, "hermes")
            right = words_of(app.query_one("#conn-right"))
            self.assertIn("what connect would write", right)
            self.assertIn("- /old/python -m x", right)
            self.assertIn("+ /home/u/.local/bin/pcbridge stdio", right)
            self.assertTrue(app.query_one("#conn-update", Button).display)
            await select(app, pilot, "claude-code")
            right = words_of(app.query_one("#conn-right"))
            self.assertIn("what disconnect would write", right)
            self.assertIn("+ (switched off or removed)", right)
            self.assertFalse(app.query_one("#conn-update", Button).display)
            self.assertEqual(backend.changes, [])          # a preview changes nothing

        run_app(self, backend, body)

    def test_space_connects_and_disconnects_and_asks_for_a_client_restart(self) -> None:
        backend = ClientsBackend()

        async def body(app, pilot):
            await select(app, pilot, "opencode")
            await pilot.press("space")
            await settle(app, pilot)
            self.assertEqual(backend.changes, [("opencode", True)])
            self.assertIn("▲ restart Opencode to pick this up", words_of(app.query_one("#conn-right")))
            self.assertIn("/home/u/.local/state/pcbridge/backups/1", words_of(app.query_one("#conn-right")))
            await select(app, pilot, "claude-code")
            await pilot.click("#conn-toggle")
            await settle(app, pilot)
            self.assertEqual(backend.changes[-1], ("claude-code", False))

        run_app(self, backend, body)

    def test_update_points_an_old_entry_at_this_pcbridge(self) -> None:
        backend = ClientsBackend()

        async def body(app, pilot):
            await select(app, pilot, "hermes")
            await pilot.press("u")
            await settle(app, pilot)
            self.assertEqual(backend.changes, [("hermes", True)])
            self.assertFalse(app.query_one("#conn-update", Button).display)

        run_app(self, backend, body)

    def test_a_missing_client_cannot_be_switched_and_says_how_to_continue(self) -> None:
        backend = ClientsBackend()

        async def body(app, pilot):
            await select(app, pilot, "oh-my-pi")
            self.assertIn("Install Oh-My-Pi, then press Look again g.", words_of(app.query_one("#conn-right")))
            await pilot.press("space")
            await settle(app, pilot)
            self.assertEqual(backend.changes, [])

        run_app(self, backend, body)

    def test_the_tab_at_80_columns(self) -> None:
        async def go():
            app = PcbridgeApp(ClientsBackend())
            async with app.run_test(size=NARROW) as pilot:
                await settle(app, pilot)
                await pilot.press("4")
                await settle(app, pilot)
                self.assertTrue(all(len(r) == 80 for r in screen_text(app)))
                self.assertIn("CLIENT", flat(app))

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
