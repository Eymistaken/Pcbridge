"""`pcbridge attach SESSION`: open a tmux_* session in this terminal.

Linux: `tmux attach -t SESSION`, as before. Windows: the session's
termhost streams its terminal here and takes the keys typed here; the
console is put in virtual-terminal mode both ways, so colors, cursor moves
and Ctrl+C go through as they would in tmux. Ctrl+] leaves the session
running and returns, as tmux's detach does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading

DETACH = b"\x1d"  # Ctrl+]


def _windows_attach(name: str) -> int:
    import ctypes
    import ctypes.wintypes as W
    import msvcrt

    from .. import conpty, localipc

    try:
        path = conpty._endpoint(name)
    except conpty.SessionError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not conpty.exists(name):
        print(f"no open session named '{name}'; `pcbridge` lists them in the terminal UI, or ask "
              "an agent for tmux_list", file=sys.stderr)
        return 1
    sock = localipc.connect(path, timeout=10)
    sock.settimeout(None)
    sock.sendall(json.dumps({"op": "attach"}).encode() + b"\n")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetConsoleMode.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
    kernel32.SetConsoleMode.argtypes = [W.HANDLE, W.DWORD]
    stdin_handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    stdout_handle = msvcrt.get_osfhandle(sys.stdout.fileno())
    old_in, old_out = W.DWORD(), W.DWORD()
    have_console = bool(kernel32.GetConsoleMode(stdin_handle, ctypes.byref(old_in))) and \
        bool(kernel32.GetConsoleMode(stdout_handle, ctypes.byref(old_out)))
    old_cp_in, old_cp_out = kernel32.GetConsoleCP(), kernel32.GetConsoleOutputCP()
    if have_console:
        # Input: virtual-terminal sequences, no line editing, no echo, and
        # Ctrl+C as a byte (ENABLE_VIRTUAL_TERMINAL_INPUT only).
        kernel32.SetConsoleMode(stdin_handle, 0x0200)
        # Output: process escape codes (ENABLE_PROCESSED_OUTPUT | VT processing).
        kernel32.SetConsoleMode(stdout_handle, old_out.value | 0x0001 | 0x0004)
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    out = sys.stdout.buffer
    done = threading.Event()

    def pump() -> None:
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                out.write(data)
                out.flush()
        except OSError:
            pass
        done.set()

    threading.Thread(target=pump, daemon=True).start()
    try:
        while not done.is_set():
            data = os.read(sys.stdin.fileno(), 1024)
            if not data:
                break
            if DETACH in data:
                data = data.split(DETACH, 1)[0]
                if data:
                    sock.sendall(data)
                break
            sock.sendall(data)
    except OSError:
        pass
    finally:
        sock.close()
        if have_console:
            kernel32.SetConsoleMode(stdin_handle, old_in.value)
            kernel32.SetConsoleMode(stdout_handle, old_out.value)
            kernel32.SetConsoleCP(old_cp_in)
            kernel32.SetConsoleOutputCP(old_cp_out)
        out.write(b"\r\n")
        out.flush()
    print(f"left '{name}'; it keeps running (pcbridge attach {name} to come back)")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge attach",
                                description="Open a live terminal session (tmux_start) in this terminal.")
    p.add_argument("session")
    args = p.parse_args(argv)
    if sys.platform == "win32":
        return _windows_attach(args.session)
    os.execvp("tmux", ["tmux", "attach", "-t", args.session])
    return 1  # pragma: no cover - exec does not return
