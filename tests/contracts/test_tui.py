"""The terminal UI, driven headless with Textual's pilot: real widgets, real
mouse clicks and keys, a fake backend. Nothing here touches the machine's
config, grant or daemon.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli.grant import GrantError, GrantState  # noqa: E402
from pcbridge.config import ConfigError  # noqa: E402
from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.dialogs import Confirm, UnlockConfirm  # noqa: E402
from pcbridge.tui.backend import Backend  # noqa: E402

SIZE = (120, 38)
NARROW = (80, 38)


class FakeBackend(Backend):
    def __init__(self, enabled: bool = True, config_error: str = "") -> None:
        self.enabled = enabled
        self.config_error = config_error
        self.open = False
        self.calls: list[str] = []
        self.cfg = SimpleNamespace(
            db_path=Path("/nowhere/oauth.db"), audit_log=Path("/nowhere/audit.log"),
            jobs_dir=Path("/nowhere/jobs"), audit_max_bytes=5_000_000,
            desktop=SimpleNamespace(enabled=enabled, unlock_default_minutes=15, unlock_max_minutes=120,
                                    unlock_idle_seconds=90),
            tools_profile="core", source_path=Path("/nowhere/config.toml"), warnings=[],
        )

    def load_config(self):
        if self.config_error:
            raise ConfigError(self.config_error)
        return self.cfg

    def grant_state(self, cfg):
        return GrantState(self.open, 754 if self.open else 0, self.enabled, "",
                          754 if self.open else 0)

    def lock(self, cfg):
        self.calls.append("lock")
        self.open = False
        return "Desktop control closed."

    def unlock(self, cfg, minutes):
        self.calls.append(f"unlock {minutes}")
        if not self.enabled:
            raise GrantError("disabled")
        self.open = True
        return "Desktop control open for 15 min."

    def status(self):
        return {"version": "x", "install": "git", "service": "active", "socket_unit": "active",
                "daemon": {"version": "9.9", "pid": 42}, "jobs_running": [], "remote_tunnel": "closed"}

    def restart(self, cfg):
        self.calls.append("restart")
        return "daemon 9.9 running, pid 43"

    def editor(self):
        raise NotImplementedError

    def tools(self, cfg):
        return []

    def clients(self):
        return []

    def remote_clients(self, cfg):
        return {"clients": [], "registrations": 0, "static_token": False,
                "remote_profile": "core", "remote_profile_set": False}

    def connected_remote(self, cfg):
        return 0

    checks = [
        {"group": "install", "name": "version", "status": "info", "detail": "pcbridge x", "fix": "",
         "fixed": False, "fixable": False},
        {"group": "daemon", "name": "pcbridge.service", "status": "warn",
         "detail": "pcbridge.service differs from this version's unit", "fix": "pcbridge doctor --fix",
         "fixed": False, "fixable": True},
        {"group": "agents", "name": "tmux", "status": "warn", "detail": "missing (tmux tools)",
         "fix": "sudo apt install tmux", "fixed": False, "fixable": False},
    ] + [{"group": "config", "name": f"check {i}", "status": "ok", "detail": "fine", "fix": "",
          "fixed": False, "fixable": False} for i in range(5)]

    def doctor(self, only=None):
        self.doctor_calls = getattr(self, "doctor_calls", [])
        self.doctor_calls.append(f"doctor {only[1]}" if only else "doctor")
        out = [dict(c) for c in self.checks]
        if only:
            for c in out:
                if (c["group"], c["name"]) == only:
                    c["fixed"] = True
            self.checks = out
        return out

    def remote_apps(self, cfg):
        return 2

    def native(self, cfg):
        return {"findings": [
            ("pass", "found: /opt/pcbridge/pcbridge-native (package: pcbridge/_native)"),
            ("pass", "version 0.1.0 · build 053cb3650344 · protocol 1.0"),
            ("pass", "capture.monitor: supported (linux.mutter.pipewire)"),
            ("warn", "input.keyboard: degraded (linux.mutter.pipewire) — checked on first use"),
            ("warn", "input.pointer: degraded (linux.mutter.pipewire) — checked on first use"),
            ("pass", "accessibility.read: supported (linux.mutter.pipewire)"),
            ("warn", "window.list: degraded (linux.mutter.pipewire) — accessibility-visible only")],
            "session": "gnome · wayland", "session_ok": True, "monitors": "2 · DP-2 1920×1080, DP-1 1920×1080"}

    panel_mode = "always"

    def panel_icon_status(self):
        return self.panel_mode, "Changes apply immediately and persist across logins."

    def set_panel_icon_mode(self, mode):
        self.calls.append(f"panel {mode}")
        self.panel_mode = mode
        return self.panel_icon_status()

    def jobs(self, cfg, limit=40, only_running=False):
        return list(getattr(self, "job_rows", []))

    def audit_page(self, cfg, skip, count, keep=None):
        rows = list(getattr(self, "audit_rows", []))
        if keep is not None:
            rows = [e for e in rows if keep(e)]
        return rows[skip:skip + count], len(rows) > skip + count


def run(coro):
    return asyncio.run(coro)


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def text_of(app, selector: str) -> str:
    return str(app.query_one(selector).render())


def screen_text(app) -> list[str]:
    """The screen as plain text, one string per row (what a person reads)."""
    return ["".join(seg.text for seg in strip) for strip in app.screen._compositor.render_strips()]


def flat(app) -> str:
    """The screen's words with the wrapping and the box borders taken out."""
    return " ".join(" ".join(screen_text(app)).replace("│", " ").split())


def words_of(node) -> str:
    """The words a widget and its children show, wrapping taken out."""
    from textual.widgets import Button, Static

    from rich.console import Console

    parts = []
    for w in [node, *node.query("*")]:
        if isinstance(w, Button):
            parts.append(str(w.label))
        elif hasattr(w, "label") and type(w).__name__ == "CheckLine":
            parts.append(("[x] " if w.value else "[ ] ") + w.label)
        elif isinstance(w, Static):
            visual = w.visual if hasattr(w, "visual") else w.render()
            renderable = getattr(visual, "_renderable", visual)
            if isinstance(renderable, str) or hasattr(renderable, "plain"):
                parts.append(str(renderable))
            else:
                console = Console(width=500, record=True, file=open("/dev/null", "w"))
                console.print(renderable)
                parts.append(console.export_text())
    return " ".join(" ".join(parts).split())


class FrameTests(unittest.TestCase):
    def test_the_bar_shows_the_grant_and_a_click_unlocks_then_locks(self) -> None:
        backend = FakeBackend()

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertIn("Desktop locked", text_of(app, "#bar-grant"))
                button = app.query_one("#grant-button")
                self.assertEqual(str(button.label), "Unlock 15 min")
                self.assertFalse(button.disabled)
                self.assertIn("daemon running", text_of(app, "#bar-daemon"))

                await pilot.click("#grant-button")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["unlock None"])
                self.assertIn("Desktop OPEN · 12:34 left", text_of(app, "#bar-grant"))
                self.assertEqual(str(button.label), "Lock now l")
                self.assertFalse(app.query_one("#grant-menu").display)

                await pilot.press("l")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["unlock None", "lock"])
                self.assertIn("locked", text_of(app, "#bar-grant"))

        run(go())

    def test_the_duration_menu_unlocks_at_once_for_the_picked_time(self) -> None:
        backend = FakeBackend()

        async def go():
            from pcbridge.tui.app import DurationMenu
            from textual.widgets import OptionList

            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                await pilot.click("#grant-menu")
                await pilot.pause()
                self.assertIsInstance(app.screen, DurationMenu)
                menu = app.screen.query_one(OptionList)
                labels = [str(menu.get_option_at_index(i).prompt) for i in range(menu.option_count)]
                self.assertEqual(labels, ["Unlock desktop control for", "  5 min", "● 15 min",
                                          "  30 min", "  1 h", "  2 h (max)"])
                menu.highlighted = 4
                await pilot.press("enter")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["unlock 60"])
                await pilot.press("l")
                await settle(app, pilot)
                self.assertEqual(str(app.query_one("#grant-button").label), "Unlock 1 h")

        run(go())

    def test_the_durations_stop_at_the_maximum(self) -> None:
        from pcbridge.tui.dialogs import durations

        self.assertEqual(durations(15, 120), [5, 15, 30, 60, 120])
        self.assertEqual(durations(20, 45), [5, 15, 20, 30, 45])
        self.assertEqual(durations(10, 10), [5, 10])

    def test_the_key_asks_before_it_unlocks_but_locks_at_once(self) -> None:
        backend = FakeBackend()

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                await pilot.press("l")
                await pilot.pause()
                self.assertIsInstance(app.screen, UnlockConfirm)
                text = words_of(app.screen)
                self.assertIn("Unlock desktop control?", text)
                self.assertIn("It locks after 90 s without agent input, at the latest in 2 h.", text)
                await pilot.click("#confirm-no")
                await settle(app, pilot)
                self.assertEqual(backend.calls, [])
                await pilot.press("l")
                await pilot.pause()
                await pilot.press("right")  # 15 min -> 30 min in the picker
                await pilot.pause()
                self.assertEqual(str(app.screen.query_one("#confirm-yes").label), "Unlock for 30 min enter")
                await pilot.press("enter")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["unlock 30"])
                await pilot.press("l")
                await settle(app, pilot)
                self.assertNotIsInstance(app.screen, (Confirm, UnlockConfirm))
                self.assertEqual(backend.calls, ["unlock 30", "lock"])

        run(go())

    def test_desktop_control_off_in_the_config_disables_the_button(self) -> None:
        backend = FakeBackend(enabled=False)

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertTrue(app.query_one("#grant-button").disabled)
                self.assertIn("Desktop control off", text_of(app, "#bar-grant"))
                await pilot.press("l")
                await settle(app, pilot)
                self.assertEqual(backend.calls, [])

        run(go())

    def test_a_broken_config_still_opens(self) -> None:
        backend = FakeBackend(config_error="port must be a number")

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                self.assertIn("config error", text_of(app, "#bar-grant"))
                self.assertEqual(app.cfg_error, "port must be a number")
                await pilot.press("r")
                await settle(app, pilot)
                self.assertNotIsInstance(app.screen, Confirm)

        run(go())

    def test_nine_tabs_switch_with_the_mouse_and_keys(self) -> None:
        from textual.widgets import TabbedContent

        async def go():
            app = PcbridgeApp(FakeBackend())
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                row = screen_text(app)[1]
                self.assertIn("1 Overview  2 Desktop  3 Agents & Jobs  4 Connections  5 Remote  "
                              "6 Tools  7 Activity  8 Logs  9 Advanced", row)
                self.assertNotIn("Commands", row)
                await pilot.click("#--content-tab-tools")
                await pilot.pause()
                self.assertEqual(app.query_one(TabbedContent).active, "tools")
                for i, tab in enumerate(["overview", "desktop", "jobs", "connections", "remote",
                                         "tools", "activity", "logs", "advanced"]):
                    await pilot.press(str(i + 1))
                    await pilot.pause()
                    self.assertEqual(app.query_one(TabbedContent).active, tab)

        run(go())

    def test_the_frame_at_80_columns(self) -> None:
        async def go():
            app = PcbridgeApp(FakeBackend())
            async with app.run_test(size=NARROW) as pilot:
                await settle(app, pilot)
                await pilot.pause(0.6)
                rows = screen_text(app)
                self.assertTrue(all(len(r) == 80 for r in rows))
                self.assertIn("pcbridge  ● running", rows[0])
                self.assertIn("◆ locked", rows[0])
                self.assertIn("Unlock", rows[0])
                self.assertIn("3 Agents  4 Clients", rows[1])
                self.assertTrue(rows[1].rstrip().endswith("+2 ›"), rows[1])
                self.assertIn("? Help  q Quit", rows[-1])

        run(go())

    def test_the_footer_shows_the_tab_keys_and_is_clickable(self) -> None:
        backend = FakeBackend()

        async def go():
            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                footer = screen_text(app)[-1]
                self.assertTrue(footer.lstrip().startswith("l Lock/Unlock  r Restart"), footer)
                self.assertIn("^p Commands", footer)
                self.assertTrue(footer.rstrip().endswith("? Help  q Quit"), footer)
                await pilot.click("#footer", offset=(2, 0))  # "l Lock/Unlock": asks first
                await pilot.pause()
                self.assertIsInstance(app.screen, UnlockConfirm)

        run(go())

    def test_callbacks_after_teardown_do_nothing(self) -> None:
        """The 1 s timer and worker callbacks can land while the app is shutting
        down and its widgets are gone (seen on a slow CI runner); they must
        return quietly instead of raising NoMatches."""
        from pcbridge.tui.tools_pane import ToolsPane

        async def go():
            app = PcbridgeApp(FakeBackend())
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                tools = app.query_one(ToolsPane)
                await app.query_one("#bar").remove()
                await tools.query_one("#tools-table").remove()
                await pilot.pause()
                app.refresh_grant()
                app._show_status(FakeBackend().status())
                app._grant_done()
                tools._loaded([], "", "full")
                await pilot.pause()

        run(go())

    def test_restart_asks_first_and_clears_the_notice(self) -> None:
        backend = FakeBackend()

        async def go():
            from pcbridge import settings as S

            app = PcbridgeApp(backend)
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                app.note_restart_due(2, S.RESTART_DAEMON)
                await pilot.press("r")
                await pilot.pause()
                self.assertIsInstance(app.screen, Confirm)
                await pilot.click("#confirm-no")
                await settle(app, pilot)
                self.assertEqual(backend.calls, [])
                self.assertEqual(app.restart_due, 2)
                await pilot.press("r")
                await pilot.pause()
                await pilot.click("#confirm-yes")
                await settle(app, pilot)
                self.assertEqual(backend.calls, ["restart"])
                self.assertEqual(app.restart_due, 0)

        run(go())


if __name__ == "__main__":
    unittest.main()
