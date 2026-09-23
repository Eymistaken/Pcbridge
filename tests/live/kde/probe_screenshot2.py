#!/usr/bin/env python3
"""Measure KWin's ScreenShot2 D-Bus interface on a Plasma session.

Run with the SYSTEM python (it needs python-gobject) inside a KDE Plasma
Wayland session, as the logged-in user. It sends no input. It prints one
JSON line per attempt: whether KWin answered, the image size and format,
and how long the call took.

    python3 tests/live/kde/probe_screenshot2.py [--screen NAME] [--repeat N]

KWin restricts ScreenShot2 to programs whose .desktop file lists the
interface in X-KDE-DBUS-Restricted-Interfaces; an unauthorized caller gets
an error, which this reports as it comes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

if os.environ.get("XDG_CURRENT_DESKTOP", "").upper() != "KDE":
    print(json.dumps({"skipped": "not a KDE Plasma session"}))
    sys.exit(0)

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS = "org.kde.KWin.ScreenShot2"
PATH = "/org/kde/KWin/ScreenShot2"


def capture(conn, method: str, args: list, signature: str) -> dict:
    read_fd, write_fd = os.pipe()
    chunks: list[bytes] = []

    def drain() -> None:
        with os.fdopen(read_fd, "rb") as fh:
            while True:
                block = fh.read(1 << 20)
                if not block:
                    break
                chunks.append(block)

    reader = threading.Thread(target=drain)
    reader.start()
    fds = Gio.UnixFDList.new()
    index = fds.append(write_fd)
    os.close(write_fd)
    options = {"include-cursor": GLib.Variant("b", False),
               "native-resolution": GLib.Variant("b", True)}
    params = GLib.Variant(signature, (*args, options, index))
    t0 = time.perf_counter()
    try:
        reply, _ = conn.call_with_unix_fd_list_sync(
            BUS, PATH, BUS, method, params, GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, 10000, fds, None)
        error = None
    except GLib.Error as exc:
        reply, error = None, exc.message
    call_ms = round((time.perf_counter() - t0) * 1000)
    # The fd list holds its own copy of the write end; close it, or the
    # reader never sees EOF.
    for fd in fds.steal_fds():
        os.close(fd)
    reader.join(10)
    ms = round((time.perf_counter() - t0) * 1000)
    if error:
        return {"method": method, "ok": False, "error": error, "ms": ms}
    meta = {k: v for k, v in reply.unpack()[0].items()}
    return {"method": method, "ok": True, "call_ms": call_ms, "ms": ms,
            "bytes": sum(map(len, chunks)), "meta": meta}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", default="")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for _ in range(args.repeat):
        print(json.dumps(capture(conn, "CaptureWorkspace", [], "(a{sv}h)"), default=str),
              flush=True)
        if args.screen:
            print(json.dumps(capture(conn, "CaptureScreen", [args.screen], "(sa{sv}h)"),
                             default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
