"""W6d: UI Automation and windows in the Windows native helper, through NativeClient.

    python scripts\\dev\\windows\\probes\\uia_native_probe.py PATH\\TO\\pcbridge-native.exe [--elevated-wait N]

Run it in the desktop session (windows-vm.sh session). It opens an empty
text file in Notepad and Paint, then through the helper: lists the windows,
reads the focused one, dumps Notepad, writes a text with set_text and reads
it back from Notepad's own control (WM_GETTEXT), clicks a button by its
dumped ref, and brings Notepad back in front of Paint with window.activate.

With --elevated-wait N it also starts a Notepad as administrator (the UAC
prompt is confirmed from the host through QEMU within N seconds) and checks
that a key and a click aimed at it are refused with TARGET_ELEVATED.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as W
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pcbridge.desktop.lease import LeaseStore
from pcbridge.native.client import NativeClient

LRESULT = ctypes.c_ssize_t
user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, W.LPARAM]
user32.EnumChildWindows.argtypes = [W.HWND, ENUMPROC, W.LPARAM]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.GetForegroundWindow.restype = W.HWND
user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]


def find(predicate, parent=None):
    found = []

    def callback(hwnd, _lp):
        if (parent is not None or user32.IsWindowVisible(hwnd)) and predicate(hwnd):
            found.append(hwnd)
        return True

    if parent is None:
        user32.EnumWindows(ENUMPROC(callback), 0)
    else:
        user32.EnumChildWindows(parent, ENUMPROC(callback), 0)
    return found[0] if found else None


def title(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def klass(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def text_of(hwnd) -> str:
    length = user32.SendMessageW(hwnd, 0x000E, 0, 0)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, 0x000D, length + 1, ctypes.addressof(buffer))
    return buffer.value


def report(label: str, value) -> None:
    print(f"== {label}: {json.dumps(value, ensure_ascii=True, default=str)[:1500]}", flush=True)


def call(client, method, params, **kw):
    started = time.perf_counter()
    try:
        response = client.request(method, params, **kw)
        return response.result, round(1000 * (time.perf_counter() - started), 1)
    except Exception as exc:  # noqa: BLE001 - a probe reports everything
        return {"error": getattr(exc, "code", type(exc).__name__), "message": str(exc)[:300]}, None


def wait_window(predicate, seconds=15.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        hwnd = find(predicate)
        if hwnd:
            return hwnd
        time.sleep(0.3)
    return None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--elevated-wait", type=int, default=0)
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="pcb-uia-"))
    (work / "state").mkdir()
    (work / "run").mkdir()
    note = work / "pcbridge-uia-probe.txt"
    note.write_text("", encoding="utf-8")
    store = LeaseStore(work / "state")
    now = time.time()
    token = store.grant(until=now + 300, reason="uia probe", granted=now, granted_by="probe").token(now)
    grant = {"grant_id": token.grant_id, "revoke_epoch": token.revoke_epoch}
    client = NativeClient(args.binary, state_dir=work / "state", runtime_dir=work / "run")
    notepad = paint = None
    try:
        subprocess.Popen(["notepad.exe", str(note)])
        notepad = wait_window(lambda h: note.stem in title(h))
        time.sleep(1.0)
        report("notepad", {"hwnd": notepad, "front": user32.GetForegroundWindow() == notepad})

        for attempt in ("first", "second"):
            result, ms = call(client, "accessibility.windows", grant)
            rows = result.get("windows", [])
            report(f"accessibility.windows ({attempt})", {"ms": ms, "count": len(rows),
                                         "rows": [(r["app"], r["window"], r["active"]) for r in rows]})
        result, ms = call(client, "accessibility.focused", grant)
        report("accessibility.focused", {"ms": ms, **result})
        result, ms = call(client, "window.focused", grant)
        report("window.focused", {"ms": ms, **result})

        dump, ms = call(client, "accessibility.dump", {**grant, "target": "focused"})
        nodes = dump.get("nodes", [])
        report("dump focused", {"ms": ms, "app": dump.get("app"), "window": dump.get("window"),
                                "scope": dump.get("scope"), "count": len(nodes),
                                "truncated": dump.get("truncated"), "error": dump.get("error")})
        for node in nodes[:40]:
            print("   ", json.dumps({k: node[k] for k in ("role", "name", "states", "actions", "editable", "depth")},
                                    ensure_ascii=True))
        named, ms = call(client, "accessibility.dump", {**grant, "target": "notepad"})
        report("dump by name 'notepad'", {"ms": ms, "scope": named.get("scope"), "count": len(named.get("nodes", [])),
                                           "error": named.get("error")})

        editable = next((n for n in nodes if n["editable"]), None)
        report("editable node", editable)
        if editable:
            sample = "set by UIA: ğüşiöç ÇĞİÖŞÜ\nline two"
            result, ms = call(client, "accessibility.set_text",
                              {**grant, "snapshot": dump["snapshot"], "ref": editable["ref"]},
                              binary=sample.encode("utf-8"))
            report("set_text", {"ms": ms, **result})
            edit = find(lambda h: klass(h) == "RichEditD2DPT", notepad)
            seen = text_of(edit) if edit else None
            report("read back (WM_GETTEXT)", {"text": seen,
                                              "matches": seen is not None and seen.replace("\r\n", "\n").replace("\r", "\n") == sample})

        button = next((n for n in nodes if n["role"] == "push button" and n["name"] == "Add New Tab"), None)
        report("button to click", button)
        if button:
            before = len([n for n in nodes if n["role"] == "page tab"])
            result, ms = call(client, "accessibility.act",
                              {**grant, "snapshot": dump["snapshot"], "ref": button["ref"], "action": "click"})
            report("act click", {"ms": ms, **result})
            time.sleep(1.0)
            again, _ = call(client, "accessibility.dump", {**grant, "target": "focused"})
            after = len([n for n in again.get("nodes", []) if n["role"] == "page tab"])
            report("tabs before and after the click", [before, after])
        stale, _ = call(client, "accessibility.act", {**grant, "snapshot": "000000000000", "ref": "rid:1", "action": "click"})
        report("act on an unknown snapshot", stale)

        subprocess.Popen(["mspaint.exe"])
        paint = wait_window(lambda h: title(h).endswith("Paint"))
        time.sleep(2.0)
        report("paint in front", {"hwnd": paint, "front_title": title(user32.GetForegroundWindow())})
        result, ms = call(client, "window.activate", {**grant, "target": "notepad"})
        report("window.activate notepad", {"ms": ms, **result,
                                           "front_title": title(user32.GetForegroundWindow())})
        result, _ = call(client, "window.activate", {**grant, "target": "no such window anywhere"})
        report("window.activate nothing", result)

        if args.elevated_wait:
            elevated_note = work / "pcbridge-elevated.txt"
            elevated_note.write_text("", encoding="utf-8")
            subprocess.Popen(["powershell", "-NoProfile", "-Command",
                              f"Start-Process notepad.exe -ArgumentList '{elevated_note}' -Verb RunAs"])
            print("== waiting for the UAC prompt to be confirmed", flush=True)
            elevated = wait_window(lambda h: elevated_note.stem in title(h), args.elevated_wait)
            report("elevated notepad", {"hwnd": elevated})
            if elevated:
                time.sleep(1.5)
                report("front is elevated notepad", user32.GetForegroundWindow() == elevated)
                keyboard = {**grant, "hold_max_seconds": 0}
                result, _ = call(client, "input.keyboard.key", {**keyboard, "combo": "a"})
                report("key into the elevated window", result)
                snapshot = client.request("display.snapshot", {}).result
                rect = W.RECT()
                user32.GetWindowRect(elevated, ctypes.byref(rect))
                pointer = {**grant, "hold_max_seconds": 0, "topology_id": snapshot["topology_id"],
                           "pointer_speed": 0, "pointer_max_ms": 200}
                call(client, "input.pointer.move", {**pointer, "x": rect.left + 200, "y": rect.top + 200,
                                                    "smooth": False})
                result, _ = call(client, "input.pointer.click", {**pointer, "button": "left", "count": 1})
                report("click on the elevated window", result)
                windows, _ = call(client, "accessibility.windows", grant)
                report("elevated window in accessibility.windows",
                       any(elevated_note.stem in row["window"] for row in windows.get("windows", [])))
                user32.PostMessageW(elevated, 0x0010, 0, 0)
    finally:
        client.close()
        for hwnd in (paint, notepad):
            if hwnd:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
