"""Who has the pcbridge browser: the agent, or the user (hand-over).

Hand-over is an explicit state, not a guess (ROADMAP, "Taking over the
agent's browser"): `<state>/browser/control.json` says the user has the
browser, why, and since when. While it does, every agent action in the
browser (opening pages, clicking, typing, hiding the window) is refused with
USER_HAS_CONTROL, as the desktop tools are refused with SCREEN_LOCKED.
Reading the page stays allowed, so an agent can wait for the user to
finish. The file is written by the hand-over tool, `pcbridge browser
pause` and the take-over controls, and removed when control goes back.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from ..desktop.errors import DesktopError, ErrorCategory, ErrorCode

FILE = "control.json"
MAX_REASON = 200


def read(directory: Path) -> dict | None:
    """The hand-over record while the user has the browser, else None."""
    try:
        data = json.loads((directory / FILE).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("user") is True else None


def give_to_user(directory: Path, reason: str, by: str) -> dict:
    record = {"user": True, "reason": " ".join(str(reason).split())[:MAX_REASON],
              "since": time.time(), "by": by}
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = directory / (FILE + ".tmp")
    tmp.write_text(json.dumps(record))
    tmp.chmod(0o600)
    os.replace(tmp, directory / FILE)
    return record


def note_notification(directory: Path, notification_id: int) -> None:
    """Remember the hand-over's notification, so that giving the browser back
    from anywhere (the CLI, the terminal UI, the phone) closes it."""
    record = read(directory)
    if record is None:
        return
    record["notification"] = int(notification_id)
    tmp = directory / (FILE + ".tmp")
    tmp.write_text(json.dumps(record))
    tmp.chmod(0o600)
    os.replace(tmp, directory / FILE)


def _close_notification(notification_id: int) -> None:
    """Measured (2026-10-17): a hand-over's notification outlived the hand-over
    and its daemon, with a button nobody listened to any more."""
    try:
        subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                        "--object-path", "/org/freedesktop/Notifications", "--method",
                        "org.freedesktop.Notifications.CloseNotification", str(int(notification_id))],
                       capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass


def give_back(directory: Path) -> bool:
    """Return the browser to the agent and close the hand-over's notification.
    False when the user did not have it."""
    record = read(directory)
    (directory / FILE).unlink(missing_ok=True)
    if record is not None and record.get("notification"):
        _close_notification(record["notification"])
    return record is not None


def refusal(record: dict) -> DesktopError:
    minutes = max(0, int((time.time() - float(record.get("since", 0))) // 60))
    why = f" ({record['reason']})" if record.get("reason") else ""
    return DesktopError(
        code=ErrorCode.USER_HAS_CONTROL,
        message=(f"The user has the pcbridge browser{why}, for {minutes} min now; nothing was "
                 "done in it. Wait until they give it back: browser_read and browser_tabs still "
                 "work, and browser_handoff(action='status') says when control is back."),
        category=ErrorCategory.SAFETY,
        retryable=True,
        suggested_action="wait_for_user",
        permission_scope="pcbridge.browser",
    )
