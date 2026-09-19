#!/usr/bin/env python3
"""Contracts for window operations behind one orchestration (Task 6.4).

`window_focus`, `computer_task(app=...)` and the batch `focus`/`launch`
actions all go through `apps.bring_to_front` / `apps.launch_application`.
Nothing here touches the real desktop: the extension, `gtk-launch`, the
accessibility reads and the keyboard are all fakes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import apps  # noqa: E402
from pcbridge.desktop import batch as batchlib  # noqa: E402
from pcbridge.desktop import ops as opslib  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402


def entry(entry_id, name, *, names=(), alt=(), exe="", hidden=False):
    return apps.Entry(entry_id, name, hidden, names or (name,), alt, exe)


# The names here are the ones measured on this machine (2026-09-19).
POOL = [
    entry("org.gnome.TextEditor", "Text Editor",
          names=("Text Editor", "Metin Düzenleyici"), exe="gnome-text-editor"),
    entry("code", "Visual Studio Code", alt=("Text Editor",), exe="code"),
    entry("codeblocks", "Code::Blocks", exe="codeblocks"),
    entry("org.gnome.Terminal", "Terminal", names=("Terminal", "Uçbirim"),
          exe="gnome-terminal"),
    entry("google-chrome", "Google Chrome", alt=("Web Browser",),
          exe="google-chrome-stable"),
    entry("com.google.Chrome", "Google Chrome"),
    entry("libreoffice-writer", "LibreOffice Writer", exe="libreoffice"),
    entry("io.github.shiftey.Desktop", "GitHub Desktop"),
    entry("ai.opencode.desktop", "OpenCode", exe="opencode"),
    entry("Pcbridge Desktop", "Pcbridge Desktop", exe="pcbridge-desktop"),
    entry("org.gnome.Settings.Panel", "Hidden Panel", hidden=True),
]


def win(app, title, active=False):
    return SimpleNamespace(app=app, title=title, active=active)


class Clock:
    """Time that moves only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Desk:
    """The desktop as the accessibility tree reports it."""

    def __init__(self, focus=("gnome-shell", ""), windows=()) -> None:
        self.focus = focus
        self.listed = list(windows)
        self.focus_error: Exception | None = None
        self.windows_error: Exception | None = None
        self.focus_reads = 0

    def focused(self):
        self.focus_reads += 1
        if self.focus_error is not None:
            raise self.focus_error
        return self.focus

    def windows(self):
        if self.windows_error is not None:
            raise self.windows_error
        return list(self.listed)


class Keys:
    """A keyboard that records; `on_return` plays what GNOME search opens."""

    def __init__(self, on_return=None) -> None:
        self.events: list[tuple] = []
        self.on_return = on_return

    def key(self, keys: str) -> None:
        self.events.append(("key", keys))
        if keys == "Return" and self.on_return is not None:
            self.on_return()

    def type_text(self, text: str, *, raw: bool) -> None:
        self.events.append(("type", text, raw))


class Harness(unittest.TestCase):
    """Patch the extension, `gtk-launch` and time for one test."""

    def setUp(self) -> None:
        self.clock = Clock()
        self.activations: list[str] = []
        self.activate_result = False
        self.launched: list[str] = []
        self.on_launch = None
        patches = [
            mock.patch.object(apps, "time", SimpleNamespace(
                monotonic=self.clock.monotonic, sleep=self.clock.sleep)),
            mock.patch.object(apps, "activate_window", side_effect=self._activate),
            mock.patch.object(apps, "_gtk_launch", side_effect=self._launch),
            mock.patch.object(apps, "entries", return_value=list(POOL)),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _activate(self, window: str) -> bool:
        self.activations.append(window)
        result = self.activate_result
        return result(window) if callable(result) else bool(result)

    def _launch(self, found: apps.Entry, timeout: int) -> None:
        self.launched.append(found.entry_id)
        if self.on_launch is not None:
            self.on_launch()

    def front(self, target, desk, keys=None, **kw):
        keys = keys if keys is not None else Keys()
        outcome = apps.bring_to_front(
            target, keys, desk.focused, desk.windows, settle=0, **kw
        )
        return outcome, keys


class ResolveApplicationTests(unittest.TestCase):
    def test_an_exact_name_is_one_application(self) -> None:
        app = apps.resolve_application("Text Editor", POOL)
        self.assertEqual(app.entry.entry_id, "org.gnome.TextEditor")
        self.assertEqual(app.rivals, ())

    def test_a_translated_name_resolves_to_the_same_application(self) -> None:
        app = apps.resolve_application("metin düzenleyici", POOL)
        self.assertEqual(app.entry.entry_id, "org.gnome.TextEditor")

    def test_two_entries_with_one_display_name_are_one_application(self) -> None:
        app = apps.resolve_application("Chrome", POOL)
        self.assertEqual(app.entry.name, "Google Chrome")
        self.assertEqual(app.rivals, ())

    def test_a_name_that_fits_several_applications_is_ambiguous(self) -> None:
        # Measured on this machine: "Desktop" is the id tail of three entries.
        app = apps.resolve_application("Desktop", POOL)
        self.assertIsNone(app.entry)
        self.assertEqual(
            set(app.rivals), {"GitHub Desktop", "OpenCode", "Pcbridge Desktop"}
        )

    def test_an_unknown_name_resolves_to_nothing(self) -> None:
        app = apps.resolve_application("Pcbridge Nested A", POOL)
        self.assertIsNone(app.entry)
        self.assertEqual(app.rivals, ())

    def test_hidden_entries_are_not_applications(self) -> None:
        self.assertIsNone(apps.resolve_application("Hidden Panel", POOL).entry)

    def test_the_first_tier_wins_like_find(self) -> None:
        # "Text Editor" is also VS Code's GenericName, one tier lower.
        for name in ("Text Editor", "code", "Terminal", "Pcbridge Desktop"):
            with self.subTest(name=name):
                self.assertIs(
                    apps.resolve_application(name, POOL).entry,
                    apps.find(name, POOL),
                )


class IdentityTests(unittest.TestCase):
    def shows(self, name, app, title):
        return apps._shows(apps.resolve_application(name, POOL), app, title)

    def test_the_process_name_of_the_entry_is_the_application(self) -> None:
        self.assertTrue(self.shows("Text Editor", "gnome-text-editor", "x"))

    def test_gnome_terminal_server_is_gnome_terminal(self) -> None:
        # audit.log 2026-08-23: the terminal's windows belong to this process.
        self.assertTrue(self.shows("Terminal", "gnome-terminal-server", "~"))

    def test_a_short_binary_name_is_not_a_prefix_of_other_programs(self) -> None:
        self.assertFalse(self.shows("code", "codeblocks-helper", "main.c"))

    def test_a_browser_tab_titled_like_the_target_is_not_the_target(self) -> None:
        # GNOME search fell through to the web search and Chrome opened a
        # tab whose title is exactly the query: the old rule called that a
        # success.
        self.assertFalse(self.shows(
            "Text Editor", "Google Chrome",
            "Text Editor - DuckDuckGo'da ara - Google Chrome",
        ))

    def test_an_unknown_process_is_recognized_by_its_title(self) -> None:
        # LibreOffice runs as `soffice`, which no desktop entry names.
        self.assertTrue(self.shows(
            "LibreOffice Writer", "soffice", "Untitled 1 - LibreOffice Writer"
        ))

    def test_a_window_title_target_matches_the_title(self) -> None:
        self.assertTrue(self.shows("notes.md", "gnome-text-editor", "notes.md - Text Editor"))
        self.assertFalse(self.shows("notes.md", "gnome-text-editor", "todo.md"))


class BringToFrontTests(Harness):
    def test_the_extension_path_types_nothing_and_reads_nothing(self) -> None:
        self.activate_result = True
        desk = Desk()
        outcome, keys = self.front("Text Editor", desk)

        self.assertEqual(outcome.path, "extension")
        self.assertEqual(keys.events, [])
        self.assertEqual(desk.focus_reads, 0)
        self.assertEqual(self.launched, [])

    def test_an_already_focused_target_gets_no_input(self) -> None:
        # Acceptance: no `super` for a target that is already in front.
        desk = Desk(focus=("gnome-text-editor", "notes.md - Text Editor"),
                    windows=[win("gnome-text-editor", "notes.md - Text Editor", True)])
        outcome, keys = self.front("Text Editor", desk)

        self.assertEqual(outcome.path, "already")
        self.assertEqual(keys.events, [])
        self.assertEqual(self.launched, [])
        self.assertIn("hicbir tus gonderilmedi", outcome.note)

    def test_a_browser_tab_named_like_the_target_is_not_already_focused(self) -> None:
        desk = Desk(focus=("Google Chrome", "Text Editor indir - Google Chrome"),
                    windows=[win("gnome-text-editor", "notes.md")])
        keys = Keys(on_return=lambda: setattr(
            desk, "focus", ("gnome-text-editor", "notes.md")))
        outcome, keys = self.front("Text Editor", desk, keys)
        self.assertEqual(outcome.path, "search")

    def test_a_name_that_is_no_application_types_nothing(self) -> None:
        desk = Desk(focus=("Google Chrome", "x"))
        with self.assertRaises(DesktopError) as caught:
            self.front("Pcbridge Nested A", desk)

        self.assertEqual(caught.exception.code, ErrorCode.TARGET_MISMATCH)
        self.assertEqual(caught.exception.execution_state, "not_started")
        self.assertEqual(self.launched, [])

    def test_an_ambiguous_name_types_nothing_and_launches_nothing(self) -> None:
        desk = Desk(focus=("Google Chrome", "x"))
        keys = Keys()
        with self.assertRaises(DesktopError) as caught:
            self.front("Desktop", desk, keys)

        self.assertEqual(caught.exception.code, ErrorCode.ELEMENT_AMBIGUOUS)
        self.assertEqual(caught.exception.execution_state, "not_started")
        self.assertIn("GitHub Desktop", str(caught.exception))
        self.assertEqual(keys.events, [])
        self.assertEqual(self.launched, [])

    def test_an_unreadable_focus_sends_nothing(self) -> None:
        desk = Desk()
        desk.focus_error = RuntimeError("AT-SPI yok")
        keys = Keys()
        with self.assertRaises(DesktopError) as caught:
            self.front("Text Editor", desk, keys)

        self.assertEqual(caught.exception.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(caught.exception.execution_state, "not_started")
        self.assertEqual(keys.events, [])
        self.assertEqual(self.launched, [])

    def test_a_closed_application_is_launched_without_input(self) -> None:
        # Acceptance: cold launch is kept, and needs no keyboard now.
        desk = Desk(focus=("Google Chrome", "x"))

        def appear():
            desk.listed = [win("gnome-text-editor", "New Document", True)]
            desk.focus = ("gnome-text-editor", "New Document")

        self.on_launch = appear
        outcome, keys = self.front("Text Editor", desk)

        self.assertEqual(outcome.path, "launch")
        self.assertEqual(self.launched, ["org.gnome.TextEditor"])
        self.assertEqual(keys.events, [])
        self.assertIn("odakta", outcome.note)

    def test_a_launched_window_without_focus_is_activated(self) -> None:
        desk = Desk(focus=("Google Chrome", "x"))
        self.on_launch = lambda: setattr(
            desk, "listed", [win("gnome-text-editor", "New Document")])
        # The window exists only after the launch: the extension finds it then.
        self.activate_result = lambda _w: bool(self.launched)
        outcome, keys = self.front("Text Editor", desk)

        self.assertEqual(outcome.path, "launch")
        self.assertEqual(self.activations, ["Text Editor", "Text Editor"])
        self.assertEqual(keys.events, [])

    def test_a_launched_window_without_focus_or_extension_is_searched(self) -> None:
        desk = Desk(focus=("Google Chrome", "x"))
        self.on_launch = lambda: setattr(
            desk, "listed", [win("gnome-text-editor", "New Document")])
        keys = Keys(on_return=lambda: setattr(
            desk, "focus", ("gnome-text-editor", "New Document")))
        outcome, keys = self.front("Text Editor", desk, keys)

        self.assertEqual(outcome.path, "search")
        self.assertEqual(keys.events[0], ("key", "super"))

    def test_a_launch_whose_window_never_appears_is_not_a_success(self) -> None:
        desk = Desk(focus=("Google Chrome", "x"))
        keys = Keys()
        with self.assertRaises(DesktopError) as caught:
            self.front("Text Editor", desk, keys)

        self.assertEqual(caught.exception.code, ErrorCode.EXECUTION_UNKNOWN)
        self.assertEqual(caught.exception.execution_state, "unknown")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(self.launched, ["org.gnome.TextEditor"])
        self.assertEqual(keys.events, [])
        self.assertGreaterEqual(sum(self.clock.slept), apps.LAUNCH_OBSERVE)

    def test_an_open_window_the_extension_cannot_activate_is_searched(self) -> None:
        desk = Desk(focus=("Google Chrome", "x"),
                    windows=[win("gnome-terminal-server", "~")])
        keys = Keys(on_return=lambda: setattr(
            desk, "focus", ("gnome-terminal-server", "~")))
        outcome, keys = self.front("Terminal", desk, keys)

        self.assertEqual(outcome.path, "search")
        self.assertEqual(
            keys.events,
            [("key", "super"), ("type", "Terminal", True), ("key", "Return")],
        )
        self.assertEqual(self.launched, [])
        self.assertIn("yedek yol", outcome.note)

    def test_a_wrong_search_result_is_not_a_success(self) -> None:
        # Acceptance: GNOME search opened a web search in the browser; the
        # tab title holds the query. That is an error, and the search is
        # closed with Escape.
        desk = Desk(focus=("claude-desktop", "Claude"),
                    windows=[win("gnome-text-editor", "notes.md")])
        keys = Keys(on_return=lambda: setattr(
            desk, "focus",
            ("Google Chrome", "Text Editor - DuckDuckGo'da ara - Google Chrome")))
        with self.assertRaises(DesktopError) as caught:
            self.front("Text Editor", desk, keys)

        self.assertEqual(caught.exception.code, ErrorCode.EXECUTION_UNKNOWN)
        self.assertEqual(caught.exception.execution_state, "unknown")
        self.assertIn("Google Chrome", str(caught.exception))
        self.assertEqual(keys.events[-2:], [("key", "Escape"), ("key", "Escape")])

    def test_an_unreadable_search_result_is_not_a_success(self) -> None:
        desk = Desk(focus=("claude-desktop", "Claude"),
                    windows=[win("gnome-text-editor", "notes.md")])

        def lose_focus():
            desk.focus_error = RuntimeError("AT-SPI dustu")

        keys = Keys(on_return=lose_focus)
        with self.assertRaises(DesktopError) as caught:
            self.front("Text Editor", desk, keys)
        self.assertEqual(caught.exception.code, ErrorCode.EXECUTION_UNKNOWN)
        self.assertEqual(keys.events[-1], ("key", "Escape"))

    def test_an_unreadable_window_list_keeps_the_old_search(self) -> None:
        desk = Desk(focus=("claude-desktop", "Claude"))
        desk.windows_error = RuntimeError("liste yok")
        keys = Keys(on_return=lambda: setattr(
            desk, "focus", ("gnome-text-editor", "notes.md")))
        outcome, keys = self.front("Text Editor", desk, keys)

        self.assertEqual(outcome.path, "search")
        self.assertEqual(self.launched, [])

    def test_no_search_starts_without_time_for_it(self) -> None:
        desk = Desk(focus=("claude-desktop", "Claude"),
                    windows=[win("gnome-text-editor", "notes.md")])
        keys = Keys()
        with self.assertRaises(apps.NoTimeLeft):
            self.front("Text Editor", desk, keys,
                       deadline=self.clock.now + apps.SEARCH_COST - 0.5)
        self.assertEqual(keys.events, [])

    def test_no_launch_starts_without_time_for_it(self) -> None:
        desk = Desk(focus=("claude-desktop", "Claude"))
        with self.assertRaises(apps.NoTimeLeft):
            self.front("Text Editor", desk,
                       deadline=self.clock.now + apps.LAUNCH_COST - 0.5)
        self.assertEqual(self.launched, [])

    def test_the_launch_watch_stops_at_the_deadline(self) -> None:
        desk = Desk(focus=("claude-desktop", "Claude"))
        with self.assertRaises(DesktopError):
            self.front("Text Editor", desk, deadline=self.clock.now + 3.0)
        self.assertLessEqual(sum(self.clock.slept), 3.0 + apps.LAUNCH_POLL)

    def test_the_old_adapter_returns_the_note(self) -> None:
        self.activate_result = True
        note = apps.focus("Text Editor", Keys(), Desk().focused)
        self.assertEqual(note, "Text Editor GNOME eklentisiyle one alindi")

    def test_computer_task_uses_the_same_order(self) -> None:
        # It used to launch every time: an open application got a second window.
        desk = Desk(focus=("gnome-text-editor", "notes.md - Text Editor"),
                    windows=[win("gnome-text-editor", "notes.md - Text Editor", True)])
        note = apps.prepare("Text Editor", Keys(), desk.focused, desk.windows)
        self.assertIn("zaten odakta", note)
        self.assertEqual(self.launched, [])


class LaunchApplicationTests(Harness):
    def launch(self, name, desk, **kw):
        return apps.launch_application(
            apps.resolve_application(name), desk.focused, desk.windows, **kw
        )

    def test_the_exit_code_alone_is_not_a_success(self) -> None:
        desk = Desk(focus=("claude-desktop", "Claude"))
        with self.assertRaises(DesktopError) as caught:
            self.launch("Text Editor", desk)
        self.assertEqual(caught.exception.code, ErrorCode.EXECUTION_UNKNOWN)
        self.assertIn("gorulmedi", str(caught.exception))

    def test_a_listed_window_is_a_success(self) -> None:
        desk = Desk(focus=("claude-desktop", "Claude"))
        self.on_launch = lambda: setattr(
            desk, "listed", [win("gnome-text-editor", "New Document")])
        outcome = self.launch("Text Editor", desk)
        self.assertEqual(outcome.path, "launch")
        self.assertIn("listede", outcome.note)

    def test_an_unknown_or_ambiguous_name_launches_nothing(self) -> None:
        for name, code in (("Pcbridge Nested A", ErrorCode.TARGET_MISMATCH),
                           ("Desktop", ErrorCode.ELEMENT_AMBIGUOUS)):
            with self.subTest(name=name), self.assertRaises(DesktopError) as caught:
                self.launch(name, Desk())
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.execution_state, "not_started")
        self.assertEqual(self.launched, [])


class GtkLaunchTests(unittest.TestCase):
    def test_a_missing_gtk_launch_is_a_capability_error(self) -> None:
        with mock.patch.object(apps.shutil, "which", return_value=None):
            with self.assertRaises(DesktopError) as caught:
                apps._gtk_launch(POOL[0], 1)
        self.assertEqual(caught.exception.code, ErrorCode.DEPENDENCY_MISSING)
        self.assertEqual(caught.exception.execution_state, "not_started")

    def launched_argv(self, which) -> list[str]:
        proc = mock.Mock()
        proc.wait.return_value = 0
        with (
            mock.patch.object(apps.shutil, "which", side_effect=which),
            mock.patch.object(apps.subprocess, "Popen", return_value=proc) as popen,
        ):
            apps._gtk_launch(POOL[0], 1)
        # A pipe would be inherited by the application and never close
        # (measured 2026-08-02 with Vesktop).
        self.assertNotEqual(popen.call_args.kwargs["stderr"], apps.subprocess.PIPE)
        return popen.call_args.args[0]

    def test_the_application_gets_its_own_systemd_scope(self) -> None:
        # Measured 2026-09-19: without it the application stays in the
        # caller's cgroup, and restarting pcbridge.service would kill it.
        argv = self.launched_argv(lambda name: f"/usr/bin/{name}")
        self.assertEqual(argv[:4], ["systemd-run", "--user", "--scope", "--collect"])
        unit = next(a for a in argv if a.startswith("--unit="))
        self.assertRegex(unit, r"^--unit=app-pcbridge-org\.gnome\.TextEditor-[0-9a-f]{8}\.scope$")
        self.assertEqual(argv[-2:], ["gtk-launch", "org.gnome.TextEditor"])

    def test_an_entry_id_that_is_no_unit_name_is_made_one(self) -> None:
        argv = apps._launch_argv(entry("Pcbridge Desktop", "Pcbridge Desktop"))
        if argv[0] == "systemd-run":
            unit = next(a for a in argv if a.startswith("--unit="))
            self.assertIn("app-pcbridge-Pcbridge_Desktop-", unit)
        self.assertEqual(argv[-1], "Pcbridge Desktop")

    def test_without_systemd_run_gtk_launch_runs_alone(self) -> None:
        argv = self.launched_argv(
            lambda name: None if name == "systemd-run" else f"/usr/bin/{name}")
        self.assertEqual(argv, ["gtk-launch", "org.gnome.TextEditor"])


class DeviceOpsTests(unittest.TestCase):
    def ops(self):
        tree = SimpleNamespace(focused_window=mock.Mock(), windows=mock.Mock())
        cfg = SimpleNamespace(
            shot_search_dirs=[],
            desktop=SimpleNamespace(agent_shot_max_age_seconds=60,
                                    ambiguous_coord_guard=True),
        )
        return opslib.DeviceOps(mock.Mock(), tree, cfg, mock.Mock()), tree

    def test_batch_focus_is_the_window_focus_orchestration(self) -> None:
        ops, tree = self.ops()
        with mock.patch.object(apps, "bring_to_front",
                               return_value=apps.Outcome("already", "zaten odakta")) as front:
            note = ops.focus("Text Editor", budget_left=30.0)
        self.assertEqual(note, "zaten odakta")
        args, kwargs = front.call_args
        self.assertEqual(args[0], "Text Editor")
        self.assertIs(args[2], tree.focused_window)
        self.assertIs(args[3], tree.windows)
        self.assertIsNotNone(kwargs["deadline"])

    def test_batch_launch_is_verified_by_observation(self) -> None:
        ops, tree = self.ops()
        with mock.patch.object(apps, "launch_application",
                               return_value=apps.Outcome("launch", "acildi")) as launch:
            self.assertEqual(ops.launch("Text Editor"), "acildi")
        args, kwargs = launch.call_args
        self.assertEqual(args[0].text, "Text Editor")
        self.assertIs(args[1], tree.focused_window)
        self.assertIs(args[2], tree.windows)
        self.assertIsNone(kwargs["deadline"])

    def test_no_time_left_becomes_a_budget_stop(self) -> None:
        ops, _tree = self.ops()
        for name, method in (("bring_to_front", ops.focus),
                             ("launch_application", ops.launch)):
            with (
                self.subTest(name=name),
                mock.patch.object(apps, name, side_effect=apps.NoTimeLeft("yok")),
                self.assertRaises(batchlib.BudgetExceeded),
            ):
                method("Text Editor", budget_left=0.5)


class BudgetOps:
    """A minimal `batch.Ops` that records the time each step was given."""

    def __init__(self, refuse: str = "") -> None:
        self.budgets: list[tuple[str, float | None]] = []
        self.refuse = refuse

    def launch(self, app, budget_left=None):
        self.budgets.append(("launch", budget_left))
        return "acildi"

    def focus(self, window, budget_left=None):
        self.budgets.append(("focus", budget_left))
        if self.refuse == "focus":
            raise batchlib.BudgetExceeded("arama sigmaz")
        return "one alindi"

    def key(self, keys):
        self.budgets.append(("key", None))
        return "ok"

    def focused(self):
        return "app | pencere"

    def held(self):
        return []

    def release_all(self):
        return []


class BatchCostTests(unittest.TestCase):
    def test_focus_costs_the_selected_path(self) -> None:
        focus = batchlib.Action("focus", {"window": "x"})
        self.assertEqual(batchlib.cost_ms(focus, fast_focus=True), batchlib.FOCUS_FAST_MS)
        self.assertEqual(batchlib.cost_ms(focus), batchlib.COST_MS["focus"])
        plan = batchlib.parse('[{"a":"focus","window":"x"},{"a":"focus","window":"y"}]')
        self.assertLess(batchlib.estimate(plan, fast_focus=True), 1.0)
        self.assertGreater(batchlib.estimate(plan), 10.0)

    def test_steps_get_the_time_that_is_left(self) -> None:
        ops = BudgetOps()
        plan = batchlib.parse('[{"a":"launch","app":"x"},{"a":"focus","window":"x"}]')
        batchlib.run(plan, ops, budget=30.0, fast_focus=True, check_focus=False)
        budgets = dict(ops.budgets)
        self.assertGreater(budgets["launch"], 29.0)
        self.assertLessEqual(budgets["focus"], 30.0)

    def test_a_step_that_does_not_fit_stops_the_batch_as_a_budget_stop(self) -> None:
        ops = BudgetOps(refuse="focus")
        plan = batchlib.parse(
            '[{"a":"focus","window":"x"},{"a":"key","keys":"a"}]')
        result = batchlib.run(plan, ops, budget=30.0, fast_focus=True, check_focus=False)

        self.assertEqual(result.stopped, "budget")
        self.assertEqual(result.steps, [])
        self.assertEqual([a.a for a in result.remaining], ["focus", "key"])
        self.assertIsNone(result.error)
        self.assertNotIn(("key", None), ops.budgets)

    def test_the_fast_path_fits_where_the_search_would_not(self) -> None:
        plan = batchlib.parse('[{"a":"focus","window":"x"}]')
        slow = batchlib.run(plan, BudgetOps(), budget=2.0, check_focus=False)
        fast = batchlib.run(plan, BudgetOps(), budget=2.0, fast_focus=True,
                            check_focus=False)
        self.assertEqual(slow.stopped, "budget")
        self.assertEqual(fast.stopped, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
