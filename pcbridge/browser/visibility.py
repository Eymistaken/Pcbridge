"""Show and hide the pcbridge browser's window, through the compositor.

Headless or headed is fixed when a browser starts, and starting it again
loses page state, so the browser always runs headed and is hidden instead
(ROADMAP, "Show and hide the browser window on demand"). A Wayland client
cannot un-minimize itself, so the compositor does it:

    GNOME       the pcbridge GNOME Shell extension (BrowserState,
                SetBrowserVisible): minimized while hidden. Mutter 14 cannot
                set another client's skip-taskbar, so the window stays in the
                task list as its own "pcbridge-browser" entry.
    KDE Plasma  a one-shot KWin script: minimized and off the task bar and
                the window switcher while hidden.
    Hyprland    the special workspace `special:pcbridge`.

Every backend finds the windows by the browser's process id, never by name,
so the user's own Chrome or Brave windows are never touched. Nothing here
sends input; like the browser tools it needs no desktop grant.

The wanted state lives in `<state>/browser/view.json` ({"visible": bool}):
the GNOME extension reads it when a browser window is created, so a browser
that starts while it should be hidden is minimized before it is shown.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

BUS = "io.github.eymistaken.Pcbridge.WindowFocus"
PATH = "/io/github/eymistaken/Pcbridge/WindowFocus"
SPECIAL = "special:pcbridge"


@dataclass
class View:
    found: bool
    visible: bool = False
    focused: bool = False
    backend: str = ""
    note: str = ""

    def describe(self) -> str:
        if not self.found:
            return self.note or "No pcbridge browser window was found."
        where = "on screen" if self.visible else "hidden"
        focus = ", focused" if self.focused and self.visible else ""
        return f"The pcbridge browser is {where}{focus} (through {self.backend})."


def write_wanted(directory: Path, visible: bool) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = directory / "view.json.tmp"
    tmp.write_text(json.dumps({"visible": bool(visible)}))
    tmp.chmod(0o600)
    os.replace(tmp, directory / "view.json")


def wanted(directory: Path) -> bool:
    try:
        return json.loads((directory / "view.json").read_text()).get("visible") is True
    except (OSError, ValueError, AttributeError):
        return False


class Gnome:
    name = "the GNOME Shell extension"

    def _call(self, method: str, *args: str) -> View:
        try:
            proc = subprocess.run(
                ["busctl", "--user", "--timeout=2s", "call", BUS, PATH, BUS, method, *args],
                capture_output=True, text=True, timeout=4)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return View(False, note=f"The GNOME Shell extension did not answer ({exc}).")
        words = proc.stdout.split()
        if proc.returncode != 0 or words[:1] != ["bbb"] or len(words) != 4:
            return View(False, note=(
                "The pcbridge GNOME Shell extension does not offer browser windows: it is "
                "missing or older than this pcbridge. Run `pcbridge setup`, then log out and "
                "back in so GNOME Shell loads the new extension."))
        found, visible, focused = (w == "true" for w in words[1:])
        return View(found, visible, focused, self.name,
                    "" if found else "The browser has no window yet.")

    def state(self, pid: int) -> View:
        return self._call("BrowserState")

    def set(self, pid: int, visible: bool, focus: bool) -> View:
        return self._call("SetBrowserVisible", "bb", str(visible).lower(), str(focus).lower())


class KWin:
    name = "a KWin script"

    def _call(self, request: dict) -> View:
        from ..desktop import apps

        reply = apps._kwin(request)
        if reply is None:
            return View(False, note="KWin did not answer the window request.")
        return View(bool(reply.get("found")), bool(reply.get("visible")),
                    bool(reply.get("focused")), self.name,
                    "" if reply.get("found") else "The browser has no window yet.")

    def state(self, pid: int) -> View:
        return self._call({"cmd": "browser", "pid": pid})

    def set(self, pid: int, visible: bool, focus: bool) -> View:
        return self._call({"cmd": "browser", "pid": pid, "visible": visible, "focus": focus})


class Hyprland:
    name = "Hyprland IPC"

    def _hyprctl(self, *args: str) -> subprocess.CompletedProcess:
        from ..desktop import session

        env = dict(os.environ)
        instance = session.hyprland_instance(env)
        if instance is None:
            raise OSError("no unambiguous Hyprland instance matches this session")
        return subprocess.run(["hyprctl", "-i", instance["instance"], *args],
                              capture_output=True, text=True, timeout=4)

    def _clients(self, pid: int) -> list[dict]:
        proc = self._hyprctl("-j", "clients")
        return [c for c in json.loads(proc.stdout or "[]") if c.get("pid") == pid]

    def state(self, pid: int) -> View:
        try:
            clients = self._clients(pid)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return View(False, note=f"Hyprland did not answer ({exc}).")
        if not clients:
            return View(False, backend=self.name, note="The browser has no window yet.")
        visible = any(not str((c.get("workspace") or {}).get("name", "")).startswith("special")
                      for c in clients)
        focused = any(c.get("focusHistoryID") == 0 for c in clients)
        return View(True, visible, focused, self.name)

    def _dispatch(self, mode: str, action: str, address: str, workspace: str = "") -> bool:
        """One dispatcher for one validated window address, in the syntax of
        the active config provider (Lua or hyprlang; measured)."""
        if not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", address):
            return False
        if mode == "lua":
            call = {
                "move": f'hl.dsp.window.move({{window="address:{address}", '
                        f'workspace="{workspace}", follow=false}})',
                "focus": f'hl.dsp.focus({{window="address:{address}"}})',
            }[action]
            args = ["dispatch", call]
        else:
            args = {"move": ["dispatch", "movetoworkspacesilent", f"{workspace},address:{address}"],
                    "focus": ["dispatch", "focuswindow", f"address:{address}"]}[action]
        proc = self._hyprctl(*args)
        return proc.returncode == 0 and proc.stdout.strip() == "ok"

    def set(self, pid: int, visible: bool, focus: bool) -> View:
        from ..desktop import hyprland

        try:
            _instance, mode = hyprland.focus_context()
            clients = self._clients(pid)
            target = SPECIAL
            if visible:
                active = json.loads(self._hyprctl("-j", "activeworkspace").stdout or "{}")
                target = str(active.get("id", "")) or "e+0"
            for c in clients:
                self._dispatch(mode, "move", str(c.get("address", "")), target)
                if visible and focus:
                    self._dispatch(mode, "focus", str(c.get("address", "")))
        except (OSError, subprocess.TimeoutExpired, ValueError, hyprland.HyprlandIPCError) as exc:
            return View(False, note=f"Hyprland did not take the request ({exc}).")
        return self.state(pid)


class Unsupported:
    name = "nothing"

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def state(self, pid: int) -> View:
        return View(False, note=f"Showing and hiding the browser needs GNOME, KDE Plasma or "
                                f"Hyprland; this session is {self.kind or 'unknown'}.")

    set = lambda self, pid, visible, focus: self.state(pid)  # noqa: E731


def for_session():
    from ..desktop import session

    try:
        kind = session.desktop_kind()
    except Exception:  # noqa: BLE001 -- no desktop: nothing to show or hide
        kind = ""
    if kind == session.GNOME:
        return Gnome()
    if kind == session.KDE:
        return KWin()
    if kind == session.HYPRLAND:
        return Hyprland()
    return Unsupported(kind)
