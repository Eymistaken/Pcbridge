"""The Overview tab: the logo and its state, service, health with Fix, jobs
and recent activity, at 120 and 80 columns, with a fake backend."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui import auditlog  # noqa: E402
from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.logo import Logo  # noqa: E402
from tests.contracts.test_tui import NARROW, SIZE, FakeBackend, flat, screen_text, settle, words_of  # noqa: E402


def entry(seconds_ago: float, **data) -> auditlog.Entry:
    data.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - seconds_ago)))
    return auditlog.Entry(json.dumps(data), data)


def running_job(seconds: int = 134) -> dict:
    return {"id": "20261114-021133-7f3a9c", "kind": "agent:claude", "label": "Fix the tests",
            "status": "running", "agent": "claude", "model": "opus", "effort": "", "cwd": "/x",
            "started": time.time() - seconds, "elapsed": seconds}


def run(backend, body, size=SIZE):
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await body(app, pilot)

    asyncio.run(go())


class OverviewTests(unittest.TestCase):
    def test_service_lines_at_120_columns(self) -> None:
        async def body(app, pilot):
            text = flat(app)
            self.assertIn("SERVICE", text)
            self.assertIn("Daemon ● running · 9.9 · pid 42 Restart r", text)
            self.assertIn("Desktop ◆ locked · desktop control enabled Unlock ▾", text)
            self.assertIn("Remote ○ tunnel closed · 2 apps hold tokens Remote ›", text)
            self.assertIn("Tools core · 24 of 40 offered · remote: core, 24", text)
            self.assertIn("/nowhere/config.toml", text)
            self.assertEqual(app.query_one(Logo).glyph, "word")
            self.assertIn("idle · listening, nothing running · click the logo for the reason", text)
            footer = screen_text(app)[-1]
            self.assertIn("l Lock/Unlock  r Restart  u Update  f Fix  ^p Commands  1-9 Tabs", footer)

        run(FakeBackend(), body)

    def test_health_counts_and_fixing_one_check(self) -> None:
        backend = FakeBackend()

        async def body(app, pilot):
            text = flat(app)
            self.assertIn("✓ 5 ok ▲ 2 warnings ✕ 0 failed", text)
            self.assertIn("▲ daemon pcbridge.service differs", text)
            self.assertIn("Fix this check f", text)
            self.assertEqual(backend.doctor_calls, ["doctor"])
            await pilot.press("f")
            await settle(app, pilot)
            self.assertEqual(backend.doctor_calls, ["doctor", "doctor pcbridge.service"])
            text = flat(app)
            self.assertIn("✓ 6 ok ▲ 1 warning ✕ 0 failed", text)
            self.assertNotIn("pcbridge.service differs", text)
            # The remaining warning has no unattended fix: its hint, no button.
            self.assertIn("fix: sudo apt install tmux", text)
            self.assertFalse(app.query_one("#health-fix").display)

        run(backend, body)

    def test_the_logo_follows_jobs_and_errors_and_a_click_shows_the_reason(self) -> None:
        backend = FakeBackend()
        backend.audit_rows = [entry(12, event="ui_click_denied", client="claude-code",
                                    reason="USER_ACTIVE: the keyboard was used 3 s ago")]

        async def body(app, pilot):
            logo = app.query_one(Logo)
            self.assertEqual(logo.state, "error")
            self.assertRegex(flat(app), r"✕ ui_click refused · 1[2-4] s ago")
            logo.click()
            await pilot.pause()
            reason = flat(app)
            self.assertIn("LAST ERROR ui_click from claude-code was refused at", reason)
            self.assertIn("USER_ACTIVE: the keyboard was used 3 s ago.", reason)
            self.assertIn("Activity ›", reason)
            self.assertFalse(logo.click())   # a reaction is still playing: ignored
            logo.finish()
            logo.click()
            await pilot.pause()
            self.assertNotIn("LAST ERROR", flat(app))
            logo.finish()

            backend.audit_rows = [entry(200, event="shell_run", client="codex", cmd="ls", exit=0)]
            backend.job_rows = [running_job()]
            app.panes["overview"].poll_activity()
            await settle(app, pilot)
            self.assertEqual(logo.state, "active")
            text = flat(app)
            self.assertRegex(text, r"● agent_run · claude · 2m 1[4-9]s")   # a slow runner ticks
            self.assertIn("JOBS 1 running", text)
            self.assertIn("7f3a claude Fix the tests", text)

        run(backend, body)

    def test_checks_with_the_same_name_each_get_a_row(self) -> None:
        """doctor reports every native helper finding as "native helper · native";
        2.9.1 crashed on the second one (a duplicate option id)."""
        backend = FakeBackend()
        backend.checks = backend.checks + [
            {"group": "native helper", "name": "native", "status": "warn", "detail": f"finding {i}",
             "fix": "", "fixed": False, "fixable": False} for i in range(3)]

        async def body(app, pilot):
            text = flat(app)
            for i in range(3):
                self.assertIn(f"finding {i}", text)
            menu = app.query_one("#health-list")
            menu.highlighted = menu.option_count - 1
            await pilot.pause()
            self.assertIn("finding 2", words_of(app.query_one("#health-detail")))
            await pilot.press("r")          # anything that redraws the list keeps working
            await pilot.press("escape")
            app.panes["overview"]._render_health()
            await pilot.pause()
            self.assertIn("finding 2", words_of(app.query_one("#health-detail")))

        run(backend, body)

    def test_the_narrow_layout_stacks_and_shows_the_icon(self) -> None:
        backend = FakeBackend()
        backend.checks = backend.checks + [
            {"group": "agents", "name": f"tool {i}", "status": "warn", "detail": f"missing {i}",
             "fix": "", "fixed": False, "fixable": False} for i in range(2)]

        async def body(app, pilot):
            rows = screen_text(app)
            self.assertTrue(all(len(r) == 80 for r in rows))
            self.assertEqual(app.query_one(Logo).glyph, "icon")
            self.assertIn("below 96 columns the wordmark gives way to the icon",
                          words_of(app.query_one("#logo-side")))
            text = flat(app)
            self.assertIn("+2 more · Show all warnings ›", text)
            await pilot.click("#health-more")
            await pilot.pause()
            self.assertIn("missing 1", flat(app))
            self.assertNotIn("1-9 Tabs", rows[-1])
            self.assertIn("? Help  q Quit", rows[-1])

        run(backend, body, size=NARROW)

    def test_logo_state_rules(self) -> None:
        now = time.time()
        ok = entry(1, event="screen_capture", client="claude.ai")
        old_error = entry(90, event="mouse_denied", client="x", reason="locked")
        fresh_error = entry(5, event="mouse_denied", client="x", reason="locked")
        self.assertEqual(auditlog.logo_state([ok], 0, now)[0], "active")
        self.assertEqual(auditlog.logo_state([old_error], 0, now)[0], "idle")
        self.assertEqual(auditlog.logo_state([fresh_error], 0, now)[0], "error")
        self.assertEqual(auditlog.logo_state([ok, fresh_error], 0, now)[0], "active")
        self.assertEqual(auditlog.logo_state([], 1, now)[0], "active")
        self.assertEqual(auditlog.logo_state([], 0, now)[0], "idle")


if __name__ == "__main__":
    unittest.main()
