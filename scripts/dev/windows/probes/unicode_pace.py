"""How fast KEYEVENTF_UNICODE text can go into Windows 11 Notepad.

    python scripts\\dev\\windows\\probes\\unicode_pace.py

Run in the desktop session. Opens an empty file in Notepad and types the
same line with direct SendInput calls at several paces and kinds (Unicode
packets or scan codes, characters per call, pause between calls), reading
the text control back after each. The numbers are in measured-facts.md (W6c).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import subprocess
import tempfile
import time
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
ENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, W.LPARAM]
user32.EnumChildWindows.argtypes = [W.HWND, ENUMPROC, W.LPARAM]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.GetForegroundWindow.restype = W.HWND
user32.GetKeyboardLayout.restype = ctypes.c_void_p


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", W.DWORD), ("u", _U)]


user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


SCAN = {c: code for c, code in zip("qwertyuiop", range(0x10, 0x1A))}
SCAN.update({c: code for c, code in zip("asdfghjkl", range(0x1E, 0x27))})
SCAN.update({c: code for c, code in zip("zxcvbnm", range(0x2C, 0x33))})
SCAN[" "] = 0x39
SCAN["\r"] = 0x1C


def send_scan(text: str) -> int:
    events = []
    for character in text:
        for flags in (0x0008, 0x000A):  # SCANCODE, then KEYUP
            event = INPUT(type=1)
            event.u.ki = KEYBDINPUT(0, SCAN[character], flags, 0, 0x50434252)
            events.append(event)
    array = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), array, ctypes.sizeof(INPUT))


def send_down_only(text: str) -> int:
    events = []
    for unit in [int.from_bytes(text.encode("utf-16-le")[i:i + 2], "little")
                 for i in range(0, len(text.encode("utf-16-le")), 2)]:
        event = INPUT(type=1)
        event.u.ki = KEYBDINPUT(0, unit, 0x0004, 0, 0x50434252)
        events.append(event)
    array = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), array, ctypes.sizeof(INPUT))


def send_per_call(text: str) -> int:
    for character in text:
        send_units(character)
    return len(text)


def send_units(text: str) -> int:
    events = []
    units = [int.from_bytes(text.encode("utf-16-le")[i:i + 2], "little")
             for i in range(0, len(text.encode("utf-16-le")), 2)]
    for unit in units:
        for flags in (0x0004, 0x0006):
            event = INPUT(type=1)
            event.u.ki = KEYBDINPUT(0, unit, flags, 0, 0x50434252)
            events.append(event)
    array = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), array, ctypes.sizeof(INPUT))


def find(predicate, parent=None):
    found = []

    def callback(hwnd, _lp):
        if predicate(hwnd):
            found.append(hwnd)
        return True

    if parent is None:
        user32.EnumWindows(ENUMPROC(callback), 0)
    else:
        user32.EnumChildWindows(parent, ENUMPROC(callback), 0)
    return found[0] if found else None


def title(hwnd):
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def klass(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def text_of(hwnd):
    length = user32.SendMessageW(hwnd, 0x000E, 0, 0)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, 0x000D, length + 1, ctypes.addressof(buffer))
    return buffer.value


def main() -> int:
    note = Path(tempfile.mkdtemp(prefix="pcb-pace-")) / "pcbridge-pace.txt"
    note.write_text("", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(note)])
    top = None
    for _ in range(50):
        top = find(lambda h: note.stem in title(h))
        if top:
            break
        time.sleep(0.3)
    time.sleep(1.0)
    edit = find(lambda h: klass(h) == "RichEditD2DPT", top)
    print("front", user32.GetForegroundWindow() == top, "layout", hex(user32.GetKeyboardLayout(0) or 0))
    line = "abc typed xyz qwerty"
    for kind, chunk, pause in [("down", len(line), 0.0), ("percall", len(line), 0.0),
                               ("unicode", 1, 0.008), ("unicode", 1, 0.006), ("down", 1, 0.003),
                               ("unicode", 2, 0.012), ("unicode", 1, 0.01)]:
        send = {"scan": send_scan, "down": send_down_only, "percall": send_per_call}.get(kind, send_units)
        before = text_of(edit)
        for start in range(0, len(line), chunk):
            send(line[start:start + chunk])
            time.sleep(pause)
        time.sleep(0.5)
        got = text_of(edit)[len(before):]
        print(f"{kind:8} chunk {chunk:>2} pause {pause * 1000:>4.0f} ms: {got!r} {'OK' if got == line else 'WRONG'}")
        send_scan("\r")
        time.sleep(0.3)
    user32.PostMessageW(top, 0x0010, 0, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
