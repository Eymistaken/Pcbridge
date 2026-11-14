"""`pcbridge upgrade`: the latest release, checked, installed and loaded.

The printed twin of the terminal UI's arrow: the same `pcbridge.upgrade`
steps, in a terminal or a script. `--check` only asks.
"""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from .. import upgrade as up
from . import install as inst

# `--check`: 0 up to date, 10 a newer release is out, 1 the check failed.
EXIT_AVAILABLE = 10


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pcbridge upgrade",
        description="Upgrade pcbridge to the latest release: download it, check it against the "
        "release's sha256sums.txt, install it the way this pcbridge was installed, then run the new "
        "version's setup, which restarts the daemon once no job runs and the desktop grant is closed. "
        "Settings and client connections stay as they are. (`pcbridge update` only loads code that is "
        "already installed.)")
    p.add_argument("--check", action="store_true",
                   help="Only say whether a newer release is out (exit code 10 if so, 0 if not, 1 if "
                        "the check failed).")
    p.add_argument("--yes", action="store_true", help="Do not ask before upgrading.")
    p.add_argument("--idle-wait", type=float, default=600,
                   help="Seconds to wait for running jobs and an open grant before restarting the "
                        "daemon (default 600); after that it restarts by itself once idle.")
    return p


def _progress() -> up.Progress | None:
    if not sys.stdout.isatty():
        return None
    last = [-1]

    def show(done: int, total: int) -> None:
        if not total:
            return
        pct = done * 100 // total
        if pct != last[0]:
            last[0] = pct
            end = "\n" if pct >= 100 else ""
            print(f"\r  downloading {done / 1e6:.1f} of {total / 1e6:.1f} MB  {pct}%", end=end, flush=True)

    return show


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    status = up.check(timeout=10, use_cache=False)
    if status.error:
        inst.warn(f"Could not check for a newer release: {status.error}. Nothing was changed.")
        return 1
    if not status.available:
        inst.ok(f"pcbridge {__version__} is the latest release.")
        return 0
    rel = status.release
    assert rel is not None
    when = f" (released {rel.published})" if rel.published else ""
    inst.say(f"pcbridge {__version__} → {rel.version}{when}")
    if args.check:
        inst.say(f"  What's new: {rel.url}")
        inst.say("  `pcbridge upgrade` installs it.")
        return EXIT_AVAILABLE
    kind = inst.install_kind()
    if kind in ("deb", "pacman"):
        inst.say("  A package install: sudo asks for your password to install the new package.")
    if not args.yes and sys.stdin.isatty():
        try:
            answer = input("Upgrade now? [Y/n] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer not in ("", "y", "yes"):
            inst.say("Nothing was changed.")
            return 1
    try:
        up.install(rel, kind=kind, progress=_progress())
    except up.UpgradeError as exc:
        inst.fail(str(exc))
        return 1
    inst.say("  Finishing with the new version's setup (service, launchers, desktop extension)…")
    ok, daemon = up.finish(args.idle_wait, line=_setup_line)
    (inst.ok if ok and daemon.startswith("daemon") else inst.warn)(daemon)
    if not ok:
        return 1
    inst.say(f"Upgraded to {rel.version}. What's new: {rel.url}")
    inst.say(f"  The new version's setup output: {up.short_home(up.log_path())}")
    return 0


def _setup_line(text: str) -> None:
    """Setup's own lines are in the log; only waiting is worth showing here."""
    if text.startswith("waiting before the restart"):
        inst.say(f"  {text}")


def status_line(timeout: float = 3.0) -> tuple[str, str] | None:
    """`pcbridge status`'s upgrade line: (level, text), None when unknown."""
    st = up.check(timeout=timeout)
    if st.error:
        return None
    if st.available:
        return "upgrade", f"upgrade: {st.latest} is out (`pcbridge upgrade`)"
    return "ok", f"upgrade: {__version__} is the latest release"


__all__ = ["EXIT_AVAILABLE", "main", "parser", "status_line"]
