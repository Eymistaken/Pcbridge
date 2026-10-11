#!/usr/bin/env python3
"""`[power] keep_awake`: the inhibitor per desktop, when it is held, and that
it never outlives the daemon.

The real systemd-inhibit and gnome-session-inhibit are replaced by
`tests/fixtures/power/fake_inhibitor.py`; nothing here inhibits anything.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import config as configlib  # noqa: E402
from pcbridge import power as powerlib  # noqa: E402
from pcbridge import settings as S  # noqa: E402

FAKE = ROOT / "tests/fixtures/power/fake_inhibitor.py"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it no longer holds anything.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class MethodTests(unittest.TestCase):
    def test_each_desktop_gets_the_inhibitor_it_honors(self) -> None:
        gnome = powerlib.method_for("gnome")
        self.assertEqual((gnome.name, gnome.what), ("gnome-session", "suspend"))
        self.assertEqual(gnome.argv[0], "gnome-session-inhibit")
        self.assertIn("suspend", gnome.argv)
        self.assertNotIn("idle", gnome.argv)          # idle would stop the screen lock
        kde = powerlib.method_for("kde", which=lambda name: f"/usr/bin/{name}")
        self.assertEqual((kde.name, kde.what), ("powerdevil", "suspend"))
        self.assertEqual(kde.argv, ("kde-inhibit", "--power", "cat"))
        self.assertTrue(kde.unconfirmed)
        # Without kde-inhibit: a logind sleep block (idle would stop Plasma's lock).
        no_kde = powerlib.method_for("kde", which=lambda name: None if name == "kde-inhibit" else name)
        self.assertEqual((no_kde.name, no_kde.what), ("logind", "sleep"))
        self.assertIn("--what=sleep", no_kde.argv)
        self.assertEqual(powerlib.method_for("kde", which=lambda n: n, skip={"powerdevil"}).name, "logind")
        for other in ("hyprland", "unknown", ""):
            m = powerlib.method_for(other)
            self.assertEqual((m.name, m.what), ("logind", "idle"), other)
            self.assertIn("--mode=block", m.argv)
        for m in (gnome, kde, no_kde, powerlib.method_for("hyprland")):
            self.assertEqual(m.argv[-1], "cat")


class KeepAwakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb-keep-awake-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pidfile = self.tmp / "pids"
        self.clock = FakeClock()
        self.spawned: list[list[str]] = []

    def make(self, mode: str = "hold", desktop: str = "hyprland", installed: bool = True,
             confirm=lambda method: True):
        def popen(argv, **kw):
            self.spawned.append(list(argv))
            return subprocess.Popen([sys.executable, str(FAKE), mode, str(self.pidfile), *argv], **kw)

        ka = powerlib.KeepAwake(
            desktop=lambda: desktop,
            which=(lambda name: f"/usr/bin/{name}") if installed else (lambda name: None),
            popen=popen, clock=self.clock, settle_s=0.3, confirm=confirm,
        )
        self.addCleanup(ka.close)
        return ka

    def holder_pids(self) -> list[int]:
        if not self.pidfile.exists():
            return []
        return [int(line.split()[0]) for line in self.pidfile.read_text().splitlines()]

    def test_off_never_starts_anything(self) -> None:
        ka = self.make()
        ka.update(False, 3)
        self.assertFalse(ka.held())
        self.assertEqual(self.spawned, [])
        self.assertEqual(ka.status()["enabled"], False)

    def test_on_without_jobs_holds_nothing(self) -> None:
        ka = self.make()
        ka.update(True, 0)
        self.assertFalse(ka.held())
        self.assertEqual(self.spawned, [])

    def test_held_while_a_job_runs_and_released_after_the_last(self) -> None:
        ka = self.make(desktop="hyprland")
        ka.update(True, 2)
        self.assertTrue(ka.held())
        st = ka.status()
        self.assertEqual((st["held"], st["method"], st["what"]), (True, "logind", "idle"))
        self.assertEqual(st["reason"], "2 job(s) running")
        self.assertEqual(st["error"], "")
        self.assertEqual(len(self.spawned), 1)
        self.assertIn("--what=idle", self.spawned[0])
        ka.update(True, 1)                       # still running: the same process
        self.assertEqual(len(self.spawned), 1)
        [pid] = self.holder_pids()
        self.assertTrue(pid_alive(pid))
        ka.update(True, 0)
        self.assertFalse(ka.held())
        self.assertTrue(wait_gone(pid), "the inhibitor outlived the last job")
        self.assertFalse(ka.status()["held"])

    def test_turning_it_off_releases_at_once(self) -> None:
        ka = self.make(desktop="gnome")
        ka.update(True, 1)
        self.assertIn("gnome-session-inhibit", self.spawned[0])
        [pid] = self.holder_pids()
        ka.update(False, 1)
        self.assertFalse(ka.held())
        self.assertTrue(wait_gone(pid))

    def test_a_refusal_is_reported_and_retried_later_not_in_a_loop(self) -> None:
        ka = self.make(mode="deny", desktop="hyprland")
        ka.update(True, 1)
        st = ka.status()
        self.assertFalse(st["held"])
        self.assertIn("Access denied", st["error"])
        self.assertIn("polkit", st["error"])
        ka.update(True, 1)
        self.assertEqual(len(self.spawned), 1, "retried at once instead of waiting")
        self.clock.now += powerlib.RETRY_S + 1
        ka.update(True, 1)
        self.assertEqual(len(self.spawned), 2)
        ka.update(True, 0)                       # the last failure stays visible
        self.assertIn("Access denied", ka.status()["error"])
        ka.update(False, 0)
        self.assertEqual(ka.status()["error"], "")

    def test_powerdevil_is_confirmed_and_logind_takes_over_when_it_did_not_take(self) -> None:
        answers = [False]
        ka = self.make(desktop="kde", confirm=lambda method: answers[-1])
        ka.update(True, 1)
        self.assertEqual(self.spawned[0], ["kde-inhibit", "--power", "cat"])
        self.assertEqual(ka.status()["method"], "powerdevil")
        [first] = self.holder_pids()
        ka.update(True, 1)                       # too early to ask PowerDevil
        self.assertEqual(len(self.spawned), 1)
        self.clock.now += powerlib.CONFIRM_AFTER_S + 0.1
        ka.update(True, 1)                       # PowerDevil does not list it
        self.assertTrue(wait_gone(first))
        self.assertEqual(len(self.spawned), 2)
        self.assertIn("--what=sleep", self.spawned[1])
        st = ka.status()
        self.assertEqual((st["held"], st["method"], st["what"]), (True, "logind", "sleep"))
        # The next job tries PowerDevil first again, and this time it takes.
        ka.update(True, 0)
        answers.append(True)
        ka.update(True, 1)
        self.assertEqual(self.spawned[2], ["kde-inhibit", "--power", "cat"])
        self.clock.now += powerlib.CONFIRM_AFTER_S + 0.1
        ka.update(True, 1)
        self.assertEqual(len(self.spawned), 3)
        self.assertEqual(ka.status()["method"], "powerdevil")

    def test_a_missing_tool_is_named(self) -> None:
        ka = self.make(installed=False, desktop="gnome")
        ka.update(True, 1)
        self.assertFalse(ka.held())
        self.assertIn("gnome-session-inhibit", ka.status()["error"])
        self.assertIn("not installed", ka.status()["error"])
        self.assertEqual(self.spawned, [])

    def test_an_inhibitor_that_dies_is_noticed_and_taken_again(self) -> None:
        ka = self.make()
        ka.update(True, 1)
        [pid] = self.holder_pids()
        os.kill(pid, signal.SIGKILL)
        self.assertTrue(wait_gone(pid))
        ka.update(True, 1)
        self.assertFalse(ka.held())              # noted, waiting for the retry
        self.assertTrue(ka.status()["error"])
        self.clock.now += powerlib.RETRY_S + 1
        ka.update(True, 1)
        self.assertTrue(ka.held())
        self.assertEqual(len(self.holder_pids()), 2)

    def test_the_inhibitor_never_outlives_a_killed_daemon(self) -> None:
        script = textwrap.dedent(f"""
            import subprocess, sys, time
            sys.path.insert(0, {str(ROOT)!r})
            from pcbridge import power
            def popen(argv, **kw):
                return subprocess.Popen([sys.executable, {str(FAKE)!r}, "hold", {str(self.pidfile)!r}, *argv], **kw)
            ka = power.KeepAwake(desktop=lambda: "hyprland", which=lambda n: "/usr/bin/" + n,
                                 popen=popen, settle_s=0.3)
            ka.update(True, 1)
            print("held" if ka.held() else "not held", flush=True)
            time.sleep(60)
        """)
        daemon = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(daemon.stdout.readline().strip(), "held")
            [pid] = self.holder_pids()
            self.assertTrue(pid_alive(pid))
            daemon.kill()                        # SIGKILL: no cleanup code runs
            daemon.wait()
            self.assertTrue(wait_gone(pid), "the inhibitor outlived the daemon")
        finally:
            if daemon.poll() is None:
                daemon.kill()
            daemon.stdout.close()


class ConfigTests(unittest.TestCase):
    def write(self, body: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="pcb-keep-awake-cfg-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        text = re.sub(r'(?m)^state_dir = .*$', f'state_dir = "{tmp / "state"}"', text, count=1)
        text = re.sub(r'(?m)^password = .*$', 'password = "keep-awake-test-password"', text, count=1)
        text = re.sub(r"(?m)^keep_awake = .*$", body, text, count=1)
        path = tmp / "config.toml"
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_default_is_off(self) -> None:
        cfg = configlib.load_config(str(self.write("keep_awake = false")), check_state=False)
        self.assertFalse(cfg.keep_awake)
        self.assertNotIn("keep_awake", " ".join(cfg.warnings))
        self.assertFalse(configlib.Config.__dataclass_fields__["keep_awake"].default)

    def test_on_is_read_and_reread_live(self) -> None:
        path = self.write("keep_awake = true")
        self.assertTrue(configlib.load_config(str(path), check_state=False).keep_awake)
        self.assertIs(configlib.read_keep_awake(path), True)
        path.write_text(path.read_text().replace("keep_awake = true", "keep_awake = false"))
        self.assertIs(configlib.read_keep_awake(path), False)

    def test_a_wrong_value_is_refused_and_the_live_read_keeps_the_old_one(self) -> None:
        path = self.write('keep_awake = "yes"')
        with self.assertRaises(SystemExit):
            configlib.load_config(str(path), check_state=False)
        self.assertIsNone(configlib.read_keep_awake(path))
        self.assertIsNone(configlib.read_keep_awake(path.with_name("missing.toml")))


class SettingTests(unittest.TestCase):
    def test_it_is_a_documented_switch_that_needs_no_restart(self) -> None:
        s = {x.key: x for x in S.registry()}["power.keep_awake"]
        self.assertEqual((s.kind, s.default, s.section_title), ("bool", False, "Power"))
        self.assertEqual(s.restart, S.RESTART_NONE)
        self.assertIn("suspend", s.help)
        self.assertEqual(S.restart_advice(None), "")

    def test_changing_it_asks_for_no_restart(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="pcb-keep-awake-ed-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        text = re.sub(r'(?m)^state_dir = .*$', f'state_dir = "{tmp / "state"}"', text, count=1)
        path = tmp / "config.toml"
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        ed = S.ConfigEditor(path)
        ed.set("power.keep_awake", True)
        result = ed.save()
        self.assertEqual(result.changed, ["power.keep_awake"])
        self.assertIsNone(result.restart)
        self.assertIs(configlib.read_keep_awake(path), True)
        self.assertIn("[power]", path.read_text())


class CliTests(unittest.TestCase):
    """`pcbridge keep-awake` against a throwaway HOME and config."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pcb-keep-awake-cli-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        text = re.sub(r'(?m)^password = .*$', 'password = "keep-awake-cli-password"', text, count=1)
        text = re.sub(r'(?m)^state_dir = .*$', f'state_dir = "{self.home / "state"}"', text, count=1)
        self.cfg = self.home / "config.toml"
        self.cfg.write_text(text)
        self.cfg.chmod(0o600)
        self.env = {
            "HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "XDG_RUNTIME_DIR": str(self.home / "run"),
            "PCBRIDGE_SOCKET": str(self.home / "run" / "mcp.sock"),
            "PCBRIDGE_CONFIG": str(self.cfg),
        }

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "pcbridge", *args], capture_output=True,
                              text=True, env=self.env, cwd=str(ROOT), timeout=60)

    def test_on_off_and_status(self) -> None:
        r = self.run_cli("keep-awake")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("keep awake: off", r.stdout)
        r = self.run_cli("keep-awake", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("power.keep_awake = true", r.stdout)
        self.assertIn("no restart", r.stdout)
        self.assertIs(configlib.read_keep_awake(self.cfg), True)
        r = self.run_cli("keep-awake", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"enabled": true', r.stdout)
        r = self.run_cli("keep-awake", "on")
        self.assertIn("already on", r.stdout)
        r = self.run_cli("get", "power.keep_awake")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("true", r.stdout)
        r = self.run_cli("keep-awake", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(configlib.read_keep_awake(self.cfg), False)
        self.assertIn("keep-awake", self.run_cli("list").stdout)

    def test_status_reads_what_the_daemon_holds(self) -> None:
        import json

        state = self.home / "state"
        state.mkdir(parents=True)
        (state / "status.json").write_text(json.dumps({
            "daemon": "running", "keep_awake": {
                "enabled": True, "held": True, "method": "logind", "what": "idle",
                "reason": "1 job(s) running", "error": ""}}))
        self.run_cli("keep-awake", "on")
        r = self.run_cli("keep-awake")
        self.assertIn("holding a logind idle inhibitor (1 job(s) running)", r.stdout)


if __name__ == "__main__":
    unittest.main()
