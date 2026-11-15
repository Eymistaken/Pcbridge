"""The operating-system calls that differ between Linux and Windows.

Everything above this package calls one function per job, and the module for
the running system answers it. The POSIX module is the code pcbridge always
had, moved here unchanged; the Windows module gives the same meaning with
Windows primitives:

    lock_fd / unlock_fd       fcntl.flock          LockFileEx / UnlockFileEx
    restrict_path             chmod 0600 / 0700    a protected DACL for the user's SID
    private_problem           mode & 0o077         an ACE for anyone but the user, SYSTEM, Administrators
    restrict_fd               fchmod 0600          nothing: the private directory's DACL is inherited
    fsync_directory           fsync of the dir     nothing: Windows cannot open a directory that way
    owned_by_me               st_uid == getuid     the owner SID is the user's
    current_user              pwd                  GetUserNameW
    pid_alive                 kill(pid, 0)         OpenProcess + GetExitCodeProcess
    process_start_identity    /proc start ticks    GetProcessTimes creation time
    process_started_at        /proc + btime        the creation time as epoch seconds
    process_owned_by_me       /proc uid            the process token's user SID
    terminate_group           killpg               taskkill /T, then taskkill /T /F
    exec_replace              os.execv             run the child, exit with its code

Never call `os.kill(pid, 0)` to test a process on Windows: there signal 0 is
CTRL_C_EVENT, and it interrupts the process group instead of probing it.
"""

from __future__ import annotations

import os
import sys

IS_WINDOWS = sys.platform == "win32"

# os.O_NOFOLLOW does not exist on Windows; callers that refuse symlinks there
# check for a reparse point themselves (see `is_link_or_reparse`).
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

if IS_WINDOWS:
    from ._windows import (  # noqa: F401
        current_user,
        exec_replace,
        fsync_directory,
        is_link_or_reparse,
        lock_fd,
        owned_by_me,
        pid_alive,
        private_problem,
        process_owned_by_me,
        process_start_identity,
        process_started_at,
        restrict_fd,
        restrict_path,
        terminate_group,
        unlock_fd,
    )
else:
    from ._posix import (  # noqa: F401
        current_user,
        exec_replace,
        fsync_directory,
        is_link_or_reparse,
        lock_fd,
        owned_by_me,
        pid_alive,
        private_problem,
        process_owned_by_me,
        process_start_identity,
        process_started_at,
        restrict_fd,
        restrict_path,
        terminate_group,
        unlock_fd,
    )

__all__ = [
    "IS_WINDOWS",
    "O_NOFOLLOW",
    "current_user",
    "exec_replace",
    "fsync_directory",
    "is_link_or_reparse",
    "lock_fd",
    "owned_by_me",
    "pid_alive",
    "private_problem",
    "process_owned_by_me",
    "process_start_identity",
    "process_started_at",
    "restrict_fd",
    "restrict_path",
    "terminate_group",
    "unlock_fd",
]
