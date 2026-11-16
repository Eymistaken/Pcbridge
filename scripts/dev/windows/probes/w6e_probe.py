"""W6e: idle-watch, the frame and the tray icon of the Windows native helper.

    python scripts\\dev\\windows\\probes\\w6e_probe.py PATH\\TO\\pcbridge-native.exe PART

Run it in the desktop session (windows-vm.sh session). PART is one of:

- `idle`: starts `idle-watch <file>`, then sends input marked as
  pcbridge's (through the helper) and unmarked input (SendInput with no
  mark, as another program or a person would), and reads the record after
  each: only the unmarked input may reset the idle time.
- `frame`: opens a grant and starts `glow-watch` with it; prints the record
  it keeps, a capture through the helper (the frame must not be in it) and
  the tray icon as UI Automation sees it; then revokes the grant and times
  how long the frame process takes to end. With `--hold` it leaves the
  frame up for N seconds first, for a screenshot from the host.
- `lock`: prints the lock state the helper sees through a capture request,
  in a loop, for N seconds (lock and unlock the VM from the host meanwhile).
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

ULONG_PTR = ctypes.c_size_t
user32 = ctypes.WinDLL("user32", use_last_error=True)


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


def unmarked_mouse_nudge() -> None:
    events = []
    for dx in (1, -1):
        event = INPUT(type=0)
        event.u.mi = MOUSEINPUT(dx, 0, 0, 0x0001, 0, 0)  # MOUSEEVENTF_MOVE, no mark
        events.append(event)
    array = (INPUT * len(events))(*events)
    user32.SendInput(len(events), array, ctypes.sizeof(INPUT))


def report(label: str, value) -> None:
    print(f"== {label}: {json.dumps(value, ensure_ascii=True, default=str)[:1200]}", flush=True)


def setup(binary: Path):
    work = Path(tempfile.mkdtemp(prefix="pcb-w6e-"))
    (work / "state").mkdir()
    (work / "run").mkdir()
    store = LeaseStore(work / "state")
    now = time.time()
    token = store.grant(until=now + 300, reason="w6e probe", granted=now, granted_by="probe").token(now)
    grant = {"grant_id": token.grant_id, "revoke_epoch": token.revoke_epoch}
    client = NativeClient(binary, state_dir=work / "state", runtime_dir=work / "run")
    return work, store, token, grant, client


def read_record(path: Path) -> dict | None:
    for _ in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
    return None


def idle_part(binary: Path) -> None:
    work, _store, _token, grant, client = setup(binary)
    record_path = work / "idle.json"
    watcher = subprocess.Popen([str(binary), "idle-watch", str(record_path)],
                               stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(2.5)
        first = read_record(record_path)
        report("record after 2.5 s without input", first)
        keyboard = {**grant, "hold_max_seconds": 0}
        pointer_snapshot = client.request("display.snapshot", {}).result
        pointer = {**grant, "hold_max_seconds": 0, "topology_id": pointer_snapshot["topology_id"],
                   "pointer_speed": 0, "pointer_max_ms": 200}
        for index in range(10):
            client.request("input.pointer.move", {**pointer, "x": 300 + index, "y": 300, "smooth": False})
            time.sleep(0.2)
        marked = read_record(record_path)
        now = int(time.time() * 1000)
        report("after 2 s of pcbridge's own pointer moves",
               {"idle": marked["idle"], "idle_ms": now - marked["since_unix_ms"]})
        unmarked_mouse_nudge()
        time.sleep(0.4)
        after = read_record(record_path)
        now = int(time.time() * 1000)
        report("0.4 s after one unmarked SendInput move",
               {"idle": after["idle"], "idle_ms": now - after["since_unix_ms"]})
        time.sleep(1.5)
        later = read_record(record_path)
        now = int(time.time() * 1000)
        report("1.9 s after it", {"idle": later["idle"], "idle_ms": now - later["since_unix_ms"],
                                  "heartbeat_age_ms": now - later["confirmed_unix_ms"]})
        print("== waiting 6 s for hardware input from the host", flush=True)
        time.sleep(6)
        hardware = read_record(record_path)
        now = int(time.time() * 1000)
        report("after the host's hardware key", {"idle": hardware["idle"],
                                                 "idle_ms": now - hardware["since_unix_ms"]})
    finally:
        client.close()
        watcher.kill()


def press(vk: int) -> None:
    events = []
    for flags in (0, 0x0002):
        event = INPUT(type=1)
        event.u.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        events.append(event)
    array = (INPUT * len(events))(*events)
    user32.SendInput(len(events), array, ctypes.sizeof(INPUT))


def frame_part(binary: Path, hold: int) -> None:
    work, store, token, grant, client = setup(binary)
    # "Lock now" runs `<program> lock`; this program only records that.
    marker = work / "lock-ran.txt"
    lock_program = work / "lockmark.cmd"
    lock_program.write_text(f'@echo %1> "{marker}"\r\n', encoding="ascii")
    started = time.perf_counter()
    frame = subprocess.Popen([str(binary), "glow-watch", str(work / "state"), token.grant_id,
                              str(token.revoke_epoch), str(lock_program)],
                             stderr=subprocess.PIPE, text=True)
    record_path = work / "state" / "glow.json"
    first_ready = None
    while time.perf_counter() - started < 5:
        record = read_record(record_path) if record_path.exists() else None
        if record and record.get("ready"):
            first_ready = round(1000 * (time.perf_counter() - started), 1)
            break
        time.sleep(0.02)
    record = read_record(record_path)
    now = int(time.time() * 1000)
    report("glow.json", {"first_ready_ms": first_ready, **(record or {}),
                         "age_ms": record and now - record["presented_unix_ms"]})
    snapshot = client.request("display.snapshot", {}).result
    monitor = snapshot["monitors"][0]
    shot = client.request("capture.frame", {
        "display_id": f"windows:{monitor['connector']}", "topology_id": snapshot["topology_id"],
        "session_id": "w6e", **grant, "timeout_ms": 8000, "freshness": "after_request",
    })
    (work / "capture.png").write_bytes(shot.binary)
    report("capture saved", str(work / "capture.png"))
    tray = client.request("accessibility.windows", grant).result
    report("windows listed while the frame is up", [row["window"] for row in tray["windows"]])
    user32.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
    user32.FindWindowW.restype = W.HWND
    user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    tray = user32.FindWindowW("pcbridgeTray", None)
    report("tray window", tray)
    if tray:
        user32.PostMessageW(tray, 0x8000 + 1, 0, 0x0202)  # WM_APP+1, WM_LBUTTONUP
        time.sleep(0.8)
        press(0x28)  # Down: the first item
        time.sleep(0.2)
        press(0x0D)  # Enter
        time.sleep(1.5)
        report("Lock now ran", marker.read_text().strip() if marker.exists() else None)
    if hold:
        print(f"== holding the frame {hold} s", flush=True)
        time.sleep(hold)
    revoked = time.perf_counter()
    store.revoke()
    try:
        frame.wait(timeout=5)
        ended = round(1000 * (time.perf_counter() - revoked), 1)
    except subprocess.TimeoutExpired:
        ended = None
        frame.kill()
    report("after revoke", {"process_ended_ms": ended, "exit": frame.returncode,
                            "stderr": frame.stderr.read().strip()[-200:],
                            "record_left": record_path.exists()})
    client.close()


def lock_part(binary: Path, seconds: int) -> None:
    _work, _store, _token, grant, client = setup(binary)
    snapshot = client.request("display.snapshot", {}).result
    monitor = snapshot["monitors"][0]
    keyboard = {**grant, "hold_max_seconds": 0}
    client.request("input.keyboard.key_down", {**keyboard, "combo": "shift"})
    report("held before the lock", client.request("input.keyboard.held", {}).result["held"])
    last = None
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            client.request("capture.frame", {
                "display_id": f"windows:{monitor['connector']}", "topology_id": snapshot["topology_id"],
                "session_id": "w6e", **grant, "timeout_ms": 2000, "freshness": "after_request",
            })
            state = "captured"
        except Exception as exc:  # noqa: BLE001 - the refusal is what is measured
            state = getattr(exc, "code", type(exc).__name__)
        if state != last:
            held = client.request("input.keyboard.held", {}).result["held"]
            report(f"{time.strftime('%H:%M:%S')}", {"state": state, "held": held})
            last = state
        time.sleep(0.5)
    client.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("part", choices=["idle", "frame", "lock"])
    parser.add_argument("--hold", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    if args.part == "idle":
        idle_part(args.binary)
    elif args.part == "frame":
        frame_part(args.binary, args.hold)
    else:
        lock_part(args.binary, args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
