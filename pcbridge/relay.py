"""The stdio relay: what every local MCP client actually runs.

A client (Claude Code, Codex, Claude Desktop) starts `pcbridge stdio` (or the
pre-2.0 `python -m pcbridge.server --stdio`). This process does not serve MCP
itself. It connects to the resident daemon's unix socket and copies bytes:

    client stdin  --line-->  daemon socket
    client stdout <--line--  daemon socket

MCP's stdio framing is one JSON message per line, and the relay forwards each
line unchanged, so image blocks, structuredContent, isError, progress and
cancellation reach the client exactly as the daemon wrote them. It only
*reads* the messages to remember three things:

    * the client's `initialize` request and `notifications/initialized`,
      to replay them when it has to reconnect;
    * which client requests are still waiting for an answer, so that if the
      daemon goes away mid-call each of them gets an error instead of a hang.

When the socket cannot be reached at start, the relay asks systemd to start
the socket unit and waits at most two seconds; after that it runs the classic
in-process server in its place (exec, same stdin and stdout), marked
"degraded: in-process". If the daemon disappears later, the relay reconnects
(socket activation brings it back); if that fails for ten seconds it starts
an in-process server as a child and carries on through it. The client never
has to restart, and never hangs.

Standard library only, and no heavy pcbridge imports: this is on the startup
path of every client.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__
from . import paths as pathslib

PROTOCOL = 1
CONNECT_WAIT_S = 2.0
HANDSHAKE_WAIT_S = 10.0
RECONNECT_WAIT_S = 10.0
RETRYABLE_CODE = -32000

UNIT_DIRS = (
    "~/.config/systemd/user",
    "~/.local/share/systemd/user",
    "/etc/systemd/user",
    "/usr/lib/systemd/user",
    "/usr/local/lib/systemd/user",
)

# Server messages are written by pydantic with a fixed key order, so the id
# and the kind of message are at the very start of the line. Reading only the
# head keeps a 1.5 MB screenshot answer from being parsed just to find its id.
_HEAD = re.compile(rb'^\{"jsonrpc":"2\.0","id":(-?\d+|"(?:[^"\\]|\\.)*"),"(result|error|method)"')

_ENV_ALLOW = ("PATH", "SSH_AUTH_SOCK", "LANG", "LANGUAGE", "PWD", "TERM")
_ENV_PROXY = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "all_proxy")


def _log(msg: str) -> None:
    # stderr only: stdout is the JSON-RPC channel.
    print(f"pcbridge relay: {msg}", file=sys.stderr, flush=True)


def _client_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k in _ENV_ALLOW or k in _ENV_PROXY or k.startswith("LC_")
    }


def _id_key(raw) -> str:
    return json.dumps(raw, sort_keys=True)


def _socket_unit_installed() -> bool:
    """True when pcbridge.socket is installed AND enabled.

    Only an enabled socket is started on demand. `pcbridge setup` installs
    the units first and enables the socket only once the service really runs
    the daemon; starting the socket while an older, socket-less server holds
    pcbridge.service would leave connections queued and unanswered.
    """
    return any(
        (Path(os.path.expanduser(d)) / "sockets.target.wants" / "pcbridge.socket").exists()
        for d in UNIT_DIRS
    )


def _start_socket_unit() -> bool:
    try:
        subprocess.run(
            ["systemctl", "--user", "start", "pcbridge.socket"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CONNECT_WAIT_S,
            check=False,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


class HandshakeError(Exception):
    pass


class Backend:
    """One connection to a server: the daemon socket or an in-process child."""

    kind = "?"

    def send(self, data: bytes) -> None:
        raise NotImplementedError

    def readline(self) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SocketBackend(Backend):
    kind = "daemon"

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.rfile = sock.makefile("rb")
        self.daemon: dict = {}

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def readline(self) -> bytes:
        return self.rfile.readline()

    def close(self) -> None:
        # Shut the socket down first: that wakes a reader blocked in
        # readline() on another thread. Closing the buffered file first would
        # wait for that reader's lock instead.
        for fn in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.rfile.close, self.sock.close):
            try:
                fn()
            except OSError:
                pass

    def handshake(self, timeout: float) -> dict:
        info = {
            "version": __version__,
            "protocol": PROTOCOL,
            "pid": os.getppid(),
            "relay_pid": os.getpid(),
            "cwd": os.getcwd() if os.path.isdir(os.getcwd()) else "",
            "env": _client_env(),
        }
        self.sock.settimeout(timeout)
        try:
            self.send(json.dumps({"pcbridge_relay": info}).encode() + b"\n")
            line = self.readline()
        except (OSError, socket.timeout) as exc:
            raise HandshakeError(f"no answer from the daemon: {exc}") from exc
        finally:
            self.sock.settimeout(None)
        try:
            reply = json.loads(line)["pcbridge_daemon"]
        except (ValueError, KeyError, TypeError) as exc:
            raise HandshakeError("the daemon did not answer the relay handshake") from exc
        if int(reply.get("protocol", 0)) != PROTOCOL:
            raise HandshakeError(
                f"daemon speaks relay protocol {reply.get('protocol')}, this relay {PROTOCOL}"
            )
        self.daemon = reply
        return reply


class ChildBackend(Backend):
    kind = "in-process"

    def __init__(self, reason: str) -> None:
        env = dict(os.environ, PCBRIDGE_IN_PROCESS="1", PCBRIDGE_MODE=f"degraded: in-process ({reason})")
        self.proc = subprocess.Popen(
            [sys.executable, "-P", "-m", "pcbridge.server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )

    def send(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def readline(self) -> bytes:
        assert self.proc.stdout is not None
        return self.proc.stdout.readline()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def connect_daemon(sock_path: Path, wait: float, activate: bool) -> SocketBackend | None:
    """Connect to the daemon socket, starting the socket unit if allowed."""
    deadline = time.monotonic() + wait
    activated = False
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(sock_path))
            return SocketBackend(s)
        except OSError:
            s.close()
        if activate and not activated and _socket_unit_installed():
            activated = True
            _start_socket_unit()
            continue
        if not activated or time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


class Relay:
    def __init__(self, backend: Backend) -> None:
        self.backend: Backend | None = backend
        self.cond = threading.Condition()
        self.out_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.in_flight: dict[str, tuple[object, str]] = {}
        self.init_line: bytes | None = None
        self.initialized_line: bytes | None = None
        self.swallow: set[str] = set()
        self.replays = 0
        self.closing = False
        self.stdout = sys.stdout.buffer

    # ------------------------------------------------------------ client side
    def _write_client(self, data: bytes) -> None:
        with self.out_lock:
            self.stdout.write(data)
            self.stdout.flush()

    def _note_client_message(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        for m in msg if isinstance(msg, list) else [msg]:
            if not isinstance(m, dict):
                continue
            method = m.get("method")
            if method == "initialize":
                self.init_line = line
            elif method == "notifications/initialized":
                self.initialized_line = line
            elif method == "notifications/cancelled":
                rid = (m.get("params") or {}).get("requestId")
                with self.cond:
                    self.in_flight.pop(_id_key(rid), None)
            if method and "id" in m:
                with self.cond:
                    self.in_flight[_id_key(m["id"])] = (m["id"], method)

    def client_loop(self) -> None:
        stdin = sys.stdin.buffer
        for line in stdin:
            if not line.strip():
                continue
            if not line.endswith(b"\n"):
                line += b"\n"
            self._note_client_message(line)
            self._send(line)
        # The client closed stdin: it is gone. Let the daemon see EOF too.
        with self.cond:
            self.closing = True
            backend = self.backend
            self.cond.notify_all()
        if backend is not None:
            backend.close()

    def _send(self, line: bytes) -> None:
        while True:
            with self.cond:
                while self.backend is None and not self.closing:
                    self.cond.wait()
                if self.closing:
                    return
                backend = self.backend
            # Never send while holding `cond`: a large line can block until the
            # server reads, and the server may be waiting for us to read its
            # answer, which needs `cond` in the other thread.
            try:
                with self.send_lock:
                    backend.send(line)
                return
            except OSError:
                # The reader thread notices the dead backend and reconnects;
                # this line waits for the new one.
                with self.cond:
                    if self.backend is backend:
                        self.backend = None
                    self.cond.notify_all()

    # ------------------------------------------------------------ server side
    def _note_server_line(self, line: bytes) -> bool:
        """Clear answered requests; False means: do not forward this line."""
        m = _HEAD.match(line)
        if m:
            rid, kind = m.group(1), m.group(2).decode()
            if kind == "method":
                return True
            key = _id_key(json.loads(rid))
        else:
            try:
                msg = json.loads(line)
            except ValueError:
                return True
            if not isinstance(msg, dict) or "method" in msg or "id" not in msg:
                return True
            key = _id_key(msg["id"])
        with self.cond:
            if key in self.swallow:
                self.swallow.discard(key)
                return False
            self.in_flight.pop(key, None)
        return True

    def server_loop(self) -> int:
        while True:
            with self.cond:
                backend = self.backend
                closing = self.closing
            if closing:
                return 0
            if backend is None:
                if not self._recover("the connection to the pcbridge server broke"):
                    return 1
                continue
            try:
                line = backend.readline()
            except OSError:
                line = b""
            if not line:
                with self.cond:
                    if self.closing:
                        return 0
                    if self.backend is backend:
                        self.backend = None
                backend.close()
                continue
            if self._note_server_line(line):
                self._write_client(line)

    # -------------------------------------------------------------- recovery
    def _fail_in_flight(self) -> None:
        with self.cond:
            pending = [v for v in self.in_flight.values() if v[1] != "initialize"]
            # An unanswered initialize stays pending: _replay resends it as is.
            self.in_flight = {k: v for k, v in self.in_flight.items() if v[1] == "initialize"}
        for rid, method in pending:
            err = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": RETRYABLE_CODE,
                    "message": (
                        "The pcbridge server restarted while this request was running. "
                        "It may or may not have completed; check before retrying anything "
                        "that is not safe to repeat."
                    ),
                    "data": {"retryable": True, "reason": "server_restarted", "method": method},
                },
            }
            self._write_client(json.dumps(err).encode() + b"\n")

    def _replay(self, backend: Backend) -> None:
        """Bring a fresh server to where the client thinks it is."""
        if self.init_line is None:
            return
        pending_init = False
        try:
            msg = json.loads(self.init_line)
            with self.cond:
                pending_init = _id_key(msg.get("id")) in self.in_flight
        except ValueError:
            return
        if pending_init:
            # The client still waits for this answer: send it as is.
            backend.send(self.init_line)
            return
        self.replays += 1
        replay_id = f"pcbridge-relay-replay-{self.replays}"
        msg["id"] = replay_id
        with self.cond:
            self.swallow.add(_id_key(replay_id))
        backend.send(json.dumps(msg).encode() + b"\n")
        if self.initialized_line is not None:
            backend.send(self.initialized_line)

    def _recover(self, why: str) -> bool:
        _log(f"{why}; reconnecting")
        self._fail_in_flight()
        backend: Backend | None = None
        try:
            sock_path = pathslib.socket_path()
            deadline = time.monotonic() + RECONNECT_WAIT_S
            while time.monotonic() < deadline and backend is None:
                candidate = connect_daemon(sock_path, 1.0, activate=True)
                if candidate is not None:
                    try:
                        candidate.handshake(HANDSHAKE_WAIT_S)
                        backend = candidate
                    except HandshakeError as exc:
                        _log(str(exc))
                        candidate.close()
                if backend is None:
                    time.sleep(0.2)
        except pathslib.RuntimeDirError as exc:
            _log(str(exc))
        if backend is None:
            _log("the daemon did not come back; continuing with an in-process server")
            backend = ChildBackend("daemon unreachable after a restart")
        try:
            self._replay(backend)
        except OSError as exc:
            _log(f"replay failed: {exc}")
            backend.close()
            return False
        with self.cond:
            self.backend = backend
            self.cond.notify_all()
        _log(f"reconnected ({backend.kind})")
        return True

    def run(self) -> int:
        reader = threading.Thread(target=self.client_loop, name="relay-client", daemon=True)
        reader.start()
        code = self.server_loop()
        with self.cond:
            backend = self.backend
        if backend is not None:
            backend.close()
        return code


def _exec_in_process(argv: list[str], reason: str) -> int:
    """Replace this process with the classic server: identical behavior, no pipe."""
    _log(f"degraded: in-process ({reason})")
    os.environ["PCBRIDGE_IN_PROCESS"] = "1"
    os.environ["PCBRIDGE_MODE"] = f"degraded: in-process ({reason})"
    args = [a for a in argv if a != "--stdio"]
    try:
        os.execv(sys.executable, [sys.executable, "-P", "-m", "pcbridge.server", "--stdio", *args])
    except OSError as exc:  # pragma: no cover - exec failing means a broken install
        _log(f"cannot start the in-process server: {exc}")
        return 1
    return 0  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-c", "--config") or a.startswith("--config=") for a in args):
        # A client pinned its own config file: the shared daemon serves one
        # config, so this client gets its own server, as before 2.0.
        return _exec_in_process(args, "explicit config file")
    if os.environ.get("PCBRIDGE_NO_DAEMON"):
        return _exec_in_process(args, "PCBRIDGE_NO_DAEMON is set")
    try:
        sock_path = pathslib.socket_path()
    except pathslib.RuntimeDirError as exc:
        return _exec_in_process(args, str(exc))
    backend = connect_daemon(sock_path, CONNECT_WAIT_S, activate=True)
    if backend is None:
        return _exec_in_process(args, "the daemon socket is not reachable")
    try:
        backend.handshake(HANDSHAKE_WAIT_S)
    except HandshakeError as exc:
        backend.close()
        return _exec_in_process(args, str(exc))
    return Relay(backend).run()


if __name__ == "__main__":
    raise SystemExit(main())
