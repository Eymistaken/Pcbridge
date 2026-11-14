"""The Ctrl+P command palette: the four groups, search, a question in place
of the list for what changes state, the output view, and the dialogs. The
backend is a fake; no command runs for real."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.palette import CommandPalette, OutputView, TerminalOnly, commands  # noqa: E402
from pcbridge.tui.remote_pane import LoginScreen  # noqa: E402
from tests.contracts.test_tui import SIZE, FakeBackend, settle, words_of  # noqa: E402
from textual.widgets import Input, OptionList  # noqa: E402


class CliBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.cli: list[list[str]] = []

    def run_cli(self, argv, timeout=300):
        self.cli.append(list(argv))
        return 0, "\n".join(["  ok    daemon running", "  warn  2 warnings",
                             "          fix: pcbridge doctor --fix"])


def options(app) -> list[str]:
    menu = app.screen.query_one("#palette-list", OptionList)
    return [str(menu.get_option_at_index(i).prompt) for i in range(menu.option_count)]


def run_app(test, backend, body) -> None:
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=SIZE) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+p")
            await pilot.pause()
            test.assertIsInstance(app.screen, CommandPalette)
            await body(app, pilot)

    asyncio.run(go())


async def search(app, pilot, text: str) -> None:
    app.screen.query_one("#palette-search", Input).value = text
    await pilot.pause()


class PaletteTests(unittest.TestCase):
    def test_the_catalog(self) -> None:
        names = [c.name for c in commands()]
        self.assertEqual(len(names), 28)
        for left_out in ("serve", "stdio", "ui", "list"):
            self.assertNotIn(left_out, names)
        lock = next(c for c in commands() if c.name == "lock")
        self.assertEqual(lock.group, "run")              # locking never asks
        self.assertEqual(next(c for c in commands() if c.name == "unlock").group, "ask")
        self.assertTrue(next(c for c in commands() if c.name == "stop").danger)

    def test_four_groups_and_the_count(self) -> None:
        async def body(app, pilot):
            rows = options(app)
            heads = [r for r in rows if r.isupper()]
            self.assertEqual(heads, ["RUN NOW", "ASKS FIRST", "OPENS A DIALOG", "GO TO"])
            self.assertIn("28 of 28", words_of(app.screen))
            self.assertTrue(any(r.startswith(" ▶ doctor") for r in rows))
            self.assertTrue(any(r.startswith(" › tools") for r in rows))
            self.assertTrue(any(r.startswith(" ? restart") for r in rows))
            self.assertTrue(any(r.startswith(" … setup") for r in rows))
            await search(app, pilot, "doc")
            self.assertIn("1 of 28", words_of(app.screen))

        run_app(self, CliBackend(), body)

    def test_run_now_shows_the_output_and_runs_again(self) -> None:
        backend = CliBackend()

        async def body(app, pilot):
            await search(app, pilot, "doctor")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, OutputView)
            text = words_of(app.screen)
            self.assertIn("$ pcbridge doctor", text)
            self.assertRegex(text, r"✓ exit 0 · \d+\.\d s")
            self.assertIn("warn 2 warnings", text)
            self.assertIn("‹ Commands esc", text)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(backend.cli, [["doctor"], ["doctor"]])
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, CommandPalette)

        run_app(self, backend, body)

    def test_lock_runs_without_a_question(self) -> None:
        backend = CliBackend()

        async def body(app, pilot):
            await search(app, pilot, "lock")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, OutputView)
            self.assertEqual(backend.cli, [["lock"]])

        run_app(self, backend, body)

    def test_asks_first_replaces_the_list_with_the_question(self) -> None:
        backend = CliBackend()

        async def body(app, pilot):
            await search(app, pilot, "restart")
            await pilot.press("enter")
            await pilot.pause()
            text = words_of(app.screen)
            self.assertIn("Restart the daemon?", text)
            self.assertIn("No job is running", text)
            self.assertIn("Cancel esc", text)
            self.assertIn("Restart enter", text)
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(app.screen.query_one("#palette-list").display)
            self.assertEqual(backend.calls, [])
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, OutputView)
            self.assertEqual(backend.calls, ["restart"])

        run_app(self, backend, body)

    def test_stop_asks_with_a_danger_button(self) -> None:
        backend = CliBackend()

        async def body(app, pilot):
            await search(app, pilot, "stop")
            await pilot.press("enter")
            await pilot.pause()
            self.assertTrue(app.screen.query_one("#palette-yes").has_class("danger"))
            await pilot.press("escape")
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, CommandPalette)
            self.assertEqual(backend.cli, [])

        run_app(self, backend, body)

    def test_unlock_asks_then_unlocks(self) -> None:
        backend = CliBackend()

        async def body(app, pilot):
            await search(app, pilot, "unlock")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Unlock desktop control?", words_of(app.screen))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(backend.calls, ["unlock None"])

        run_app(self, backend, body)

    def test_dialogs_and_tabs(self) -> None:
        async def body(app, pilot):
            await search(app, pilot, "setup")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, TerminalOnly)
            self.assertIn("$ pcbridge setup", words_of(app.screen))
            await pilot.press("escape")
            await pilot.press("ctrl+p")
            await pilot.pause()
            await search(app, pilot, "go to logs")
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.active_tab(), "logs")
            await pilot.press("ctrl+p")
            await pilot.pause()
            await search(app, pilot, "auth.password")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(app.active_tab(), "remote")
            self.assertIsInstance(app.screen, LoginScreen)

        run_app(self, CliBackend(), body)


if __name__ == "__main__":
    unittest.main()
