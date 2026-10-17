"""The remote password in a terminal: choose or generate it, then show it once.

`pcbridge set auth.password`, `pcbridge remote start` (when there is no
password yet) and the end of `pcbridge setup` share this dialog. The saving
itself is `remotepassword.save()`.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from .. import remotepassword as rp
from .. import settings as S
from . import install as inst


def ask_login(tries: int = 3) -> str | None:
    """The user's login password, checked; None after `tries` wrong answers or an empty one."""
    from .. import loginpw

    inst.say(f"  First, the login password of {loginpw.user()}: whoever sets this password "
             "gets remote access.")
    for _ in range(tries):
        given = getpass.getpass("  Login password: ")
        if not given:
            return None
        if rp.login_ok(given):
            return given
        inst.warn(rp.wrong_login())
    return None


def ask() -> tuple[str, bool] | None:
    """Ask for a generated or a typed password: (password, generated), or None to cancel."""
    inst.say("  1) Generate a passphrase: six words, easy to type on a phone  [default]")
    inst.say(f"  2) Type my own (at least {rp.MIN_LENGTH} characters)")
    inst.say("  q) Cancel")
    while True:
        try:
            choice = input("  Choice [1]: ").strip().lower()
        except EOFError:
            return None
        if choice in ("", "1"):
            return rp.generate(), True
        if choice == "2":
            break
        if choice in ("q", "quit", "n", "no"):
            return None
        inst.say("  Type 1, 2 or q.")
    while True:
        first = getpass.getpass("  New password: ")
        if not first:
            return None
        why = rp.check(first)
        if why:
            inst.warn(why)
            continue
        if getpass.getpass("  Again: ") != first:
            inst.warn("The two entries differ; try again (an empty line cancels).")
            continue
        return first, False


def show_once(password: str, address: str) -> None:
    """Show a generated password next to the remote address until the user says it is saved."""
    inst.say("")
    inst.say(f"  Remote address:  {address}")
    inst.say(f"  Password:        {password}")
    inst.say("")
    inst.warn(rp.ONCE_NOTE)
    if not sys.stdin.isatty():
        return
    while True:
        try:
            answer = input("  Press Enter once it is saved (c and Enter copies it first): ")
        except EOFError:
            return
        if answer.strip().lower() != "c":
            return
        if rp.copy_to_clipboard(password):
            inst.ok("copied to the clipboard")
        else:
            inst.warn("wl-copy is not available; copy it by hand")


def create(path: Path | str, address: str, *, replacing: bool) -> S.SaveResult | None:
    """The whole dialog: choose or generate, save, show once. None when cancelled."""
    if replacing:
        inst.say("A new remote password replaces the old one; remote apps that already "
                 "connected keep their sessions until they are revoked.")
    else:
        inst.say("Remote access needs a password. Remote clients type it on the approval "
                 "page; local clients never use it.")
    try:
        login = ask_login()
    except S.SettingsError as exc:  # no libpam
        inst.fail(str(exc))
        return None
    if login is None:
        inst.say("  Nothing was changed.")
        return None
    picked = ask()
    if picked is None:
        inst.say("  Nothing was changed.")
        return None
    password, generated = picked
    try:
        result = rp.save(password, path, login_password=login)
    except S.SettingsError as exc:
        inst.fail(str(exc))
        return None
    inst.ok("remote password " + ("replaced" if replacing else "created")
            + " (only its hash is stored)")
    if generated:
        show_once(password, address)
    else:
        inst.say(f"  Remote address:  {address}")
        inst.say("  It cannot be read back later, only replaced.")
    return result
