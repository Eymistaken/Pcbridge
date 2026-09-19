#!/usr/bin/env python3
"""Fullscreen windows on every monitor that report the input they receive.

Run with the SYSTEM python3: GTK lives there, not in the venv. The window only
listens. It sends no input, and it exits on `quit`, on stdin EOF, and on its
own after `--timeout` seconds, so a test that dies can never leave the screens
covered.

Covering every monitor is the safety: a pointer event that lands in the wrong
place still lands here, where it is reported instead of clicking one of the
user's windows. The right-most monitor's window holds the one text field the
keyboard tests type into.

Every event goes to stdout as one JSON line, positions in global canvas pixels
(the window's own position plus its monitor's origin):

    {"event": "ready", "monitors": [...], "field": [x, y, width, height]}
    {"event": "motion", "x": 2620, "y": 300}
    {"event": "press" | "release", "button": 1, "x": ..., "y": ...}
    {"event": "key_press" | "key_release", "keyval": "A", "shift": true, ...}
    {"event": "scroll", "dx": 0.0, "dy": 1.0}
    {"event": "active", "monitor": 1, "active": true}
    {"event": "focus", "field": true}
    {"event": "text", "value": "..."}

Commands on stdin: `clear` (empty the field) and `quit`.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

# The text field on the right-most monitor, in that window's own pixels.
FIELD = (100, 150, 1200, 300)
NOTICE = "pcbridge girdi testi - klavyeye ve fareye dokunmayin"


def emit(**event) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


class Windows:
    def __init__(self, app: Gtk.Application) -> None:
        self.app = app
        self.painted: set[int] = set()
        self.count = 0
        self.geometry: list[Gdk.Rectangle] = []
        self.buffer: Gtk.TextBuffer | None = None
        self.announced = False

    def draw(self, _area, cr, width, height, index):
        cr.set_source_rgb(0.12, 0.12, 0.16)
        cr.paint()
        cr.set_source_rgb(0.95, 0.85, 0.2)
        cr.select_font_face("Sans")
        cr.set_font_size(40)
        cr.move_to(100, height - 120)
        cr.show_text(NOTICE)
        self.painted.add(index)
        GLib.idle_add(self.ready)

    def ready(self):
        if self.announced or len(self.painted) < self.count:
            return False
        self.announced = True
        right = self.geometry[-1]
        emit(
            event="ready",
            monitors=[[g.x, g.y, g.width, g.height] for g in self.geometry],
            field=[right.x + FIELD[0], right.y + FIELD[1], FIELD[2], FIELD[3]],
        )
        return False

    def on_event(self, _controller, event, index):
        origin = self.geometry[index]
        kind = event.get_event_type()
        if kind in (Gdk.EventType.MOTION_NOTIFY, Gdk.EventType.BUTTON_PRESS,
                    Gdk.EventType.BUTTON_RELEASE):
            ok, x, y = event.get_position()
            if not ok:
                return False
            gx, gy = round(origin.x + x), round(origin.y + y)
            if kind == Gdk.EventType.MOTION_NOTIFY:
                emit(event="motion", x=gx, y=gy)
            else:
                emit(
                    event="press" if kind == Gdk.EventType.BUTTON_PRESS else "release",
                    button=event.get_button(), x=gx, y=gy,
                )
        elif kind in (Gdk.EventType.KEY_PRESS, Gdk.EventType.KEY_RELEASE):
            state = event.get_modifier_state()
            emit(
                event="key_press" if kind == Gdk.EventType.KEY_PRESS else "key_release",
                keyval=Gdk.keyval_name(event.get_keyval()) or "",
                keycode=event.get_keycode(),
                shift=bool(state & Gdk.ModifierType.SHIFT_MASK),
                control=bool(state & Gdk.ModifierType.CONTROL_MASK),
                alt=bool(state & Gdk.ModifierType.ALT_MASK),
            )
        elif kind == Gdk.EventType.SCROLL:
            direction = event.get_direction()
            if direction == Gdk.ScrollDirection.SMOOTH:
                dx, dy = event.get_deltas()
            else:
                dx, dy = {
                    Gdk.ScrollDirection.UP: (0.0, -1.0),
                    Gdk.ScrollDirection.DOWN: (0.0, 1.0),
                    Gdk.ScrollDirection.LEFT: (-1.0, 0.0),
                    Gdk.ScrollDirection.RIGHT: (1.0, 0.0),
                }.get(direction, (0.0, 0.0))
            emit(event="scroll", dx=float(dx), dy=float(dy))
        return False  # observe only; the text field still gets its keys

    def activate(self, app):
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        found = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        found.sort(key=lambda m: (m.get_geometry().x, m.get_geometry().y))
        self.count = len(found)
        self.geometry = [monitor.get_geometry() for monitor in found]
        for index, monitor in enumerate(found):
            window = Gtk.ApplicationWindow(application=app)
            window.set_decorated(False)
            overlay = Gtk.Overlay()
            area = Gtk.DrawingArea()
            area.set_draw_func(self.draw, index)
            overlay.set_child(area)
            if index == len(found) - 1:
                fixed = Gtk.Fixed()
                field = Gtk.TextView()
                field.set_size_request(FIELD[2], FIELD[3])
                field.set_wrap_mode(Gtk.WrapMode.CHAR)
                fixed.put(field, FIELD[0], FIELD[1])
                overlay.add_overlay(fixed)
                self.buffer = field.get_buffer()
                self.buffer.connect("changed", self.on_text)
                focus = Gtk.EventControllerFocus()
                focus.connect("enter", lambda *_: emit(event="focus", field=True))
                focus.connect("leave", lambda *_: emit(event="focus", field=False))
                field.add_controller(focus)
            window.set_child(overlay)
            legacy = Gtk.EventControllerLegacy()
            legacy.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            legacy.connect("event", self.on_event, index)
            window.add_controller(legacy)
            window.connect(
                "notify::is-active",
                lambda w, _p, i=index: emit(event="active", monitor=i, active=w.is_active()),
            )
            window.fullscreen_on_monitor(monitor)
            window.present()

    def on_text(self, buffer):
        start, end = buffer.get_bounds()
        emit(event="text", value=buffer.get_text(start, end, True))

    def command(self, line: str):
        if line == "quit":
            self.app.quit()
        elif line == "clear" and self.buffer is not None:
            self.buffer.set_text("")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    windows = Windows(app)
    app.connect("activate", windows.activate)
    # Never outlive a test that forgot to say goodbye.
    GLib.timeout_add_seconds(max(5, args.timeout), lambda: app.quit() or False)

    def read_commands():
        for line in sys.stdin:
            GLib.idle_add(windows.command, line.strip())
        GLib.idle_add(lambda: app.quit() or False)

    threading.Thread(target=read_commands, daemon=True).start()
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
