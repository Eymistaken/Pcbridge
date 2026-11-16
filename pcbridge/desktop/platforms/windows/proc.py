"""Processes and the session on Windows, through the Win32 API (ctypes).

The Linux readers of the native helper's records check the writer through
`/proc`: its start time, its parent, its executable, its session. These
are the same questions answered by Windows: the process creation time
(100 ns since 1601, what the helper writes as `writer_start_ticks`), the
parent from a process snapshot, the image path, and the Remote Desktop
Services session id.

The lock state is read the way the helper reads it (`desktop_state.rs`):
the session's lock flag and whether the input desktop opens, which must
agree; anything else is unknown.

Every handle parameter has its `argtypes`: on 64-bit Windows a call
without them fails quietly (measured in W0).
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as W
from pathlib import Path

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_WTS_CURRENT_SERVER_HANDLE = W.HANDLE(0)
_WTS_CURRENT_SESSION = 0xFFFFFFFF
_WTS_SESSION_INFO_EX = 25
_DESKTOP_READOBJECTS = 0x0001

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

    _kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    _kernel32.OpenProcess.restype = W.HANDLE
    _kernel32.CloseHandle.argtypes = [W.HANDLE]
    _kernel32.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    _kernel32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR,
                                                     ctypes.POINTER(W.DWORD)]
    _kernel32.ProcessIdToSessionId.argtypes = [W.DWORD, ctypes.POINTER(W.DWORD)]
    _kernel32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = W.HANDLE
    _kernel32.Process32FirstW.argtypes = [W.HANDLE, ctypes.c_void_p]
    _kernel32.Process32NextW.argtypes = [W.HANDLE, ctypes.c_void_p]
    _user32.OpenInputDesktop.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    _user32.OpenInputDesktop.restype = W.HANDLE
    _user32.CloseDesktop.argtypes = [W.HANDLE]
    _wtsapi32.WTSQuerySessionInformationW.argtypes = [
        W.HANDLE, W.DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(W.DWORD)]
    _wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", W.DWORD),
                ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", W.DWORD),
                ("szExeFile", W.WCHAR * 260)]


def _open(pid: int):
    if sys.platform != "win32" or pid <= 0:
        return None
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return handle or None


def start_ticks(pid: int) -> int | None:
    """The process's creation time in 100 ns units since 1601, or None."""
    handle = _open(pid)
    if handle is None:
        return None
    try:
        times = [W.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        created = times[0]
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def image_path(pid: int) -> Path | None:
    """The executable of `pid`, or None."""
    handle = _open(pid)
    if handle is None:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = W.DWORD(len(buffer))
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value)
    finally:
        _kernel32.CloseHandle(handle)


def session_id(pid: int | None = None) -> int | None:
    """The Remote Desktop Services session of `pid` (this process by default)."""
    if sys.platform != "win32":
        return None
    session = W.DWORD()
    if not _kernel32.ProcessIdToSessionId(os.getpid() if pid is None else pid, ctypes.byref(session)):
        return None
    return int(session.value)


def parent_pid(pid: int) -> int | None:
    """The parent of `pid`, from a process snapshot."""
    if sys.platform != "win32":
        return None
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == W.HANDLE(-1).value:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return None
    finally:
        _kernel32.CloseHandle(snapshot)


def same_file(left: Path | None, right: Path) -> bool:
    """Whether two paths name the same file (case and 8.3 names aside)."""
    if left is None:
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _session_lock_flag() -> bool | None:
    """The lock flag of this session (`WTSSessionInfoEx`): True locked."""
    buffer = ctypes.c_void_p()
    size = W.DWORD()
    if not _wtsapi32.WTSQuerySessionInformationW(_WTS_CURRENT_SERVER_HANDLE, _WTS_CURRENT_SESSION,
                                                 _WTS_SESSION_INFO_EX, ctypes.byref(buffer),
                                                 ctypes.byref(size)):
        return None
    try:
        if size.value < 20:
            return None
        level = ctypes.c_uint32.from_address(buffer.value).value
        if level != 1:
            return None
        # WTSINFOEX_LEVEL1_W starts at offset 8 (its union holds LARGE_INTEGERs,
        # measured in W0); SessionId and SessionState come first, then
        # SessionFlags.
        flags = ctypes.c_int32.from_address(buffer.value + 8 + 8).value
        return {0: True, 1: False}.get(flags)
    finally:
        _wtsapi32.WTSFreeMemory(buffer)


def _input_desktop_opens() -> bool:
    desktop = _user32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
    if not desktop:
        return False
    _user32.CloseDesktop(desktop)
    return True


def screen_locked() -> bool | None:
    """True locked, False unlocked, None unknown: both signals must agree."""
    if sys.platform != "win32":
        return None
    flag = _session_lock_flag()
    opens = _input_desktop_opens()
    if flag is False and opens:
        return False
    if flag is True and not opens:
        return True
    return None
