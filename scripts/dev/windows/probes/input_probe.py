"""W6c: the Windows native helper's input and clipboard, through NativeClient.

    python scripts\\dev\\windows\\probes\\input_probe.py PATH\\TO\\pcbridge-native.exe

Run it in the desktop session (windows-vm.sh session), never on a desktop
that matters: it types and clicks. It opens an empty text file in Notepad
first and checks Notepad is in front before any key goes out.

Low-level keyboard and mouse hooks in this process watch every event, so
the checks are what Windows delivered, not what the helper answered:

- text through input.keyboard.type and keys through input.keyboard.key,
  read back from Notepad's text control (WM_GETTEXT) and from the file
  after Ctrl+S;
- every injected event carries LLxHF_INJECTED and pcbridge's dwExtraInfo
  mark;
- pointer moves land on the exact pixel (GetCursorPos), clicks, a drag and
  the wheel arrive as button and wheel events;
- a held key is released by the 5 s auto-release;
- a held key and a held button are released after the grant is revoked,
  and how long that took;
- the clipboard written, read back here and by PowerShell, then cleared.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from pcbridge.desktop.lease import LeaseStore
from pcbridge.native.client import NativeClient

MARK = 0x50434252
ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, W.WPARAM, W.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
user32.SetWindowsHookExW.restype = W.HHOOK
user32.CallNextHookEx.argtypes = [W.HHOOK, ctypes.c_int, W.WPARAM, W.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [W.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
user32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
user32.GetForegroundWindow.restype = W.HWND
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
kernel32.GetModuleHandleW.restype = W.HMODULE
ENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, W.LPARAM]
user32.EnumChildWindows.argtypes = [W.HWND, ENUMPROC, W.LPARAM]


class KBDLL(ctypes.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MSLL(ctypes.Structure):
    _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class Hooks:
    """Both low-level hooks on a thread of their own, with a message loop."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.thread_id = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(5)

    def _record(self, kind: str, wparam: int, flags: int, extra: int, **more) -> None:
        with self.lock:
            self.events.append({"t": time.perf_counter(), "kind": kind, "msg": wparam,
                                "flags": flags, "extra": extra, **more})

    def _run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()

        def keyboard(code, wparam, lparam):
            if code >= 0:
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLL)).contents
                self._record("key", wparam, info.flags, info.dwExtraInfo,
                             vk=info.vkCode, scan=info.scanCode)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def mouse(code, wparam, lparam):
            if code >= 0:
                info = ctypes.cast(lparam, ctypes.POINTER(MSLL)).contents
                self._record("mouse", wparam, info.flags, info.dwExtraInfo,
                             x=info.pt.x, y=info.pt.y, data=ctypes.c_int32(info.mouseData).value)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._procs = (HOOKPROC(keyboard), HOOKPROC(mouse))
        module = kernel32.GetModuleHandleW(None)
        hooks = [user32.SetWindowsHookExW(13, self._procs[0], module, 0),
                 user32.SetWindowsHookExW(14, self._procs[1], module, 0)]
        self.ready.set()
        msg = W.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for hook in hooks:
            user32.UnhookWindowsHookEx(hook)

    def take(self) -> list[dict]:
        time.sleep(0.15)
        with self.lock:
            events, self.events = self.events, []
        return events

    def stop(self) -> None:
        user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT


def window_class(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def window_title(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def find_window(predicate) -> int | None:
    found: list[int] = []

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and predicate(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(ENUMPROC(callback), 0)
    return found[0] if found else None


def edit_control(top) -> int | None:
    found: list[int] = []

    def callback(hwnd, _lparam):
        if window_class(hwnd) in ("RichEditD2DPT", "Edit"):
            found.append(hwnd)
        return True

    user32.EnumChildWindows(top, ENUMPROC(callback), 0)
    return found[0] if found else None


def control_text(hwnd) -> str:
    length = user32.SendMessageW(hwnd, 0x000E, 0, 0)  # WM_GETTEXTLENGTH
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, 0x000D, length + 1, ctypes.addressof(buffer))  # WM_GETTEXT
    return buffer.value


def cursor() -> tuple[int, int]:
    point = W.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def report(label: str, value) -> None:
    print(f"== {label}: {json.dumps(value, ensure_ascii=True, default=str)[:900]}")


def all_marked(events: list[dict]) -> bool:
    injected = {"key": 0x10, "mouse": 0x01}
    return bool(events) and all(
        event["flags"] & injected[event["kind"]] and event["extra"] == MARK for event in events)


def main() -> int:
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    binary = Path(sys.argv[1])
    hooks = Hooks()
    work = Path(tempfile.mkdtemp(prefix="pcb-input-"))
    (work / "state").mkdir()
    (work / "run").mkdir()
    note = work / "pcbridge-input-probe.txt"
    note.write_text("", encoding="utf-8")
    store = LeaseStore(work / "state")
    now = time.time()
    lease = store.grant(until=now + 300, reason="input probe", granted=now, granted_by="probe")
    token = lease.token(now)
    grant = {"grant_id": token.grant_id, "revoke_epoch": token.revoke_epoch}
    client = NativeClient(binary, state_dir=work / "state", runtime_dir=work / "run")
    notepad = None
    try:
        snapshot = client.request("display.snapshot", {}).result
        topology = snapshot["topology_id"]
        canvas = snapshot["canvas"]
        report("capabilities", [(c["name"], c["status"]) for c in
                                client.request("capabilities", {}).result["capabilities"]])
        keyboard = {**grant, "hold_max_seconds": 5}
        pointer = {**grant, "hold_max_seconds": 5, "topology_id": topology,
                   "pointer_speed": 0, "pointer_max_ms": 200}
        report("keyboard.ensure", client.request("input.keyboard.ensure", keyboard).result)
        report("pointer.ensure", client.request("input.pointer.ensure",
                                                {**pointer, "relative": True}).result)
        hooks.take()

        # --- Notepad in front, with an empty file --------------------------
        subprocess.Popen(["notepad.exe", str(note)])
        deadline = time.time() + 15
        while time.time() < deadline:
            notepad = find_window(lambda h: note.stem in window_title(h))
            if notepad:
                break
            time.sleep(0.3)
        report("notepad window", {"hwnd": notepad, "title": notepad and window_title(notepad)})
        if not notepad:
            return 1
        time.sleep(1.0)
        if user32.GetForegroundWindow() != notepad:
            user32.SetForegroundWindow(notepad)
            time.sleep(0.5)
        front = user32.GetForegroundWindow() == notepad
        report("notepad in front", front)
        if not front:
            return 1
        edit = edit_control(notepad)
        report("text control", {"hwnd": edit, "class": edit and window_class(edit)})
        hooks.take()

        # --- text and keys ----------------------------------------------------
        text = "pcbridge typed ğüşiöç ÇĞİÖŞÜ 123 \U0001F600\nsecond line"
        started = time.perf_counter()
        typed = client.request("input.keyboard.type", keyboard, binary=text.encode("utf-8")).result
        report("type", {**typed, "ms": round(1000 * (time.perf_counter() - started), 1)})
        client.request("input.keyboard.key", {**keyboard, "combo": "return"})
        for combo in ["shift+a", "b", "space", "backspace", "c"]:
            client.request("input.keyboard.key", {**keyboard, "combo": combo})
        time.sleep(0.4)
        events = hooks.take()
        report("key events seen, all marked as pcbridge's", {"count": len(events), "marked": all_marked(events)})
        seen = control_text(edit) if edit else None
        expected = text.replace("\n", "\r") + "\rAbc"
        report("text read back (WM_GETTEXT)", {"text": seen, "matches": seen is not None and seen.replace("\r\n", "\r") == expected})
        client.request("input.keyboard.key", {**keyboard, "combo": "ctrl+s"})
        time.sleep(1.5)
        saved = note.read_text(encoding="utf-8-sig")
        report("file after ctrl+s", {"text": saved, "matches": saved.replace("\r\n", "\n") == expected.replace("\r", "\n")})
        hooks.take()

        # --- pointer ----------------------------------------------------------
        rect = W.RECT()
        user32.GetWindowRect(notepad, ctypes.byref(rect))
        misses = []
        for x, y in [(0, 0), (1, 1), (canvas[0] - 1, canvas[1] - 1), (canvas[0] // 2, canvas[1] // 3),
                     (123, 457), (rect.left + 200, rect.top + 200)]:
            client.request("input.pointer.move", {**pointer, "x": x, "y": y, "smooth": False})
            if cursor() != (x, y):
                misses.append([(x, y), cursor()])
        report("absolute moves on the exact pixel", {"misses": misses})
        started = time.perf_counter()
        client.request("input.pointer.move", {**pointer, "x": rect.left + 300, "y": rect.top + 250,
                                               "smooth": True})
        report("smooth move", {"ms": round(1000 * (time.perf_counter() - started), 1), "at": cursor()})
        hooks.take()
        client.request("input.pointer.click", {**pointer, "button": "left", "count": 2, "hold_ms": 60})
        events = hooks.take()
        downs = [e for e in events if e["msg"] == 0x0201]
        ups = [e for e in events if e["msg"] == 0x0202]
        hold = [round(1000 * (u["t"] - d["t"]), 1) for d, u in zip(downs, ups)]
        report("double click", {"downs": len(downs), "ups": len(ups), "hold_ms": hold,
                                "marked": all_marked(events)})
        client.request("input.pointer.scroll", {**pointer, "amount": -3, "horizontal": False})
        wheel = [e["data"] for e in hooks.take() if e["msg"] == 0x020A]
        report("wheel", wheel)
        client.request("input.pointer.drag", {**pointer, "x1": rect.left + 250, "y1": rect.top + 240,
                                               "x2": rect.left + 450, "y2": rect.top + 260,
                                               "button": "left"})
        events = hooks.take()
        report("drag", {"down": sum(e["msg"] == 0x0201 for e in events),
                        "moves": sum(e["msg"] == 0x0200 for e in events),
                        "up": sum(e["msg"] == 0x0202 for e in events), "end": cursor()})
        before = cursor()
        client.request("input.pointer.move_by", {**grant, "dx": 40, "dy": -20})
        after = cursor()
        report("relative move", {"before": before, "after": after})

        # --- held key, auto-release ---------------------------------------------
        hooks.take()
        client.request("input.keyboard.key_down", {**keyboard, "combo": "shift"})
        held = client.request("input.keyboard.held", {}).result
        time.sleep(5.6)
        auto = client.request("input.keyboard.take_auto_released", {}).result
        ups = [e for e in hooks.take() if e["kind"] == "key" and e["msg"] in (0x0101, 0x0105)]
        report("auto-release after 5 s", {"held": held["held"], "released": auto["released"],
                                         "key_up_seen": len(ups)})

        # --- clipboard ------------------------------------------------------------
        sample = "abc-ğüş-clipboard"
        report("clipboard.write", client.request("clipboard.write", {**grant, "mime": "text/plain;charset=utf-8"},
                                                 binary=sample.encode("utf-8")).result)
        read = client.request("clipboard.read", grant)
        report("clipboard.read", {**read.result, "text": read.binary.decode("utf-8"),
                                  "matches": read.binary.decode("utf-8") == sample})
        shell = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
            capture_output=True, encoding="utf-8")
        report("Get-Clipboard", {"text": shell.stdout.strip(), "matches": shell.stdout.strip() == sample})
        client.request("clipboard.clear", grant)
        report("clipboard after clear", client.request("clipboard.read", grant).result)

        # --- revoke releases what is held ---------------------------------------
        client.request("input.keyboard.key_down", {**keyboard, "combo": "ctrl"})
        client.request("input.pointer.mouse_down", {**pointer, "button": "right"})
        hooks.take()
        revoked = time.perf_counter()
        store.revoke()
        time.sleep(1.0)
        events = hooks.take()
        key_up = next((e for e in events if e["kind"] == "key" and e["msg"] in (0x0101, 0x0105)), None)
        button_up = next((e for e in events if e["msg"] == 0x0205), None)
        report("released after revoke", {
            "key_up_ms": key_up and round(1000 * (key_up["t"] - revoked), 1),
            "button_up_ms": button_up and round(1000 * (button_up["t"] - revoked), 1),
        })
        try:
            client.request("input.keyboard.key", {**keyboard, "combo": "a"})
            report("key after revoke", "ACCEPTED (wrong)")
        except Exception as exc:  # noqa: BLE001 - the refusal is the expected answer
            report("key after revoke", getattr(exc, "code", str(exc)))
    finally:
        client.close()
        hooks.stop()
        if notepad:
            user32.PostMessageW(notepad, 0x0010, 0, 0)  # WM_CLOSE; the file is saved
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
