"""Handing the pcbridge browser to the user and back (stage 6).

`give` is the agent's (browser_handoff): the user gets the browser, its
window is shown without taking focus, and a notification says why, with a
"Give it back" action. `take` is the user's (`pcbridge browser pause`, the
terminal UI, the panel menu): the same state, and the window is shown with
focus because the user asked for it. `give_back` ends either; only the
user's places call it (`pcbridge browser resume`, the terminal UI, the
panel menu, the notification's action), never an agent's tool.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from typing import Any

from . import control

TITLE = "pcbridge: the browser is yours"


def _notify(manager: Any, reason: str) -> None:
    """A lasting notification with a "Give it back" action; clicking it gives
    the browser back. Runs in the background: notify-send --wait blocks until
    the notification is answered or closed."""
    exe = shutil.which("notify-send")
    if exe is None:
        return
    body = (f"{reason}\n\nWhen you are done, give it back here, in the pcbridge panel menu, "
            "or with: pcbridge browser resume")
    try:
        proc = subprocess.Popen(
            [exe, "-a", "pcbridge", "-u", "critical", "-A", "resume=Give it back to the agent",
             "--wait", TITLE, body],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return

    def wait() -> None:
        out = proc.communicate()[0] or ""
        if out.strip() == "resume":
            control.give_back(manager.dir)

    threading.Thread(target=wait, daemon=True).start()


def give(manager: Any, reason: str, by: str) -> str:
    control.give_to_user(manager.dir, reason, by)
    view = manager.view("show")
    _notify(manager, reason)
    where = view.describe() if view.found else "Its window could not be shown: " + view.describe()
    return ("The user has the browser now; browser_navigate and browser_act are refused until "
            f"they give it back. {where} Poll browser_handoff(action='status') or browser_read "
            "to see when they are done.")


def take(manager: Any, reason: str = "", by: str = "user") -> str:
    control.give_to_user(manager.dir, reason or "taken over by the user", by)
    view = manager.view("show", focus=True)
    return ("You have the pcbridge browser; agents cannot act in it until you give it back "
            f"(pcbridge browser resume). {view.describe()}")


def give_back(manager: Any) -> str:
    if control.give_back(manager.dir):
        return "The agent has the browser again."
    return "The user did not have the browser; nothing changed."
