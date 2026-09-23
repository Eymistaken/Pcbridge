"""The notification that shows a desktop grant, and on Plasma ends it.

GNOME: pcbridge's shell extension draws a frame and a panel indicator for as
long as the grant lasts; the notification is a one-off courtesy
(`[desktop] unlock_notification`).

KDE Plasma has neither, and KWin's screenshots show no sharing indicator,
so the notification carries the signal (measured on Plasma 6.7.5): a
critical notification stays on screen until it is closed, and its
"Lock now" button makes `notify-send --wait` print the action and exit.
pcbridge keeps that notify-send running for the grant; the button runs the
kill switch (`pcbridge lock`), and every end of the grant closes the
notification. Its id and the notify-send pid are kept in the runtime
directory, so the kill switch, run as another process, can close it too.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from .. import paths as pathslib
from . import compositor as compositorlib

log = logging.getLogger("pcbridge")

STATE_FILE = "grant-notice.json"
LOCK_ACTION = "lock"


def _state_path() -> Path | None:
    base = pathslib.runtime_base()
    return base / "pcbridge" / STATE_FILE if base else None


def _lock_now() -> None:
    """The button's action: the kill switch, as its own process."""
    # -P: the daemon's working directory is the home directory, and a checkout
    # named ~/pcbridge there would shadow the package.
    subprocess.Popen([sys.executable, "-P", "-m", "pcbridge.cli.lock"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def show(title: str, body: str) -> None:
    """Show the grant. On GNOME a plain notification; on Plasma a lasting one."""
    if not compositorlib.is_kde():
        try:
            subprocess.run(["notify-send", "-a", "pcbridge", "-u", "critical", title, body],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    close()
    try:
        proc = subprocess.Popen(
            ["notify-send", "-a", "pcbridge", "-u", "critical", "-p", "-w",
             "-A", f"{LOCK_ACTION}=Lock now", title, body],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        log.warning("grant notification not shown: %s", exc)
        return

    def watch() -> None:
        assert proc.stdout is not None
        first = proc.stdout.readline().strip()
        path = _state_path()
        if first.isdigit() and path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"id": int(first), "pid": proc.pid}),
                                encoding="utf-8")
            except OSError:
                pass
        for line in proc.stdout:
            if line.strip() == LOCK_ACTION:
                log.info("grant notification: Lock now")
                _lock_now()
        proc.wait()

    threading.Thread(target=watch, name="grant-notice", daemon=True).start()


def close() -> bool:
    """Close the grant notification, from any process. True when one was open."""
    path = _state_path()
    if path is None:
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
    except (OSError, ValueError):
        return False
    notice_id, pid = state.get("id"), state.get("pid")
    if isinstance(notice_id, int):
        try:
            subprocess.run(["busctl", "--user", "call", "org.freedesktop.Notifications",
                            "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                            "CloseNotification", "u", str(notice_id)],
                           capture_output=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
    if isinstance(pid, int):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            if cmdline.split(b"\0")[0].endswith(b"notify-send"):
                os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
    return True
