#!/usr/bin/env python3
"""Show and hide the pcbridge browser in a nested GNOME Shell.

Starts `gnome-shell --nested` under its own `dbus-run-session`, with
XDG_DATA_HOME pointing at a temporary directory whose only extension is
this checkout's (the real session's extension and settings are not
touched), starts the pcbridge browser headed inside it, and measures:

  - whether a browser started hidden is ever seen on screen or focused
    while it starts (sampled every 50 ms);
  - loading, acting and CDP screenshots while hidden (non-blank pixels);
  - show without focus, show with focus, hide again.

Everything is collected at the end: the browser, the shell and the session
services it left (gnome-extension/nested.sh --clean). Run from the repo:

    ./.venv/bin/python scripts/dev/browser-nested.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.browser.manager import BrowserManager  # noqa: E402
from pcbridge.config import BrowserSpec  # noqa: E402
from tests.fixtures.browser.router_site import RouterSite  # noqa: E402

EXT = "pcbridge-gorunur@eymistaken.local"
EXTRA = [a for a in os.environ.get("PCBRIDGE_NESTED_BROWSER_ARGS", "").split() if a]
BUS = "io.github.eymistaken.Pcbridge.WindowFocus"
PATH = "/io/github/eymistaken/Pcbridge/WindowFocus"


def gdbus(env: dict, method: str, *args: str) -> str:
    out = subprocess.run(["gdbus", "call", "--session", "--dest", BUS, "--object-path", PATH,
                          "--method", f"{BUS}.{method}", *args],
                         env=env, capture_output=True, text=True, timeout=10)
    return (out.stdout or out.stderr).strip()


def state(env: dict) -> tuple[bool, bool, bool]:
    m = re.findall(r"true|false", gdbus(env, "BrowserState"))
    return tuple(x == "true" for x in m) if len(m) == 3 else (False, False, False)  # type: ignore[return-value]


# A plain window that holds focus, standing in for the user's own work.
USER_WINDOW = """
imports.gi.versions.Gtk = '4.0';
const {Gtk, GLib} = imports.gi;
Gtk.init();
const w = new Gtk.Window({title: 'pcbridge-user-window'});
w.present();
new GLib.MainLoop(null, false).run();
"""


def focused(env: dict) -> str:
    """The focused window's (wm_class, app_id, title), as gdbus prints it."""
    return gdbus(env, "FocusedWindow")


def busy_pixels(png: bytes) -> int:
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("L")
    return len(set(img.get_flattened_data()))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pcbridge-nested-browser-"))
    data = tmp / "data"
    (data / "gnome-shell" / "extensions").mkdir(parents=True)
    (data / "gnome-shell" / "extensions" / EXT).symlink_to(ROOT / "gnome-extension" / EXT)
    state_dir = tmp / "state"
    (state_dir / "browser").mkdir(parents=True)
    log = tmp / "shell.log"
    env = dict(os.environ, XDG_DATA_HOME=str(data),
               PCBRIDGE_GORUNUR_STATE=str(tmp / "unlock.json"),
               PCBRIDGE_GORUNUR_STATUS=str(tmp / "status.json"),
               PCBRIDGE_GORUNUR_BROWSER=str(state_dir / "browser"),
               MUTTER_DEBUG_NUM_DUMMY_MONITORS="1", MUTTER_DEBUG_DUMMY_MODE_SPECS="1280x900")
    env.pop("WAYLAND_DISPLAY", None)
    before = set(p.name for p in Path(os.environ["XDG_RUNTIME_DIR"]).glob("wayland-*"))
    shell = subprocess.Popen(["dbus-run-session", "--", "gnome-shell", "--nested", "--wayland"],
                             env=env, stdout=open(log, "wb"), stderr=subprocess.STDOUT,
                             start_new_session=True)
    site = RouterSite()
    results: dict = {}
    try:
        display = bus = ""
        for _ in range(600):
            new = [p.name for p in Path(os.environ["XDG_RUNTIME_DIR"]).glob("wayland-*")
                   if p.name not in before and not p.name.endswith(".lock")]
            if new and not display:
                display = sorted(new)[0]
            pids = subprocess.run(["pgrep", "-s", str(shell.pid), "-x", "gnome-shell"],
                                  capture_output=True, text=True).stdout.split()
            for pid in pids:
                envs = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
                for e in envs:
                    if e.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                        bus = e.split(b"=", 1)[1].decode()
            if display and bus and gdbus(dict(env, DBUS_SESSION_BUS_ADDRESS=bus),
                                          "BrowserState").startswith("("):
                break
            time.sleep(0.1)
        else:
            lines = log.read_text(errors="replace").splitlines()
            print("\n".join(ln for ln in lines if "pcbridge" in ln or "JS ERROR" in ln)[-3000:])
            print(f"display={display!r} bus={bus!r}")
            raise SystemExit("the nested shell did not come up")
        nenv = dict(env, WAYLAND_DISPLAY=display, DBUS_SESSION_BUS_ADDRESS=bus,
                    XDG_SESSION_TYPE="wayland")
        print(f"nested shell: display {display}, bus {bus}")
        (tmp / "unlock.json").write_text(json.dumps({"until": time.time() + 900}))
        editor = subprocess.Popen(["gjs", "-c", USER_WINDOW], env=dict(nenv, GDK_BACKEND="wayland"),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        for _ in range(100):
            if "pcbridge-user-window" in focused(nenv):
                break
            time.sleep(0.1)
        print("user window focused:", focused(nenv))
        results["launch"] = asyncio.run(run(nenv, state_dir, site))
        os.killpg(editor.pid, signal.SIGTERM)
    finally:
        site.close()
        os.killpg(shell.pid, signal.SIGTERM)
        try:
            shell.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(shell.pid, signal.SIGKILL)
        subprocess.run([str(ROOT / "gnome-extension" / "nested.sh"), "--clean"])
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


async def run(env: dict, state_dir: Path, site: RouterSite) -> None:
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        cfg = SimpleNamespace(state_dir=state_dir,
                              browser=BrowserSpec(extra_args=["--password-store=basic", *EXTRA]))
        m = BrowserManager(cfg)
        seen = []

        async def sample() -> None:
            while True:
                seen.append(state(env) + (focused(env),))
                await asyncio.sleep(0.05)

        sampler = asyncio.create_task(sample())
        t0 = time.monotonic()
        await m.connect(start=True)
        t, _ = await m.navigate("open", site.url + "/login.html", None, False, 20)
        await asyncio.sleep(1.0)
        sampler.cancel()
        print(f"started in {time.monotonic() - t0:.1f} s; samples while starting: {len(seen)}; "
              f"ever visible: {any(s[1] for s in seen)}; ever focused: {any(s[2] for s in seen)}; "
              f"found at end: {seen[-1][0] if seen else None}; focus kept by: "
              f"{sorted(set(x[3] for x in seen))}")
        print("windows:", gdbus(env, "BrowserWindows"))

        # Hidden: load, act, screenshot.
        sid = await m.session(t.id)
        page = await m.describe(t, sid)
        user = re.search(r'"Username" \[(e\d+)\]', page).group(1)
        await m.act("type", t.id, user, "admin", False, 5)
        value = await m.evaluate(sid, "document.querySelector('input[name=user]').value")
        _t, png = await m.screenshot(t.id, False)
        print(f"hidden: state {state(env)}; typed value ok: {value == 'admin'}; "
              f"screenshot distinct gray levels: {busy_pixels(png)}")
        await m.navigate("open", site.url + "/wifi", None, False, 20)
        print("hidden: loaded", await m.evaluate(sid, "document.title"))

        print("show, no focus ->", m.view("show").describe(), state(env))
        for wait in (0.5, 1.0, 2.5):
            await asyncio.sleep(wait)
            print(f"  +{wait} s ->", state(env), focused(env))
        print("hide          ->", m.view("hide").describe(), state(env))
        await asyncio.sleep(0.5)
        print("show again, no focus ->", m.view("show").describe(), state(env))
        await asyncio.sleep(1.0)
        print("after 1 s ->", state(env), focused(env))
        print("show, focus   ->", m.view("show", focus=True).describe(), state(env))
        for wait in (0.3, 1.0):
            await asyncio.sleep(wait)
            print(f"  +{wait} s ->", state(env), focused(env))
        print("hide          ->", m.view("hide").describe(), state(env))
        _t, png = await m.screenshot(t.id, False)
        print(f"hidden again: screenshot distinct gray levels: {busy_pixels(png)}")
        await m.stop()
    finally:
        os.environ.clear()
        os.environ.update(saved)


if __name__ == "__main__":
    sys.exit(main())
