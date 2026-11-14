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

from pcbridge import paths as pathslib  # noqa: E402
from pcbridge.config import ConfigError  # noqa: E402
from pcbridge import settings as S  # noqa: E402
from pcbridge.desktop import session  # noqa: E402
from pcbridge.desktop.panelicon import PanelIconError  # noqa: E402
from pcbridge.tui.app import Confirm, PcbridgeApp  # noqa: E402
from pcbridge.tui.settings_pane import block_id  # noqa: E402
from pcbridge.tui.widgets import NumberInput, Segmented  # noqa: E402
from pcbridge.tui.backend import Backend  # noqa: E402
from tests.contracts.test_settings import PASSWORD, TOKEN, example_config  # noqa: E402
from tests.contracts.test_tui import (NARROW, SIZE, FakeBackend, flat, screen_text, settle,  # noqa: E402
                                     words_of)
from textual.widgets import Input, OptionList, Switch, TabbedContent, TabPane  # noqa: E402


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


class UnreadableConfigTests(unittest.TestCase):
    def test_invalid_config_is_shown_without_enabling_control(self) -> None:
        class InvalidConfigBackend(FakeBackend):
            def editor(self):
                raise ConfigError("Fix the invalid desktop setting.")

            def panel_icon_status(self):
                return None, "No panel icon is available."

        async def go():
            backend = InvalidConfigBackend(config_error="Fix the invalid desktop setting.")
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertIn("Fix the invalid desktop setting",
                              str(app.query_one("#settings-message").render()))
                self.assertIsNone(app.cfg)
                self.assertTrue(app.query_one("#grant-button").disabled)
                self.assertTrue(app.query_one("#settings-save").disabled)
                self.assertEqual(backend.calls, [])

        asyncio.run(go())

    def test_missing_config_preserves_every_tab_without_grant_or_file_writes(self) -> None:
        class MissingConfigBackend(FakeBackend):
            load_config = Backend.load_config
            editor = Backend.editor
            panel_icon_status = Backend.panel_icon_status

        async def go(root):
            backend = MissingConfigBackend()
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertEqual({pane.id for pane in app.query(TabPane)},
                                 {"overview", "desktop", "jobs", "connections", "remote", "tools", "activity", "logs", "advanced"})
                self.assertIsNone(app.cfg)
                self.assertTrue(app.query_one("#grant-button").disabled)
                self.assertIn("No pcbridge config file found", app.cfg_error)
                self.assertIn("pcbridge setup", str(app.query_one("#settings-message").render()))
                for tab in ("overview", "desktop", "jobs", "connections", "remote", "tools", "activity", "logs", "advanced"):
                    app.query_one(TabbedContent).active = tab
                    await settle(app, pilot)
                    self.assertEqual(app.query_one(TabbedContent).active, tab)
                app.query_one(TabbedContent).active = "advanced"
                await settle(app, pilot)
                self.assertTrue(app.query_one("#settings-save").disabled)
                self.assertTrue(app.query_one("#settings-discard").disabled)
                app.query_one(TabbedContent).active = "overview"
                await pilot.pause()
                await pilot.click("#grant-button")
                await pilot.press("l", "r")
                await settle(app, pilot)
                self.assertEqual(backend.calls, [])
                self.assertFalse(backend.open)
                self.assertEqual(list(root.iterdir()), [])

        for kind in (session.GNOME, session.KDE, session.HYPRLAND, session.UNKNOWN):
            with self.subTest(desktop=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = {k: v for k, v in os.environ.items() if not k.startswith("PCBRIDGE_")}
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(pathslib, "config_file", return_value=root / "config.toml"), \
                        mock.patch.object(pathslib, "LEGACY_REPO_CONFIG", root / "legacy.toml"), \
                        mock.patch.object(session, "desktop_kind", return_value=kind), \
                        mock.patch.object(session, "support_note", return_value=""), \
                        mock.patch("pcbridge.desktop.panelicon.get_mode",
                                   side_effect=PanelIconError("not installed")):
                    asyncio.run(go(root))


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
                app.query_one(TabbedContent).active = "advanced"
                await pilot.pause()
                await body(app, pilot)

        asyncio.run(go())

    async def select(self, app, pilot, key: str):
        """Filter down to one setting, as a person typing in the filter box would."""
        await pilot.click("#settings-filter")
        app.query_one("#settings-filter", Input).value = key
        await settle(app, pilot)
        block = app.query_one(f"#{block_id(key)}")
        return block

    def control(self, app, key: str):
        return app.query_one(f"#ctl-{block_id(key)}")

    def pending(self, app) -> str:
        return str(app.query_one("#settings-pending").render())

    def test_sections_and_blocks(self) -> None:
        async def body(app, pilot):
            sections = app.query_one("#settings-sections", OptionList)
            titles = [str(sections.get_option_at_index(i).prompt).strip()
                      for i in range(sections.option_count)]
            self.assertEqual(titles[:3], ["General", "Server", "Authentication"])
            self.assertIn("Agent: claude", titles)
            self.assertNotIn("Panel icon", titles)        # it lives in the Desktop tab now
            self.assertTrue(app.query(f"#{block_id('port')}"))
            sections.highlighted = titles.index("Desktop control")
            await settle(app, pilot)
            self.assertTrue(app.query(f"#{block_id('desktop.enabled')}"))
            self.assertFalse(app.query(f"#{block_id('port')}"))
            self.assertIsInstance(self.control(app, "desktop.enabled"), Switch)
            self.assertIsInstance(self.control(app, "desktop.unlock_idle_seconds"), NumberInput)
            text = words_of(app.query_one(f"#{block_id('desktop.unlock_idle_seconds')}"))
            self.assertIn("unlock_idle_seconds restart daemon", text)
            self.assertIn("default 90 s · 0 (off) or 10 s and more", text)
            self.assertIn("No unsaved changes. ◦ marks a value that differs from the default.", flat(app))
            screen = app.export_screenshot()
            self.assertNotIn(PASSWORD, screen)

        self.run_app(body)

    def test_default_agent_is_a_segmented_choice(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "default_agent")
            picker = self.control(app, "default_agent")
            self.assertIsInstance(picker, Segmented)
            self.assertEqual([v for v, _l in picker.options][:1], ["claude"])
            picker.focus()
            await pilot.press("right")
            await settle(app, pilot)
            self.assertIn("1 unsaved change", self.pending(app))

        self.run_app(body)

    def test_a_switch_click_then_save_asks_for_a_sensitive_setting(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.enabled")
            await pilot.click(f"#ctl-{block_id('desktop.enabled')}")
            await settle(app, pilot)
            self.assertIn("1 unsaved change", self.pending(app))
            self.assertEqual(self.path.read_text(), self.original)  # staged only
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            self.assertIs(tomllib.loads(self.path.read_text())["desktop"]["enabled"], True)
            self.assertEqual(len(list(self.dir.glob("config.toml.backup-*"))), 1)
            self.assertEqual(app.restart_due, 1)
            self.assertIn("▲ 1 saved change applies after restarting the daemon", flat(app))

        self.run_app(body)

    def test_keep_awake_applies_without_a_restart(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "power.keep_awake")
            self.assertIn("applies now", flat(app))
            switch = self.control(app, "power.keep_awake")
            self.assertFalse(switch.value)                        # off by default
            await pilot.click(f"#ctl-{block_id('power.keep_awake')}")
            await settle(app, pilot)
            await pilot.press("ctrl+s")
            await settle(app, pilot)
            self.assertIs(tomllib.loads(self.path.read_text())["power"]["keep_awake"], True)
            self.assertEqual(app.restart_due, 0)

        self.run_app(body)

    def test_a_number_out_of_range_is_refused_in_place(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.pointer_speed")
            number = self.control(app, "desktop.pointer_speed")
            field = number.query_one(Input)
            field.value = "50"
            field.focus()
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("takes 0 (off) or 200 px/s to 100000 px/s",
                          str(app.query_one(f"#{block_id('desktop.pointer_speed')} .key-error").render()))
            self.assertIn("No unsaved changes", self.pending(app))
            field.value = "3000"
            await pilot.press("enter")
            await settle(app, pilot)
            await pilot.click("#settings-save")
            await settle(app, pilot)
            self.assertEqual(tomllib.loads(self.path.read_text())["desktop"]["pointer_speed"], 3000)

        self.run_app(body)

    def test_a_value_the_loader_refuses_is_not_written(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.unlock_default_minutes")
            number = self.control(app, "desktop.unlock_default_minutes")
            number.set_value(500)
            await settle(app, pilot)
            await pilot.click("#settings-save")
            await settle(app, pilot)
            self.assertEqual(self.path.read_text(), self.original)
            self.assertIn("1 unsaved change", self.pending(app))
            await pilot.click("#settings-discard")
            await settle(app, pilot)
            self.assertIn("No unsaved changes", self.pending(app))

        self.run_app(body)

    def test_reset_key_puts_the_default_back(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.idle_guard_seconds")
            number = self.control(app, "desktop.idle_guard_seconds")
            number.set_value(30)
            await settle(app, pilot)
            await pilot.click("#settings-save")
            await settle(app, pilot)
            self.assertEqual(tomllib.loads(self.path.read_text())["desktop"]["idle_guard_seconds"], 30)
            await self.select(app, pilot, "desktop.idle_guard_seconds")
            self.assertIn("◦ idle_guard_seconds", flat(app).replace("desktop.", ""))
            self.control(app, "desktop.idle_guard_seconds").query_one(Input).focus()
            await pilot.press("ctrl+z")
            await settle(app, pilot)
            await pilot.press("ctrl+s")
            await settle(app, pilot)
            self.assertNotEqual(tomllib.loads(self.path.read_text())["desktop"].get("idle_guard_seconds"), 30)

        self.run_app(body)

    def test_a_generated_token_is_stored_as_a_hash_and_shown_once(self) -> None:
        from pcbridge import hashing
        from pcbridge.tui.dialogs import ShowOnce

        async def body(app, pilot):
            await self.select(app, pilot, "auth.static_token")
            self.assertIn("✓ set · hashed", flat(app))
            await pilot.click(f"#ctl-{block_id('auth.static_token')}")
            await settle(app, pilot)
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertIsInstance(app.screen, Confirm)
            await pilot.click("#confirm-yes")
            await settle(app, pilot)
            stored = tomllib.loads(self.path.read_text())["auth"]["static_token"]
            self.assertTrue(stored.startswith("sha256$"), stored)
            # The token is on screen once, until the user says it is saved.
            self.assertIsInstance(app.screen, ShowOnce)
            shown = app.screen.secret
            self.assertTrue(hashing.verify_token(shown, stored))
            self.assertNotIn(shown, self.path.read_text())
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, ShowOnce)
            self.assertTrue(app.screen.query_one("#once-done").disabled)
            await pilot.click("#once-saved")
            await pilot.click("#once-done")
            await settle(app, pilot)
            self.assertNotIsInstance(app.screen, ShowOnce)
            screen = app.export_screenshot()
            self.assertNotIn(shown, screen)
            self.assertNotIn(stored, screen)
            self.assertNotIn(TOKEN, screen)

        self.run_app(body)

    def test_the_password_is_replaced_through_the_login_check(self) -> None:
        from pcbridge.tui.remote_pane import LoginScreen

        async def body(app, pilot):
            await self.select(app, pilot, "auth.password")
            self.assertEqual(len(app.query(".edit-input")), 0)
            self.assertIn("✓ set · hashed", flat(app))
            await pilot.click(f"#ctl-{block_id('auth.password')}")
            await pilot.pause()
            self.assertIsInstance(app.screen, LoginScreen)

        self.run_app(body)

    def test_quitting_with_unsaved_changes_asks(self) -> None:
        async def body(app, pilot):
            await self.select(app, pilot, "desktop.restore_clipboard")
            self.control(app, "desktop.restore_clipboard").value = False
            await settle(app, pilot)
            app.set_focus(None)
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
            field = self.control(app, "agents.claude.command")
            field.focus()
            await pilot.press("enter")  # apply the value as shown
            await settle(app, pilot)
            await pilot.click("#settings-save")
            await settle(app, pilot)
            after = self.path.read_text()
            self.assertEqual(tomllib.loads(after)["agents"]["claude"]["command"],
                             tomllib.loads(self.original)["agents"]["claude"]["command"])
            self.assertTrue(re.search(r"command = \[\n", after))

        self.run_app(body)

    def test_the_tab_at_80_columns(self) -> None:
        async def go():
            app = PcbridgeApp(EditorBackend(self.path))
            async with app.run_test(size=NARROW) as pilot:
                await settle(app, pilot)
                await pilot.press("9")
                await settle(app, pilot)
                rows = screen_text(app)
                self.assertTrue(all(len(r) == 80 for r in rows))
                self.assertIn("General", flat(app))
                self.assertIn("Save ^s", flat(app))

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
