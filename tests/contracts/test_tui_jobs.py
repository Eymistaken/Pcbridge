"""The Agents & Jobs tab: the job table and its filter, one job's details
and output, Cancel asked inline, the full output, and the agents with the
default agent, which saves at once to a throwaway config."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.dialogs import TextView  # noqa: E402
from tests.contracts.test_settings import example_config  # noqa: E402
from tests.contracts.test_tui import (NARROW, SIZE, FakeBackend, flat, screen_text, settle,  # noqa: E402
                                     words_of)
from tests.contracts.test_tui_desktop import FileBackend  # noqa: E402
from textual.widgets import DataTable  # noqa: E402

NOW = time.time()
RUNNING = "20261114-021133-7f3a9c"
DONE = "20261114-013000-81c0aa"


def job(job_id: str, status: str, agent: str, exit_code=None, age: float = 60) -> dict:
    return {"id": job_id, "kind": f"agent:{agent}", "label": f"prompt of {job_id[-6:]}",
            "status": status, "exit_code": exit_code, "agent": agent, "model": "opus",
            "effort": "", "cwd": "/home/u/project", "started": NOW - age, "elapsed": age}


class JobsBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.job_rows = [job(RUNNING, "running", "claude", age=134),
                         job(DONE, "finished", "codex", exit_code=0, age=3000)]
        self.cancelled: list[str] = []
        self.cfg.default_agent = "claude"
        self.cfg.agents = {
            "claude": SimpleNamespace(enabled=True, models=["opus", "sonnet", "haiku"],
                                      efforts=["low", "high"], command=["claude", "-p", "{prompt}"]),
            "codex": SimpleNamespace(enabled=False, models=["gpt-5"], efforts=[],
                                     command=["codex", "exec", "{prompt}"]),
        }

    def job_detail(self, cfg, job_id, tail=4000):
        row = next(j for j in self.job_rows if j["id"] == job_id)
        out = "\n".join(f"line {i} of {job_id[-6:]}" for i in range(40))
        return {**row, "prompt": f"the whole prompt of {job_id[-6:]}", "session_id": "sess-1234",
                "output": out, "tail": out[-tail:]}

    def job_started_by(self, cfg, job_id):
        return "claude.ai"

    def cancel_job(self, cfg, job_id):
        self.cancelled.append(job_id)
        for j in self.job_rows:
            if j["id"] == job_id:
                j["status"], j["exit_code"] = "failed", 130
        return "job stopped"

    def copy_text(self, text):
        self.copied = text
        return True


def run_app(test, backend, body, size=SIZE) -> None:
    async def go():
        app = PcbridgeApp(backend)
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await pilot.press("3")
            await settle(app, pilot)
            await body(app, pilot)

    asyncio.run(go())


def rows(app) -> list[str]:
    return [str(k.value) for k in app.query_one("#jobs-table", DataTable).rows]


class JobsTabTests(unittest.TestCase):
    def test_running_jobs_and_the_selected_one(self) -> None:
        async def body(app, pilot):
            self.assertEqual(rows(app), [RUNNING])
            text = flat(app)
            self.assertIn("[ Running 1 Recent All ]", text)
            self.assertIn("7f3a claude opus prompt of 7f3a9c", text)
            detail = words_of(app.query_one("#job-detail"))
            self.assertIn("Prompt the whole prompt of 7f3a9c", detail)
            self.assertIn("Folder /home/u/project", detail)
            self.assertRegex(detail, r"Started today \d\d:\d\d · by claude\.ai")
            self.assertIn("Session sess-1234", detail)
            self.assertIn("line 39 of 7f3a9c", detail)
            self.assertIn("Cancel c", detail)
            self.assertIn("↑↓ Job  c Cancel  o Output  d Default agent", screen_text(app)[-1])

        run_app(self, JobsBackend(), body)

    def test_the_filter_shows_finished_jobs(self) -> None:
        async def body(app, pilot):
            app.query_one("#jobs-filter").value = "all"
            await settle(app, pilot)
            self.assertEqual(rows(app), [RUNNING, DONE])
            self.assertIn("✓ done", flat(app))
            app.query_one("#jobs-table", DataTable).move_cursor(row=1)
            await settle(app, pilot)
            detail = words_of(app.query_one("#job-detail"))
            self.assertIn("the whole prompt of 81c0aa", detail)
            self.assertFalse(app.query_one("#job-cancel").display)   # only while running

        run_app(self, JobsBackend(), body)

    def test_cancel_asks_inline_and_keeps_the_output(self) -> None:
        backend = JobsBackend()

        async def body(app, pilot):
            await pilot.press("c")
            await pilot.pause()
            self.assertIn("Stop job 7f3a (claude)? Its output so far is kept.",
                          words_of(app.query_one("#job-detail")))
            await pilot.click("#job-keep")
            await settle(app, pilot)
            self.assertEqual(backend.cancelled, [])
            await pilot.press("c")
            await pilot.pause()
            await pilot.click("#job-stop")
            await settle(app, pilot)
            self.assertEqual(backend.cancelled, [RUNNING])
            app.query_one("#jobs-filter").value = "all"
            await settle(app, pilot)
            self.assertIn("⊘ cancelled", flat(app))

        run_app(self, backend, body)

    def test_full_output_opens_a_view(self) -> None:
        async def body(app, pilot):
            await pilot.press("o")
            await pilot.pause()
            self.assertIsInstance(app.screen, TextView)
            self.assertIn("line 0 of 7f3a9c", app.screen.text)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, TextView)

        run_app(self, JobsBackend(), body)

    def test_the_agents_and_their_settings(self) -> None:
        async def body(app, pilot):
            text = words_of(app.query_one("#agents-col"))
            self.assertIn("Used by agent_run when a call names neither an agent nor a model", text)
            menu = app.query_one("#agents-list")
            agents = [str(menu.get_option_at_index(i).prompt) for i in range(menu.option_count)]
            self.assertEqual(agents, ["● claude  3 models · default", "○ codex  1 model"])
            self.assertIn("opus", text)
            self.assertIn("Every setting of claude in Advanced ›", text)
            self.assertEqual(app.query_one("#default-agent").value, "claude")
            await pilot.click("#agent-advanced")
            await settle(app, pilot)
            self.assertEqual(app.active_tab(), "advanced")


        run_app(self, JobsBackend(), body)

    def test_the_default_agent_saves_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(example_config(Path(tmp) / "state"))
            path.chmod(0o600)

            async def body(app, pilot):
                picker = app.query_one("#default-agent")
                self.assertGreaterEqual(len(picker.options), 2)
                other = [v for v, _l in picker.options if v != picker.value][0]
                picker.value = other
                await settle(app, pilot)
                self.assertEqual(tomllib.loads(path.read_text())["default_agent"], other)
                self.assertEqual(app.restart_due, 1)

            with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                              if not k.startswith("PCBRIDGE_")}, clear=True):
                run_app(self, FileBackend(path), body)

    def test_the_tab_at_80_columns(self) -> None:
        async def body(app, pilot):
            self.assertTrue(all(len(r) == 80 for r in screen_text(app)))
            self.assertIn("JOBS", flat(app))

        run_app(self, JobsBackend(), body, size=NARROW)


if __name__ == "__main__":
    unittest.main()
