#!/usr/bin/env python3
"""A small GTK4 window whose accessibility tree the live tests act on.

Run with the SYSTEM python3: GTK lives there, not in the venv. The window only
publishes widgets and reports what happens to them. It never sends input, and
it exits on `quit`, on stdin EOF, and on its own after `--timeout` seconds.

The tree is built to hold the cases that matter for target identity:

- two buttons both named "Kapat", one in group "Belge A" and one in "Belge B",
  so a name alone cannot tell them apart;
- a text field "Ad" and a password field "Parola";
- commands that change the tree the way real applications do.

Every report goes to stdout as one JSON line:

    {"event": "ready", "pid": 1234, "app": "pcbridge-a11y-test"}
    {"event": "clicked", "button": "close-a" | "close-b" | "extra" | ...}
    {"event": "text", "field": "name", "chars": 5, "value": "..."}
    {"event": "done", "command": "prepend"}

Commands on stdin, one per line:

    prepend   put a new button before everything else (index paths shift)
    rebuild   replace the "Kapat" button of group A with a new object
    remove-a  remove group A with its "Kapat" button
    retitle   change the window title
    quit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

APP_NAME = "pcbridge-a11y-test"
TITLE = "pcbridge erisilebilirlik testi"


def emit(**event) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


class Window:
    def __init__(self, app: Gtk.Application) -> None:
        self.app = app
        self.window: Gtk.ApplicationWindow | None = None
        self.box: Gtk.Box | None = None
        self.group_a: Gtk.Frame | None = None
        self.group_a_box: Gtk.Box | None = None
        self.close_a: Gtk.Button | None = None
        self.rebuilt = 0
        self.extra = 0

    def button(self, label: str, report: str) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda *_: emit(event="clicked", button=report))
        return button

    def group(self, title: str, report: str) -> tuple[Gtk.Frame, Gtk.Box, Gtk.Button]:
        frame = Gtk.Frame(label=title)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        inner.append(Gtk.Label(label=f"{title} icerigi"))
        close = self.button("Kapat", report)
        inner.append(close)
        frame.set_child(inner)
        return frame, inner, close

    def field(self, entry: Gtk.Widget, label: str, report: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label=label))
        row.append(entry)
        # A relation would need a GList of widgets, which PyGObject cannot
        # build; the plain label property names the field just as well.
        entry.update_property([Gtk.AccessibleProperty.LABEL], [label])
        entry.connect("changed", lambda widget: self.on_text(widget, report))
        return row

    def on_text(self, widget: Gtk.Editable, report: str) -> None:
        value = widget.get_text()
        if report == "password":
            # Report only the length: the password field must stay opaque even
            # in a test log, the same rule the audit log follows.
            emit(event="text", field=report, chars=len(value))
        else:
            emit(event="text", field=report, chars=len(value), value=value)

    def activate(self, app: Gtk.Application) -> None:
        window = Gtk.ApplicationWindow(application=app, title=TITLE)
        window.set_default_size(640, 360)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        self.group_a, self.group_a_box, self.close_a = self.group("Belge A", "close-a")
        group_b, _inner_b, _close_b = self.group("Belge B", "close-b")
        box.append(self.group_a)
        box.append(group_b)
        box.append(self.field(Gtk.Entry(), "Ad", "name"))
        box.append(self.field(Gtk.PasswordEntry(), "Parola", "password"))
        box.append(self.button("Tamam", "ok"))
        window.set_child(box)
        window.present()
        self.window, self.box = window, box
        threading.Thread(target=self.commands, daemon=True).start()
        emit(event="ready", pid=os.getpid(), app=APP_NAME, title=TITLE)

    # Commands arrive on a reader thread; the tree changes on the GTK thread.
    def commands(self) -> None:
        for line in sys.stdin:
            command = line.strip()
            if command:
                GLib.idle_add(self.run, command)
        GLib.idle_add(self.app.quit)

    def run(self, command: str) -> bool:
        if command == "quit":
            self.app.quit()
            return False
        if command == "prepend":
            self.extra += 1
            self.box.prepend(self.button(f"Ek {self.extra}", f"extra-{self.extra}"))
        elif command == "rebuild" and self.close_a is not None:
            self.rebuilt += 1
            self.group_a_box.remove(self.close_a)
            self.close_a = self.button("Kapat", f"close-a-{self.rebuilt}")
            self.group_a_box.append(self.close_a)
        elif command == "remove-a" and self.group_a is not None:
            self.box.remove(self.group_a)
            self.group_a = self.group_a_box = self.close_a = None
        elif command == "retitle":
            self.window.set_title(f"{TITLE} (degisti)")
        else:
            emit(event="error", command=command)
            return False
        emit(event="done", command=command)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    GLib.set_prgname(APP_NAME)
    app = Gtk.Application(application_id="org.pcbridge.A11yTest")
    window = Window(app)
    app.connect("activate", window.activate)
    GLib.timeout_add_seconds(args.timeout, lambda: app.quit() or False)
    return app.run([])


if __name__ == "__main__":
    sys.exit(main())
