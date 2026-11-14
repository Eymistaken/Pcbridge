"""`pcbridge setup`'s screen and the pieces it shares with the printed setup:
the steps, the choices, the install rows, the sudo hand-off, the PATH offer,
at 120 and 80 columns. The install itself is a fake: nothing is installed."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli import install as inst  # noqa: E402
from pcbridge.cli import plan as planlib  # noqa: E402
from pcbridge.cli import setupflow  # noqa: E402
from pcbridge.platforminfo import Platform  # noqa: E402
from pcbridge.tui import setup_app  # noqa: E402
from pcbridge.tui.setup_app import Bridge, Checklist, SetupApp, TaskList  # noqa: E402
from tests.contracts.test_tui import screen_text  # noqa: E402

WIDE = (120, 34)
NARROW = (80, 24)


def platform(desktop: bool = True) -> Platform:
    return Platform(arch="x86_64", distro_name="Zorin OS 18", distro_id="zorin", family="debian",
                    package_manager="apt-get", session_type="wayland" if desktop else "",
                    desktop="GNOME" if desktop else "", desktop_kind="gnome" if desktop else "unknown",
                    desktop_version="46", session_source="environment", glibc=(2, 39), language="tr")


def context(desktop: bool = True, argv: list[str] | None = None) -> setupflow.Context:
    found = platform(desktop)
    components = [planlib.Component("ocr", "OCR", ["tesseract-ocr", "tesseract-ocr-tur"], True,
                                    ["eng", "tur"])] if desktop else []
    plan = planlib.Plan(platform=found, family="debian", needs=["tmux"], packages=["tmux", "wl-clipboard"],
                        components=components, profile_now="full", profile_target=None, profile_owned=True,
                        profile_note="full", native_note="", extras=desktop, native_fetch=desktop)
    args = setupflow.parser().parse_args(argv or [])
    return setupflow.Context(args=args, found=found, plan=plan, xdg=Path("/nowhere/config.toml"),
                             backup=inst.Backup(root=Path(tempfile.mkdtemp()) / "backup"))


CLIENTS = {"claude-code": (True, "~/.claude.json"), "codex": (True, "~/.codex/config.toml"),
           "opencode": (True, "~/.config/opencode/opencode.json")}


class FakeReg:
    def __init__(self, source: str) -> None:
        self.present, self.enabled, self.source = False, False, source


def patched_clients():
    return mock.patch.multiple(
        setup_app.connectlib,
        current=lambda c: FakeReg(CLIENTS.get(c, (False, ""))[1]),
        client_available=lambda c: CLIENTS.get(c, (False, ""))[0],
    )


class FakeInstall:
    """Emits what `setupflow.execute` emits, asks for sudo once, records the call."""

    def __init__(self, ready: bool = True, fail: str = "", path_fix: inst.PathFix | None = None,
                 gate: threading.Event | None = None, warn_on: str = "") -> None:
        self.calls: list[tuple[bool, list[str] | None]] = []
        self.sudo: list[bool] = []
        self.ready, self.fail, self.path_fix, self.gate, self.warn_on = ready, fail, path_fix, gate, warn_on

    def __call__(self, ctx, install_now, clients):
        self.calls.append((install_now, clients))
        for key, title in setupflow.steps(ctx):
            inst.step(key, title)
            if key == "packages" and install_now:
                self.sudo.append(inst.sudo_prompt(["sudo", "apt-get", "install", "-y", "tmux"]))
                inst.say("  $ sudo -n apt-get install -y tmux")
                inst._emit("out", "Setting up tmux (3.4-1) ...")
            if self.gate is not None and key == "config":
                self.gate.wait(5)
            if self.fail and key == "service":
                raise RuntimeError(self.fail)
            if key == self.warn_on:
                inst.warn(f"{title}: something to look at")
            else:
                inst.ok(f"{title} done")
        return setupflow.Outcome(ready=self.ready, tools=40 if self.ready else "timeout", want=40,
                                 notes=[] if self.ready else ["pcbridge doctor"],
                                 clients=[(c, "ok", "connected") for c in (clients or [])],
                                 rollback=Path("/tmp/backup/ROLLBACK.md"), path_fix=self.path_fix)


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    await pilot.pause()


def flat(app) -> str:
    return " ".join(" ".join(screen_text(app)).split())


def run(body, ctx=None, size=WIDE, fake=None, path_fix=None, sudo=lambda argv: True):
    fake = fake or FakeInstall()
    ctx = ctx or context()
    out = {}

    async def go():
        with patched_clients(), mock.patch.object(setup_app.inst, "path_fix", return_value=path_fix):
            app = SetupApp(ctx, executor=fake, sudo_runner=sudo, motion=False)
            async with app.run_test(size=size) as pilot:
                await settle(app, pilot)
                await body(app, pilot)
                out["app"] = app

    asyncio.run(go())
    return out["app"], fake


class WalkThroughTests(unittest.TestCase):
    def test_every_step_with_the_keyboard_at_120_columns(self) -> None:
        sudo_calls: list[list[str]] = []

        def sudo(argv):
            sudo_calls.append(argv)
            return True

        async def body(app, pilot):
            text = flat(app)
            self.assertIn("Welcome · step 1 of 6", text)
            self.assertIn("this pc", text)
            self.assertIn("agents", text)
            probes = " ".join(app._probes_text(99).plain.split())
            self.assertIn("✓ System Zorin OS 18 (Debian family) · GNOME 46 · Wayland · x86_64", probes)
            self.assertIn("✓ Clients 3 found: Claude Code, Codex, OpenCode", probes)
            self.assertIn("Looking at this computer", text)
            self.assertIn("Start ⏎", text)
            await pilot.press("enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("Components · step 2 of 6", text)
            self.assertIn("◆ System packages tmux, wl-clipboard · needs sudo", text)
            self.assertIn("[✓] OCR", text)
            self.assertIn("Recommended", text)
            self.assertIn("4 system packages need sudo", text)
            await pilot.press("space")      # untick OCR
            await settle(app, pilot)
            self.assertIn("[ ] OCR", flat(app))
            self.assertIn("2 system packages need sudo", flat(app))
            await pilot.press("enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("Clients · step 3 of 6", text)
            self.assertIn("[✓] Claude Code", text)
            self.assertIn("[ ] OpenCode", text)
            self.assertIn("on request", text)
            self.assertIn("· Claude Desktop", text)      # not found
            await pilot.press("down", "space")           # untick Codex
            await settle(app, pilot)
            self.assertIn("1 ticked: Claude Code.", flat(app))
            await pilot.press("enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("Review · step 4 of 6", text)
            self.assertIn("Install 2 system packages asks for your password", text)
            self.assertIn("$ sudo apt-get install -y tmux wl-clipboard", text)
            self.assertNotIn("tesseract", text)
            self.assertIn("Connect Claude Code", text)
            self.assertIn("Desktop control stays off until you turn it on.", text)
            self.assertEqual(app.executor.calls, [])     # nothing ran before Install
            await pilot.press("enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("✓ Everything is installed.", text)
            self.assertIn("✓ System packages", text)
            self.assertIn("✓ Readiness check", text)
            self.assertIn("Readiness check done", text)
            await pilot.press("enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("pcbridge is ready", text)
            self.assertIn("✓ Claude Code connected", text)
            self.assertNotIn("Codex connected", text)
            self.assertIn("Type pcbridge to check and change anything.", text)
            self.assertIn("Undo: /tmp/backup/ROLLBACK.md", text)

        app, fake = run(body, sudo=sudo)
        self.assertEqual(fake.calls, [(True, ["claude-code"])])
        self.assertEqual(sudo_calls, [["sudo", "apt-get", "install", "-y", "tmux"]])
        self.assertEqual(fake.sudo, [True])
        self.assertFalse(app.ctx.plan.components[0].ticked)

    def test_the_mouse_ticks_rows_and_the_bridge_goes_back(self) -> None:
        async def body(app, pilot):
            await pilot.click("#go-start")
            await settle(app, pilot)
            self.assertEqual(app.step, "parts")
            await pilot.click("#optional", offset=(3, 0))
            await settle(app, pilot)
            self.assertFalse(app.optional[0].on)
            await pilot.click("#go-next-parts")
            await settle(app, pilot)
            self.assertEqual(app.step, "clients")
            # The bridge: a click on the first plank's label goes back to hello.
            bridge = app.query_one(Bridge)
            start, _end = bridge._spans[0]
            await pilot.click(Bridge, offset=(start + 2, 4))
            await settle(app, pilot)
            self.assertEqual(app.step, "welcome")

        run(body)

    def test_escape_goes_back_and_q_stops_with_nothing_changed(self) -> None:
        async def body(app, pilot):
            await pilot.press("enter", "enter")
            await settle(app, pilot)
            self.assertEqual(app.step, "clients")
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertEqual(app.step, "parts")
            await pilot.press("q")
            await settle(app, pilot)

        app, fake = run(body)
        self.assertEqual(fake.calls, [])
        with mock.patch("builtins.print") as printed:
            self.assertEqual(app.report(setup_app.STOPPED), 1)
        printed.assert_called_with("Stopped; nothing was changed.")

    def test_quit_is_refused_while_the_install_runs(self) -> None:
        gate = threading.Event()
        fake = FakeInstall(gate=gate)

        async def body(app, pilot):
            await pilot.press("enter", "enter", "enter", "enter")
            for _ in range(50):
                await pilot.pause(0.02)
                if app.query_one(TaskList).tasks[3].state == "run":
                    break
            await pilot.press("q")
            await pilot.pause()
            self.assertTrue(app.is_running)
            self.assertTrue(app.installing)
            self.assertIn("Setup is still running", " ".join(str(n.message) for n in app._notifications))
            gate.set()
            await settle(app, pilot)
            self.assertTrue(app.finished)

        run(body, fake=fake)

    def test_the_output_log_opens_with_l(self) -> None:
        async def body(app, pilot):
            await pilot.press("enter", "enter", "enter", "enter")
            await settle(app, pilot)
            await pilot.press("l")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("Hide the output l", text)
            self.assertIn("✓ Readiness check done", text)

        run(body)


class OutcomeTests(unittest.TestCase):
    def test_a_warning_is_counted_and_listed(self) -> None:
        async def body(app, pilot):
            await pilot.press("enter", "enter", "enter", "enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("▲ Native helper", text)
            self.assertIn("Native helper: something to look at", text)
            self.assertIn("1 step needs attention", text)

        run(body, fake=FakeInstall(warn_on="native"))

    def test_a_crash_stops_with_the_reason(self) -> None:
        async def body(app, pilot):
            await pilot.press("enter", "enter", "enter", "enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("✕ Background service", text)
            self.assertIn("Setup stopped: the unit file is unreadable", text)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Setup stopped before the end", flat(app))

        app, _ = run(body, fake=FakeInstall(fail="the unit file is unreadable"))
        with mock.patch("builtins.print"):
            self.assertEqual(app.report(setup_app.DONE), 1)

    def test_a_failed_readiness_check_says_what_to_do(self) -> None:
        async def body(app, pilot):
            await pilot.press("enter", "enter", "enter", "enter")
            await settle(app, pilot)
            await pilot.press("enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("pcbridge is installed, but the readiness check failed", text)
            self.assertIn("pcbridge doctor", text)

        app, _ = run(body, fake=FakeInstall(ready=False))
        with mock.patch("builtins.print"):
            self.assertEqual(app.report(setup_app.DONE), 1)

    def test_no_desktop_means_no_optional_part_and_no_sudo_note_for_it(self) -> None:
        async def body(app, pilot):
            text = flat(app)
            self.assertIn("▲ Desktop", text)
            await pilot.press("enter")
            await settle(app, pilot)
            text = flat(app)
            self.assertIn("Nothing optional on this computer", text)
            self.assertNotIn("Native helper", text)
            self.assertIn("⏎ next esc back", text)

        run(body, ctx=context(desktop=False))


class PathTests(unittest.TestCase):
    def fix(self, tmp: Path) -> inst.PathFix:
        return inst.PathFix("bash", tmp / ".bashrc", 'export PATH="$HOME/.local/bin:$PATH"')

    def test_yes_adds_the_line_and_lists_the_file_in_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / ".bashrc").write_text("alias ll='ls -l'\n")
            fix = self.fix(tmp)
            ctx = context()

            async def body(app, pilot):
                self.assertIn("path", app.keys)
                await pilot.press("enter", "enter", "enter", "enter")
                await settle(app, pilot)
                await pilot.press("enter")
                await settle(app, pilot)
                text = flat(app)
                self.assertIn("Shell PATH · step 6 of 7", text)
                self.assertIn("is not on PATH", text)
                self.assertIn('export PATH="$HOME/.local/bin:$PATH" # added by pcbridge setup', text)
                await pilot.press("y")
                await settle(app, pilot)
                self.assertIn("✓ Added to", flat(app))
                self.assertIn("source", flat(app))
                await pilot.press("enter")
                await settle(app, pilot)
                self.assertIn("Open a new terminal first", flat(app))

            run(body, ctx=ctx, fake=FakeInstall(path_fix=fix), path_fix=fix)
            text = (tmp / ".bashrc").read_text()
            self.assertTrue(text.startswith("alias ll='ls -l'\n"))
            self.assertIn('export PATH="$HOME/.local/bin:$PATH"   # added by pcbridge setup', text)
            rollback = (ctx.backup.root / "ROLLBACK.md").read_text()
            self.assertIn(str(tmp / ".bashrc"), rollback)

    def test_no_leaves_the_file_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / ".bashrc").write_text("x\n")
            fix = self.fix(tmp)

            async def body(app, pilot):
                await pilot.press("enter", "enter", "enter", "enter")
                await settle(app, pilot)
                await pilot.press("enter")
                await settle(app, pilot)
                await pilot.press("n")
                await settle(app, pilot)
                self.assertIn("Nothing was changed.", flat(app))

            run(body, fake=FakeInstall(path_fix=fix), path_fix=fix)
            self.assertEqual((tmp / ".bashrc").read_text(), "x\n")


class NarrowTests(unittest.TestCase):
    def test_every_step_keeps_its_buttons_on_screen_at_80_by_24(self) -> None:
        async def body(app, pilot):
            seen = []
            for key, button in (("welcome", "Start ⏎"), ("parts", "Next ⏎"), ("clients", "Next ⏎"),
                                ("review", "Install ⏎")):
                self.assertEqual(app.step, key)
                self.assertIn(button, flat(app))
                seen.append(key)
                await pilot.press("enter")
                await settle(app, pilot)
            self.assertIn("Continue ⏎", flat(app))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Open pcbridge ⏎", flat(app))
            rows = screen_text(app)
            self.assertTrue(all(len(r) == 80 for r in rows))
            # Numbers under the planks instead of names; no logo.
            self.assertIn(" 1 ", rows[5])
            self.assertNotIn("hello", rows[5])

        run(body, size=NARROW)


class SinkTests(unittest.TestCase):
    def test_lines_go_to_the_sink_instead_of_stdout(self) -> None:
        got: list[tuple[str, str]] = []
        inst.set_sink(lambda k, m: got.append((k, m)))
        try:
            with mock.patch("builtins.print") as printed:
                inst.step("config", "Configuration", 2)
                inst.ok("fine")
                inst.warn("hm")
                inst.say("plain")
                self.assertEqual(inst.run_streamed([sys.executable, "-c", "print('a'); print('b')"]), 0)
            printed.assert_not_called()
        finally:
            inst.set_sink(None)
        self.assertEqual(got, [("step", "config\tConfiguration"), ("ok", "fine"), ("warn", "hm"),
                               ("say", "plain"), ("out", "a"), ("out", "b")])

    def test_without_a_sink_the_printed_output_is_unchanged(self) -> None:
        with mock.patch("builtins.print") as printed:
            inst.step("config", "Configuration", 2)
            inst.step("python", "Python packages")
        printed.assert_called_once_with("\n2. Configuration", flush=True)

    def test_sudo_asks_first_then_runs_without_a_terminal(self) -> None:
        asked: list[list[str]] = []
        ran: list[list[str]] = []

        def prompt(argv):
            asked.append(argv)
            return True

        with mock.patch.object(inst, "sudo_prompt", prompt), \
                mock.patch.object(inst, "run_streamed", side_effect=lambda argv, **kw: ran.append(argv) or 0), \
                mock.patch.object(inst, "say"):
            self.assertTrue(planlib.run_sudo(["sudo", "apt-get", "install", "-y", "tmux"]))
        self.assertEqual(asked, [["sudo", "apt-get", "install", "-y", "tmux"]])
        self.assertEqual(ran, [["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install",
                                "-y", "tmux"]])

    def test_a_refused_password_runs_nothing(self) -> None:
        with mock.patch.object(inst, "sudo_prompt", lambda argv: False), \
                mock.patch.object(inst, "run_streamed") as streamed, mock.patch.object(inst, "fail"):
            self.assertFalse(planlib.run_sudo(["sudo", "pacman", "-S", "tmux"]))
        streamed.assert_not_called()

    def test_the_screen_is_only_for_a_person_at_a_terminal(self) -> None:
        def wants(argv, term="xterm-256color", tty=True):
            args = setupflow.parser().parse_args(argv)
            with mock.patch.dict(os.environ, {"TERM": term}), \
                    mock.patch.object(sys.stdin, "isatty", return_value=tty), \
                    mock.patch.object(sys.stdout, "isatty", return_value=tty):
                os.environ.pop("PCBRIDGE_SETUP_PLAIN", None)
                return setupflow.wants_screen(args)

        self.assertTrue(wants([]))
        self.assertFalse(wants(["--yes"]))
        self.assertFalse(wants(["--plain"]))
        self.assertFalse(wants([], term="dumb"))
        self.assertFalse(wants([], tty=False))

    def test_the_rows_follow_the_options(self) -> None:
        keys = [k for k, _ in setupflow.steps(context())]
        self.assertEqual(keys, ["packages", "python", "native", "config", "commands", "service", "desktop",
                                "clients", "aliases", "ready"])
        keys = [k for k, _ in setupflow.steps(context(desktop=False, argv=["--no-connect", "--no-aliases",
                                                                            "--no-extension"]))]
        self.assertEqual(keys, ["packages", "config", "commands", "service", "ready"])


class PathFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(inst, "HOME", self.tmp),
                        mock.patch.object(inst, "LOCAL_BIN", self.tmp / ".local" / "bin"),
                        mock.patch.object(inst, "is_system_package", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()

    def test_bash_zsh_and_fish_get_their_own_file(self) -> None:
        fix = inst.path_fix(path="/usr/bin", shell="/bin/bash")
        self.assertEqual((fix.shell, fix.file), ("bash", self.tmp / ".bashrc"))
        with mock.patch.dict(os.environ, {"ZDOTDIR": ""}):
            fix = inst.path_fix(path="/usr/bin", shell="/usr/bin/zsh")
        self.assertEqual(fix.file, self.tmp / ".zshrc")
        fix = inst.path_fix(path="/usr/bin", shell="/usr/bin/fish")
        self.assertEqual(fix.file, self.tmp / ".config" / "fish" / "conf.d" / "pcbridge-path.fish")
        self.assertEqual(fix.line, "fish_add_path -g $HOME/.local/bin")
        self.assertIsNone(inst.path_fix(path="/usr/bin", shell="/bin/tcsh"))

    def test_nothing_is_offered_when_it_is_not_needed(self) -> None:
        on_path = str(self.tmp / ".local" / "bin") + ":/usr/bin"
        self.assertIsNone(inst.path_fix(path=on_path, shell="/bin/bash"))
        (self.tmp / ".profile").write_text('PATH="$HOME/.local/bin:$PATH"\n')
        self.assertIsNone(inst.path_fix(path="/usr/bin", shell="/bin/bash"))
        (self.tmp / ".profile").unlink()
        (self.tmp / ".bashrc").write_text('export PATH="$HOME/.local/bin:$PATH"\n')
        self.assertIsNone(inst.path_fix(path="/usr/bin", shell="/bin/bash"))

    def test_a_new_file_is_listed_as_created_in_rollback(self) -> None:
        fix = inst.path_fix(path="/usr/bin", shell="/usr/bin/fish")
        backup = inst.Backup(root=self.tmp / "backup")
        self.assertIn("added to", inst.apply_path_fix(fix, backup))
        self.assertEqual(fix.file.read_text(), "fish_add_path -g $HOME/.local/bin   # added by pcbridge setup\n")
        rollback = backup.write_rollback().read_text()
        self.assertIn(f"rm -f '{fix.file}'   # did not exist before", rollback)
        self.assertIn("already in", inst.apply_path_fix(fix, backup))


class StarsTests(unittest.TestCase):
    def test_the_same_region_gets_the_same_stars(self) -> None:
        a = setup_app.make_stars(40, 6, "x")
        b = setup_app.make_stars(40, 6, "x")
        self.assertEqual(a, b)
        count = sum(len(r) for r in a.values())
        self.assertEqual(count, int(40 * 6 * 0.03))

    def test_the_sky_fills_as_the_bridge_grows(self) -> None:
        async def body(app, pilot):
            levels = [app.sky_level()]
            for _ in range(3):
                await pilot.press("enter")
                await settle(app, pilot)
                levels.append(app.sky_level())
            self.assertEqual(levels, sorted(levels))
            self.assertLess(levels[0], 0.4)

        run(body)


if __name__ == "__main__":
    unittest.main()
