"""The stdio relay against a fake daemon: framing, failures, reconnection.

The relay runs as a real subprocess (`python -m pcbridge.relay`), because its
whole job is stdin/stdout. The daemon side is a small socket server in this
test process, so each failure can be staged exactly.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class FakeDaemon:
    """Speaks the relay preamble, then answers each request with its method.

    A `tools/call` whose tool name is "hang" gets no answer, so the test can
    drop the connection while it is in flight.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(8)
        self.received: list[dict] = []
        self.preambles: list[dict] = []
        self.conns: list[socket.socket] = []
        self.lock = threading.Lock()
        self.stopped = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.stopped:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self.lock:
                self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        f = conn.makefile("rb")
        try:
            pre = json.loads(f.readline())["pcbridge_relay"]
            with self.lock:
                self.preambles.append(pre)
            conn.sendall(json.dumps({"pcbridge_daemon": {"version": "test", "protocol": 1, "pid": 1}}).encode() + b"\n")
            for line in f:
                msg = json.loads(line)
                with self.lock:
                    self.received.append(msg)
                if "id" not in msg or "method" not in msg:
                    continue
                if msg["method"] == "tools/call" and msg["params"]["name"] == "hang":
                    continue
                if (
                    msg["method"] == "initialize"
                    and msg["params"]["clientInfo"]["name"] == "hang"
                    and len(self.preambles) == 1
                ):
                    continue
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"echo": msg["method"], "conn": len(self.conns)}}
                conn.sendall(json.dumps(reply, separators=(",", ":")).encode() + b"\n")
        except (OSError, ValueError, KeyError):
            pass

    def drop_all(self) -> None:
        with self.lock:
            conns, self.conns = self.conns, []
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def stop(self) -> None:
        self.stopped = True
        self.drop_all()
        self.sock.close()


class RelayProcess:
    def __init__(self, sock_path: Path, extra_env: dict[str, str] | None = None, args: list[str] | None = None):
        env = dict(os.environ)
        env.update({"PCBRIDGE_SOCKET": str(sock_path), "HOME": str(sock_path.parent)})
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pcbridge.relay", *(args or [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(ROOT),
        )
        self.lines: list[dict] = []
        self.cv = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            with self.cv:
                self.lines.append(json.loads(line))
                self.cv.notify_all()

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
        self.proc.stdin.flush()

    def wait_for(self, pred, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        with self.cv:
            while True:
                for m in self.lines:
                    if pred(m):
                        return m
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no matching message; got {self.lines}")
                self.cv.wait(left)

    def close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=5)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}


class RelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcbr-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sock = self.tmp / "mcp.sock"

    def _daemon(self) -> FakeDaemon:
        d = FakeDaemon(self.sock)
        self.addCleanup(d.stop)
        return d

    def test_passthrough_preamble_and_clean_exit(self) -> None:
        d = self._daemon()
        r = RelayProcess(self.sock, {"SSH_AUTH_SOCK": "/x/agent", "CLAUDECODE": "1"})
        r.send(INIT)
        r.wait_for(lambda m: m.get("id") == 1)
        r.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        r.send({"jsonrpc": "2.0", "id": "abc", "method": "tools/list"})
        got = r.wait_for(lambda m: m.get("id") == "abc")
        self.assertEqual(got["result"]["echo"], "tools/list")
        self.assertEqual(r.close(), 0)
        pre = d.preambles[0]
        self.assertEqual(pre["protocol"], 1)
        self.assertEqual(pre["env"].get("SSH_AUTH_SOCK"), "/x/agent")
        self.assertNotIn("CLAUDECODE", pre["env"])

    def test_in_flight_call_gets_a_retryable_error_and_the_session_survives(self) -> None:
        d = self._daemon()
        r = RelayProcess(self.sock)
        self.addCleanup(r.close)
        r.send(INIT)
        r.wait_for(lambda m: m.get("id") == 1)
        r.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        r.send({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "hang", "arguments": {}}})
        deadline = time.monotonic() + 5
        while not any(m.get("id") == 7 for m in d.received) and time.monotonic() < deadline:
            time.sleep(0.02)
        started = time.monotonic()
        d.drop_all()
        err = r.wait_for(lambda m: m.get("id") == 7)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(err["error"]["code"], -32000)
        self.assertTrue(err["error"]["data"]["retryable"])
        # The next call goes through a new connection, after a silent replay.
        r.send({"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
        ok = r.wait_for(lambda m: m.get("id") == 8)
        self.assertEqual(ok["result"]["echo"], "tools/list")
        replayed = [m for m in d.received if m.get("method") == "initialize"]
        self.assertEqual(len(replayed), 2)
        self.assertTrue(str(replayed[1]["id"]).startswith("pcbridge-relay-replay-"))
        # The replayed initialize's answer was swallowed, not shown to the client.
        self.assertFalse(any(str(m.get("id", "")).startswith("pcbridge-relay") for m in r.lines))
        inits = [m for m in d.received if m.get("method") == "notifications/initialized"]
        self.assertEqual(len(inits), 2)

    def test_initialize_in_flight_is_resent_not_failed(self) -> None:
        d = self._daemon()
        r = RelayProcess(self.sock)
        self.addCleanup(r.close)
        # The first connection never answers this initialize; drop it.
        init = {**INIT, "id": 99, "params": {**INIT["params"], "clientInfo": {"name": "hang", "version": "1"}}}
        r.send(init)
        deadline = time.monotonic() + 5
        while not any(m.get("id") == 99 for m in d.received) and time.monotonic() < deadline:
            time.sleep(0.02)
        d.drop_all()
        got = r.wait_for(lambda m: m.get("id") == 99)
        self.assertEqual(got["result"]["echo"], "initialize")
        self.assertEqual(len(d.preambles), 2)

    def test_no_daemon_falls_back_to_in_process(self) -> None:
        # No socket, no systemd unit (HOME is the temp dir): the relay execs the
        # classic server. Prove it without importing FastMCP here: the exec'd
        # process must answer initialize like a real server.
        r = RelayProcess(
            self.tmp / "missing.sock",
            {"PCBRIDGE_CONFIG": os.environ.get("PCBRIDGE_CONFIG", "") or str(ROOT / "config.example.toml")},
        )
        self.addCleanup(r.close)
        r.send(INIT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if r.proc.poll() is not None:
                self.skipTest("no runnable config for the in-process server")
            with r.cv:
                got = next((m for m in r.lines if m.get("id") == 1), None)
            if got:
                break
            time.sleep(0.05)
        else:
            self.fail("the in-process fallback never answered initialize")
        self.assertEqual(got["result"]["serverInfo"]["name"], "pcbridge")
        assert r.proc.stderr is not None


if __name__ == "__main__":
    unittest.main()
