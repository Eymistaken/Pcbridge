#!/usr/bin/env python3
"""A static test pattern, fullscreen on every monitor, for the capture parity gate.

Run with the SYSTEM python3: GTK lives there, not in the venv. It only draws.
It sends no input, and it exits on `quit`, on stdin EOF, and on its own after
`--timeout` seconds, so a test that dies can never leave the screens covered.

Every monitor gets the same bars and checkerboard, a marker square whose color
names the monitor (ordered left to right, like pcbridge's monitor numbers), and
a strip of 16 cells holding a counter in binary. The strip is how freshness is
tested: change the counter, then a frame taken after the change must show it.

Protocol, one line each way:

    -> ready DP-4,DP-3      every monitor has painted at least once
    <- show 42              paint 42 into the strip
    -> shown 42             every monitor has painted a frame holding 42
    <- quit
"""

from __future__ import annotations

import argparse
import sys
import threading

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

# The geometry is shared with tests/live/test_capture_parity.py; change both.
MARKERS = ((255, 0, 255), (0, 255, 255), (255, 255, 0), (255, 128, 0))
MARKER_BOX = (60, 60, 200, 200)  # x, y, width, height at 1920x1080
STRIP_ORIGIN = (60, 820)
CELL = 48
BITS = 16
BARS = (
    (230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200),
    (245, 130, 48), (145, 30, 180), (70, 240, 240), (240, 50, 230),
)


def rgb(color):
    return tuple(channel / 255.0 for channel in color)


class Pattern:
    def __init__(self, app: Gtk.Application) -> None:
        self.app = app
        self.counter = 0
        self.areas: list[tuple[Gtk.DrawingArea, int]] = []
        self.painted: dict[int, int] = {}  # area id -> last counter painted
        self.announced_ready = False
        self.pending_ack: int | None = None

    def draw(self, area, cr, width, height, marker_index):
        sx, sy = width / 1920.0, height / 1080.0
        bar = width / len(BARS)
        for number, color in enumerate(BARS):
            cr.set_source_rgb(*rgb(color))
            cr.rectangle(number * bar, 0, bar + 1, height)
            cr.fill()
        square = 32 * sx
        for row in range(int(280 * sy / square) + 1):
            for column in range(int(width / square) + 1):
                shade = 1.0 if (row + column) % 2 else 0.0
                cr.set_source_rgb(shade, shade, shade)
                cr.rectangle(column * square, 400 * sy + row * square, square, square)
                cr.fill()
        x, y, w, h = MARKER_BOX
        cr.set_source_rgb(*rgb(MARKERS[marker_index % len(MARKERS)]))
        cr.rectangle(x * sx, y * sy, w * sx, h * sy)
        cr.fill()
        ox, oy = STRIP_ORIGIN
        for bit in range(BITS):
            on = (self.counter >> (BITS - 1 - bit)) & 1
            cr.set_source_rgb(on, on, on)
            cr.rectangle((ox + bit * CELL) * sx, oy * sy, CELL * sx, CELL * sy)
            cr.fill()
        self.painted[id(area)] = self.counter
        GLib.idle_add(self.report)

    def report(self):
        if len(self.painted) == len(self.areas) and self.areas:
            if not self.announced_ready:
                self.announced_ready = True
                names = ",".join(connector for _area, _index, connector in self.order)
                print(f"ready {names}", flush=True)
            if self.pending_ack is not None and all(
                value == self.pending_ack for value in self.painted.values()
            ):
                print(f"shown {self.pending_ack}", flush=True)
                self.pending_ack = None
        return False

    def activate(self, app):
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        found = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        found.sort(key=lambda m: (m.get_geometry().x, m.get_geometry().y))
        self.order = []
        for index, monitor in enumerate(found):
            window = Gtk.ApplicationWindow(application=app)
            window.set_decorated(False)
            area = Gtk.DrawingArea()
            area.set_draw_func(self.draw, index)
            window.set_child(area)
            window.fullscreen_on_monitor(monitor)
            window.present()
            self.areas.append((area, index))
            self.order.append((area, index, monitor.get_connector() or f"monitor-{index}"))

    def command(self, line: str):
        parts = line.split()
        if parts[:1] == ["quit"]:
            self.app.quit()
        elif len(parts) == 2 and parts[0] == "show" and parts[1].isdigit():
            self.counter = int(parts[1]) & ((1 << BITS) - 1)
            self.pending_ack = self.counter
            for area, _index in self.areas:
                area.queue_draw()
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    pattern = Pattern(app)
    app.connect("activate", pattern.activate)
    # Never outlive a test that forgot to say goodbye.
    GLib.timeout_add_seconds(max(5, args.timeout), lambda: app.quit() or False)

    def read_commands():
        for line in sys.stdin:
            GLib.idle_add(pattern.command, line.strip())
        GLib.idle_add(lambda: app.quit() or False)

    threading.Thread(target=read_commands, daemon=True).start()
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
