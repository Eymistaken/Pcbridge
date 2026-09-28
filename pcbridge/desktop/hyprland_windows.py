"""Hyprland window identities from fresh, read-only compositor IPC."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import hyprland
from .errors import DesktopError, ErrorCategory, ErrorCode

BACKEND = "linux.hyprland-ipc"
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{1,16}\Z")
_STABLE_ID = re.compile(r"[0-9]{1,20}\Z")


def _unavailable(message: str) -> DesktopError:
    return DesktopError(
        code=ErrorCode.BACKEND_UNAVAILABLE,
        category=ErrorCategory.CAPABILITY,
        message=message,
        retryable=True,
        suggested_action="Check the selected Hyprland session with pcbridge doctor.",
        permission_scope="os.window",
        backend=BACKEND,
        execution_state="not_started",
    )


@dataclass(frozen=True)
class Window:
    address: str
    stable_id: str
    app: str
    title: str
    app_pid: int
    active: bool = False

    @property
    def ref(self) -> str:
        return f"hyprland:{self.address}"

    @property
    def identity(self) -> tuple[str, str, int, str]:
        # stableId distinguishes address reuse within one application's lifetime.
        return self.address, self.stable_id, self.app_pid, self.app


def _window(raw: object, *, active: bool = False) -> Window:
    if not isinstance(raw, dict):
        raise hyprland.HyprlandIPCError("Hyprland window is not an object")
    address = raw.get("address")
    stable_id = raw.get("stableId", "")
    pid = raw.get("pid")
    if (not isinstance(address, str) or not _ADDRESS.fullmatch(address)
            or int(address, 16) == 0
            or not isinstance(stable_id, str)
            or (stable_id and not _STABLE_ID.fullmatch(stable_id))
            or type(pid) is not int or pid <= 0
            or raw.get("mapped") is not True
            or not isinstance(raw.get("class"), str)
            or not isinstance(raw.get("title"), str)):
        raise hyprland.HyprlandIPCError("Hyprland window identity is incomplete")
    return Window(address.lower(), stable_id, raw["class"], raw["title"], pid, active)


class HyprlandWindowProvider:
    """List and focus observations never use AT-SPI or a guessed keybind."""

    def _focused(self) -> Window | None:
        raw = hyprland._query("activewindow", json_output=True)
        if raw == {}:
            return None
        return _window(raw, active=True)

    def focused_window(self) -> tuple[str, str]:
        try:
            window = self._focused()
        except hyprland.HyprlandIPCError as exc:
            raise _unavailable(str(exc)) from exc
        return (window.app, window.title) if window else ("", "")

    def focused_identity(self) -> str:
        try:
            window = self._focused()
        except hyprland.HyprlandIPCError as exc:
            raise _unavailable(str(exc)) from exc
        if window is None:
            raise _unavailable("Hyprland has no focused window")
        return json.dumps([*window.identity, window.title], ensure_ascii=True)

    def windows(self) -> list[Window]:
        try:
            raw = hyprland._query("clients", json_output=True)
            if not isinstance(raw, list) or len(raw) > 10000:
                raise hyprland.HyprlandIPCError("Hyprland clients returned an invalid table")
            windows = []
            addresses = set()
            stable_ids = set()
            for item in raw:
                if isinstance(item, dict) and item.get("mapped") is False:
                    continue
                window = _window(item)
                if window.address in addresses or (window.stable_id and window.stable_id in stable_ids):
                    raise hyprland.HyprlandIPCError("Hyprland clients returned duplicate identities")
                addresses.add(window.address)
                if window.stable_id:
                    stable_ids.add(window.stable_id)
                windows.append(window)
            focused = self._focused()
            if focused and focused.identity not in {window.identity for window in windows}:
                raise hyprland.HyprlandIPCError("Hyprland focus changed while listing windows; retry")
            return [Window(w.address, w.stable_id, w.app, w.title, w.app_pid,
                           bool(focused and w.identity == focused.identity)) for w in windows]
        except hyprland.HyprlandIPCError as exc:
            raise _unavailable(str(exc)) from exc

    def available(self) -> tuple[bool, str]:
        try:
            self.windows()
        except DesktopError as exc:
            return False, str(exc)
        return True, ""

    def capability_token(self) -> tuple[bool, str]:
        return self.available()

    def describe_windows(self, windows: list[Window]) -> str:
        if not windows:
            return "No mapped Hyprland windows."
        return "\n".join(
            f"{'*' if w.active else ' '} {w.ref} · "
            f"{json.dumps(w.app)} · {json.dumps(w.title)} · pid {w.app_pid}"
            for w in windows
        )
