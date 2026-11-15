"""Windows implementations, through ctypes only (no pywin32).

Every handle parameter has its argtypes: on 64-bit Windows ctypes otherwise
passes a C int and the call fails without saying so (measured in W0, see
docs/dev/measured-facts.md, "Windows 11 baseline for the port").
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import errno
import functools
import msvcrt
import os
import stat
import subprocess
import sys
from typing import NoReturn

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
ERROR_LOCK_VIOLATION = 33
ERROR_IO_PENDING = 997
LOCKFILE_FAIL_IMMEDIATELY = 0x1
LOCKFILE_EXCLUSIVE_LOCK = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x1
DACL_SECURITY_INFORMATION = 0x4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
WAIT_TIMEOUT = 0x102
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CREATE_NO_WINDOW = 0x08000000


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", W.DWORD), ("OffsetHigh", W.DWORD), ("hEvent", W.HANDLE)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", W.DWORD), ("dwHighDateTime", W.DWORD)]


kernel32.LockFileEx.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.POINTER(OVERLAPPED)]
kernel32.LockFileEx.restype = W.BOOL
kernel32.UnlockFileEx.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.POINTER(OVERLAPPED)]
kernel32.UnlockFileEx.restype = W.BOOL
kernel32.GetCurrentProcess.restype = W.HANDLE
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel32.OpenProcess.restype = W.HANDLE
kernel32.CloseHandle.argtypes = [W.HANDLE]
kernel32.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
kernel32.WaitForSingleObject.restype = W.DWORD
kernel32.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(FILETIME)] * 4
kernel32.GetProcessTimes.restype = W.BOOL
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
advapi32.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
advapi32.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD,
                                         ctypes.POINTER(W.DWORD)]
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.LPWSTR)]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    W.LPCWSTR, W.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
advapi32.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.BOOL),
                                               ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(W.BOOL)]
advapi32.GetSecurityDescriptorOwner.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                                ctypes.POINTER(W.BOOL)]
advapi32.SetNamedSecurityInfoW.argtypes = [W.LPWSTR, ctypes.c_int, W.DWORD, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
advapi32.SetNamedSecurityInfoW.restype = W.DWORD
advapi32.GetNamedSecurityInfoW.argtypes = [W.LPCWSTR, ctypes.c_int, W.DWORD,
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.POINTER(ctypes.c_void_p)]
advapi32.GetNamedSecurityInfoW.restype = W.DWORD
advapi32.GetUserNameW.argtypes = [W.LPWSTR, ctypes.POINTER(W.DWORD)]


# ------------------------------------------------------------------ locking

def lock_fd(fd: int, *, exclusive: bool = True, blocking: bool = True) -> None:
    """Lock the first byte of an open file. Raises BlockingIOError when not
    blocking and another process holds it. The lock files pcbridge uses are
    never read, so the mandatory byte-range lock blocks nothing else."""
    flags = (LOCKFILE_EXCLUSIVE_LOCK if exclusive else 0) | (0 if blocking else LOCKFILE_FAIL_IMMEDIATELY)
    overlapped = OVERLAPPED()
    if not kernel32.LockFileEx(msvcrt.get_osfhandle(fd), flags, 0, 1, 0, ctypes.byref(overlapped)):
        error = ctypes.get_last_error()
        if error in (ERROR_LOCK_VIOLATION, ERROR_IO_PENDING):
            raise BlockingIOError(errno.EAGAIN, "the file is locked by another process")
        raise ctypes.WinError(error)


def unlock_fd(fd: int) -> None:
    overlapped = OVERLAPPED()
    if not kernel32.UnlockFileEx(msvcrt.get_osfhandle(fd), 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())


# ------------------------------------------------------------ the user's SID

def _token_user_sid(token: W.HANDLE) -> str:
    size = W.DWORD()
    advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi32.GetTokenInformation(token, TOKEN_USER, buffer, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    return _sid_string(sid_pointer)


def _sid_string(sid_pointer: int | None) -> str:
    text = W.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value or ""
    finally:
        kernel32.LocalFree(text)


@functools.lru_cache(maxsize=1)
def user_sid() -> str:
    """The SID of the user this process runs as, e.g. S-1-5-21-...-1001."""
    token = W.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _token_user_sid(token)
    finally:
        kernel32.CloseHandle(token)


# ------------------------------------------------------------ private files

def restrict_fd(fd: int) -> None:
    """Nothing to do: pcbridge creates its private files inside directories
    that `restrict_path` gave an inheritable owner-only DACL."""


def restrict_path(path: str | os.PathLike[str], *, directory: bool = False) -> None:
    """Make this user the owner of `path` and give it a protected DACL that
    allows only this user: the Windows counterpart of mode 0700 / 0600. A
    directory's entry is inherited by the files and directories created in
    it. The owner is set too because an elevated process creates objects
    owned by the Administrators group (measured over SSH, which logs an
    administrator in elevated), and `owned_by_me` must hold afterwards."""
    inherit = "OICI" if directory else ""
    sid = user_sid()
    sddl = f"O:{sid}D:P(A;{inherit};FA;;;{sid})"
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted = W.BOOL(), W.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                  ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        owner = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = advapi32.SetNamedSecurityInfoW(
            os.fspath(path), SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            owner, None, dacl, None)
        if result:
            raise ctypes.WinError(result)
    finally:
        kernel32.LocalFree(descriptor)


def fsync_directory(path: str | os.PathLike[str]) -> None:
    """Nothing to do: NTFS journals the rename itself, and a directory cannot
    be opened with os.open."""


def owned_by_me(path: str | os.PathLike[str] | os.stat_result) -> bool:
    """Whether this user owns `path`. Needs the path: Windows stat results
    carry no owner. An elevated process creates files owned by the
    Administrators group; those do not count as the user's until
    `restrict_path` takes them over."""
    if isinstance(path, os.stat_result):
        raise TypeError("owned_by_me needs a path on Windows")
    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(os.fspath(path), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                            ctypes.byref(owner), None, None, None, ctypes.byref(descriptor))
    if result:
        return False
    try:
        return _sid_string(owner.value) == user_sid()
    finally:
        kernel32.LocalFree(descriptor)


def is_link_or_reparse(path: str | os.PathLike[str]) -> bool:
    """A symlink, junction or any other reparse point: the Windows ways to
    make one path lead somewhere else."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def current_user() -> str:
    size = W.DWORD(257)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not advapi32.GetUserNameW(buffer, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


# --------------------------------------------------------------- processes

def _open_process(pid: int, access: int) -> W.HANDLE | None:
    handle = kernel32.OpenProcess(access, False, int(pid))
    return handle or None


def pid_alive(pid: int) -> bool:
    handle = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE)
    if handle is None:
        # Access denied means the process exists but belongs to someone
        # else; anything else (invalid parameter) means it is gone.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def process_start_identity(pid: int) -> str:
    """The process creation time in 100 ns units: it does not repeat when
    Windows reuses a PID."""
    handle = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        raise ProcessLookupError(f"process {pid} cannot be opened")
    try:
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            raise ValueError("process start identity is unavailable")
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


_FILETIME_EPOCH = 116444736000000000  # 1970-01-01 in 100 ns units since 1601


def process_started_at(pid: int) -> float | None:
    """When a process started (epoch seconds); None if unknown."""
    try:
        return (int(process_start_identity(pid)) - _FILETIME_EPOCH) / 1e7
    except (OSError, ValueError):
        return None


def process_owned_by_me(pid: int) -> bool:
    handle = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        return False
    try:
        token = W.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return False
        try:
            return _token_user_sid(token) == user_sid()
        except OSError:
            return False
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(handle)


def terminate_group(pid: int, *, force: bool = False, own_group: bool = False) -> None:
    """End `pid` and its children with taskkill /T, politely (a close request)
    or with force. Raises ProcessLookupError when the process is gone.
    `own_group` is accepted for the POSIX signature; the tree is always the
    unit here. Job Objects replace this for jobs (W3)."""
    del own_group
    argv = ["taskkill", "/T", "/PID", str(int(pid))]
    if force:
        argv.insert(1, "/F")
    result = subprocess.run(argv, capture_output=True, creationflags=CREATE_NO_WINDOW, check=False)
    if result.returncode == 128:  # "The process ... not found"
        raise ProcessLookupError(pid)


def exec_replace(argv: list[str]) -> NoReturn:
    """Windows has no exec that keeps the process: run the child with the
    same standard handles and exit with its code, so the caller (an MCP
    client watching this process) sees the child's lifetime."""
    sys.exit(subprocess.call(argv))
