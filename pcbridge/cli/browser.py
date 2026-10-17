"""`pcbridge browser`: the pcbridge browser from a terminal.

It reads the keeper's record and signals the keeper; it never connects to
the browser's CDP socket, which belongs to the daemon (the keeper takes one
client at a time, so a second one would cut the daemon off).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from . import install as inst


def _manager():
    from . import load
    from ..browser.manager import BrowserManager

    return BrowserManager(load())


def status(args: argparse.Namespace) -> int:
    m = _manager()
    info = m.status()
    if args.json:
        print(json.dumps({"running": info is not None, "profile": str(m.profile), **(info or {})}))
        return 0
    if info is None:
        inst.say(f"The pcbridge browser is not running. Profile: {m.profile}")
        return 0
    window = m.view("status").describe()
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(info.get("started_at", 0)))
    inst.ok(f"The pcbridge browser is running: {info.get('executable')} "
            f"(pid {info.get('browser_pid')}, since {since}). Profile: {m.profile}")
    inst.say(f"  {window}")
    return 0


def stop(args: argparse.Namespace) -> int:
    if asyncio.run(_manager().stop()):
        inst.ok("The pcbridge browser is closed; its sign-ins stay in its profile.")
    else:
        inst.say("The pcbridge browser was not running.")
    return 0


def view(args: argparse.Namespace) -> int:
    v = _manager().view(args.action, getattr(args, "focus", False))
    if args.action != "status" and not v.found:
        inst.fail(v.describe())
        return 1
    (inst.ok if v.found else inst.say)(v.describe())
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="pcbridge browser",
        description="The browser pcbridge runs on its own profile for the browser_* tools.",
    )
    sub = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    s = sub.add_parser("status", help="Whether it runs, which browser, where its profile is.")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=status)
    sub.add_parser("stop", help="Close it cleanly; the sign-ins stay.").set_defaults(func=stop)
    show = sub.add_parser("show", help="Put its window on screen, without taking focus.")
    show.add_argument("--focus", action="store_true", help="Also give it keyboard focus.")
    show.set_defaults(func=view)
    sub.add_parser("hide", help="Take its window off screen; it keeps working.").set_defaults(func=view)
    args = p.parse_args(argv)
    return args.func(args)
