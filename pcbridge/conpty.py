"""The tmux_* tools' sessions on Windows: the client side of pcbridge.termhost.

tmuxctl calls these on Windows with the same names and the same answers it
gives on Linux. Each session is a `pcbridge.termhost` process found through
`<runtime>/terms/NAME.json`; a file whose process is gone is removed.
tmux's key names (`Enter`, `C-c`, `Up`, `PageDown`, `F5`, ...) are turned
into the bytes a terminal sends for them; a name tmux does not know is sent
as text, as tmux does.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from . import localipc, oscompat
from . import paths as pathslib
from .termhost import NAME_RE

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

KEYS = {
    "enter": "\r", "c-m": "\r", "tab": "\t", "c-i": "\t", "bspace": "\x7f", "escape": "\x1b",
    "space": " ", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D",
    "home": "\x1b[H", "end": "\x1b[F", "ic": "\x1b[2~", "dc": "\x1b[3~", "npage": "\x1b[6~",
    "ppage": "\x1b[5~", "pagedown": "\x1b[6~", "pageup": "\x1b[5~", "btab": "\x1b[Z",
    "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS", "f5": "\x1b[15~",
    "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~", "f9": "\x1b[20~", "f10": "\x1b[21~",
    "f11": "\x1b[23~", "f12": "\x1b[24~",
}


class SessionError(RuntimeError):
    pass


def key_bytes(key: str) -> str:
    lowered = key.lower()
    if lowered in KEYS:
        return KEYS[lowered]
    if len(key) == 3 and lowered.startswith("c-") and key[2].isalpha():
        return chr(ord(key[2].lower()) & 0x1F)
    if len(key) == 3 and lowered.startswith("m-"):
        return "\x1b" + key[2]
    return key


def available() -> bool:
    try:
        import pyte  # noqa: F401
        import winpty  # noqa: F401
    except ImportError:
        return False
    return True


def _folder() -> Path:
    return pathslib.runtime_dir(create=True) / "terms"


def _endpoint(name: str) -> Path:
    if not NAME_RE.match(name):
        raise SessionError(f"invalid session name '{name}': use letters, digits, '.', '_' or '-'")
    return _folder() / f"{name}.json"


def _alive(path: Path) -> bool:
    try:
        pid = localipc.read_endpoint(path)["pid"]
    except OSError:
        return False
    if isinstance(pid, int) and oscompat.pid_alive(pid):
        return True
    try:
        path.unlink()
    except OSError:
        pass
    return False


def _request(name: str, payload: dict, timeout: float = 10.0) -> dict:
    path = _endpoint(name)
    if not _alive(path):
        raise SessionError(f"no open session named '{name}'")
    try:
        sock = localipc.connect(path, timeout=timeout)
    except OSError as exc:
        raise SessionError(f"session '{name}' does not answer: {exc}") from exc
    with sock:
        sock.settimeout(timeout)
        sock.sendall(json.dumps(payload).encode() + b"\n")
        line = sock.makefile("rb").readline()
    try:
        reply = json.loads(line)
    except ValueError as exc:
        raise SessionError(f"session '{name}' gave no answer") from exc
    if "error" in reply:
        raise SessionError(str(reply["error"]))
    return reply


def exists(name: str) -> bool:
    try:
        return _alive(_endpoint(name))
    except SessionError:
        return False


def list_sessions() -> list[dict]:
    rows = []
    folder = _folder()
    if not folder.is_dir():
        return rows
    for path in sorted(folder.glob("*.json")):
        name = path.stem
        try:
            info = _request(name, {"op": "info"}, timeout=3.0)
        except SessionError:
            continue
        rows.append({
            "session": name,
            "windows": "1",
            "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(float(info.get("created", 0)))),
            "attached_on_pc": bool(info.get("attached")),
            "running": info.get("command") or "shell",
            "path": info.get("cwd", ""),
        })
    return rows


def start(name: str, command: str | None, cwd: str) -> str:
    if exists(name):
        return f"'{name}' is already open"
    argv = [sys.executable, "-P", "-m", "pcbridge.termhost", name, cwd]
    if command:
        argv.append(command)
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    common = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  cwd=cwd, close_fds=True)
    try:
        subprocess.Popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **common)
    except OSError:
        subprocess.Popen(argv, creationflags=flags, **common)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if exists(name):
            return f"'{name}' created"
        time.sleep(0.2)
    raise SessionError(f"session '{name}' did not start within 15 s")


def send_text(name: str, text: str, press_enter: bool = True, collapse_newlines: bool = True) -> None:
    payload = " ".join(text.split()) if collapse_newlines else text
    _request(name, {"op": "send", "data": payload})
    if press_enter:
        time.sleep(0.15)
        _request(name, {"op": "send", "data": "\r"})


def send_keys(name: str, keys: list[str]) -> None:
    _request(name, {"op": "send", "data": "".join(key_bytes(k) for k in keys)})


def capture(name: str, lines: int = 60) -> str:
    return "\n".join(_request(name, {"op": "capture", "lines": lines})["lines"])


def kill(name: str) -> str:
    if not exists(name):
        return f"'{name}' does not exist"
    try:
        _request(name, {"op": "kill"})
    except (SessionError, ConnectionResetError):
        if exists(name):
            raise
    return f"'{name}' closed"


def attach_hint(name: str) -> str:
    return f"pcbridge attach {name}"
