"""The browser keeper: holds the browser's CDP pipe so the browser outlives
the daemon.

Measured (docs/dev/measured-facts.md, "Browser: attaching to a session"):
closing pcbridge's end of `--remote-debugging-pipe` ends the browser within
0.2 s. If the daemon held the pipe, every daemon restart would close the
browser and lose its page state. So this small process starts the browser
with the pipe on fds 3 and 4, and relays CDP between the pipe and a Unix
socket (mode 0600) that the daemon connects to. The daemon starts it in its
own systemd scope, as it does for jobs.

The relay forwards only whole messages (CDP over the pipe is JSON separated
by NUL bytes), in both directions: a client that disconnects mid-message
cannot leave half a message in the pipe, and a client that connects late
never receives the tail of one. Messages from the browser while no client
is connected are dropped. One client at a time; a new one replaces the old.

    python -m pcbridge.browser.keeper --socket S --info I --profile P -- EXE [ARGS...]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

CHUNK = 1 << 20


def write_info(path: Path, info: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(info))
    tmp.chmod(0o600)
    os.replace(tmp, path)


class Keeper:
    def __init__(self, sock_path: Path, info_path: Path, argv: list[str]) -> None:
        self.sock_path, self.info_path = sock_path, info_path
        # The socket first: if it cannot be had, no browser is started.
        sock_path.unlink(missing_ok=True)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            self.listener.bind(str(sock_path))
        finally:
            os.umask(old)
        self.listener.listen(2)
        self.listener.setblocking(False)
        to_browser_r, self.to_browser = os.pipe()
        self.from_browser, from_browser_w = os.pipe()
        os.set_inheritable(to_browser_r, True)
        os.set_inheritable(from_browser_w, True)

        def fds() -> None:
            # Move both ends above 4 first, so neither dup2 can overwrite
            # the other when one of them already is fd 3 or 4.
            r = fcntl.fcntl(to_browser_r, fcntl.F_DUPFD, 10)
            w = fcntl.fcntl(from_browser_w, fcntl.F_DUPFD, 10)
            os.dup2(r, 3)
            os.dup2(w, 4)

        log = open(info_path.with_name("browser.log"), "ab")
        self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     preexec_fn=fds, pass_fds=(3, 4), close_fds=True)
        log.close()
        os.close(to_browser_r)
        os.close(from_browser_w)
        os.set_blocking(self.from_browser, False)

        self.client: socket.socket | None = None
        self.from_client = b""    # partial message from the client
        self.from_pipe = b""      # partial message from the browser
        self.to_client = b""      # whole messages waiting for the client
        self.stopping = False
        write_info(info_path, {
            "keeper_pid": os.getpid(), "browser_pid": self.proc.pid, "executable": argv[0],
            "socket": str(sock_path), "started_at": time.time(),
        })

    # -- relay --------------------------------------------------------------
    def _drop_client(self, sel: selectors.BaseSelector) -> None:
        if self.client is not None:
            sel.unregister(self.client)
            self.client.close()
        self.client, self.from_client, self.to_client = None, b"", b""

    def _write_pipe(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            n = os.write(self.to_browser, view)
            view = view[n:]

    def run(self) -> int:
        sel = selectors.DefaultSelector()
        sel.register(self.listener, selectors.EVENT_READ, "accept")
        sel.register(self.from_browser, selectors.EVENT_READ, "pipe")
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        try:
            while True:
                if self.stopping:
                    return self._close_browser()
                if self.proc.poll() is not None:
                    return self.proc.returncode or 0
                for key, events in sel.select(timeout=0.5):
                    if key.data == "accept":
                        conn, _ = self.listener.accept()
                        conn.setblocking(False)
                        self._drop_client(sel)
                        self.client = conn
                        sel.register(conn, selectors.EVENT_READ, "client")
                    elif key.data == "pipe":
                        chunk = os.read(self.from_browser, CHUNK)
                        if not chunk:
                            self.proc.wait(10)
                            return self.proc.returncode or 0
                        self.from_pipe += chunk
                        cut = self.from_pipe.rfind(b"\0")
                        if cut >= 0:
                            whole, self.from_pipe = self.from_pipe[:cut + 1], self.from_pipe[cut + 1:]
                            if self.client is not None:
                                self.to_client += whole
                    elif key.data == "client":
                        if events & selectors.EVENT_READ:
                            try:
                                chunk = self.client.recv(CHUNK)
                            except (BlockingIOError, InterruptedError):
                                chunk = None
                            except OSError:
                                chunk = b""
                            if chunk == b"":
                                self._drop_client(sel)
                                continue
                            if chunk:
                                self.from_client += chunk
                                cut = self.from_client.rfind(b"\0")
                                if cut >= 0:
                                    self._write_pipe(self.from_client[:cut + 1])
                                    self.from_client = self.from_client[cut + 1:]
                if self.client is not None and self.to_client:
                    try:
                        sent = self.client.send(self.to_client)
                        self.to_client = self.to_client[sent:]
                    except (BlockingIOError, InterruptedError):
                        pass
                    except OSError:
                        self._drop_client(sel)
                if self.client is not None:
                    want = selectors.EVENT_READ | (selectors.EVENT_WRITE if self.to_client else 0)
                    sel.modify(self.client, want, "client")
        finally:
            self.sock_path.unlink(missing_ok=True)
            try:
                info = json.loads(self.info_path.read_text())
                if info.get("keeper_pid") == os.getpid():
                    self.info_path.unlink()
            except (OSError, ValueError):
                pass

    def _close_browser(self) -> int:
        """Close the browser cleanly (it writes cookies and session state
        on Browser.close), then by signal if it does not go."""
        try:
            self._write_pipe(json.dumps({"id": 0, "method": "Browser.close"}).encode() + b"\0")
            self.proc.wait(8)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pcbridge.browser.keeper")
    ap.add_argument("--socket", required=True, type=Path)
    ap.add_argument("--info", required=True, type=Path)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        ap.error("the browser command is missing")
    return Keeper(args.socket, args.info, command).run()


if __name__ == "__main__":
    sys.exit(main())
