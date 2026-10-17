"""Check the user's own login password through PAM, before the remote password changes.

Creating or replacing the remote password hands out remote access, so it
first asks for the password of the account pcbridge runs as. It is checked
through PAM with libpam directly (ctypes), not through sudo:

  * a user without sudo rights must still manage their own remote access;
  * `NOPASSWD` sudo would skip the question, and sudo's 15-minute cache
    would skip it again.

The PAM service is `login`, which every supported distribution has; unlike
`su` it does not start with `pam_rootok`. As an unprivileged user, pam_unix
checks only the caller's own password (through the setuid `unix_chkpwd`).
Wrong answers go through the same stack as the login screen: `pam_faildelay`
waits a few seconds, and where `pam_faillock` is on (Arch) they count
towards its lockout.

This is a guard for pcbridge's own paths (the terminal UI, `pcbridge set`,
`remote start`, setup). It is not a boundary against someone who already
has a shell as the user: `config.toml` is the user's file and anyone who can
write it can store a hash of their own choosing.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import getpass
import os
import pwd

SERVICE = "login"

PAM_SUCCESS = 0
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2


class LoginCheckError(Exception):
    """The login password could not be checked at all (no libpam)."""


def user() -> str:
    """The account pcbridge runs as."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return getpass.getuser()


class _Message(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _Response(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


_CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_Message)),
                         ctypes.POINTER(ctypes.POINTER(_Response)), ctypes.c_void_p)


class _Conv(ctypes.Structure):
    _fields_ = [("conv", _CONV), ("appdata_ptr", ctypes.c_void_p)]


def _libs() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    name = ctypes.util.find_library("pam") or "libpam.so.0"
    try:
        pam = ctypes.CDLL(name)
    except OSError as exc:
        raise LoginCheckError(f"libpam is not available ({exc}); the login password cannot be "
                              "checked, so the remote password was not changed.") from None
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
    libc.calloc.restype = ctypes.c_void_p
    libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    libc.strdup.restype = ctypes.c_void_p
    libc.strdup.argtypes = [ctypes.c_char_p]
    pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_Conv),
                              ctypes.POINTER(ctypes.c_void_p)]
    pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return pam, libc


def verify(password: str) -> bool:
    """True when `password` is the login password of the account pcbridge runs as.

    An empty password is refused here, before PAM: with `nullok` an account
    without a password would otherwise pass with nothing typed.
    Raises LoginCheckError when PAM itself is missing.
    """
    if not password:
        return False
    pam, libc = _libs()
    secret = password.encode("utf-8")
    name = user().encode("utf-8")

    @_CONV
    def conversation(n, messages, responses, _data):
        # PAM frees the responses, so they must come from malloc.
        block = libc.calloc(n, ctypes.sizeof(_Response))
        if not block:
            return 5  # PAM_BUF_ERR
        answers = ctypes.cast(block, ctypes.POINTER(_Response))
        for i in range(n):
            style = messages[i].contents.msg_style
            if style == PAM_PROMPT_ECHO_OFF:
                answers[i].resp = libc.strdup(secret)
            elif style == PAM_PROMPT_ECHO_ON:
                answers[i].resp = libc.strdup(name)
        responses[0] = answers
        return PAM_SUCCESS

    handle = ctypes.c_void_p()
    conv = _Conv(conversation, None)
    rc = pam.pam_start(SERVICE.encode(), name, ctypes.byref(conv), ctypes.byref(handle))
    if rc != PAM_SUCCESS:
        return False
    try:
        rc = pam.pam_authenticate(handle, 0)
    finally:
        pam.pam_end(handle, rc)
    return rc == PAM_SUCCESS
