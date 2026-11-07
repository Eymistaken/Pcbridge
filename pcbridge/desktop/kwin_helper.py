#!/usr/bin/env python3
"""Ask KWin about windows through a one-shot KWin script (KDE Plasma).

Runs under the SYSTEM python3 (python-gobject), like `atspi_helper.py`: the
venv has no GObject. One JSON request on stdin, one JSON reply on stdout.

    {"cmd": "activate", "target": "kate"}  -> {"ok": true, "activated": true,
                                               "app": "org.kde.kate", "title": "..."}
    {"cmd": "focused"}                     -> {"ok": true, "found": true, "app": ...,
                                               "title": ..., "geometry": [x, y, w, h]}

HOW (measured on Plasma 6.7.5, docs/dev/measured-facts.md)
    KWin runs a script given to `org.kde.KWin /Scripting loadScript`; the
    script answers with `callDBus` to a name this process owns for the call.
    A round trip is 1-5 ms and needs no authorization. The script only
    reads the window list and, for `activate`, sets `workspace.activeWindow`
    on ONE window; it cannot do anything else and is unloaded right after.

MATCHING
    The same rules as the GNOME Shell extension (`windowcontrol.js`): the
    target is normalized, compared with the caption, the resource class and
    the desktop file name; exact beats prefix/suffix beats substring; a tie
    between windows is ambiguous and activates nothing. Windows that skip
    the taskbar are not candidates.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import warnings

MAX_TARGET_LENGTH = 200
MAX_FIELD_LENGTH = 200
REPLY_TIMEOUT_MS = 2000

SCRIPT = r"""
var request = %(request)s;
function normalize(value) {
    var text = String(value === undefined || value === null ? "" : value);
    try { text = text.normalize("NFKC"); } catch (e) {}
    return text.replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase()
               .replace(/[\s._-]+/g, " ").trim();
}
function fieldScore(query, value) {
    if (!value) return 0;
    if (value === query) return 3;
    if (value.indexOf(query) === 0 || value.slice(-query.length) === query) return 2;
    return value.indexOf(query) >= 0 ? 1 : 0;
}
function describe(w) {
    if (!w) return {found: false, app: "", title: "", geometry: null};
    var g = w.frameGeometry;
    return {found: true,
            app: String(w.desktopFileName || w.resourceClass || "").slice(0, %(max)d),
            title: String(w.caption || "").slice(0, %(max)d),
            geometry: [g.x, g.y, g.width, g.height]};
}
var out = {ok: true};
if (request.cmd === "activate") {
    var query = normalize(request.target);
    var ranked = [];
    var list = workspace.windowList();
    for (var i = 0; query && query.length <= %(max_target)d && i < list.length; i++) {
        var w = list[i];
        if (!w || w.skipTaskbar || !w.normalWindow) continue;
        var score = Math.max(fieldScore(query, normalize(w.caption)),
                             fieldScore(query, normalize(w.resourceClass)),
                             fieldScore(query, normalize(w.desktopFileName)));
        if (score > 0) ranked.push({w: w, score: score});
    }
    var best = 0;
    for (var j = 0; j < ranked.length; j++) best = Math.max(best, ranked[j].score);
    var top = ranked.filter(function (item) { return item.score === best; });
    out.activated = false;
    if (top.length === 1) {
        var target = top[0].w;
        if (target.minimized) target.minimized = false;
        workspace.activeWindow = target;
        out.activated = workspace.activeWindow === target;
    }
    out.ambiguous = top.length > 1;
    var d = describe(workspace.activeWindow);
    out.app = d.app; out.title = d.title;
} else if (request.cmd === "focused") {
    var f = describe(workspace.activeWindow);
    out.found = f.found; out.app = f.app; out.title = f.title; out.geometry = f.geometry;
} else {
    out = {ok: false, error: "unknown command"};
}
callDBus("%(name)s", "/", "%(name)s", "Reply", JSON.stringify(out));
"""


def _reply(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()
    return 0


def run(request: dict) -> dict:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    name = f"io.github.eymistaken.Pcbridge.KWin.p{os.getpid()}"
    xml = (f'<node><interface name="{name}"><method name="Reply">'
           '<arg type="s" direction="in"/></method></interface></node>')
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    answers: list[str] = []

    def on_call(_c, _s, _p, _i, _m, params, invocation):
        answers.append(params.unpack()[0])
        invocation.return_value(None)
        loop.quit()

    node = Gio.DBusNodeInfo.new_for_xml(xml)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        conn.register_object("/", node.interfaces[0], on_call, None, None)
    Gio.bus_own_name_on_connection(conn, name, Gio.BusNameOwnerFlags.NONE, None, None)

    safe = {"cmd": str(request.get("cmd", "")),
            "target": str(request.get("target", ""))[:MAX_TARGET_LENGTH + 1]}
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(SCRIPT % {"request": json.dumps(safe), "name": name,
                           "max": MAX_FIELD_LENGTH, "max_target": MAX_TARGET_LENGTH})
        path = fh.name
    plugin = f"pcbridge-{os.getpid()}-{time.monotonic_ns()}"
    try:
        sid = conn.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                             "loadScript", GLib.Variant("(ss)", (path, plugin)),
                             GLib.VariantType("(i)"), Gio.DBusCallFlags.NONE, 3000,
                             None)[0]
        if sid < 0:
            return {"ok": False, "error": "KWin did not load the script"}
        conn.call_sync("org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script",
                       "run", None, None, Gio.DBusCallFlags.NONE, 3000, None)
        GLib.timeout_add(REPLY_TIMEOUT_MS, loop.quit)
        loop.run()
    except GLib.Error as exc:
        return {"ok": False, "error": f"KWin scripting: {exc.message}"}
    finally:
        try:
            conn.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                           "unloadScript", GLib.Variant("(s)", (plugin,)),
                           GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass
        os.unlink(path)
    if not answers:
        return {"ok": False, "error": "KWin's script did not answer"}
    try:
        return json.loads(answers[0])
    except ValueError:
        return {"ok": False, "error": "KWin's script answered with no JSON"}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError as exc:
        return _reply({"ok": False, "error": f"bad request: {exc}"})
    try:
        return _reply(run(request))
    except Exception as exc:  # noqa: BLE001 — one JSON error, never a traceback
        return _reply({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})


if __name__ == "__main__":
    sys.exit(main())
