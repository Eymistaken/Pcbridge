"""Call pcbridge's desktop tools through a real MCP client on Windows (W7).

    python tests\\live\\windows\\check_mcp.py [path\\to\\pcbridge.exe]

Run it in the desktop session (windows-vm.sh session), with the daemon
running and desktop control enabled. It opens an empty text file in
Notepad, then through `pcbridge.exe stdio` (the relay, so the running
daemon answers): opens the grant, reads the screen and the windows, brings
Notepad to the front, writes into it by UI Automation and by the keyboard,
clicks, captures, and closes the grant; a key after that must be refused.
Notepad's own text control is read back (WM_GETTEXT) after each write.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "readiness"))
from check import StdioClient, _tool_text  # noqa: E402

LRESULT = ctypes.c_ssize_t
user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, W.LPARAM]
user32.EnumChildWindows.argtypes = [W.HWND, ENUMPROC, W.LPARAM]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.SendMessageW.restype = LRESULT


def title(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def notepad_text(stem: str) -> str | None:
    found = []

    def top(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and stem in title(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(ENUMPROC(top), 0)
    if not found:
        return None
    edits = []

    def child(hwnd, _lp):
        buffer = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buffer, 64)
        if buffer.value == "RichEditD2DPT":
            edits.append(hwnd)
        return True

    user32.EnumChildWindows(found[0], ENUMPROC(child), 0)
    if not edits:
        return None
    length = user32.SendMessageW(edits[0], 0x000E, 0, 0)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(edits[0], 0x000D, length + 1, ctypes.addressof(buffer))
    return buffer.value.replace("\r\n", "\n").replace("\r", "\n")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    exe = sys.argv[1] if len(sys.argv) > 1 else str(Path(sys.executable).with_name("pcbridge.exe"))
    note = Path(tempfile.mkdtemp(prefix="pcb-w7-")) / "pcbridge-w7-probe.txt"
    note.write_text("", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(note)])
    time.sleep(3)
    client = StdioClient([exe, "stdio"], dict(os.environ), 110.0)
    client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "pcbridge-w7-probe", "version": "1"}})
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(name: str, **arguments) -> str:
        started = time.perf_counter()
        result = client.request("tools/call", {"name": name, "arguments": arguments})
        text = _tool_text(result.get("result", result))
        ms = round(1000 * (time.perf_counter() - started))
        print(f"===== {name} {json.dumps(arguments, ensure_ascii=True)[:200]} ({ms} ms)\n"
              f"{text[:1400]}\n", flush=True)
        return text

    call("system_capabilities")
    call("desktop_unlock", minutes=5, reason="W7 probe")
    call("screen_info")
    call("window_list")
    call("window_focus", window="notepad")
    dump = call("ui_dump")
    ref = None
    for line in dump.splitlines():
        if "Text editor" in line:
            match = re.search(r"(#[0-9a-f]+)", line)
            if match:
                ref = match.group(1)
                break
    print(f"== text editor id: {ref}", flush=True)
    if ref:
        call("ui_set_text", id=ref, text="set through ui_set_text: ğüş")
        print(f"== notepad holds: {ascii(notepad_text(note.stem))}\n", flush=True)
    call("keyboard", action="key", keys="ctrl+a")
    call("keyboard", action="type", text="typed through the clipboard: ğüş ÇĞİ\n")
    print(f"== notepad holds: {ascii(notepad_text(note.stem))}\n", flush=True)
    call("keyboard", action="type", text="raw keys: abc XYZ 123 ğ", raw=True)
    print(f"== notepad holds: {ascii(notepad_text(note.stem))}\n", flush=True)
    shot = call("screen_capture", scale=640)
    match = re.search(r"shot[^`]*`([a-z0-9-]+)`", shot)
    call("mouse", action="move", x=640, y=400, monitor=1)
    call("mouse", action="click", button="left")
    call("find_text", text="raw keys")
    call("computer_batch", actions=json.dumps([{"action": "key", "keys": "end"},
                                    {"action": "type", "text": " batch"}]))
    print(f"== notepad holds: {ascii(notepad_text(note.stem))}\n", flush=True)
    call("desktop_lock")
    call("keyboard", action="key", keys="a")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
