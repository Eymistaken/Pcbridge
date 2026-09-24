"""The Settings tab of the terminal UI, on a throwaway copy of
config.example.toml: clicks and keys stage changes, Save writes them with
the same checks as `pcbridge set`, secrets never reach the screen.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import settings as S  # noqa: E402
from pcbridge.desktop.panelicon import PanelIconError  # noqa: E402
from pcbridge.tui.app import Confirm, PcbridgeApp  # noqa: E402
from tests.contracts.test_settings import PASSWORD, TOKEN, example_config  # noqa: E402
from tests.contracts.test_tui import SIZE, FakeBackend, settle  # noqa: E402
from textual.widgets import DataTable, Input, OptionList, Switch, TabbedContent  # noqa: E402


class EditorBackend(FakeBackend):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.panel_mode = "always"
        self.panel_note = "Changes apply immediately."
        self.panel_error = ""
        self.panel_calls: list[str] = []

    def editor(self):
        return S.ConfigEditor(self.path)

    def panel_icon_status(self):
        return self.panel_mode, self.panel_note

    def set_panel_icon_mode(self, mode: str):
        self.panel_calls.append(mode)
        if self.panel_error:
            raise PanelIconError(self.panel_error)
        self.panel_mode = mode
        return self.panel_icon_status()


class SettingsTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.toml"
        self.original = example_config(self.dir / "state")
        self.path.write_text(self.original)
        self.path.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PCBRIDGE_")}
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def run_app(self, body, backend: EditorBackend | None = None) -> None:
        async def go():
            app = PcbridgeApp(backend or EditorBackend(self.path))
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                app.query_one(TabbedContent).active = "settings"
                await pilot.pause()
                await body(app, pilot)

        asyncio.run(go())

    async def panel_section(self, app, pilot) -> None:
        sections = app.query_one("#settings-sections", OptionList)
        titles = [str(sections.get_option_at_index(i).prompt)
                  for i in range(sections.option_count)]
        sections.highlighted = titles.index("Panel icon")
        await settle(app, pilot)

    async def select(self, app, pilot, key: str) -> None:
        """Filter down to one setting, as a person typing in the filter box would."""
        await pilot.click("#settings-filter")
        app.query_one("#settings-filter", Input).value = key
        await pilot.pause()
        table = app.query_one("#settings-table", DataTable)
        keys = [str(k.value) for k in table.rows]
        table.move_cursor(row=keys.index(key))
        await pilot.pause()
        self.assertEqual(str(app.query_one("#editor-title").render()), key)

    def test_sections_and_rows(self) -> None:
        async def body(app, pilot):
            sections = app.query_one("#settings-sections", OptionList)
            titles = [str(sections.get_option_at_index(i).prompt) for i in range(sections.option_count)]
            self.assertEqual(titles[:3], ["General", "Server", "Authentication"])
            self.assertIn("Agent: claude", titles)
            table = app.query_one("#settings-table", DataTable)
            self.assertIn("port", [str(k.value) for k in table.rows])
            # Click a section with the mouse.
            sections.highlighted = titles.index("Desktop control")
            await pilot.pause()
            keys = [str(k.value) for k in table.rows]
            self.assertIn("desktop.enabled", keys)
            self.assertNotIn("port", keys)
            screen = app.export_screenshot()
            self.assertNotIn(PASSWORD, screen)

        self.run_app(body)

    def test_panel_icon_changes_immediately_without_editing_config(self) -> None:
        backend = EditorBackend(self.path)

        async def body(app, pilot):
            await self.panel_section(app, pilot)
            self.assertIn("shown all the time", str(app.query_one("#panel-icon-state").render()))
            await pilot.click("#panel-icon-hide")
            await settle(app, pilot)
            self.assertEqual(backend.panel_calls, ["when-granted"])
            self.assertIn("hidden while desktop control is closed",
                          str(app.query_one("#panel-icon-state").render()))
            self.assertEqual(self.path.read_text(), self.original)
            self.assertIn("differs from the default", str(app.query_one("#settings-pending").render()))
            await pilot.click("#panel-icon-show")
            await settle(app, pilot)
            self.assertEqual(backend.panel_calls, ["when-granted", "always"])
            self.assertIn("shown all the time", str(app.query_one("#panel-icon-state").render()))

        self.run_app(body, backend)

    def test_panel_icon_unavailable_and_write_failure(self) -> None:
        backend = EditorBackend(self.path)
        backend.panel_mode = None
        backend.panel_note = "KDE Plasma has no pcbridge panel icon."

        async def unavailable(app, pilot):
            await self.panel_section(app, pilot)
            self.assertIn("KDE Plasma", str(app.query_one("#panel-icon-note").render()))
            self.assertTrue(app.query_one("#panel-icon-show").disabled)
            self.assertTrue(app.query_one("#panel-icon-hide").disabled)

        self.run_app(unavailable, backend)

        backend.panel_mode = "always"
        backend.panel_error = "gsettings failed"

        async def failed(app, pilot):
            await self.panel_section(app, pilot)
            await pilot.click("#panel-icon-hide")
            await settle(app, pilot)
            self.assertEqual(backend.panel_mode, "always")
            self.assertIn("gsettings failed", str(app.query_one("#panel-icon-note").render()))
            self.assertEqual(self.path.read_text(), self.original)

        self.run_app(failed, backend)

    def test_a_switch_click_then_save_asks_for_a_sensitive_setting(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.enabled")
            await pilot.click(".edit-switch")
            await pilot.pause()
            self.assertIn("1 unsaved change", str(app.query_one("#settings-pending").render()))
            self.assertEqual(self.path.read_text(), self.original)  # staged only
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            self.assertIs(tomllib.loads(self.path.read_text())["desktop"]["enabled"], True)
            self.assertEqual(len(list(self.dir.glob("config.toml.backup-*"))), 1)
            self.assertTrue(app.query_one("#settings-restart").display)

        self.run_app(body)

    def test_a_bad_number_is_refused_in_place(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.pointer_speed")
            box = app.query_one(".edit-input", Input)
            box.value = "fast"
            await pilot.click(".edit-apply")
            await pilot.pause()
            self.assertIn("whole number", str(app.query_one("#editor-error").render()))
            # A button ignores a second press while it still shows the first one.
            await pilot.pause(0.3)
            box.value = "3000"
            await pilot.click(".edit-apply")
            await pilot.pause()
            await pilot.click("#settings-save")
            await settle(app, pilot)
            self.assertEqual(tomllib.loads(self.path.read_text())["desktop"]["pointer_speed"], 3000)

        self.run_app(body)

    def test_a_value_the_loader_refuses_is_not_written(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.unlock_default_minutes")
            app.query_one(".edit-input", Input).value = "500"
            await pilot.click(".edit-apply")
            await pilot.pause()
            await pilot.click("#settings-save")
            await settle(app, pilot)
            self.assertEqual(self.path.read_text(), self.original)
            self.assertIn("1 unsaved change", str(app.query_one("#settings-pending").render()))
            # Default puts it back; nothing is pending any more.
            await pilot.click(".edit-reset")
            await pilot.pause()
            await pilot.click("#settings-discard")
            await pilot.pause()
            self.assertIn("differs from the default", str(app.query_one("#settings-pending").render()))

        self.run_app(body)

    def test_a_generated_token_is_saved_but_never_shown(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "auth.static_token")
            await pilot.click(".edit-generate")
            await pilot.pause()
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            token = tomllib.loads(self.path.read_text())["auth"]["static_token"]
            self.assertNotEqual(token, TOKEN)
            screen = app.export_screenshot()
            self.assertNotIn(token, screen)
            self.assertNotIn(TOKEN, screen)

        self.run_app(body)

    def test_quitting_with_unsaved_changes_asks(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.restore_clipboard")
            app.query_one(".edit-switch", Switch).value = False
            await pilot.pause()
            app.query_one(DataTable).focus()
            await pilot.press("q")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            await pilot.click("#confirm-no")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, Confirm)
            self.assertEqual(self.path.read_text(), self.original)

        self.run_app(body)

    def test_the_example_file_round_trips_through_the_tab_untouched(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "agents.claude.command")
            await pilot.click(".edit-apply")  # apply the value as shown
            await pilot.pause()
            await pilot.click("#settings-save")
            await settle(app, pilot)
            after = self.path.read_text()
            self.assertEqual(tomllib.loads(after)["agents"]["claude"]["command"],
                             tomllib.loads(self.original)["agents"]["claude"]["command"])
            self.assertTrue(re.search(r"command = \[\n", after))

        self.run_app(body)


if __name__ == "__main__":
    unittest.main()
