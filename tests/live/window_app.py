#!/usr/bin/env python3
"""A plain GTK4 application window for the window-operation live tests.

Run with the SYSTEM python3: GTK lives there, not in the venv. Unlike
`a11y_window.py` it reads nothing from stdin, because `gtk-launch` starts it
from a desktop entry with stdin closed, the way any application is started.
It never sends input. It exits on SIGTERM or SIGINT, and on its own after
`--timeout` seconds.

The application id is also the program name, so the accessibility tree names
the application after its desktop entry. A second launch with the same id
brings the existing window forward instead of opening another one.
"""

from __future__ import annotations

import argparse
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    GLib.set_prgname(args.app_id)
    app = Gtk.Application(application_id=args.app_id)
    windows: list[Gtk.ApplicationWindow] = []

    def activate(app: Gtk.Application) -> None:
        if windows:
            windows[0].present()
            return
        window = Gtk.ApplicationWindow(application=app, title=args.title)
        window.set_default_size(420, 160)
        window.set_child(Gtk.Label(label=args.title))
        windows.append(window)
        window.present()

    def stop() -> bool:
        app.quit()
        return GLib.SOURCE_REMOVE

    app.connect("activate", activate)
    GLib.timeout_add(int(args.timeout * 1000), stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
