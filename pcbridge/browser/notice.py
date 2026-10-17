#!/usr/bin/env python3
"""A hand-over notification with a "Give it back" action (stage 6).

Runs under the SYSTEM python3 (python-gobject), like the KWin helper: the
venv has no GObject. It sends one notification over D-Bus, prints its id on
the first line at once, then prints "resume" if the action is clicked, and
exits when the notification closes.

Why not notify-send: measured (2026-10-17), `notify-send -p --wait` prints
the id only when it exits, so the hand-over could not close its own
notification, and a notify-send whose daemon had died kept waiting under a
notification whose button nobody listened to. This helper closes its
notification when the process that started it goes away.

    python3 notice.py TITLE BODY ACTION_LABEL
"""

from __future__ import annotations

import os
import sys

BUS = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


def main() -> int:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    title, body, label = sys.argv[1:4]
    parent = os.getppid()
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    nid = conn.call_sync(
        BUS, PATH, BUS, "Notify",
        GLib.Variant("(susssasa{sv}i)", ("pcbridge", 0, "", title, body, ["resume", label],
                                          {"urgency": GLib.Variant("y", 2)}, 0)),
        GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)[0]
    print(nid, flush=True)

    def on_signal(_conn, _sender, _path, _iface, signal, params):
        if params[0] != nid:
            return
        if signal == "ActionInvoked" and params[1] == "resume":
            print("resume", flush=True)
        loop.quit()

    conn.signal_subscribe(None, BUS, None, PATH, None, Gio.DBusSignalFlags.NONE, on_signal)

    def parent_alive() -> bool:
        if os.getppid() == parent:
            return True
        try:  # the daemon is gone: nobody would hear the button any more
            conn.call_sync(BUS, PATH, BUS, "CloseNotification", GLib.Variant("(u)", (nid,)),
                           None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass
        loop.quit()
        return False

    GLib.timeout_add_seconds(2, parent_alive)
    loop.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- no notification is not a failure of the hand-over
        sys.exit(1)
