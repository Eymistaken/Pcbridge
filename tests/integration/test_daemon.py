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
        self.assertEqual(len(tools), 36)
        status = _text(c.tool("system_status"))
        self.assertIn("daemon pid", status)
        self.assertIn(f"pid {self.daemon.pid}", status)

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


if __name__ == "__main__":
    unittest.main()
