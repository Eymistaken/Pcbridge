"""Selected-session observations and narrowly scoped guarded window focus."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Callable

from . import session


class HyprlandIPCError(RuntimeError):
    """The selected compositor did not return usable runtime state."""


def _query(command: str, *, json_output: bool, env: dict[str, str] | None = None) -> Any:
    if command not in {"binds", "submap", "monitors", "clients", "activewindow", "locked", "layers", "status"}:
        raise ValueError("Hyprland read-only query is not allowed")
    current = os.environ if env is None else env
    instance = session.hyprland_instance(current)
    if instance is None:
        raise HyprlandIPCError("No unambiguous Hyprland IPC instance matches this session")
    args = ["hyprctl", "-i", instance["instance"]]
    if json_output:
        args.append("-j")
    args.append(command)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=3,
            env={**os.environ, **current},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HyprlandIPCError(f"Hyprland {command} query failed: {exc}") from exc
    if proc.returncode != 0:
        raise HyprlandIPCError(f"Hyprland {command} query exited {proc.returncode}")
    if not json_output:
        return proc.stdout.strip()
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise HyprlandIPCError(f"Hyprland {command} returned invalid JSON") from exc


def bindings_snapshot(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return every registered binding and the active submap, unchanged.

    Hyprland changes its bind fields between versions. Keep each JSON object
    rather than reducing it to a key label or guessing the meaning of an
    opaque dispatcher argument.
    """
    try:
        bindings = _query("binds", json_output=True, env=env)
        if not isinstance(bindings, list) or any(not isinstance(b, dict) for b in bindings):
            raise HyprlandIPCError("Hyprland binds returned an unexpected shape")
    except HyprlandIPCError as exc:
        return {"available": False, "active_submap": None, "bindings": [],
                "count": 0, "reason": str(exc)}
    try:
        submap = _query("submap", json_output=False, env=env)
    except HyprlandIPCError:
        submap = ""
    return {
        "available": True,
        "active_submap": submap or None,
        "bindings": bindings,
        "count": len(bindings),
        "source": "hyprctl runtime IPC",
    }


def monitors(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The active output table from the selected compositor instance."""
    data = _query("monitors", json_output=True, env=env)
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise HyprlandIPCError("Hyprland monitors returned an unexpected shape")
    return data


def screen_locked() -> bool | None:
    """Authoritative compositor lock state; no process-name or idle inference."""
    try:
        data = _query("locked", json_output=True)
    except HyprlandIPCError:
        return None
    value = data.get("locked") if isinstance(data, dict) else None
    return value if type(value) is bool else None


def focus_context() -> tuple[str, str]:
    """Observe the actual active config provider; version alone is insufficient."""
    env = dict(os.environ)
    instance = session.hyprland_instance(env)
    if instance is None:
        raise HyprlandIPCError("No unambiguous Hyprland focus instance matches this session")
    env["HYPRLAND_INSTANCE_SIGNATURE"] = instance["instance"]
    env["WAYLAND_DISPLAY"] = instance["wl_socket"]
    status = _query("status", json_output=True, env=env)
    mode = status.get("configProvider") if isinstance(status, dict) else None
    if mode not in ("lua", "hyprlang"):
        raise HyprlandIPCError("Hyprland did not report a supported runtime config provider")
    return instance["instance"], mode


def focus_exact(address: str, stable_id: str, *, checkpoint: Callable[[], None]) -> None:
    """Only one validated identity can enter this dispatcher; no arbitrary code."""
    if (not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", address) or int(address, 16) == 0
            or (stable_id and not re.fullmatch(r"[0-9a-fA-F]{1,16}", stable_id))):
        raise HyprlandIPCError("Invalid exact Hyprland focus identity")
    instance, mode = focus_context()
    selector = f"stableid:{stable_id}" if stable_id else f"address:{address}"
    args = ["hyprctl", "-i", instance, "dispatch"]
    if mode == "lua":
        args.append(f'hl.dsp.focus({{window="{selector}"}})')
    else:
        args.extend(["focuswindow", f"address:{address}"])
    checkpoint()  # Recheck the captured admission after every read-only lookup.
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError) as error:
        raise HyprlandIPCError("Hyprland focus dispatch did not return an acknowledgment") from error
    if proc.returncode != 0 or proc.stdout.strip() != "ok":
        raise HyprlandIPCError("Hyprland focus dispatch was not acknowledged")
