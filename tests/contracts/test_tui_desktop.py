"""The Desktop tab: the grant with its duration picker, switches and numbers
that save at once to a throwaway config, the panel icon and the native
helper, at 120 and 80 columns."""

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

from pcbridge import settings as S  # noqa: E402
from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.dialogs import Confirm  # noqa: E402
from tests.contracts.test_settings import example_config  # noqa: E402
from tests.contracts.test_tui import (NARROW, SIZE, FakeBackend, flat, screen_text, settle,  # noqa: E402
                                     words_of)
from textual.widgets import Switch  # noqa: E402


class FileBackend(FakeBackend):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def editor(self):
        return S.ConfigEditor(self.path)

    def load_config(self):
        from pcbridge.config import load_config

        return load_config(str(self.path))


class DesktopTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.toml"
        self.path.write_text(example_config(self.dir / "state"))
        self.path.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PCBRIDGE_")}
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        self.backend = FileBackend(self.path)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def desktop(self) -> dict:
        return tomllib.loads(self.path.read_text())["desktop"]

    def run_tab(self, body, size=SIZE):
        async def go():
            app = PcbridgeApp(self.backend)
            async with app.run_test(size=size) as pilot:
                await settle(app, pilot)
                await pilot.press("2")
                await settle(app, pilot)
                await body(app, pilot)

        asyncio.run(go())

    def test_the_tab_at_120_columns(self) -> None:
        async def body(app, pilot):
            text = words_of(app.query_one("#desk-left")) + " " + words_of(app.query_one("#desk-right"))
            self.assertIn("GRANT", text)
            self.assertIn("◆ Locked. Agents cannot use the keyboard, the pointer or the screen.", text)
            self.assertIn("For [ 5 min 15 min 30 min 1 h 2 h ]", flat(app))
            self.assertIn("Unlock for 15 min l", text)
            self.assertIn("The l key asks before it unlocks; a click acts at once. Locking never asks.", text)
            self.assertIn("Desktop control desktop.enabled", text)
            self.assertIn("Show pcbridge in the top bar", text)
            self.assertIn("● locked ● open ● working ● error", text)
            self.assertIn("NATIVE HELPER ✓ healthy", flat(app))
            self.assertIn("✓ capture", text)
            self.assertIn("▲ keyboard", text)
            self.assertTrue(app.query_one("#sw-panel-icon", Switch).value)
            self.assertIn("space Toggle", screen_text(app)[-1])

        self.run_tab(body)

    def test_a_switch_saves_at_once_and_raises_the_restart_notice(self) -> None:
        async def body(app, pilot):
            before = self.desktop().get("unlock_notification", True)
            await pilot.click("#sw-desktop-unlock_notification")
            await settle(app, pilot)
            self.assertEqual(self.desktop()["unlock_notification"], not before)
            self.assertEqual(app.restart_due, 1)
            self.assertIn("▲ 1 saved change applies after restarting the daemon", flat(app))
            self.assertIn("Restart now r", flat(app))

        self.run_tab(body)

    def test_turning_desktop_control_on_asks_first(self) -> None:
        self.assertFalse(self.desktop().get("enabled", False))

        async def body(app, pilot):
            await pilot.click("#sw-desktop-enabled")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            await pilot.click("#confirm-no")
            await settle(app, pilot)
            self.assertFalse(self.desktop().get("enabled", False))
            await pilot.click("#sw-desktop-enabled")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            self.assertTrue(self.desktop()["enabled"])

        self.run_tab(body)

    def test_numbers_step_and_refuse_out_of_range(self) -> None:
        async def body(app, pilot):
            await pilot.click("#num-idle_guard_seconds .num-up")
            await settle(app, pilot)
            self.assertEqual(self.desktop()["idle_guard_seconds"], 61)
            field = app.query_one("#num-unlock_idle_seconds Input")
            field.value = "5"
            field.focus()
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertNotEqual(self.desktop().get("unlock_idle_seconds"), 5)

        self.run_tab(body)

    def test_the_picker_sets_the_unlock_time_and_the_clock_restarts(self) -> None:
        backend = self.backend

        async def body(app, pilot):
            app.query_one("#grant-minutes").value = "30"
            await pilot.pause()
            self.assertIn("Unlock for 30 min l", flat(app))
            await pilot.click("#desk-grant")
            await settle(app, pilot)
            self.assertEqual(backend.calls, ["unlock 30"])
            self.assertIn("◇ Open · 12:34 left", flat(app))
            self.assertIn("Restart the clock at 30 min", flat(app))
            await pilot.click("#desk-reclock")
            await settle(app, pilot)
            self.assertEqual(backend.calls, ["unlock 30", "unlock 30"])
            await pilot.click("#desk-grant")
            await settle(app, pilot)
            self.assertEqual(backend.calls[-1], "lock")

        self.run_tab(body)

    def test_the_panel_icon_switch(self) -> None:
        async def body(app, pilot):
            await pilot.click("#sw-panel-icon")
            await settle(app, pilot)
            self.assertIn("panel when-granted", self.backend.calls)
            self.assertFalse(app.query_one("#sw-panel-icon", Switch).value)

        self.run_tab(body)

    def test_the_tab_at_80_columns(self) -> None:
        async def body(app, pilot):
            rows = screen_text(app)
            self.assertTrue(all(len(r) == 80 for r in rows))
            self.assertIn("GRANT", flat(app))
            self.assertIn("DESKTOP CONTROL", flat(app))

        self.run_tab(body, size=NARROW)


if __name__ == "__main__":
    unittest.main()
