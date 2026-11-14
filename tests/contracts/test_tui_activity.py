"""The Activity tab: the audit log newest first, grouped by day, filtered by
kind and client, paged from the end, with the selected entry as written.
Reads a throwaway audit log through the real backend reader."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui import auditlog  # noqa: E402
from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.backend import Backend  # noqa: E402
from tests.contracts.test_tui import NARROW, SIZE, FakeBackend, flat, screen_text, settle  # noqa: E402
from textual.widgets import DataTable  # noqa: E402

NOW = time.time()


def ts(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - seconds_ago))


def write_log(path: Path, n: int = 30) -> list[dict]:
    """n entries, oldest first, the newest one a refused click from yesterday's
    neighbour and today."""
    rows = []
    for i in range(n):
        rows.append({"ts": ts(86400 + 1000 - i), "event": "shell_run", "client": "codex",
                     "cmd": f"echo {i}", "exit": 0})
    rows.append({"ts": ts(60), "event": "fs_read", "client": "claude-code", "path": "/etc/hosts",
                 "bytes": 10})
    rows.append({"ts": ts(30), "event": "token_issued", "client_id": "abc", "grant": "x"})
    rows.append({"ts": ts(10), "event": "ui_click_denied", "client": "claude.ai",
                 "reason": "USER_ACTIVE: the keyboard was used 3 s ago"})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


class LogBackend(FakeBackend):
    audit_page = Backend.audit_page
    audit_totals = Backend.audit_totals

    def copy_text(self, text):
        self.copied = text
        return True


def run_app(test, backend, body, size=SIZE) -> None:
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await pilot.press("7")
            await settle(app, pilot)
            await body(app, pilot)

    asyncio.run(go())


def keys(app) -> list[str]:
    return [str(k.value) for k in app.query_one("#activity-table", DataTable).rows]


class ActivityTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "audit.log"
        write_log(self.log)
        self.backend = LogBackend()
        self.backend.cfg.audit_log = self.log

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_newest_first_grouped_by_day_with_the_entry_as_written(self) -> None:
        async def body(app, pilot):
            text = flat(app)
            self.assertIn("Newest first, read from the end of audit.log · 33 entries since", text)
            self.assertIn("MB before it rotates", text)
            self.assertEqual(keys(app)[0], "day:0")
            self.assertIn("TODAY", text)
            self.assertIn("YESTERDAY", text)
            self.assertIn("claude.ai desktop ui_click_denied USER_ACTIVE", text)
            self.assertIn("refused", text)
            self.assertIn("remote token_issued", text)
            self.assertIn("codex shell echo", text.replace("shell_run ", ""))
            self.assertIn('"event": "ui_click_denied"', flat(app))
            self.assertIn("as written, one JSON line · contents are never recorded", text)
            self.assertIn("page 1 · 22 of 33", text)
            self.assertIn("k Kind  c Client  pgup/pgdn Newer/Older  y Copy", screen_text(app)[-1])

        run_app(self, self.backend, body)

    def test_pages_go_older_and_newer(self) -> None:
        async def body(app, pilot):
            app.query_one("#activity-table").focus()
            await pilot.press("pagedown")
            await settle(app, pilot)
            self.assertIn("page 2 · 11 of 33", flat(app))
            self.assertTrue(app.query_one("#entry-older").disabled)
            await pilot.press("pageup")
            await settle(app, pilot)
            self.assertIn("page 1 · 22 of 33", flat(app))
            self.assertTrue(app.query_one("#entry-newer").disabled)

        run_app(self, self.backend, body)

    def test_kind_and_client_filters(self) -> None:
        async def body(app, pilot):
            app.query_one("#activity-kind").value = "file"
            await settle(app, pilot)
            rows = [k for k in keys(app) if not k.startswith("day:")]
            self.assertEqual(len(rows), 1)
            self.assertIn("fs_read /etc/hosts", flat(app))
            self.assertIn("page 1 · 1 shown", flat(app))
            app.query_one("#activity-kind").value = "all"
            await settle(app, pilot)
            client = app.query_one("#activity-client")
            self.assertIn("claude.ai", [v for v, _l in client.options])
            client.value = "claude.ai"
            await settle(app, pilot)
            self.assertEqual(len([k for k in keys(app) if not k.startswith("day:")]), 1)

        run_app(self, self.backend, body)

    def test_copy_the_selected_line(self) -> None:
        async def body(app, pilot):
            await pilot.press("y")
            await pilot.pause()
            self.assertEqual(json.loads(self.backend.copied)["event"], "ui_click_denied")

        run_app(self, self.backend, body)

    def test_the_rotated_copy_is_read_after_the_log(self) -> None:
        rotated = Path(f"{self.log}.1")
        rotated.write_text(json.dumps({"ts": ts(9 * 86400), "event": "mouse", "client": "old"}) + "\n")
        entries = list(auditlog.newest(self.log))
        self.assertEqual(entries[-1].client, "old")
        self.assertEqual(entries[0].event, "ui_click_denied")

    def test_the_tab_at_80_columns(self) -> None:
        async def body(app, pilot):
            self.assertTrue(all(len(r) == 80 for r in screen_text(app)))
            self.assertIn("TIME", flat(app))

        run_app(self, self.backend, body, size=NARROW)


class ReaderTests(unittest.TestCase):
    def test_kinds_results_and_summaries(self) -> None:
        cases = [
            ({"event": "shell_run", "cmd": "ls -la", "exit": 2}, "shell", "exit 2", "ls -la"),
            ({"event": "fs_write", "path": "/tmp/x", "bytes": 3}, "file", "ok", "/tmp/x"),
            ({"event": "agent_run", "agent": "claude", "model": "opus", "job": "j1"}, "agent", "ok",
             "claude · opus · job j1"),
            ({"event": "mouse_denied", "reason": "locked"}, "desktop", "refused", "locked"),
            ({"event": "screen_capture_error", "error": "no monitor"}, "desktop", "failed", "no monitor"),
            ({"event": "computer_batch", "done": 2, "total": 5, "stopped": "user"}, "desktop", "stopped",
             "2 of 5 steps"),
            ({"event": "password_failed", "ip": "1.2.3.4"}, "remote", "refused", ""),
        ]
        for data, kind, result, what in cases:
            e = auditlog.Entry(json.dumps(data), data)
            self.assertEqual((e.kind, e.result), (kind, result), data)
            self.assertIn(what, e.what(), data)

    def test_a_large_log_is_read_from_the_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.log"
            with path.open("w") as fh:
                for i in range(20000):
                    fh.write(json.dumps({"ts": "2026-11-14T00:00:00", "event": "shell_run", "i": i}) + "\n")
            t0 = time.monotonic()
            page, more = auditlog.page(path, 0, 22)
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertEqual(page[0].data["i"], 19999)
            self.assertTrue(more)
            self.assertEqual(auditlog.totals(path).entries, 20000)


if __name__ == "__main__":
    unittest.main()
