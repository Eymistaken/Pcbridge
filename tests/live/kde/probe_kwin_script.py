#!/usr/bin/env python3
"""Measure a KWin script round trip on a Plasma session.

Run with the SYSTEM python (python-gobject) inside a KDE Plasma Wayland
session. It sends no input. It owns a D-Bus name, loads a one-shot KWin
script that calls back with `callDBus`, and prints what came back and how
long the round trip took:

    python3 tests/live/kde/probe_kwin_script.py [--activate TEXT] [--repeat N]

With --activate, the script also makes the first window whose caption or
resource class contains TEXT the active window (this changes the focus in
the session, nothing else).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

if os.environ.get("XDG_CURRENT_DESKTOP", "").upper() != "KDE":
    print(json.dumps({"skipped": "not a KDE Plasma session"}))
    sys.exit(0)

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

NAME = "io.github.eymistaken.Pcbridge.Probe"
XML = f"""<node><interface name="{NAME}">
  <method name="Reply"><arg type="s" direction="in"/></method>
</interface></node>"""

SCRIPT = r"""
var want = %(want)s;
var out = {cursor: [workspace.cursorPos.x, workspace.cursorPos.y], windows: []};
var list = workspace.windowList();
for (var i = 0; i < list.length; i++) {
    var w = list[i];
    if (!w.normalWindow) continue;
    out.windows.push({caption: w.caption, cls: w.resourceClass,
                      desktopFile: w.desktopFileName, pid: w.pid,
                      geometry: [w.frameGeometry.x, w.frameGeometry.y,
                                 w.frameGeometry.width, w.frameGeometry.height]});
    if (want && (w.caption.indexOf(want) >= 0 || w.resourceClass.indexOf(want) >= 0)
            && out.activated === undefined) {
        workspace.activeWindow = w;
        out.activated = w.caption;
    }
}
out.active = workspace.activeWindow ? workspace.activeWindow.caption : null;
callDBus("%(name)s", "/", "%(name)s", "Reply", JSON.stringify(out));
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activate", default="")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    replies: list[str] = []

    def on_call(_c, _s, _p, _i, method, params, invocation):
        replies.append(params.unpack()[0])
        invocation.return_value(None)
        loop.quit()

    node = Gio.DBusNodeInfo.new_for_xml(XML)
    conn.register_object("/", node.interfaces[0], on_call, None, None)
    Gio.bus_own_name_on_connection(conn, NAME, Gio.BusNameOwnerFlags.NONE, None, None)

    for n in range(args.repeat):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(SCRIPT % {"want": json.dumps(args.activate), "name": NAME})
            path = fh.name
        plugin = f"pcbridge-probe-{os.getpid()}-{n}"
        t0 = time.perf_counter()
        sid = conn.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                             "loadScript", GLib.Variant("(ss)", (path, plugin)),
                             GLib.VariantType("(i)"), Gio.DBusCallFlags.NONE, 3000, None)[0]
        conn.call_sync("org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script",
                       "run", None, None, Gio.DBusCallFlags.NONE, 3000, None)
        GLib.timeout_add(3000, loop.quit)
        loop.run()
        ms = round((time.perf_counter() - t0) * 1000)
        conn.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                       "unloadScript", GLib.Variant("(s)", (plugin,)),
                       GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 3000, None)
        os.unlink(path)
        print(json.dumps({"script_id": sid, "ms": ms,
                          "reply": json.loads(replies[-1]) if replies else None}), flush=True)
        replies.clear()
    return 0


if __name__ == "__main__":
    sys.exit(main())
