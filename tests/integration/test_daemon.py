"""A real daemon behind the real relay, with a throwaway config (no desktop).

Runs anywhere (CI included): it binds a socket in a temp directory, never
touches the user's service, socket or state, and needs no display.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG = """\
config_version = 2
public_url = "http://localhost:8765"
default_agent = "claude"

[auth]
password = "integration-test-password"

[paths]
state_dir = "{state}"
default_workdir = "{work}"

[desktop]
enabled = false

[agents.claude]
command = ["true"]
"""


class Client:
    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pcbridge", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=str(ROOT),
        )
        self.msgs: dict = {}
        self.cv = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()
        self.next = 0

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            msg = json.loads(line)
            if "id" in msg:
                with self.cv:
                    self.msgs[msg["id"]] = msg
                    self.cv.notify_all()

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
        self.proc.stdin.flush()

    def start(self, method: str, params: dict | None = None) -> int:
        self.next += 1
        self.send({"jsonrpc": "2.0", "id": self.next, "method": method, "params": params or {}})
        return self.next

    def wait(self, rid: int, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        with self.cv:
            while rid not in self.msgs:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no answer to {rid}")
                self.cv.wait(left)
            return self.msgs[rid]

    def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        return self.wait(self.start(method, params), timeout)

    def tool(self, name: str, args: dict | None = None) -> dict:
        return self.call("tools/call", {"name": name, "arguments": args or {}})

    def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _text(res: dict) -> str:
    return "\n".join(b.get("text", "") for b in res["result"]["content"] if b.get("type") == "text")


class DaemonIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcbd-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "state").mkdir()
        (self.tmp / "work").mkdir()
        self.cfg = self.tmp / "config.toml"
        self.cfg.write_text(CONFIG.format(state=self.tmp / "state", work=self.tmp / "work"))
        os.chmod(self.cfg, 0o600)
        self.sock = self.tmp / "mcp.sock"
        self.env = dict(os.environ)
        for k in list(self.env):
            if k.startswith(("CLAUDE", "MCP_", "PCBRIDGE_")):
                self.env.pop(k)
        self.env.update({
            "PCBRIDGE_CONFIG": str(self.cfg),
            "PCBRIDGE_SOCKET": str(self.sock),
            "XDG_DATA_HOME": str(self.tmp / "data"),
        })
        self.daemon: subprocess.Popen | None = None
        self.addCleanup(self._stop_daemon)

    def _start_daemon(self) -> None:
        log = open(self.tmp / "daemon.log", "ab")
        self.addCleanup(log.close)
        self.daemon = subprocess.Popen(
            [sys.executable, "-m", "pcbridge", "serve", "--socket", str(self.sock), "--no-http"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=self.env, cwd=str(ROOT),
        )
        deadline = time.monotonic() + 30
        while True:
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(str(self.sock))
                break
            except OSError:
                pass
            finally:
                probe.close()
            if self.daemon.poll() is not None or time.monotonic() > deadline:
                raise AssertionError((self.tmp / "daemon.log").read_text()[-2000:])
            time.sleep(0.05)

    def _stop_daemon(self) -> None:
        if self.daemon and self.daemon.poll() is None:
            self.daemon.send_signal(signal.SIGTERM)
            try:
                self.daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.daemon.kill()

    def _client(self) -> Client:
        c = Client(self.env)
        self.addCleanup(c.close)
        c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "integration", "version": "1"}})
        c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return c

    def test_relay_session_is_served_by_the_daemon(self) -> None:
        self._start_daemon()
        c = self._client()
        tools = c.call("tools/list")["result"]["tools"]
        self.assertEqual(len(tools), 38)
        status = _text(c.tool("system_status"))
        self.assertIn("daemon pid", status)
        self.assertIn(f"pid {self.daemon.pid}", status)

    def test_opt_in_grant_context_reports_the_resident_state_directory(self) -> None:
        from pcbridge.relay import SocketBackend

        self.env["XDG_CURRENT_DESKTOP"] = "GNOME"
        self._start_daemon()
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(str(self.sock))
        backend = SocketBackend(sock)
        try:
            reply = backend.handshake(5, desktop_context=True)
            self.assertEqual(reply["desktop_context"], {
                "version": 1, "state_dir": str((self.tmp / "state").resolve()),
                "compositor": "gnome", "wayland_display": "", "hyprland_instance": ""})
        finally:
            backend.close()

    def test_handshake_names_the_code_the_daemon_started_with(self) -> None:
        from pcbridge import build as buildlib
        from pcbridge.cli import ops
        from pcbridge.cli.doctor import handshake

        self._start_daemon()
        reply = handshake(self.sock)
        self.assertEqual(reply["build"], buildlib.fingerprint())
        self.assertTrue(ops.runs_installed_code(reply))

    def test_three_sessions_at_once(self) -> None:
        self._start_daemon()
        clients = [self._client() for _ in range(3)]
        ids = [c.start("tools/call", {"name": "shell_run", "arguments": {"command": f"echo s{i}"}})
               for i, c in enumerate(clients)]
        for i, (c, rid) in enumerate(zip(clients, ids)):
            self.assertIn(f"s{i}", _text(c.wait(rid)))

    def test_daemon_death_mid_call_and_recovery(self) -> None:
        self._start_daemon()
        c = self._client()
        rid = c.start("tools/call", {"name": "shell_run", "arguments": {"command": "sleep 5", "timeout": 20}})
        time.sleep(0.5)
        assert self.daemon is not None
        self.daemon.kill()  # SIGKILL: the socket file stays behind, stale
        self.daemon.wait()
        err = c.wait(rid, timeout=5)
        self.assertEqual(err["error"]["code"], -32000)
        self.assertTrue(err["error"]["data"]["retryable"])
        self._start_daemon()  # must replace the stale socket file
        res = c.tool("shell_run", {"command": "echo back"})
        self.assertIn("back", _text(res))
        self.assertIn(f"pid {self.daemon.pid}", _text(c.tool("system_status")))

    def test_a_broken_client_environment_does_not_reach_the_tools(self) -> None:
        # Codex hands over the literal text; Claude Desktop sends an empty
        # session type (both measured, see CLAUDE.md). Through the relay the
        # tools run in the daemon's environment, so neither arrives.
        self._start_daemon()
        broken = dict(self.env)
        broken["DBUS_SESSION_BUS_ADDRESS"] = "$DBUS_SESSION_BUS_ADDRESS"
        broken["XDG_SESSION_TYPE"] = ""
        c = Client(broken)
        self.addCleanup(c.close)
        c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "codex-like", "version": "1"}})
        c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        out = _text(c.tool("shell_run", {
            "command": 'echo "bus=[$DBUS_SESSION_BUS_ADDRESS] type=[$XDG_SESSION_TYPE]"'}))
        # The first line echoes the command itself; the answer follows it.
        result = [ln for ln in out.splitlines() if ln.startswith("bus=[")]
        self.assertEqual(len(result), 1, out)
        self.assertNotIn("$", result[0])
        # The daemon's own bus: the one it was given, or the one
        # ensure_session_env derived when it was given none (a CI runner).
        own = self.env.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path=/run/user/{os.getuid()}/bus"
        self.assertIn(f"bus=[{own}]", result[0])
        if self.env.get("XDG_SESSION_TYPE"):
            self.assertIn(f"type=[{self.env['XDG_SESSION_TYPE']}]", result[0])

    def test_status_file_for_the_panel_indicator(self) -> None:
        self.env["PCBRIDGE_STATUS_POLL_S"] = "0.2"
        self._start_daemon()
        status = self.tmp / "state" / "status.json"

        def read_until(pred, timeout=10.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    data = json.loads(status.read_text())
                    if pred(data):
                        return data
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            raise AssertionError(status.read_text() if status.exists() else "no status.json")

        assert self.daemon is not None
        data = read_until(lambda d: d["daemon"] == "running")
        self.assertEqual(data["pid"], self.daemon.pid)
        self.assertEqual(data["schema"], 1)
        self.assertEqual(oct(status.stat().st_mode & 0o777), "0o600")
        c = self._client()
        c.tool("shell_run_background", {"command": "sleep 2", "workdir": str(self.tmp / "work")})
        read_until(lambda d: d["jobs_running"] == 1)
        read_until(lambda d: d["jobs_running"] == 0)
        self._stop_daemon()
        read_until(lambda d: d["daemon"] == "stopped" and d["pid"] == 0)

    def test_keep_awake_follows_the_setting_and_the_jobs(self) -> None:
        # A stub systemd-inhibit records how it was called and holds like the
        # real one: it runs `cat` on the daemon's pipe.
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        calls = self.tmp / "inhibit.log"
        stub = bin_dir / "systemd-inhibit"
        stub.write_text(f'#!/bin/sh\necho "$$ $*" >> "{calls}"\nexec cat\n')
        stub.chmod(0o755)
        self.env["PATH"] = f"{bin_dir}:{self.env.get('PATH', '/usr/bin:/bin')}"
        self.env["XDG_CURRENT_DESKTOP"] = "Hyprland"
        self.env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        self.env["PCBRIDGE_STATUS_POLL_S"] = "0.2"
        self.env["PCBRIDGE_POWER_POLL_S"] = "0.2"
        self._start_daemon()
        status = self.tmp / "state" / "status.json"

        def read_until(pred, timeout=10.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    data = json.loads(status.read_text())
                    if pred(data):
                        return data
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            raise AssertionError(status.read_text() if status.exists() else "no status.json")

        def holder() -> int:
            return int(calls.read_text().splitlines()[-1].split()[0])

        def gone(pid: int, timeout: float = 5.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                time.sleep(0.05)
            return False

        c = self._client()
        work = str(self.tmp / "work")
        # Off (the default): a running job takes nothing.
        c.tool("shell_run_background", {"command": "sleep 3", "workdir": work})
        read_until(lambda d: d["jobs_running"] == 1)
        time.sleep(0.8)
        self.assertFalse(calls.exists(), "an inhibitor was taken while keep_awake is off")
        self.assertEqual(json.loads(status.read_text())["keep_awake"]["held"], False)

        # On, in the file, with the daemon running: held within a poll.
        self.cfg.write_text(self.cfg.read_text() + "\n[power]\nkeep_awake = true\n")
        data = read_until(lambda d: d["keep_awake"].get("held") is True)
        self.assertEqual((data["keep_awake"]["method"], data["keep_awake"]["what"]), ("logind", "idle"))
        self.assertIn("--what=idle --mode=block --who=pcbridge", calls.read_text())
        first = holder()

        # The last job ends: released, and the holder is gone.
        read_until(lambda d: d["jobs_running"] == 0 and d["keep_awake"]["held"] is False)
        self.assertTrue(gone(first), "the inhibitor outlived the last job")

        # Held again for a new job; then the daemon is killed outright.
        c.tool("shell_run_background", {"command": "sleep 20", "workdir": work})
        read_until(lambda d: d["keep_awake"].get("held") is True)
        second = holder()
        self.assertNotEqual(first, second)
        assert self.daemon is not None
        self.daemon.kill()
        self.daemon.wait()
        self.assertTrue(gone(second), "the inhibitor outlived a killed daemon")

    def test_an_update_during_a_job_waits_until_idle(self) -> None:
        stamp = self.tmp / "data" / "pcbridge" / "version-stamp"
        stamp.parent.mkdir(parents=True)
        stamp.write_text("2.0.0 old\n")
        self.env["PCBRIDGE_STAMP_POLL_S"] = "0.2"
        self._start_daemon()
        c = self._client()
        started = _text(c.tool("shell_run_background", {"command": "sleep 3", "workdir": str(self.tmp / "work")}))
        self.assertIn("job", started.lower())
        stamp.write_text("2.0.0 new\n")
        time.sleep(1.5)
        assert self.daemon is not None
        self.assertIsNone(self.daemon.poll(), "restarted while a job was running")
        self.assertIn("restart deferred", (self.tmp / "daemon.log").read_text())
        # Idle once the job ends: the daemon exits 75 so systemd restarts it.
        self.assertEqual(self.daemon.wait(timeout=15), 75)
        self._start_daemon()  # what systemd does after RestartForceExitStatus=75
        res = c.tool("shell_run", {"command": "echo after-update"})
        self.assertIn("after-update", _text(res))

    def test_reinstalling_the_same_code_does_not_restart(self) -> None:
        from pcbridge import build as buildlib

        stamp = self.tmp / "data" / "pcbridge" / "version-stamp"
        stamp.parent.mkdir(parents=True)
        stamp.write_text("2.5.0 user 1790799767\n")
        self.env["PCBRIDGE_STAMP_POLL_S"] = "0.2"
        self._start_daemon()
        stamp.write_text(f"2.5.0 user {buildlib.fingerprint()}\n")
        time.sleep(1.5)
        assert self.daemon is not None
        self.assertIsNone(self.daemon.poll(), "restarted for a reinstall of the same code")
        stamp.write_text(f"2.5.0 user {'0' * 64}\n")
        self.assertEqual(self.daemon.wait(timeout=15), 75)


if __name__ == "__main__":
    unittest.main()
