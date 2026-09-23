#!/usr/bin/env python3
"""Fault injection for pcbridge 2.0 (Step 8): each scenario states what must
happen and checks it against a fake or a throwaway environment.

Nothing here touches the real desktop, the real grant or the real service.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import contextvars  # noqa: E402
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from pcbridge.config import DesktopSpec  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from pcbridge.desktop.safety import (  # noqa: E402
    ActivityObservation,
    ActivityState,
    SafetyGate,
    ScreenLockObservation,
    ScreenLockState,
)
from pcbridge.native.client import NativeClient  # noqa: E402
from pcbridge.native.registry import NativeRegistry  # noqa: E402

FAKE_HELPER = ROOT / "tests/fixtures/native/fake_native_helper.py"


def run_pcbridge(*args: str, env: dict[str, str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pcbridge", *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=timeout,
    )


class NativeHelperCrashTests(unittest.TestCase):
    """The native helper dies mid-capture: the next call gets a new process
    and nothing of the dead one is left behind."""

    def test_a_crash_leaves_no_registry_entry_and_the_next_call_respawns(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            client = NativeClient(
                FAKE_HELPER,
                state_dir=root / "state",
                runtime_dir=root / "runtime",
                environment={"PCBRIDGE_FAKE_NATIVE_MODE": "crash"},
                default_timeout=1.0,
            )
            self.addCleanup(client.close)
            registry = NativeRegistry(root / "runtime")

            client.request("ping")
            first = client.pid
            self.assertEqual([e.pid for e in registry.entries()], [first])

            with self.assertRaises(DesktopError) as crashed:
                client.request("crash")
            self.assertEqual(crashed.exception.code, ErrorCode.NATIVE_CRASHED)
            deadline = time.monotonic() + 2.0
            while registry.entries() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(registry.entries(), [], "the dead helper is still registered")

            client.request("ping")
            self.assertNotEqual(client.pid, first)
            self.assertEqual([e.pid for e in registry.entries()], [client.pid])


class _Unlocked:
    """Screen unlocked, the user known to be away for ten minutes."""

    def screen_lock(self) -> ScreenLockObservation:
        return ScreenLockObservation(ScreenLockState.KNOWN_UNLOCKED, observed_at=1.0)

    def user_activity(self) -> ActivityObservation:
        return ActivityObservation(ActivityState.KNOWN, idle_ms=600_000, observed_at=1.0)


def _gate(root: Path) -> SafetyGate:
    cfg = SimpleNamespace(
        desktop=DesktopSpec(enabled=True, idle_guard_seconds=60, max_actions_per_second=0),
        state_dir=root, audit_log=root / "audit.log", audit_max_bytes=0,
    )
    return SafetyGate(cfg, state_provider=_Unlocked())


_GRANT_LOOP = """
import sys, time
sys.path.insert(0, {root!r})
from pcbridge.desktop.lease import LeaseStore
store = LeaseStore({state!r})
for i in range(40):
    now = time.time()
    store.grant(until=now + 60, reason="stress", granted=now, granted_by={who!r})
"""


class ConcurrentUnlockTests(unittest.TestCase):
    """Two clients call `desktop_unlock` at the same time."""

    def test_two_processes_granting_at_once_never_tear_the_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            procs = [
                subprocess.Popen([sys.executable, "-c", _GRANT_LOOP.format(
                    root=str(ROOT), state=str(state), who=who)])
                for who in ("client-a", "client-b")
            ]
            store = LeaseStore(state)
            seen = set()
            while any(p.poll() is None for p in procs):
                snap = store.snapshot()  # a torn file would read as empty
                if snap.grant_id:
                    seen.add(snap.grant_id)
                    self.assertGreater(snap.until, time.time())
            self.assertEqual([p.returncode for p in procs], [0, 0])
            final = json.loads((state / "desktop_unlock.json").read_text())
            self.assertEqual(len(final["grant_id"]), 32)
            self.assertIn(final["granted_by"], ("client-a", "client-b"))
            self.assertEqual(list(state.glob(".desktop_unlock.json.*.tmp")), [])

    def test_a_second_unlock_ends_the_first_sessions_sequence_not_its_next_call(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            gate = _gate(Path(raw))

            def session_a_admitted():
                gate.unlock(5)
                decision = gate.check("computer_batch")
                return decision, gate.last_token()

            decision, token_a = contextvars.copy_context().run(session_a_admitted)
            self.assertTrue(decision.allowed, decision.reason)
            self.assertTrue(gate.verify(token_a).allowed)

            # Client B unlocks in its own session.
            contextvars.copy_context().run(gate.unlock, 5)

            # A's running sequence is under the old grant: it stops (fail closed)...
            refused = gate.verify(token_a)
            self.assertFalse(refused.allowed)
            self.assertIsNotNone(refused.code)
            # ...but A's next call is admitted under the grant that is open now.
            def session_a_next():
                d = gate.check("computer_batch")
                return d, gate.last_token()
            again, token_next = contextvars.copy_context().run(session_a_next)
            self.assertTrue(again.allowed, again.reason)
            self.assertNotEqual(token_next.grant_id, token_a.grant_id)


_BLOCKER = """
import os, sys
_blocked = set(filter(None, os.environ.get("PCB_BLOCK", "").split(",")))
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in _blocked:
            raise ModuleNotFoundError(f"No module named {name!r} (hidden by a test)")
        return None
sys.meta_path.insert(0, _Block())
"""

_SESSION = """
import json, subprocess, sys
p = subprocess.Popen([sys.executable, "-m", "pcbridge.server", "--stdio"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def send(m): p.stdin.write(json.dumps(m) + "\\n"); p.stdin.flush()
def recv(i):
    for line in p.stdout:
        m = json.loads(line)
        if m.get("id") == i:
            return m
    raise SystemExit("server exited")
send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "t", "version": "1"}}})
recv(1)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
out = {}
for i, (name, args) in enumerate([("system_capabilities", {}),
                                  ("shell_run", {"command": "echo still-alive"})], start=2):
    send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
          "params": {"name": name, "arguments": args}})
    r = recv(i)["result"]
    out[name] = "\\n".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
p.stdin.close(); p.wait(timeout=10)
print(json.dumps(out))
"""

_CONFIG = """\
config_version = 2
public_url = "http://localhost:8765"
default_agent = "claude"
[auth]
password = "hardening-test-password"
[paths]
state_dir = "{state}"
[desktop]
enabled = true
[agents.claude]
command = ["true"]
"""


class MissingDependencyTests(unittest.TestCase):
    """Pillow, evdev and wl-clipboard missing: the related capabilities say
    what to install, and the rest of the server works."""

    def test_capabilities_name_the_fix_and_other_tools_keep_working(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "site").mkdir()
            (root / "site" / "sitecustomize.py").write_text(_BLOCKER)
            # A PATH without wl-copy / wl-paste / gnome-screenshot.
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for entry in Path("/usr/bin").iterdir():
                if entry.name not in {"wl-copy", "wl-paste", "gnome-screenshot"}:
                    try:
                        (bin_dir / entry.name).symlink_to(entry)
                    except OSError:
                        pass
            cfg = root / "config.toml"
            cfg.write_text(_CONFIG.format(state=root / "state"))
            cfg.chmod(0o600)
            env = {k: v for k, v in os.environ.items() if not k.startswith("PCBRIDGE_")}
            env.update({
                "PATH": str(bin_dir), "PYTHONPATH": str(root / "site"),
                "PCB_BLOCK": "evdev,PIL", "PCBRIDGE_IN_PROCESS": "1",
                "PCBRIDGE_CONFIG": str(cfg), "XDG_STATE_HOME": str(root / "xdg-state"),
            })
            res = subprocess.run([sys.executable, "-c", _SESSION], capture_output=True,
                                 text=True, env=env, cwd=str(ROOT), timeout=120)
            self.assertEqual(res.returncode, 0, res.stderr[-2000:])
            out = json.loads(res.stdout.strip().splitlines()[-1])
            self.assertIn("still-alive", out["shell_run"])
            caps = out["system_capabilities"]
            missing = [ln for ln in caps.splitlines() if "DEPENDENCY_MISSING" in ln]
            self.assertTrue(any("clipboard.read" in ln for ln in missing), caps)
            # Without a readable monitor table (a CI runner) capture.monitor
            # reports that instead; where the table is readable, Pillow is named.
            monitor_line = next((ln for ln in caps.splitlines() if "`capture.monitor`" in ln), "")
            self.assertTrue("Pillow" in monitor_line or "DISPLAY_MAPPING_UNKNOWN" in monitor_line,
                            monitor_line)
            for line in missing:
                self.assertIn(" — ", line, f"no fix named: {line}")
            self.assertNotIn("Traceback", res.stderr)


class UnsupportedSessionTests(unittest.TestCase):
    """X11 or a non-GNOME desktop: desktop tools refuse and say why."""

    def test_the_note_names_the_session_and_stays_quiet_on_gnome_wayland(self) -> None:
        from pcbridge.desktop.session import support_note

        self.assertEqual(support_note({"XDG_SESSION_TYPE": "wayland",
                                       "XDG_CURRENT_DESKTOP": "zorin:GNOME"}), "")
        # Empty is not a verdict: stdio clients and systemd often pass none.
        self.assertEqual(support_note({}), "")
        note = support_note({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"})
        self.assertIn("an X11 session on the KDE desktop", note)
        self.assertIn("GNOME on Wayland", note)
        self.assertIn("the sway desktop",
                      support_note({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "sway"}))

    def test_the_platform_report_names_untested_versions_instead_of_failing(self) -> None:
        from unittest import mock

        from pcbridge.desktop import session

        def fake(version):
            def busctl(*args):
                if "ShellVersion" in args:
                    return f's "{version}"' if version else ""
                return '{"data":[["org.gnome.Mutter.ScreenCast"]]}'
            return busctl

        env = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}
        with mock.patch.object(session, "_busctl", fake("46.2")):
            ok = session.platform_summary(env)
        self.assertEqual(ok["gnome_shell"], "46.2")
        self.assertTrue(ok["screencast"])
        self.assertFalse(ok["remote_desktop"])
        self.assertEqual(ok["notes"], [])
        with mock.patch.object(session, "_busctl", fake("49.1")):
            newer = session.platform_summary(env)
        self.assertIn("GNOME Shell 49.1 is untested", newer["notes"][0])
        with mock.patch.object(session, "_busctl", fake("")):
            gone = session.platform_summary(env)
        self.assertIsNone(gone["gnome_shell"])
        self.assertIn("did not answer", gone["notes"][0])

    def test_an_unknown_lock_state_in_such_a_session_refuses_with_the_reason(self) -> None:
        from unittest import mock

        from pcbridge.desktop.safety import screen_lock_decision

        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11",
                                          "XDG_CURRENT_DESKTOP": "KDE"}):
            decision = screen_lock_decision(
                ScreenLockObservation(ScreenLockState.UNKNOWN, observed_at=1.0))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, ErrorCode.LOCK_STATE_UNKNOWN)
        self.assertIn("Unsupported session", decision.reason)


class UnwritableStateTests(unittest.TestCase):
    """Disk full or read-only state directory: an English error, no crash
    loop, and never a job that runs without a record."""

    def test_config_load_refuses_with_exit_78_and_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "state"
            (state / "jobs").mkdir(parents=True)
            cfg = root / "config.toml"
            # Desktop OFF on purpose: nothing here may touch the real screen.
            cfg.write_text(_CONFIG.format(state=state).replace("enabled = true", "enabled = false"))
            cfg.chmod(0o600)
            env = {k: v for k, v in os.environ.items() if not k.startswith("PCBRIDGE_")}
            env.update({"PCBRIDGE_CONFIG": str(cfg), "PCBRIDGE_IN_PROCESS": "1"})
            (state / "jobs").chmod(0o500)
            try:
                res = subprocess.run([sys.executable, "-m", "pcbridge.server", "--stdio"],
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                     env=env, cwd=str(ROOT), timeout=60)
            finally:
                (state / "jobs").chmod(0o700)
            self.assertEqual(res.returncode, 78, res.stderr)
            self.assertIn("cannot be written", res.stderr)
            self.assertNotIn("Traceback", res.stderr)

    def test_a_job_is_not_started_when_its_record_cannot_be_written(self) -> None:
        from pcbridge.jobs import JobManager, JobStartError

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jm = JobManager(root / "jobs")
            marker = root / "ran"
            (root / "jobs").chmod(0o500)
            try:
                with self.assertRaises(JobStartError) as refused:
                    jm.start(kind="shell", argv=["touch", str(marker)], cwd=root)
            finally:
                (root / "jobs").chmod(0o700)
            self.assertIn("NOT started", str(refused.exception))
            time.sleep(0.3)
            self.assertFalse(marker.exists(), "the job ran without a record")


if __name__ == "__main__":
    unittest.main()
