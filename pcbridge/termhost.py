"""One live terminal session on Windows: what a tmux server is on Linux.

    python -P -m pcbridge.termhost NAME CWD [COMMAND]

Windows has no tmux, so each session the tmux_* tools open is a process of
its own that holds a ConPTY (pywinpty) running COMMAND, or the shell when
there is none, and keeps its screen with pyte, scrollback included (50,000
lines, tmux's history-limit in pcbridge). It listens on 127.0.0.1 and
publishes `<runtime>/terms/NAME.json` with its port and a secret, and every
client proves the secret first, the daemon's channel's exchange (localipc).
It outlives a daemon restart; when its program exits, the session ends, as
a tmux session does.

One JSON request per line, one JSON answer per line:

    {"op": "info"}                         -> name, cwd, command, pid, created, running
    {"op": "send", "data": "..."}          -> the bytes go to the terminal as typed
    {"op": "capture", "lines": N}          -> the last N lines, scrollback included
    {"op": "kill"}                         -> the program ends, then this process
    {"op": "attach"}                       -> the connection turns into the terminal
                                              itself (`pcbridge attach NAME`)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

from . import localipc
from . import paths as pathslib

COLUMNS = 200
ROWS = 50
HISTORY = 50000
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def terms_dir() -> Path:
    return pathslib.runtime_dir(create=True) / "terms"


class Host:
    def __init__(self, name: str, cwd: str, command: str | None) -> None:
        import pyte
        from winpty import PtyProcess

        self.name, self.cwd, self.command = name, cwd, command
        self.created = time.time()
        self.screen = pyte.HistoryScreen(COLUMNS, ROWS, history=HISTORY)
        self.stream = pyte.Stream(self.screen)
        self.lock = threading.Lock()
        self.attached: list[socket.socket] = []
        import shutil

        # pywinpty splits a string argv and looks its first word up on PATH,
        # quotes and all, so the argv is always a list with a full path. A
        # COMMAND runs through the shell, as tmux runs it through `sh -c`.
        shell = shutil.which("pwsh") or shutil.which("powershell.exe") or "powershell.exe"
        argv = [shell, "-NoLogo"]
        if command:
            argv += ["-NoProfile", "-Command", command]
        self.pty = PtyProcess.spawn(argv, cwd=cwd, dimensions=(ROWS, COLUMNS))
        self.done = threading.Event()

    # -- the terminal ----------------------------------------------------------

    def pump(self) -> None:
        while True:
            try:
                data = self.pty.read(65536)
            except EOFError:
                break
            if not data:
                if not self.pty.isalive():
                    break
                time.sleep(0.02)
                continue
            with self.lock:
                self.stream.feed(data)
                listeners = list(self.attached)
            raw = data.encode("utf-8", "replace")
            for client in listeners:
                try:
                    client.sendall(raw)
                except OSError:
                    pass
        self.done.set()

    def lines(self, count: int) -> list[str]:
        with self.lock:
            def render(row) -> str:
                return "".join(row[x].data for x in range(self.screen.columns)).rstrip()

            older = [render(row) for row in self.screen.history.top]
            current = [line.rstrip() for line in self.screen.display]
        rows = older + current
        while rows and not rows[-1]:
            rows.pop()
        return rows[-max(count, 1):]

    def redraw(self) -> bytes:
        """The visible screen as escape codes, for a client that attaches."""
        with self.lock:
            text = "\r\n".join(line.rstrip() for line in self.screen.display)
            y, x = self.screen.cursor.y, self.screen.cursor.x
        return (f"\x1b[2J\x1b[H{text}\x1b[{y + 1};{x + 1}H").encode("utf-8", "replace")

    # -- the endpoint ----------------------------------------------------------

    def serve(self, listener: socket.socket, secret: bytes) -> None:
        listener.settimeout(0.5)
        while not self.done.is_set():
            try:
                client, _ = listener.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self.handle, args=(client, secret), daemon=True).start()

    def handle(self, client: socket.socket, secret: bytes) -> None:
        client.settimeout(10)
        if not localipc.server_authenticate_sync(client, secret):
            client.close()
            return
        client.settimeout(None)
        reader = client.makefile("rb")
        try:
            for raw in reader:
                try:
                    request = json.loads(raw)
                except ValueError:
                    break
                op = request.get("op")
                if op == "attach":
                    self.attach(client, reader)
                    return
                reply = self.answer(op, request)
                client.sendall(json.dumps(reply).encode() + b"\n")
                if op == "kill":
                    # The answer first: ending the program ends this
                    # process, which would reset the connection unanswered.
                    self.pty.terminate(force=True)
                    self.done.set()
                    return
        except OSError:
            pass
        finally:
            client.close()

    def answer(self, op: str, request: dict) -> dict:
        if op == "info":
            return {"name": self.name, "cwd": self.cwd, "command": self.command or "",
                    "pid": os.getpid(), "created": self.created, "running": self.pty.isalive(),
                    "attached": len(self.attached)}
        if op == "send":
            self.pty.write(str(request.get("data", "")))
            return {"ok": True}
        if op == "capture":
            return {"lines": self.lines(int(request.get("lines", 60)))}
        if op == "kill":
            return {"ok": True}
        return {"error": f"unknown op {op!r}"}

    def attach(self, client: socket.socket, reader) -> None:
        client.sendall(self.redraw())
        with self.lock:
            self.attached.append(client)
        try:
            while not self.done.is_set():
                data = reader.read1(4096)
                if not data:
                    break
                self.pty.write(data.decode("utf-8", "replace"))
        except OSError:
            pass
        finally:
            with self.lock:
                if client in self.attached:
                    self.attached.remove(client)


def run(name: str, cwd: str, command: str | None) -> int:
    if not NAME_RE.match(name):
        print(f"invalid session name {name!r}", file=sys.stderr)
        return 2
    folder = terms_dir()
    folder.mkdir(exist_ok=True)
    endpoint = folder / f"{name}.json"
    try:
        localipc.connect(endpoint, timeout=0.5).close()
        print(f"session {name!r} is already open", file=sys.stderr)
        return 1
    except OSError:
        pass
    host = Host(name, cwd, command)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    secret = secrets.token_bytes(32)
    payload = {"port": listener.getsockname()[1], "secret": secret.hex(), "pid": os.getpid(),
               "name": name, "cwd": cwd, "command": command or "", "created": host.created}
    temporary = endpoint.with_name(f".{endpoint.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, endpoint)
    threading.Thread(target=host.serve, args=(listener, secret), daemon=True).start()
    try:
        host.pump()
    finally:
        listener.close()
        localipc.remove_endpoint(endpoint)
    return 0


def main(args: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    if len(args) not in (2, 3):
        print("usage: python -m pcbridge.termhost NAME CWD [COMMAND]", file=sys.stderr)
        return 2
    return run(args[0], args[1], args[2] if len(args) == 3 else None)


if __name__ == "__main__":
    raise SystemExit(main())
