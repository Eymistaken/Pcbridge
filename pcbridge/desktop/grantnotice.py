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

Xfce and Cinnamon get the same lasting notification. Xfce has no pcbridge
panel item, so the button is its kill switch beside the native grant frame;
Cinnamon has the pcbridge applet too, but keeps a critical notification on
screen until it is closed (measured on Cinnamon 5.6.8), so a one-off one
would still say "granted" after the grant ended.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
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


def lasting() -> bool:
    """Does the grant notification stay, with its "Lock now" button?"""
    from . import session

    return compositorlib.is_kde() or compositorlib.current().kind in session.X11_KINDS


def show(title: str, body: str) -> None:
    """Show the grant. A plain notification, or on Plasma, Xfce and Cinnamon a lasting one."""
    if not lasting():
        try:
            subprocess.run(["notify-send", "-a", "pcbridge", "-u", "critical", title, body],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    close()
    if not _notify_send_has_actions():
        _show_through_bus(title, body)
        return
    # Line-buffered: libnotify 0.8.1 (Debian 12) keeps the printed id in its
    # stdout buffer until it exits when stdout is a pipe (measured: no line
    # within 3 s; with `stdbuf -oL` 10 ms), and without the id the
    # notification could not be closed when the grant ends.
    prefix = ["stdbuf", "-oL"] if shutil.which("stdbuf") else []
    try:
        proc = subprocess.Popen(
            [*prefix, "notify-send", "-a", "pcbridge", "-u", "critical", "-p", "-w",
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


_ACTIONS_SUPPORT: bool | None = None


def _notify_send_has_actions() -> bool:
    """Can `notify-send` wait for a button? libnotify 0.7.9 (Ubuntu 22.04,
    Linux Mint 21) has no --action, --wait or --print-id (measured)."""
    global _ACTIONS_SUPPORT
    if _ACTIONS_SUPPORT is None:
        try:
            out = subprocess.run(["notify-send", "--help"], capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _ACTIONS_SUPPORT = "--action" in out
    return _ACTIONS_SUPPORT


_NOTIFICATIONS = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                  "org.freedesktop.Notifications")


def _show_through_bus(title: str, body: str) -> None:
    """The same lasting notification without notify-send: `busctl` asks the
    notification server and a `busctl monitor` waits for its button.

    The monitor starts first, so a click cannot come before anyone listens.
    Measured on Linux Mint 21.3 (systemd 249): the monitor needs no privilege
    on the user's own bus and prints the signals as JSON lines.
    """
    try:
        monitor = subprocess.Popen(
            ["busctl", "--user", "monitor", "--json=short", "--match",
             "type=signal,interface=org.freedesktop.Notifications"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True)
        sent = subprocess.run(
            ["busctl", "--user", "call", *_NOTIFICATIONS, "Notify", "susssasa{sv}i",
             "pcbridge", "0", "", title, body, "2", LOCK_ACTION, "Lock now",
             "1", "urgency", "y", "2", "0"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("grant notification not shown: %s", exc)
        return
    parts = sent.stdout.split()
    if sent.returncode != 0 or len(parts) != 2 or not parts[1].isdigit():
        monitor.terminate()
        log.warning("grant notification not shown: %s", sent.stderr.strip()[:200])
        return
    notice_id = int(parts[1])
    path = _state_path()
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"id": notice_id, "pid": monitor.pid}), encoding="utf-8")
        except OSError:
            pass

    def watch() -> None:
        assert monitor.stdout is not None
        for line in monitor.stdout:
            try:
                message = json.loads(line)
                data = message["payload"]["data"]
            except (ValueError, KeyError, TypeError):
                continue
            if not data or data[0] != notice_id:
                continue
            if message.get("member") == "ActionInvoked" and data[1:] == [LOCK_ACTION]:
                log.info("grant notification: Lock now")
                _lock_now()
            elif message.get("member") == "NotificationClosed":
                break
        monitor.terminate()
        monitor.wait()

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
            if cmdline.split(b"\0")[0].endswith((b"notify-send", b"busctl")):
                os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
    return True
