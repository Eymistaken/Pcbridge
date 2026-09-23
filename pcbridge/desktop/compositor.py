"""Which compositor pcbridge is talking to, and what differs between them.

pcbridge's desktop tools run on two compositors: GNOME (Mutter and GNOME
Shell) and KDE Plasma (KWin), both on Wayland. Most of the stack does not
care which: uinput input, AT-SPI, wl-clipboard and the PipeWire consumer are
the same on both. What differs is a handful of transports, each one
function in the module that owns it (the lock and idle readers in
`safety`, the monitor table in `monitors`, window focus in `apps`); those
functions branch on `current()`. This module holds the names that go with
each compositor, so the capability report and the error texts say what
actually answered.

An unknown desktop (nothing set, nothing on the bus) is treated as GNOME:
the GNOME calls then fail and every desktop tool refuses, which is the same
fail-closed behavior as before KDE support existed.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import session


@dataclass(frozen=True)
class Compositor:
    kind: str
    name: str
    # Backend names in the capability report.
    lock_backend: str
    idle_backend: str
    display_backend: str
    focus_backend: str
    search_backend: str
    # Words for the window.focus limitation text.
    focus_path: str
    search_path: str


GNOME_SHELL = Compositor(
    kind=session.GNOME,
    name="GNOME Shell",
    lock_backend="linux.gnome-screen-saver",
    idle_backend="linux.mutter-idle-monitor",
    display_backend="linux.mutter-display-config",
    focus_backend="linux.gnome-shell-extension",
    search_backend="linux.gnome-search",
    focus_path="the GNOME Shell extension",
    search_path="GNOME search",
)

KWIN = Compositor(
    kind=session.KDE,
    name="KWin",
    lock_backend="linux.freedesktop-screen-saver",
    idle_backend="linux.freedesktop-screen-saver",
    display_backend="linux.kscreen",
    focus_backend="linux.kwin-script",
    search_backend="linux.krunner",
    focus_path="a KWin script",
    search_path="KRunner",
)


def for_kind(kind: str) -> Compositor:
    return KWIN if kind == session.KDE else GNOME_SHELL


def current() -> Compositor:
    """The compositor of this session (GNOME when it cannot be told)."""
    return for_kind(session.desktop_kind())


def is_kde() -> bool:
    return current().kind == session.KDE
