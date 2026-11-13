"""Which compositor pcbridge is talking to, and what differs between them.

pcbridge's desktop tools run on GNOME (Mutter and GNOME Shell), KDE Plasma
(KWin), and Hyprland, all on Wayland, and on Cinnamon (Muffin) and Xfce
(xfwm4) on X11, where the native helper speaks X11 for all of them. Most of
the stack does not care which: uinput input and AT-SPI are the same
everywhere. What differs is a handful of transports, each one
function in the module that owns it (the lock and idle readers in
`safety`, the monitor table in `monitors`, window focus in `apps`); those
functions branch on `current()`. This module holds the names that go with
each compositor, so the capability report and the error texts say what
actually answered.

An unknown desktop has its own descriptor and must fail closed.
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

HYPRLAND = Compositor(
    kind=session.HYPRLAND,
    name="Hyprland",
    lock_backend="linux.hyprland-ipc",
    idle_backend="linux.ext-idle-notifier",
    display_backend="linux.hyprland-ipc",
    focus_backend="linux.hyprland-ipc",
    search_backend="linux.hyprland-ipc",
    focus_path="Hyprland IPC",
    search_path="Hyprland IPC",
)

CINNAMON = Compositor(
    kind=session.CINNAMON,
    name="Cinnamon",
    lock_backend="linux.cinnamon-screensaver+logind",
    idle_backend="linux.x11-screensaver",
    display_backend="linux.x11.randr",
    focus_backend="linux.x11.ewmh",
    search_backend="linux.x11.ewmh",
    focus_path="EWMH window activation",
    search_path="EWMH window activation",
)

XFCE = Compositor(
    kind=session.XFCE,
    name="Xfce",
    lock_backend="linux.xfce-screensaver+logind",
    idle_backend="linux.x11-screensaver",
    display_backend="linux.x11.randr",
    focus_backend="linux.x11.ewmh",
    search_backend="linux.x11.ewmh",
    focus_path="EWMH window activation",
    search_path="EWMH window activation",
)

UNKNOWN = Compositor(
    kind=session.UNKNOWN,
    name="Unknown desktop",
    lock_backend="unavailable",
    idle_backend="unavailable",
    display_backend="unavailable",
    focus_backend="unavailable",
    search_backend="unavailable",
    focus_path="an unsupported compositor",
    search_path="an unsupported compositor",
)


def for_kind(kind: str) -> Compositor:
    return {
        session.GNOME: GNOME_SHELL,
        session.KDE: KWIN,
        session.HYPRLAND: HYPRLAND,
        session.CINNAMON: CINNAMON,
        session.XFCE: XFCE,
    }.get(kind, UNKNOWN)


def current() -> Compositor:
    """The detected compositor, or an explicit unsupported descriptor."""
    session.refresh_session_env()
    return for_kind(session.desktop_kind())


def is_kde() -> bool:
    return current().kind == session.KDE


def is_x11() -> bool:
    """Cinnamon or Xfce: the native helper's X11 backend."""
    return current().kind in session.X11_KINDS
