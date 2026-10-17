#!/usr/bin/env python3
"""Show and hide the pcbridge browser on KDE Plasma or Hyprland, inside the
test VMs (scripts/dev/arch-vm.sh, scripts/dev/hyprland-vm.sh).

Run in the VM's graphical session, with the system python3:

    scripts/dev/arch-vm.sh session 'cd ~/pcbridge && python3 scripts/dev/browser-vm-check.py'

A plain window stands in for the user's work and holds focus. The browser
(headed) starts on a throwaway profile, and the script measures what
scripts/dev/browser-nested.py measures on GNOME: whether the starting
browser is ever seen or focused, loading, typing and CDP screenshots while
hidden, show without focus, show with focus, hide.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.browser import visibility  # noqa: E402
from pcbridge.browser.manager import BrowserManager  # noqa: E402
from pcbridge.desktop import session  # noqa: E402
from tests.fixtures.browser.router_site import RouterSite  # noqa: E402


def focused_app() -> str:
    """The focused window's app id and title, from the compositor."""
    kind = session.desktop_kind()
    if kind == session.KDE:
        from pcbridge.desktop import apps

        r = apps._kwin({"cmd": "focused"}) or {}
        return f"{r.get('app', '')} | {r.get('title', '')}"
    if kind == session.HYPRLAND:
        out = subprocess.run(["hyprctl", "-j", "activewindow"], capture_output=True, text=True).stdout
        try:
            w = json.loads(out or "{}")
        except ValueError:
            return "?"
        return f"{w.get('class', '')} | {w.get('title', '')}"
    return "?"


def grey_levels(png: bytes) -> int:
    from PIL import Image

    return len(set(Image.open(io.BytesIO(png)).convert("L").get_flattened_data()))


async def run(args) -> None:
    backend = visibility.for_session()
    print(f"desktop: {session.desktop_kind()}, backend: {backend.name}")
    user = subprocess.Popen(args.user_window.split(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(100):
        if args.user_match in focused_app().lower():
            break
        await asyncio.sleep(0.1)
    print("user window:", focused_app())
    site = RouterSite()
    tmp = Path(tempfile.mkdtemp(prefix="pcbridge-vm-browser-"))
    # The VM's system python has no tomlkit, so not pcbridge.config's BrowserSpec.
    spec = SimpleNamespace(executable=args.exe, profile_dir="", allow_password_fields=False,
                           extra_args=["--password-store=basic"])
    m = BrowserManager(SimpleNamespace(state_dir=tmp, browser=spec))
    try:
        seen: list[tuple] = []

        async def sample() -> None:
            while True:
                info = m.status()
                if info:
                    v = await asyncio.to_thread(backend.state, int(info["browser_pid"]))
                    seen.append((v.found, v.visible, v.focused, await asyncio.to_thread(focused_app)))
                await asyncio.sleep(0.05)

        sampler = asyncio.create_task(sample())
        t0 = time.monotonic()
        await m.connect(start=True)
        t, _ = await m.navigate("open", site.url + "/login.html", None, False, 30)
        await asyncio.sleep(2.0)
        sampler.cancel()
        found = [s for s in seen if s[0]]
        print(f"started in {time.monotonic() - t0:.1f} s; samples {len(seen)}, with a window {len(found)}; "
              f"visible in {sum(1 for s in found if s[1])}, focused in {sum(1 for s in found if s[2])}; "
              f"focus seen: {sorted(set(s[3] for s in seen))}")
        print("now:", m.view("status").describe(), "| focus:", focused_app())

        sid = await m.session(t.id)
        page = await m.describe(t, sid)
        user_ref = re.search(r'"Username" \[(e\d+)\]', page).group(1)
        await m.act("type", t.id, user_ref, "admin", False, 5)
        typed = await m.evaluate(sid, "document.querySelector('input[name=user]').value")
        _t, png = await m.screenshot(t.id, False)
        await m.navigate("open", site.url + "/wifi", None, False, 20)
        title = await m.evaluate(sid, "document.title")
        print(f"hidden: typed ok {typed == 'admin'}; screenshot grey levels {grey_levels(png)}; "
              f"loaded {title!r}")

        print("show, no focus ->", m.view("show").describe())
        for wait in (0.5, 1.0, 2.5):
            await asyncio.sleep(wait)
            print(f"  +{wait} s ->", m.view("status").describe(), "| focus:", focused_app())
        print("hide ->", m.view("hide").describe())
        await asyncio.sleep(0.5)
        print("show again, no focus ->", m.view("show").describe())
        await asyncio.sleep(1.0)
        print("  +1 s ->", m.view("status").describe(), "| focus:", focused_app())
        print("show, focus ->", m.view("show", focus=True).describe())
        await asyncio.sleep(0.5)
        print("  +0.5 s ->", m.view("status").describe(), "| focus:", focused_app())
        print("hide ->", m.view("hide").describe())
        await asyncio.sleep(0.5)
        print("  +0.5 s ->", m.view("status").describe(), "| focus:", focused_app())
        _t, png = await m.screenshot(t.id, False)
        print(f"hidden again: screenshot grey levels {grey_levels(png)}")
        if args.listing:
            print(args.listing, subprocess.run(args.listing.split(), capture_output=True, text=True).stdout[:2000])
    finally:
        await m.stop()
        site.close()
        user.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", default="chromium")
    ap.add_argument("--user-window", default="foot", help="command for the stand-in user window")
    ap.add_argument("--user-match", default="foot", help="text in its app id or title")
    ap.add_argument("--listing", default="", help="a command whose output lists windows, printed at the end")
    asyncio.run(run(ap.parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
