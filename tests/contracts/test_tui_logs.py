"""The Logs tab: the journal's last lines, then followed live; pause collects
new lines behind a button; level and Find; the parser of journalctl's JSON.
The backend is a fake; nothing here runs journalctl."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui import journal  # noqa: E402
from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from tests.contracts.test_tui import NARROW, SIZE, FakeBackend, flat, screen_text, settle  # noqa: E402

T0 = time.time() - 600


def line(i: int, level: str = "INFO", message: str = "") -> journal.Line:
    return journal.Line(T0 + i, level, message or f"message {i}", f"c{i}")


class JournalBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.first = [line(0), line(1, "WARN", "token store slow"), line(2, "ERROR", "capture failed"),
                      line(3, "DEBUG", "probe")]
        self.incoming: list[journal.Line] = []
        self.lock = threading.Lock()
        self.befores: list[str] = []

    def journal(self, lines=300, before=""):
        if before:
            self.befores.append(before)
            return [line(-1, "INFO", "an older line")]
        return list(self.first)

    def journal_follow(self, after=""):
        while True:
            with self.lock:
                batch, self.incoming = self.incoming, []
            for item in batch:
                yield item
            time.sleep(0.05)
            yield None

    def push(self, *lines):
        with self.lock:
            self.incoming.extend(lines)

    def copy_text(self, text):
        self.copied = text
        return True


def text_of(app) -> str:
    return str(app.query_one("#logs-text").render())


def run_app(test, backend, body, size=SIZE) -> None:
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await pilot.press("8")
            await pilot.pause(0.3)
            await body(app, pilot)
            for worker in app.workers:
                worker.cancel()

    asyncio.run(go())


class LogsTabTests(unittest.TestCase):
    def test_the_last_lines_then_new_ones_live(self) -> None:
        backend = JournalBackend()

        async def body(app, pilot):
            text = text_of(app)
            self.assertIn("INFO  message 0", text)
            self.assertIn("WARN  token store slow", text)
            self.assertIn("ERROR capture failed", text)
            self.assertIn("4 lines shown", flat(app))
            self.assertIn("● Following f", flat(app))
            self.assertIn("journalctl --user -u pcbridge.service · this boot", flat(app))
            backend.push(line(4, "INFO", "a new request"))
            await pilot.pause(0.6)
            self.assertIn("a new request", text_of(app))

        run_app(self, backend, body)

    def test_paused_lines_collect_behind_a_button(self) -> None:
        backend = JournalBackend()

        async def body(app, pilot):
            await pilot.press("f")
            await pilot.pause()
            self.assertIn("❚❚ Paused f", flat(app))
            backend.push(line(5, "INFO", "while paused one"), line(6, "INFO", "while paused two"))
            await pilot.pause(0.6)
            self.assertNotIn("while paused one", text_of(app))
            self.assertIn("↓ 2 new lines · follow again", flat(app))
            await pilot.click("#logs-new-button")
            await pilot.pause()
            self.assertIn("while paused two", text_of(app))
            self.assertIn("● Following f", flat(app))
            self.assertFalse(app.query_one("#logs-new").has_class("shown"))

        run_app(self, backend, body)

    def test_level_find_older_and_copy(self) -> None:
        backend = JournalBackend()

        async def body(app, pilot):
            app.query_one("#logs-level").value = "warn"
            await pilot.pause()
            self.assertNotIn("message 0", text_of(app))
            self.assertIn("2 lines shown", flat(app))
            app.query_one("#logs-level").value = "all"
            app.query_one("#logs-find").value = "capture"
            await pilot.pause()
            self.assertIn("capture failed", text_of(app))   # highlighted, not filtered
            self.assertIn("message 0", text_of(app))
            await pilot.click("#logs-older")
            await pilot.pause(0.5)   # the follow worker never completes: no settle
            self.assertEqual(backend.befores, ["c0"])
            self.assertTrue(text_of(app).startswith(time.strftime("%H:%M:%S", time.localtime(T0 - 1))))
            await pilot.click("#logs-copy")
            await pilot.pause()
            self.assertIn("an older line", backend.copied)

        run_app(self, backend, body)

    def test_the_tab_at_80_columns(self) -> None:
        async def body(app, pilot):
            self.assertTrue(all(len(r) == 80 for r in screen_text(app)))
            self.assertIn("Following", flat(app))

        run_app(self, JournalBackend(), body, size=NARROW)


class ParserTests(unittest.TestCase):
    def entry(self, message, priority=6, ts=1794644270346697):
        return json.dumps({"MESSAGE": message, "PRIORITY": str(priority),
                           "__REALTIME_TIMESTAMP": str(ts), "__CURSOR": "s=1"})

    def test_the_level_comes_from_the_message(self) -> None:
        got = journal.parse(self.entry("2026-11-14 11:17:50,346 WARNING pcbridge.daemon: slow"))
        self.assertEqual((got.level, got.message), ("WARN", "pcbridge.daemon: slow"))
        got = journal.parse(self.entry("ERROR:    Exception in ASGI application"))
        self.assertEqual((got.level, got.message), ("ERROR", "Exception in ASGI application"))
        got = journal.parse(self.entry("INFO:     127.0.0.1 - GET /healthz 200"))
        self.assertEqual(got.level, "INFO")
        self.assertAlmostEqual(got.ts, 1794644270.346697, places=3)

    def test_the_priority_fills_in(self) -> None:
        self.assertEqual(journal.parse(self.entry("Started pcbridge", priority=3)).level, "ERROR")
        self.assertEqual(journal.parse(self.entry("plain", priority=4)).level, "WARN")
        self.assertEqual(journal.parse(self.entry("plain", priority=7)).level, "DEBUG")

    def test_bytes_and_garbage(self) -> None:
        raw = json.dumps({"MESSAGE": list("hé".encode()), "__REALTIME_TIMESTAMP": "0"})
        self.assertEqual(journal.parse(raw).message, "hé")
        self.assertIsNone(journal.parse("not json"))


if __name__ == "__main__":
    unittest.main()
