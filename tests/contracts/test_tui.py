"""The terminal UI, driven headless with Textual's pilot: real widgets, real
mouse clicks and keys, a fake backend. Nothing here touches the machine's
config, grant or daemon.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli.grant import GrantError, GrantState  # noqa: E402
from pcbridge.config import ConfigError  # noqa: E402
from pcbridge.tui.app import Confirm, PcbridgeApp  # noqa: E402
from pcbridge.tui.backend import Backend  # noqa: E402

SIZE = (130, 40)


class FakeBackend(Backend):
    def __init__(self, enabled: bool = True, config_error: str = "") -> None:
        self.enabled = enabled
        self.config_error = config_error
        self.open = False
        self.calls: list[str] = []
        self.cfg = SimpleNamespace(
            desktop=SimpleNamespace(enabled=enabled, unlock_default_minutes=15),
            tools_profile="core", source_path=Path("/nowhere/config.toml"), warnings=[],
        )

    def load_config(self):
        if self.config_error:
            raise ConfigError(self.config_error)
        return self.cfg

    def grant_state(self, cfg):
        return GrantState(self.open, 754 if self.open else 0, self.enabled, "",
                          754 if self.open else 0)

    def lock(self, cfg):
        self.calls.append("lock")
        self.open = False
        return "Desktop control closed."

    def unlock(self, cfg, minutes):
        self.calls.append(f"unlock {minutes}")
        if not self.enabled:
            raise GrantError("disabled")
        self.open = True
        return "Desktop control open for 15 min."

    def status(self):
        return {"version": "x", "install": "git", "service": "active", "socket_unit": "active",
                "daemon": {"version": "9.9", "pid": 42}, "jobs_running": [], "remote_tunnel": "closed"}

    def restart(self, cfg):
        self.calls.append("restart")
        return "daemon 9.9 running, pid 43"

    def editor(self):
        raise NotImplementedError

    def tools(self, cfg):
        return []

    def clients(self):
        return []


def run(coro):
    return asyncio.run(coro)


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def text_of(app, selector: str) -> str:
    return str(app.query_one(selector).render())


class FrameTests(unittest.TestCase):
    def test_the_bar_shows_the_grant_and_a_click_unlocks_then_locks(self) -> None:
        backend = FakeBackend()

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertIn("Desktop: locked", text_of(app, "#bar-grant"))
                button = app.query_one("#grant-button")
                self.assertEqual(str(button.label), "Unlock 15 min")
                self.assertFalse(button.disabled)
                self.assertIn("daemon: running", text_of(app, "#bar-daemon"))

                await pilot.click("#grant-button")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["unlock None"])
                self.assertIn("OPEN, 12:34 left", text_of(app, "#bar-grant"))
                self.assertEqual(str(button.label), "Lock now")

                await pilot.press("l")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["unlock None", "lock"])
                self.assertIn("locked", text_of(app, "#bar-grant"))

        run(go())

    def test_the_key_asks_before_it_unlocks_but_locks_at_once(self) -> None:
        backend = FakeBackend()

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                await pilot.press("l")
                await pilot.pause()
                self.assertIsInstance(app.screen, Confirm)
                await pilot.click("#confirm-no")
                await settle(app, pilot)
                self.assertEqual(backend.calls, [])
                await pilot.press("l")
                await pilot.pause()
                await pilot.click("#confirm-yes")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["unlock None"])
                await pilot.press("l")
                await settle(app, pilot)
                self.assertNotIsInstance(app.screen, Confirm)
                self.assertEqual(backend.calls, ["unlock None", "lock"])

        run(go())

    def test_desktop_control_off_in_the_config_disables_the_button(self) -> None:
        backend = FakeBackend(enabled=False)

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertTrue(app.query_one("#grant-button").disabled)
                self.assertIn("off in config", text_of(app, "#bar-grant"))
                await pilot.press("l")
                await settle(app, pilot)
                self.assertEqual(backend.calls, [])

        run(go())

    def test_a_broken_config_still_opens(self) -> None:
        backend = FakeBackend(config_error="port must be a number")

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertIn("config error", text_of(app, "#bar-grant"))
                self.assertIn("port must be a number", text_of(app, "#overview-body"))
                await pilot.press("r")
                await settle(app, pilot)
                self.assertNotIsInstance(app.screen, Confirm)

        run(go())

    def test_overview_lists_the_status(self) -> None:
        async def go():
            app = PcbridgeApp(FakeBackend())
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                body = text_of(app, "#overview-body")
                self.assertIn("pcbridge 9.9, pid 42", body)
                self.assertIn("core: 21 of 37 tools offered", body)
                self.assertIn("/nowhere/config.toml", body)

        run(go())

    def test_tabs_switch_with_the_mouse_and_keys(self) -> None:
        from pcbridge.cli.main import COMMANDS
        from textual.widgets import DataTable, TabbedContent

        async def go():
            app = PcbridgeApp(FakeBackend())
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                await pilot.click("#--content-tab-commands")
                await pilot.pause()
                self.assertEqual(app.query_one(TabbedContent).active, "commands")
                self.assertEqual(app.query_one("#commands-table", DataTable).row_count, len(COMMANDS))
                await pilot.press("1")
                await pilot.pause()
                self.assertEqual(app.query_one(TabbedContent).active, "overview")

        run(go())

    def test_callbacks_after_teardown_do_nothing(self) -> None:
        """The 1 s timer and worker callbacks can land while the app is shutting
        down and its widgets are gone (seen on a slow CI runner); they must
        return quietly instead of raising NoMatches."""
        from pcbridge.tui.tools_pane import ToolsPane

        async def go():
            app = PcbridgeApp(FakeBackend())
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                tools = app.query_one(ToolsPane)
                await app.query_one("#bar").remove()
                await app.query_one("#overview-body").remove()
                await tools.query_one("#tools-table").remove()
                await pilot.pause()
                app.refresh_grant()
                app._show_status(FakeBackend().status())
                app._grant_done()
                tools._loaded([], "", "full")
                await pilot.pause()

        run(go())

    def test_restart_asks_first(self) -> None:
        backend = FakeBackend()

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                await pilot.press("r")
                await pilot.pause()
                self.assertIsInstance(app.screen, Confirm)
                await pilot.click("#confirm-no")
                await settle(app, pilot)
                self.assertEqual(backend.calls, [])
                await pilot.press("r")
                await pilot.pause()
                await pilot.click("#confirm-yes")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["restart"])

        run(go())


if __name__ == "__main__":
    unittest.main()
