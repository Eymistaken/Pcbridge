"""W0 measurements for the Windows port, in the logged-on desktop session.

Run it through `scripts/dev/windows-vm.sh session` (an SSH logon has no
desktop). Standard library only: every call goes through ctypes, so the
numbers are what pcbridge itself would see without extra packages.

It answers, and prints as JSON:
  * monitors, DPI and the virtual screen under Per-Monitor-V2 awareness;
  * whether input injected with SendInput resets GetLastInputInfo (the
    USER_ACTIVE guard's clock), and whether a low-level hook can tell
    injected input (and pcbridge's own, by dwExtraInfo) from hardware input;
    with --hardware-window N it waits N seconds for input that is NOT
    injected, which the host sends through QEMU while the probe runs;
  * the lock state as WTS reports it, and the input desktop's name;
  * Windows' foreground rules: which window a launched process gets, and
    whether SetForegroundWindow from a background process works, before and
    after the process injects a key;
  * how long a GDI capture of the whole virtual screen takes.

It opens Notepad and Paint and closes them again (both untitled and unchanged).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as W
import json
import subprocess
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shcore = ctypes.WinDLL("shcore")
wtsapi = ctypes.WinDLL("wtsapi32")

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
PCBRIDGE_MARK = 0x50434252  # "PCBR" in dwExtraInfo: pcbridge's own input

user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.CallNextHookEx.argtypes = [W.HHOOK, ctypes.c_int, W.WPARAM, W.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = W.HWND
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.OpenInputDesktop.restype = W.HANDLE
user32.GetUserObjectInformationW.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
user32.CloseDesktop.argtypes = [W.HANDLE]
kernel32.GetModuleHandleW.restype = W.HMODULE


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", W.DWORD), ("u", _INPUTUNION)]


def send_mouse_rel(dx: int, dy: int) -> int:
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(dx, dy, 0, 0x0001, 0, PCBRIDGE_MARK)  # MOUSEEVENTF_MOVE
    return user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def send_key(vk: int) -> int:
    down = INPUT(type=1)
    down.u.ki = KEYBDINPUT(vk, 0, 0, 0, PCBRIDGE_MARK)
    up = INPUT(type=1)
    up.u.ki = KEYBDINPUT(vk, 0, 0x0002, 0, PCBRIDGE_MARK)  # KEYEVENTF_KEYUP
    arr = (INPUT * 2)(down, up)
    return user32.SendInput(2, arr, ctypes.sizeof(INPUT))


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]


def idle_ms() -> int:
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    user32.GetLastInputInfo(ctypes.byref(li))
    return (kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF


def window_text(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def foreground_title() -> str:
    return window_text(user32.GetForegroundWindow())


def find_window(fragment: str):
    found = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and fragment in window_text(hwnd):
            found.append(hwnd)
        return True

    cbp = proc(cb)
    user32.EnumWindows(cbp, 0)
    return found[0] if found else None


def monitors() -> dict:
    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT),
                    ("dwFlags", W.DWORD), ("szDevice", W.WCHAR * 32)]

    result = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)

    def cb(hmon, _hdc, _rc, _lp):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        dx, dy = W.UINT(), W.UINT()
        shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
        r = mi.rcMonitor
        result.append({"device": mi.szDevice, "rect": [r.left, r.top, r.right, r.bottom],
                       "primary": bool(mi.dwFlags & 1), "dpi": dx.value})
        return True

    cbp = proc(cb)
    user32.EnumDisplayMonitors(None, None, cbp, 0)
    return {"monitors": result,
            "virtual_screen": [user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)]}


class MouseHook:
    """A WH_MOUSE_LL hook on its own thread with a message loop."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._ready = threading.Event()
        self._tid = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD),
                        ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]

        proto = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, W.WPARAM, W.LPARAM)

        def proc(code, wparam, lparam):
            if code == 0:
                s = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.events.append({"msg": int(wparam), "injected": bool(s.flags & 0x1),
                                    "lower_il_injected": bool(s.flags & 0x2),
                                    "pcbridge_mark": s.dwExtraInfo == PCBRIDGE_MARK,
                                    "t": time.monotonic()})
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = proto(proc)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, proto, W.HINSTANCE, W.DWORD]
        user32.SetWindowsHookExW.restype = W.HHOOK
        hook = user32.SetWindowsHookExW(14, self._proc, kernel32.GetModuleHandleW(None), 0)
        self.installed = bool(hook)
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        msg = W.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
        self._thread.join(5)


def idle_and_hooks(hardware_window: float) -> dict:
    out: dict = {}
    hook = MouseHook()
    hook.start()
    out["ll_hook_installed"] = hook.installed
    time.sleep(2.0)
    out["idle_before_inject_ms"] = idle_ms()
    t0 = time.monotonic()
    send_mouse_rel(1, 0)
    send_mouse_rel(-1, 0)
    time.sleep(0.3)
    out["idle_after_inject_ms"] = idle_ms()
    injected = [e for e in hook.events if e["t"] >= t0]
    out["hook_saw_injected"] = [{k: v for k, v in e.items() if k != "t"} for e in injected[:2]]
    if hardware_window > 0:
        t1 = time.monotonic()
        time.sleep(hardware_window)
        hw = [e for e in hook.events if e["t"] >= t1]
        out["hardware_window_s"] = hardware_window
        out["hook_events_in_window"] = len(hw)
        out["hook_noninjected_in_window"] = sum(1 for e in hw if not e["injected"])
        out["idle_after_hardware_ms"] = idle_ms()
    hook.stop()
    return out


def lock_state() -> dict:
    class WTSINFOEX_LEVEL1_W(ctypes.Structure):
        _fields_ = [("SessionId", W.ULONG), ("SessionState", ctypes.c_int),
                    ("SessionFlags", W.LONG)]  # the rest of the struct is not needed

    class WTSINFOEXW(ctypes.Structure):
        # The real union holds LARGE_INTEGERs, so it starts 8-byte aligned:
        # without the pad the fields read one slot early (SessionFlags read
        # SessionState, always 0 while active; measured 2026-11-15).
        _fields_ = [("Level", W.DWORD), ("_pad", W.DWORD), ("Data", WTSINFOEX_LEVEL1_W)]

    buf = ctypes.c_void_p()
    size = W.DWORD()
    ok = wtsapi.WTSQuerySessionInformationW(None, W.DWORD(0xFFFFFFFF), 25,
                                            ctypes.byref(buf), ctypes.byref(size))
    out: dict = {"wts_query_ok": bool(ok)}
    if ok:
        info = ctypes.cast(buf, ctypes.POINTER(WTSINFOEXW)).contents
        flags = info.Data.SessionFlags
        out["wts_session_flags"] = flags
        out["wts_meaning"] = {0: "locked", 1: "unlocked"}.get(flags, "unknown")
        wtsapi.WTSFreeMemory(buf)
    desk = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if desk:
        name = ctypes.create_unicode_buffer(64)
        needed = W.DWORD()
        user32.GetUserObjectInformationW(desk, 2, name, ctypes.sizeof(name), ctypes.byref(needed))
        out["input_desktop"] = name.value
        user32.CloseDesktop(desk)
    else:
        out["input_desktop"] = None
        out["open_input_desktop_error"] = ctypes.get_last_error()
    return out


def foreground() -> dict:
    out: dict = {"initial": foreground_title()}
    subprocess.Popen(["notepad.exe"])
    time.sleep(3)
    out["after_launch_notepad"] = foreground_title()
    subprocess.Popen(["mspaint.exe"])
    time.sleep(4)
    out["after_launch_paint"] = foreground_title()
    notepad = find_window("Notepad")
    paint = find_window("Paint")
    out["found"] = {"notepad": bool(notepad), "paint": bool(paint)}
    if notepad and paint:
        out["set_fg_notepad_return"] = bool(user32.SetForegroundWindow(notepad))
        time.sleep(0.5)
        out["after_set_fg_notepad"] = foreground_title()
        # The documented condition: the calling process received the last input
        # event. Inject one harmless key (VK_F24, bound to nothing) and retry.
        send_key(0x87)
        out["set_fg_paint_after_inject_return"] = bool(user32.SetForegroundWindow(paint))
        time.sleep(0.5)
        out["after_set_fg_paint_after_inject"] = foreground_title()
    for hwnd in (notepad, paint):
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    time.sleep(1)
    out["closed"] = {"notepad": find_window("Notepad") is None, "paint": find_window("Paint") is None}
    return out


def foreground_after_user_click(timeout: float) -> dict:
    """SetForegroundWindow from a process that is no longer in front.

    The probe opens Notepad, then waits for a hardware click (the host sends
    one through QEMU onto the taskbar), which moves the foreground to another
    process. Then it asks for Notepad's window plainly, after injecting a key,
    and after injecting an Alt press, and reports each result.
    """
    out: dict = {}
    hook = MouseHook()
    hook.start()
    subprocess.Popen(["notepad.exe"])
    time.sleep(3)
    notepad = find_window("Notepad")
    out["after_launch"] = foreground_title()
    end = time.monotonic() + timeout
    clicked = False
    while time.monotonic() < end and not clicked:
        clicked = any(e["msg"] == 0x0202 and not e["injected"] for e in hook.events)  # WM_LBUTTONUP
        time.sleep(0.1)
    hook.stop()
    out["user_click_seen"] = clicked
    time.sleep(0.5)
    out["after_user_click"] = foreground_title() or "(no title)"
    if notepad and clicked:
        out["plain_return"] = bool(user32.SetForegroundWindow(notepad))
        time.sleep(0.5)
        out["plain_result"] = foreground_title() or "(no title)"
        send_key(0x87)  # VK_F24, bound to nothing
        out["after_f24_return"] = bool(user32.SetForegroundWindow(notepad))
        time.sleep(0.5)
        out["after_f24_result"] = foreground_title() or "(no title)"
        send_key(0x12)  # VK_MENU: the Alt press that lifts the foreground lock
        out["after_alt_return"] = bool(user32.SetForegroundWindow(notepad))
        time.sleep(0.5)
        out["after_alt_result"] = foreground_title() or "(no title)"
    if notepad:
        user32.PostMessageW(notepad, 0x0010, 0, 0)  # WM_CLOSE
    return out


def gdi_capture() -> dict:
    x, y, w, h = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    times = []
    for _ in range(5):
        t = time.perf_counter()
        gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020)  # SRCCOPY
        times.append(round((time.perf_counter() - t) * 1000, 2))
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    return {"size": [w, h], "bitblt_ms": times}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hardware-window", type=float, default=0.0)
    parser.add_argument("--skip-foreground", action="store_true")
    parser.add_argument("--foreground-after-click", type=float, default=0.0,
                        help="only run foreground_after_user_click, waiting this long for the click")
    args = parser.parse_args()
    if args.foreground_after_click:
        print(json.dumps(foreground_after_user_click(args.foreground_after_click), indent=1))
        return
    result = {"dpi_awareness_set": bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))}
    result["display"] = monitors()
    result["lock"] = lock_state()
    result["idle"] = idle_and_hooks(args.hardware_window)
    if not args.skip_foreground:
        result["foreground"] = foreground()
    result["gdi_capture"] = gdi_capture()
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
