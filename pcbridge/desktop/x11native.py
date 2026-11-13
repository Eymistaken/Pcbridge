"""What the native helper answers for the X11 desktops (Cinnamon, Xfce).

pcbridge has no X11 code of its own: the monitor table, the focused window
and window activation on X11 come from the native helper, as the GNOME
extension and the KWin scripts answer them elsewhere. Each call here starts
one helper, asks one question and stops it, as `kwin_helper.py` starts one
Python per question on Plasma. The window requests carry the current
desktop grant, which the helper checks: a title is screen content.

`configure()` hands over the process's config; without it the default
config file is read, as the CLI does.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from .errors import DesktopError

log = logging.getLogger("pcbridge.desktop")

_lock = threading.Lock()
_config: Any = None

# One question per helper; a helper that starts but does not answer within
# this is treated as no answer, like the KWin script's timeout.
REQUEST_TIMEOUT_S = 3.0


def configure(cfg: Any) -> None:
    """Use `cfg` (the daemon's or the CLI's) for later requests."""
    global _config
    with _lock:
        _config = cfg


def _cfg() -> Any:
    global _config
    with _lock:
        if _config is None:
            from ..config import load_config

            _config = load_config(check_state=False)
        return _config


def request(method: str, params: dict | None = None, *, grant: bool = True) -> dict | None:
    """One helper request; None when there is no helper, grant or answer.

    `grant=True` adds the current grant's id and epoch, and returns None
    without starting anything when no grant is open.
    """
    from ..native import NativeClient, discover_native_binary
    from .backends.rust import runtime_dir
    from .lease import LeaseStore

    cfg = _cfg()
    body = dict(params or {})
    if grant:
        token = LeaseStore(cfg.state_dir).snapshot().token()
        if token is None:
            return None
        body.update(grant_id=str(token.grant_id), revoke_epoch=int(token.revoke_epoch))
    try:
        binary = discover_native_binary(cfg.native)
    except DesktopError as exc:
        log.info("x11: no native helper: %s", exc.message)
        return None
    client = NativeClient(binary, state_dir=Path(cfg.state_dir), runtime_dir=runtime_dir(),
                          default_timeout=REQUEST_TIMEOUT_S)
    try:
        response = client.request(method, body)
    except DesktopError as exc:
        log.info("x11: %s failed: %s", method, exc.message)
        return None
    finally:
        client.close()
    return response.result if isinstance(response.result, dict) else None


def display_snapshot() -> dict | None:
    """The helper's `display.snapshot`: RandR's monitor table. No grant."""
    return request("display.snapshot", grant=False)


def focused() -> dict | None:
    """{found, app, title, geometry} of the active window, under the grant."""
    return request("window.focused")


def activate(target: str) -> dict | None:
    """{activated, ambiguous, found, app, title} after asking for `target`."""
    return request("window.activate", {"target": target})


__all__ = ["REQUEST_TIMEOUT_S", "activate", "configure", "display_snapshot", "focused", "request"]
