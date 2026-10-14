"""What this machine is, found once: the facts the install plan rests on.

`pcbridge setup` (and `pcbridge doctor --fix`) decide from this what to
install and which tools to offer. The install script hands over to setup
instead of detecting anything in shell, so the logic lives here only.

Run over SSH, the process has none of the graphical session's variables
(`XDG_SESSION_TYPE` is "tty", `XDG_CURRENT_DESKTOP` is unset), so the
session is read from logind instead: the user's local graphical session
gives its type, and the user manager's environment (or the session's
`Desktop` property) names the desktop.

Every probe is read-only, has a short timeout, and reports an unknown piece
as "" instead of raising.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import distro as distrolib
from .desktop.session import GNOME, HYPRLAND, KDE, UNKNOWN, _kind_from_tokens

MIN_PYTHON = (3, 12)

# token in XDG_CURRENT_DESKTOP / XDG_SESSION_DESKTOP (lowercase) -> the name
# people know the desktop by.
DESKTOP_NAMES = {
    "gnome": "GNOME", "ubuntu": "GNOME", "zorin": "GNOME", "pop": "GNOME",
    "kde": "KDE Plasma", "plasma": "KDE Plasma", "plasmawayland": "KDE Plasma",
    "hyprland": "Hyprland", "x-cinnamon": "Cinnamon", "cinnamon": "Cinnamon",
    "xfce": "Xfce", "mate": "MATE", "lxqt": "LXQt", "lxde": "LXDE",
    "budgie": "Budgie", "budgie-desktop": "Budgie", "sway": "Sway",
    "pantheon": "Pantheon", "deepin": "Deepin", "dde": "Deepin",
    "unity": "Unity", "i3": "i3", "cosmic": "COSMIC", "niri": "niri",
}

# desktop name -> the command that prints its version without a display.
VERSION_COMMANDS = {
    "GNOME": ["gnome-shell", "--version"],
    "KDE Plasma": ["plasmashell", "--version"],
    "Hyprland": ["Hyprland", "--version"],
    "Cinnamon": ["cinnamon", "--version"],
    "Xfce": ["xfce4-session", "--version"],
    "MATE": ["mate-session", "--version"],
    "LXQt": ["lxqt-session", "--version"],
    "Budgie": ["budgie-desktop", "--version"],
    "Sway": ["sway", "--version"],
}

SUPPORTED_KINDS = (GNOME, KDE, HYPRLAND)


def _run(cmd: list[str], timeout: float = 3.0, env: dict[str, str] | None = None) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _properties(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


@dataclass(frozen=True)
class GraphicalSession:
    """The user's graphical session as logind and the user manager see it."""

    type: str = ""          # "wayland", "x11", or "" when there is none
    desktop: str = ""       # XDG_CURRENT_DESKTOP, or the session's Desktop property
    language: str = ""      # LANG of that session
    source: str = "none"    # "environment", "loginctl" or "none"


def session_from_environment(env: dict[str, str]) -> GraphicalSession | None:
    """The session this process runs in, when it is a graphical one."""
    kind = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    desktop = (env.get("XDG_CURRENT_DESKTOP") or env.get("XDG_SESSION_DESKTOP") or "").strip()
    if kind in ("wayland", "x11") and desktop:
        return GraphicalSession(kind, desktop, env.get("LANG", ""), "environment")
    return None


def session_from_logind(uid: int | None = None) -> GraphicalSession:
    """The user's local graphical session, read through `loginctl`.

    The active one wins over a background one; a remote (XRDP, VNC login)
    session is not the desktop pcbridge would drive.
    """
    uid = os.getuid() if uid is None else uid
    listed = _properties(_run(["loginctl", "show-user", str(uid), "-p", "Sessions", "-p", "Display"]))
    ids = listed.get("Sessions", "").split()
    display = listed.get("Display", "")
    if display and display in ids:
        ids.remove(display)
        ids.insert(0, display)
    best: dict[str, str] | None = None
    for sid in ids:
        props = _properties(_run(["loginctl", "show-session", sid, "-p", "Type", "-p", "Desktop",
                                  "-p", "Class", "-p", "Remote", "-p", "Active"]))
        if props.get("Type") not in ("wayland", "x11") or props.get("Class", "user") != "user":
            continue
        if props.get("Remote") == "yes":
            continue
        if best is None or (props.get("Active") == "yes" and best.get("Active") != "yes"):
            best = props
    if best is None:
        return GraphicalSession()
    # `systemctl --user` finds the user manager through XDG_RUNTIME_DIR, which
    # `su` and `env -i` leave out; SSH logins have it.
    env = dict(os.environ)
    runtime = Path(f"/run/user/{uid}")
    if not env.get("XDG_RUNTIME_DIR") and runtime.is_dir():
        env["XDG_RUNTIME_DIR"] = str(runtime)
    manager = _properties(_run(["systemctl", "--user", "show-environment"], env=env))
    desktop = manager.get("XDG_CURRENT_DESKTOP", "") or best.get("Desktop", "")
    return GraphicalSession(best["Type"], desktop, manager.get("LANG", ""), "loginctl")


def graphical_session(env: dict[str, str] | None = None) -> GraphicalSession:
    """This process's graphical session, or the user's one when it has none (SSH)."""
    e = dict(os.environ if env is None else env)
    return session_from_environment(e) or session_from_logind()


def desktop_name(desktop: str) -> str:
    """"GNOME" for "zorin:GNOME", "Cinnamon" for "X-Cinnamon"; the raw value otherwise."""
    tokens = [t.strip() for t in desktop.split(":") if t.strip()]
    for token in reversed(tokens):  # "ubuntu:GNOME": the last token is the desktop
        name = DESKTOP_NAMES.get(token.lower())
        if name:
            return name
    return tokens[-1] if tokens else ""


def desktop_version(name: str) -> str:
    """The desktop's version, from its own binary; "" when unknown."""
    cmd = VERSION_COMMANDS.get(name)
    if not cmd:
        return ""
    out = _run(cmd)
    if name == "Xfce":  # "xfce4-session 4.18.1 (Xfce 4.18)"
        m = re.search(r"\(Xfce ([\d.]+)\)", out)
        if m:
            return m.group(1)
    m = re.search(r"(\d+(?:\.\d+)+)", out)
    return m.group(1) if m else ""


def glibc_version() -> tuple[int, ...]:
    """The C library's version, () when it is not glibc (musl)."""
    try:
        raw = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (ValueError, OSError):
        raw = ""
    m = re.search(r"(\d+)\.(\d+)", raw)
    return (int(m.group(1)), int(m.group(2))) if m else ()


def locale_language(*candidates: str) -> str:
    """The two-letter language of the first real locale: "tr" for tr_TR.UTF-8."""
    values = [c for c in candidates if c]
    values += [os.environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    for path in (Path("/etc/locale.conf"), Path("/etc/default/locale")):
        try:
            values.append(_properties(path.read_text(encoding="utf-8")).get("LANG", "").strip('"'))
        except OSError:
            pass
    for value in values:
        m = re.match(r"([a-z]{2,3})(?:[_.@]|$)", value)
        if m and value not in ("C", "POSIX") and not value.startswith("C."):
            return m.group(1)
    return ""


@dataclass(frozen=True)
class Platform:
    arch: str
    distro_name: str        # "Debian GNU/Linux 12", "Arch Linux"
    distro_id: str
    family: str             # distro.DEBIAN / ARCH / FEDORA, or "" when unknown
    package_manager: str    # "apt-get", "pacman", "dnf", or "" (none found)
    session_type: str       # "wayland", "x11", or ""
    desktop: str            # "GNOME", "Cinnamon", ... or ""
    desktop_kind: str       # session.GNOME / KDE / HYPRLAND / UNKNOWN
    desktop_version: str
    session_source: str     # "environment", "loginctl", "none"
    glibc: tuple[int, ...]
    language: str           # "tr", or ""

    @property
    def desktop_supported(self) -> bool:
        return self.desktop_kind in SUPPORTED_KINDS and self.session_type == "wayland"

    def unsupported_reason(self) -> str:
        """Why desktop control will not work here, "" when it will."""
        if self.desktop_supported:
            return ""
        if not self.session_type:
            return ("no graphical session was found for this user (log in to the desktop "
                    "once, then run `pcbridge setup` again)")
        session = "X11" if self.session_type == "x11" else self.session_type
        name = self.desktop or "this desktop"
        if self.desktop_kind in SUPPORTED_KINDS:
            return f"{name} on {session} is not supported; log in to the {name} Wayland session"
        return f"{name} on {session} is not supported yet"

    def found_line(self) -> str:
        """"Found: Pardus 25 (Debian family) · Cinnamon 6.4 · X11 · x86_64"."""
        fam = distrolib.FAMILY_NAMES.get(self.family)
        parts = [f"{self.distro_name or 'unknown distribution'}"
                 + (f" ({fam} family)" if fam and self.distro_id != self.family else "")]
        if self.session_type:
            parts.append(" ".join(p for p in (self.desktop or "unknown desktop",
                                              self.desktop_version) if p))
            parts.append("Wayland" if self.session_type == "wayland" else
                         "X11" if self.session_type == "x11" else self.session_type)
        else:
            parts.append("no graphical session")
        parts.append(self.arch)
        return "Found: " + " · ".join(parts)


def detect(env: dict[str, str] | None = None, os_release_text: str | None = None) -> Platform:
    """Everything above, measured now (a re-run after an update sees the new state)."""
    fields = distrolib.os_release(os_release_text)
    name = fields.get("NAME", "") or fields.get("PRETTY_NAME", "")
    version = fields.get("VERSION_ID", "")
    if version and version not in name:
        name = f"{name} {version}"
    fam = distrolib.detected_family(os_release_text)
    session = graphical_session(env)
    tokens = [t.strip() for t in session.desktop.split(":") if t.strip()]
    kind = _kind_from_tokens(tokens) if tokens else UNKNOWN
    if kind == UNKNOWN and session.desktop.lower() in ("plasma", "plasmawayland"):
        kind = KDE  # logind's Desktop property says "plasma", not "KDE"
    pretty = desktop_name(session.desktop)
    return Platform(
        arch=platform.machine(),
        distro_name=name,
        distro_id=fields.get("ID", ""),
        family=fam,
        package_manager=distrolib.package_manager(fam) if fam else "",
        session_type=session.type,
        desktop=pretty,
        desktop_kind=kind,
        desktop_version=desktop_version(pretty) if session.type else "",
        session_source=session.source,
        glibc=glibc_version(),
        language=locale_language(session.language),
    )


__all__ = ["MIN_PYTHON", "GraphicalSession", "Platform", "desktop_name", "detect",
           "glibc_version", "graphical_session", "locale_language"]
