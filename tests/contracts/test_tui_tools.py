"""The Tools tab of the terminal UI with the real catalog of a `core`
profile: search as you type, the offered-only filter, details on click.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from tests.contracts.test_toolcatalog import catalog  # noqa: E402
from tests.contracts.test_tui import (NARROW, SIZE, FakeBackend, flat, screen_text, settle,  # noqa: E402
                                     words_of)
from textual.widgets import DataTable, TabbedContent  # noqa: E402

CORE = catalog("core", enabled=True)


class CatalogBackend(FakeBackend):
    def tools(self, cfg):
        return CORE


def rows(app) -> list[str]:
    return [str(k.value) for k in app.query_one("#tools-table", DataTable).rows]


class ToolsTabTests(unittest.TestCase):
    def run_app(self, body) -> None:
        async def go():
            app = PcbridgeApp(CatalogBackend())
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                await pilot.click("#--content-tab-tools")
                await settle(app, pilot)
                await body(app, pilot)

        asyncio.run(go())

    def test_every_tool_is_listed_with_the_profile_count(self) -> None:
        async def body(app, pilot):
            self.assertEqual(len(rows(app)), 40)
            self.assertIn("40 shown · 24 of 40 offered locally", str(app.query_one("#tools-count").render()))
            text = flat(app)
            self.assertIn("Profile [ full core desktop readonly ] local · remote: core", text)
            self.assertIn("[ All 40 Offered 24 Hidden 16 ]", text)
            self.assertEqual(app.query_one("#tools-profile").value, "core")

        self.run_app(body)

    def test_typing_searches_by_name_and_description(self) -> None:
        async def body(app, pilot):
            await pilot.press("/")
            await pilot.press(*"screenshot")
            await pilot.pause()
            found = rows(app)
            self.assertIn("screen_capture", found)
            self.assertNotIn("job_cancel", found)
            self.assertIn("name matches first", str(app.query_one("#tools-count").render()))
            self.assertIn(found[0], str(app.query_one("#tool-title").render()))

        self.run_app(body)

    def test_offered_and_hidden_filters(self) -> None:
        async def body(app, pilot):
            app.query_one("#tools-show").value = "offered"
            await pilot.pause()
            self.assertEqual(len(rows(app)), 24)
            self.assertNotIn("mouse", rows(app))
            app.query_one("#tools-show").value = "hidden"
            await pilot.pause()
            self.assertEqual(len(rows(app)), 16)
            self.assertIn("mouse", rows(app))

        self.run_app(body)

    def test_clicking_a_row_shows_its_details_and_hints(self) -> None:
        async def body(app, pilot):
            table = app.query_one("#tools-table", DataTable)
            target = rows(app).index("agent_run")
            table.move_cursor(row=target)
            await pilot.pause()
            self.assertIn("agent_run", str(app.query_one("#tool-title").render()))
            text = flat(app)
            self.assertIn("PARAMETERS", text)
            self.assertIn("required", text)
            self.assertIn("changes things", text)
            self.assertIn("Offered by the core profile.", text)
            table.move_cursor(row=rows(app).index("job_list"))
            await pilot.pause()
            self.assertIn("read-only", flat(app))
            # A tool the profile leaves out says why.
            table.move_cursor(row=rows(app).index("mouse"))
            await pilot.pause()
            self.assertIn("Hidden by the core profile; a call by name is refused and audited.",
                          words_of(app.query_one("#tools-detail")))
            self.assertEqual(app.query_one(TabbedContent).active, "tools")

        self.run_app(body)

    def test_changing_the_profile_saves_it_and_asks_for_a_restart(self) -> None:
        import tempfile
        import tomllib

        from tests.contracts.test_settings import example_config
        from tests.contracts.test_tui_desktop import FileBackend

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(example_config(Path(tmp) / "state"))
            path.chmod(0o600)

            class Backend(FileBackend):
                def tools(self, cfg):
                    return catalog(cfg.tools_profile, enabled=True)

            async def go():
                app = PcbridgeApp(Backend(path))
                async with app.run_test(size=SIZE) as pilot:
                    await settle(app, pilot)
                    await pilot.press("6")
                    await settle(app, pilot)
                    self.assertEqual(app.query_one("#tools-profile").value, "full")
                    app.query_one("#tools-profile").value = "readonly"
                    await settle(app, pilot)
                    self.assertEqual(tomllib.loads(path.read_text())["tools"]["profile"], "readonly")
                    self.assertIn("▲ 1 saved change applies after restarting the daemon and the clients",
                                  flat(app))

            with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                              if not k.startswith("PCBRIDGE_")}, clear=True):
                asyncio.run(go())

    def test_the_tab_at_80_columns(self) -> None:
        async def go():
            app = PcbridgeApp(CatalogBackend())
            async with app.run_test(size=NARROW) as pilot:
                await settle(app, pilot)
                await pilot.press("6")
                await settle(app, pilot)
                self.assertTrue(all(len(r) == 80 for r in screen_text(app)))
                self.assertIn("TOOL", flat(app))

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
