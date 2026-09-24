"""The Tools tab of the terminal UI with the real catalog of a `core`
profile: search as you type, the offered-only filter, details on click.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from tests.contracts.test_toolcatalog import catalog  # noqa: E402
from tests.contracts.test_tui import SIZE, FakeBackend, settle  # noqa: E402
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
            self.assertEqual(len(rows(app)), 37)
            self.assertIn("profile core: 21 of 37 tools offered", str(app.query_one("#tools-count").render()))

        self.run_app(body)

    def test_typing_searches_by_name_and_description(self) -> None:
        async def body(app, pilot):
            await pilot.click("#tools-search")
            await pilot.press(*"screenshot")
            await pilot.pause()
            found = rows(app)
            self.assertIn("screen_capture", found)
            self.assertNotIn("job_cancel", found)
            self.assertIn("shown", str(app.query_one("#tools-count").render()))
            self.assertIn(found[0], str(app.query_one("#tool-title").render()))

        self.run_app(body)

    def test_only_offered_hides_what_the_profile_leaves_out(self) -> None:
        async def body(app, pilot):
            await pilot.click("#tools-active-only")
            await pilot.pause()
            self.assertEqual(len(rows(app)), 21)
            self.assertNotIn("mouse", rows(app))

        self.run_app(body)

    def test_clicking_a_row_shows_its_details(self) -> None:
        async def body(app, pilot):
            table = app.query_one("#tools-table", DataTable)
            target = rows(app).index("agent_run")
            await pilot.click("#tools-table", offset=(3, target + 1))  # +1: the header row
            await pilot.pause()
            self.assertEqual(table.cursor_row, target)
            self.assertIn("agent_run", str(app.query_one("#tool-title").render()))
            screen = app.export_screenshot()
            self.assertIn("Parameters", screen)
            self.assertIn("required", screen)
            # A tool the profile leaves out says why.
            table.move_cursor(row=rows(app).index("mouse"))
            await pilot.pause()
            self.assertIn("profile leaves it out", str(app.query_one("#tool-meta").render()))
            self.assertEqual(app.query_one(TabbedContent).active, "tools")

        self.run_app(body)


if __name__ == "__main__":
    unittest.main()
