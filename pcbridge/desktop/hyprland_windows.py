"""Hyprland window identities from fresh, read-only compositor IPC."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Callable

from . import hyprland
from .errors import DesktopError, ErrorCategory, ErrorCode


def capture_region(table):
    """Visible focused-window portion on its center output, with an identity check."""
    from . import monitors

    def focused():
        try:
            raw = hyprland._query("activewindow", json_output=True)
            return raw, _window(raw, active=True)
        except hyprland.HyprlandIPCError as error:
            raise _unavailable("Hyprland focused-window capture is unavailable") from error

    raw, window = focused()
    at, size = raw.get("at"), raw.get("size")
    if (not isinstance(at, list) or not isinstance(size, list) or len(at) != 2 or len(size) != 2
            or any(type(value) not in (int, float) or abs(value) > 2**31 - 1
                   or not math.isfinite(value) for value in [*at, *size])
            or any(value <= 0 for value in size)):
        raise _unavailable("Hyprland focused-window geometry is unavailable")
    left, top = monitors.platform_origin(table)
    x, y = at[0] - left, at[1] - top
    home = monitors.find_monitor(int(x + size[0] / 2), int(y + size[1] / 2), table)
    if home is None:
        raise _unavailable("The focused window is outside the current outputs")
    x1, y1 = max(home.x, math.floor(x)), max(home.y, math.floor(y))
    x2, y2 = min(home.x + home.width, math.ceil(x + size[0])), min(home.y + home.height, math.ceil(y + size[1]))
    if x2 - x1 < 8 or y2 - y1 < 8:
        raise _unavailable("The focused window is too small to capture")
    def verify():
        current, identity = focused()
        if (identity.identity != window.identity
                or current.get("at") != at or current.get("size") != size):
            raise DesktopError(code=ErrorCode.TARGET_MISMATCH, category=ErrorCategory.SAFETY,
                message="The focused window changed during capture", retryable=True,
                suggested_action="Inspect the focused window and capture again.", backend=BACKEND)
    return ((x1, y1, x2 - x1, y2 - y1), home), verify

BACKEND = "linux.hyprland-ipc"
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{1,16}\Z")
_STABLE_ID = re.compile(r"[0-9a-fA-F]{1,16}\Z")


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
    return Window(address.lower(), stable_id.lower(), raw["class"], raw["title"], pid, active)


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

    def activate(self, target: str, *, checkpoint: Callable[[], None],
                 deadline: float | None = None, application=None) -> Window | None:
        """Resolve unambiguously, dispatch one exact identity, verify actual focus."""
        from .apps import _is_app, _norm, NoTimeLeft

        query = _norm(target)
        if not query or len(target) > 200:
            raise _focus_error(ErrorCode.TARGET_MISMATCH, "A window target must contain 1–200 characters")
        ranked = []
        for window in self.windows():
            exact = target.lower() in (window.ref, f"address:{window.address}",
                                       f"stableid:{window.stable_id}" if window.stable_id else "")
            scores = [4 if exact else 0]
            if not target.lower().startswith(("hyprland:", "address:", "stableid:")):
                for field in (window.app, window.title):
                    value = _norm(field)
                    scores.append(3 if value == query else
                                  2 if value.startswith(query) or value.endswith(query) else
                                  1 if query in value else 0)
                if application and application.entry and _is_app(application.entry, window.app):
                    scores.append(3)
            if max(scores):
                ranked.append((max(scores), window))
        if not ranked:
            return None
        best = max(score for score, _ in ranked)
        matches = [window for score, window in ranked if score == best]
        if len(matches) != 1:
            raise _focus_error(ErrorCode.ELEMENT_AMBIGUOUS,
                "More than one Hyprland window matches; use the exact identity from window_list")
        window = matches[0]
        checkpoint()
        if window.identity not in {current.identity for current in self.windows()}:
            raise _focus_error(ErrorCode.ELEMENT_STALE, "The selected Hyprland window identity changed")
        def before_dispatch():
            checkpoint()
            if deadline is not None and time.monotonic() >= deadline:
                raise NoTimeLeft("No time remains to activate the selected Hyprland window")
        try:
            hyprland.focus_exact(window.address, window.stable_id, checkpoint=before_dispatch)
            until = min(time.monotonic() + 1, deadline) if deadline is not None else time.monotonic() + 1
            while True:
                checkpoint()
                focused = self._focused()
                if focused and focused.identity == window.identity:
                    return focused
                if time.monotonic() >= until:
                    break
                time.sleep(0.05)
        except hyprland.HyprlandIPCError as error:
            raise _focus_error(ErrorCode.EXECUTION_UNKNOWN,
                "Hyprland activation could not be verified; inspect focus before retrying", sent=True) from error
        raise _focus_error(ErrorCode.EXECUTION_UNKNOWN,
            "Hyprland acknowledged activation but the selected window did not gain focus", sent=True)

    def describe_windows(self, windows: list[Window]) -> str:
        if not windows:
            return "No mapped Hyprland windows."
        return "\n".join(
            f"{'*' if w.active else ' '} {w.ref} · "
            f"{json.dumps(w.app)} · {json.dumps(w.title)} · pid {w.app_pid}"
            for w in windows
        )


def _focus_error(code: ErrorCode, message: str, *, sent: bool = False) -> DesktopError:
    return DesktopError(code=code, category=ErrorCategory.EXECUTION, message=message,
        retryable=False, suggested_action="Inspect window_list and use an exact window identity.",
        permission_scope="os.window", backend=BACKEND, execution_state="unknown" if sent else "not_started")
