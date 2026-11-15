"""W0 measurements for the Windows port: UI Automation and UIPI.

Run it in the desktop session with the probe virtualenv (comtypes):

    windows-vm.sh session "%USERPROFILE%\\probe-venv\\Scripts\\python ^
        scripts\\dev\\windows\\probes\\uia_probe.py [--elevated-wait N]"

Part 1 opens Notepad as the user and checks what UIA gives for its text
area: the RuntimeId (pcbridge's element identity), ValuePattern, IsPassword,
and whether a written value reads back.

Part 2 (with --elevated-wait N) opens a second Notepad elevated. The UAC
prompt is confirmed from the host through QEMU, so it waits up to N seconds
for the window. Then, from this unelevated process, it tries SendInput typing
and UIA ValuePattern.SetValue into that window and reports what arrived.
Both Notepads are closed again without saving.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as W
import json
import subprocess
import time

import comtypes
import comtypes.client

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32.GetForegroundWindow.restype = W.HWND
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

automation = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

ULONG_PTR = ctypes.c_size_t


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


def type_unicode(text: str) -> int:
    events = []
    for ch in text:
        for flags in (0x0004, 0x0004 | 0x0002):  # KEYEVENTF_UNICODE, then KEYUP
            i = INPUT(type=1)
            i.u.ki = KEYBDINPUT(0, ord(ch), flags, 0, 0)
            events.append(i)
    arr = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def notepad_windows() -> list[int]:
    found = []
    proto = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if user32.IsWindowVisible(hwnd) and buf.value.endswith("Notepad"):
            found.append(hwnd)
        return True

    cbp = proto(cb)
    user32.EnumWindows(cbp, 0)
    return found


def text_area(hwnd: int):
    root = automation.ElementFromHandle(hwnd)
    cond = automation.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_DocumentControlTypeId)
    el = root.FindFirst(UIA.TreeScope_Descendants, cond)
    if not el:
        cond = automation.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_EditControlTypeId)
        el = root.FindFirst(UIA.TreeScope_Descendants, cond)
    return el


def read_text(el) -> str | None:
    try:
        tp = el.GetCurrentPattern(UIA.UIA_TextPatternId).QueryInterface(UIA.IUIAutomationTextPattern)
        return tp.DocumentRange.GetText(-1)
    except Exception:  # noqa: BLE001
        pass
    try:
        vp = el.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)
        return vp.CurrentValue
    except Exception:  # noqa: BLE001
        return None


def describe(el) -> dict:
    out = {"name": el.CurrentName, "class": el.CurrentClassName,
           "control_type": el.CurrentControlType, "is_password": bool(el.CurrentIsPassword),
           "runtime_id": list(el.GetRuntimeId())}
    patterns = {}
    for label, pid in (("value", UIA.UIA_ValuePatternId), ("text", UIA.UIA_TextPatternId),
                       ("invoke", UIA.UIA_InvokePatternId)):
        try:
            patterns[label] = el.GetCurrentPattern(pid) is not None
        except Exception:  # noqa: BLE001
            patterns[label] = False
    out["patterns"] = patterns
    return out


def set_value(el, text: str) -> str:
    try:
        vp = el.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)
        vp.SetValue(text)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return repr(exc)


def close(hwnd: int) -> None:
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


def user_notepad() -> dict:
    before = set(notepad_windows())
    subprocess.Popen(["notepad.exe"])
    time.sleep(3)
    new = [h for h in notepad_windows() if h not in before]
    if not new:
        return {"error": "no Notepad window"}
    hwnd = new[0]
    el = text_area(hwnd)
    out = {"element": describe(el) if el else None}
    if el:
        rid1 = list(el.GetRuntimeId())
        out["set_value"] = set_value(el, "pcbridge uia ğüşiöç")
        time.sleep(0.3)
        out["read_back"] = read_text(text_area(hwnd))
        out["runtime_id_stable_after_edit"] = list(text_area(hwnd).GetRuntimeId()) == rid1
    # Undo the edit so closing does not ask to save.
    if el:
        set_value(el, "")
    time.sleep(0.3)
    close(hwnd)
    time.sleep(1)
    out["closed"] = hwnd not in notepad_windows()
    return out


def elevated_notepad(wait: float) -> dict:
    before = set(notepad_windows())
    shell32.ShellExecuteW(None, "runas", "notepad.exe", None, None, 1)
    end = time.monotonic() + wait
    new: list[int] = []
    while time.monotonic() < end and not new:
        time.sleep(0.5)
        new = [h for h in notepad_windows() if h not in before]
    if not new:
        return {"error": "no elevated Notepad window (UAC not confirmed?)"}
    hwnd = new[0]
    out: dict = {}
    time.sleep(1.5)
    el = text_area(hwnd)
    out["uia_element_found"] = bool(el)
    if el:
        out["uia_element"] = describe(el)
        out["uia_set_value"] = set_value(el, "uia-into-elevated")
        time.sleep(0.3)
        out["uia_read_back"] = read_text(text_area(hwnd))
    out["foreground_is_elevated_notepad"] = user32.GetForegroundWindow() == hwnd
    out["sendinput_returned"] = type_unicode("sendinput-into-elevated")
    out["sendinput_last_error"] = ctypes.get_last_error()
    time.sleep(0.5)
    out["read_back_after_sendinput"] = read_text(text_area(hwnd)) if el else None
    close(hwnd)
    time.sleep(1)
    out["close_message_reached_it"] = hwnd not in notepad_windows()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elevated-wait", type=float, default=0.0)
    args = parser.parse_args()
    result = {"user_notepad": user_notepad()}
    if args.elevated_wait:
        result["elevated_notepad"] = elevated_notepad(args.elevated_wait)
    print(json.dumps(result, indent=1, default=str))  # stdout to a file is cp1252 on Windows


if __name__ == "__main__":
    main()
