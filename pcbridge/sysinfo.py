"""What system_status reports about the machine, per operating system.

Linux runs the commands system_status always ran (hostnamectl, uptime,
/proc/loadavg, free, df) through bash. Windows reads the same facts through
the API: the uptime from GetTickCount64, memory from GlobalMemoryStatusEx,
the drives from os.listdrives and their usage from shutil.disk_usage.
Windows has no load average.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"


def _sh(command: str) -> str:
    # LC_ALL=C: under a Turkish locale `free` prints "Bellek:", not "Mem:",
    # and the memory line came back empty (measured 2026-11-15); df's
    # headers would be Turkish too, and a model reads this text.
    try:
        return subprocess.run(["bash", "-lc", command], capture_output=True, text=True,
                              timeout=15, env={**os.environ, "LC_ALL": "C"}).stdout.strip()
    except Exception:  # noqa: BLE001 - a missing fact must not fail the status
        return ""


def _size(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}T"


def _windows_memory() -> str:
    import ctypes
    import ctypes.wintypes as W

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", W.DWORD), ("dwMemoryLoad", W.DWORD), ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64), ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return ""
    used = status.ullTotalPhys - status.ullAvailPhys
    return f"{_size(used)} / {_size(status.ullTotalPhys)}"


def _windows_uptime() -> str:
    import ctypes

    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_uint64
    minutes = int(ctypes.windll.kernel32.GetTickCount64() // 60000)
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    parts = [f"{days} days" if days else "", f"{hours} hours" if hours else "", f"{minutes} minutes"]
    return "up " + ", ".join(p for p in parts if p)


def _windows_disks() -> str:
    rows = [f"{'drive':<8}{'size':>9}{'used':>9}{'avail':>9}{'use%':>6}"]
    for drive in os.listdrives():
        try:
            usage = shutil.disk_usage(drive)
        except OSError:
            continue  # an empty card reader or a lost network drive
        percent = round(usage.used / usage.total * 100) if usage.total else 0
        rows.append(f"{drive:<8}{_size(usage.total):>9}{_size(usage.used):>9}{_size(usage.free):>9}{percent:>5}%")
    return "\n".join(rows)


def facts() -> dict[str, str]:
    """host, uptime, load, memory, disks: each a short text, "" when unknown."""
    if not IS_WINDOWS:
        return {
            "host": _sh("hostnamectl --static 2>/dev/null || hostname"),
            "uptime": _sh("uptime -p"),
            "load": _sh("cut -d' ' -f1-3 /proc/loadavg"),
            "memory": _sh("free -h | awk '/Mem:/ {print $3 \" / \" $2}'"),
            "disks": _sh("df -h -x tmpfs -x devtmpfs --output=target,size,used,avail,pcent | head -12"),
        }
    return {
        "host": socket.gethostname(),
        "uptime": _windows_uptime(),
        "load": "not reported on Windows",
        "memory": _windows_memory(),
        "disks": _windows_disks(),
    }


def gpu() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        return subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader"], capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""
