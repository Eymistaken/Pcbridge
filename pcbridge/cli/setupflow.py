"""`pcbridge setup` in two halves: deciding what to do, and doing it.

`prepare()` detects the machine and builds the plan; nothing changes yet.
`execute()` runs the steps: packages, configuration, commands, service,
desktop integration, clients, aliases and the readiness check. The printed
setup (`run_plain`, also `--yes` and a run without a terminal) and the
setup screen (`pcbridge/tui/setup_app.py`) share both halves; the screen
only takes the answers with the arrow keys and the mouse and draws the
lines `install.py` emits as rows instead of printing them.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import oscompat
from .. import __version__
from .. import distro as distrolib
from .. import paths as pathslib
from ..platforminfo import Platform
from . import connect as connectlib
from . import install as inst
from . import plan as planlib


@dataclass
class Context:
    """What setup found and planned, before anything changes."""

    args: argparse.Namespace
    found: Platform
    plan: planlib.Plan
    xdg: Path
    backup: inst.Backup = field(default_factory=inst.Backup)


@dataclass
class Outcome:
    """What a run did, for the summary or the last screen."""

    ready: bool
    tools: int | str
    want: int
    notes: list[str]
    clients: list[tuple[str, str, str]]
    rollback: Path | None
    path_fix: inst.PathFix | None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pcbridge setup", description="Install or update pcbridge for this user. "
        "Detects the machine, shows the plan and asks once; run it again after an update. "
        "On a terminal the choices are a screen (arrow keys or the mouse); --yes, --plain "
        "or a run without a terminal print the steps instead.")
    p.add_argument("--yes", action="store_true", help="Do not ask; take the defaults (installs the packages).")
    p.add_argument("--plain", action="store_true",
                   help="Print the steps and ask in the terminal instead of the setup screen.")
    p.add_argument("--optional", metavar="LIST",
                   help="Optional components to install, comma-separated (ocr), or none. "
                        "Default: every one the plan ticks.")
    p.add_argument("--no-packages", action="store_true",
                   help="Install no system packages; print the command instead.")
    p.add_argument("--from-config", help="Migrate this pre-2.0 config.toml instead of the one found automatically.")
    p.add_argument("--no-connect", action="store_true", help="Do not register MCP clients.")
    p.add_argument("--no-aliases", action="store_true", help="Leave ~/.bashrc alone.")
    p.add_argument("--no-extension", action="store_true")
    p.add_argument("--idle-wait", type=float, default=600, help="Seconds to wait for running jobs before restarting (default 600).")
    return p


def wants_screen(args: argparse.Namespace) -> bool:
    """The setup screen needs a real terminal and a person to answer it."""
    if args.yes or args.plain or os.environ.get("PCBRIDGE_SETUP_PLAIN"):
        return False
    if os.environ.get("TERM", "dumb") in ("", "dumb"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def prepare(args: argparse.Namespace) -> Context:
    """Detect the machine and build the plan. Changes nothing."""
    from .. import platforminfo
    from .. import settings as settingslib

    xdg = pathslib.config_file()
    found = platforminfo.detect()
    profile_now = "full"
    if xdg.exists():
        try:
            profile_now = settingslib.ConfigEditor(xdg).value("tools.profile")
        except settingslib.SettingsError as exc:
            inst.warn(str(exc))
    plan = planlib.build(found, profile_now=profile_now,
                         recorded_profile=planlib.read_state().get("profile"))
    plan.desktop_on = desktop_enabled(xdg)
    if args.optional is not None:
        wanted = {w.strip().lower() for w in args.optional.split(",") if w.strip()}
        unknown = wanted - {c.key for c in plan.components} - {"none"}
        if unknown:
            inst.warn("not offered on this machine: " + ", ".join(sorted(unknown)))
        for c in plan.components:
            c.ticked = c.key in wanted
    return Context(args=args, found=found, plan=plan, xdg=xdg)


def desktop_enabled(xdg: Path) -> bool:
    """`[desktop] enabled` as it is after setup: a config setup creates turns it
    on; an existing one keeps the user's value."""
    if not xdg.exists():
        return True
    from .. import settings as settingslib

    try:
        return bool(settingslib.ConfigEditor(xdg).value("desktop.enabled"))
    except Exception:  # noqa: BLE001 - an unreadable config: say what pcbridge then does
        return False


def desktop_title(kind: str) -> str:
    from ..desktop.session import CINNAMON, GNOME, HYPRLAND, KDE, WINDOWS, XFCE

    return {CINNAMON: "Cinnamon", XFCE: "Xfce", KDE: "KDE Plasma", GNOME: "GNOME Shell extension",
            HYPRLAND: "Hyprland", WINDOWS: "Windows"}.get(kind, "Desktop integration")


def steps(ctx: Context) -> list[tuple[str, str]]:
    """The rows `execute` will report, in order: (key, title)."""
    args, plan = ctx.args, ctx.plan
    out = [("packages", "System packages")]
    if plan.extras:
        out.append(("python", "Python packages"))
    if plan.native_fetch:
        out.append(("native", "Native helper"))
    out += [("config", "Configuration"), ("commands", "Commands"), ("service", "Background service")]
    if not args.no_extension:
        out.append(("desktop", desktop_title(ctx.found.desktop_kind)))
    if not args.no_connect:
        out.append(("clients", "MCP clients"))
    if not args.no_aliases:
        out.append(("aliases", "Shell aliases"))
    out.append(("ready", "Readiness check"))
    return out


def execute(ctx: Context, install_now: bool, clients: list[str] | None = None) -> Outcome:
    """Run every step. `clients`: the ones to connect (None: the default three)."""
    from .. import config as configlib
    from . import ops

    args, plan, found, xdg, backup = ctx.args, ctx.plan, ctx.found, ctx.xdg, ctx.backup
    notes: list[str] = []

    inst.step("packages", "System packages", 1)
    chosen = plan.chosen_packages()
    if not chosen:
        inst.ok("all present")
    elif install_now:
        done, left = planlib.install_packages(plan)
        if done:
            inst.ok("installed: " + " ".join(chosen))
        else:
            inst.warn("not installed: " + " ".join(chosen))
            notes.append(left)
    else:
        argv_ = plan.install_argv()
        command = shlex.join(argv_) if argv_ else distrolib.install_command(raw=tuple(chosen), fam=plan.family)
        inst.warn("missing: " + " ".join(chosen))
        inst.say(f"          install with: {command}")
        notes.append(command)
    if plan.extras:
        inst.step("python", "Python packages")
        if planlib.install_extras():
            inst.ok("Python package for desktop control: Pillow")
        else:
            inst.warn("Pillow was not installed; desktop control needs it")
            notes.append("pcbridge setup   # tries Pillow again")
    if plan.native_fetch:
        inst.step("native", "Native helper")
        # A download of an executable waits for the same yes as the packages:
        # --yes, or the plan confirmed on a terminal (a "no" returned before).
        if args.yes or planlib.interactive():
            fetched, line = planlib.fetch_native()
            (inst.ok if fetched else inst.warn)(line)
            if not fetched:
                notes.append("pcbridge doctor --fix   # tries the native helper download again")
        else:
            inst.warn("native helper: not downloaded without a terminal or --yes")
            notes.append("pcbridge doctor --fix   # downloads the native helper")
    if (found.desktop_supported and not oscompat.IS_WINDOWS
            and not os.access("/dev/uinput", os.R_OK | os.W_OK)):
        from .doctor import udev_hint

        inst.warn("/dev/uinput is not writable: keyboard and pointer control will not work")
        inst.say(f"          fix with: {udev_hint()}")
        notes.append(udev_hint())

    inst.step("config", "Configuration", 2)
    if xdg.exists():
        res = configlib.migrate_config(xdg, xdg)
        inst.ok(f"{xdg} " + ("migrated to config_version %d" % res.to_version if res.changed else "is current"))
        if res.backup:
            backup.entries.append(("copy", str(xdg), str(res.backup)))
    else:
        src = Path(args.from_config).expanduser() if args.from_config else None
        cands = [src] if src else ops._legacy_config_candidates()
        if cands and cands[0] and cands[0].exists():
            res = configlib.migrate_config(cands[0], xdg)
            inst.ok(f"migrated {cands[0]} -> {xdg} (the old file is left in place)")
        else:
            ops._fresh_config(xdg, desktop=found.desktop_supported)
            inst.ok(f"created {xdg}; desktop control is "
                    + ("ON" if found.desktop_supported else "OFF (not supported here)")
                    + " and remote access has no password yet")
    for line in planlib.apply_settings(plan, xdg):
        inst.ok(line)
    settings_changed = plan.profile_target is not None
    cfg = configlib.load_config(str(xdg))
    db = Path(cfg.db_path)
    if db.exists():
        oscompat.restrict_path(db)

    inst.step("commands", "Commands", 3)
    for n in inst.install_launchers(backup):
        inst.ok(n)
    inst.ok(f"launcher: {inst.launcher_path()}")
    inst.ok(inst.install_skill_link(backup))
    path_note = inst.path_note()
    if path_note:
        inst.warn(path_note)
        notes.append(path_note)

    inst.step("service", "Background service", 4)
    changed = inst.install_units(backup)
    if oscompat.IS_WINDOWS:
        inst.ok("the supervisor starts at logon from the Run key "
                "(HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run)")
    elif inst.units_managed_by_package():
        inst.ok("units provided by the package in /usr/lib/systemd/user"
                + ("; leftover user units moved to the backup" if changed else ""))
    else:
        inst.ok("units " + ("installed" if changed else "up to date") + f" in {inst.USER_UNIT_DIR}")
    # Stamp BEFORE the restart: the new daemon then reads it as its starting
    # point. Written after, the daemon saw a "newer" install five seconds
    # later and restarted itself once more (seen in the journal, 2.0 install).
    inst.write_version_stamp()
    running_daemon = False
    from .doctor import handshake

    try:
        running_daemon = ops.runs_installed_code(handshake(pathslib.socket_path()))
    except pathslib.RuntimeDirError:
        pass
    if running_daemon and not changed and not settings_changed:
        inst.systemctl("enable", *inst.UNIT_NAMES)
        inst.ok("daemon already running the installed code")
    else:
        msg = ops._restart_when_idle(cfg, args.idle_wait)
        if msg.startswith("daemon"):
            inst.systemctl("enable", *inst.UNIT_NAMES)
            inst.ok(msg + "; socket and service enabled (start at login)")
        else:
            inst.warn(msg)
            notes.append("pcbridge update   # finishes the service switch when no job is running")

    ops.setup_desktop(cfg.native, backup, no_extension=args.no_extension, kind=found.desktop_kind)

    results: list[tuple[str, str, str]] = []
    if not args.no_connect:
        inst.step("clients", "MCP clients", 6)
        results = connectlib.connect(list(connectlib.CLIENTS if clients is None else clients), backup)
        for client, st, detail in results:
            (inst.warn if st == "warn" else inst.ok)(f"{client}: {detail}")
        if not results:
            inst.ok("none chosen; `pcbridge connect NAME` adds one later")

    if not args.no_aliases:
        inst.step("aliases", "Shell aliases", 7)
        inst.ok(inst.write_aliases(backup))

    inst.step("ready", "Readiness", 8)
    from .doctor import probe

    want = ops.expected_tools(cfg.tools_profile)
    t0 = time.monotonic()
    n = probe(inst.client_command())
    ready = isinstance(n, int) and n >= want
    if ready:
        inst.ok(f"a fresh client got {n} tools (profile \"{cfg.tools_profile}\") in {(time.monotonic() - t0) * 1000:.0f} ms")
    else:
        inst.fail(f"a fresh client failed: {n}" + (f" (expected {want})" if isinstance(n, int) else ""))
    outcome = Outcome(ready=ready, tools=n, want=want, notes=notes, clients=results, rollback=None,
                      path_fix=inst.path_fix() if path_note else None)
    write_rollback(ctx, outcome)
    return outcome


def write_rollback(ctx: Context, outcome: Outcome) -> None:
    """ROLLBACK.md for this run; again after a later change (the PATH line)."""
    notes = outcome.notes
    outcome.rollback = ctx.backup.write_rollback(
        extra=["Commands that still need you:", *[f"    {n}" for n in notes]] if notes else None)


def add_path_line(ctx: Context, outcome: Outcome) -> str:
    """The user said yes: put ~/.local/bin on PATH in their shell's file."""
    fix = outcome.path_fix
    if fix is None:
        return ""
    line = inst.apply_path_fix(fix, ctx.backup)
    outcome.notes = [n for n in outcome.notes
                     if not n.startswith((str(inst.LOCAL_BIN), str(fix.file)))]
    outcome.path_fix = None
    write_rollback(ctx, outcome)
    return line


def _ask_yes(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_plain(args: argparse.Namespace) -> int:
    """The printed setup: the plan, one question, then every step as a line."""
    from . import ops

    inst.say(f"pcbridge {__version__} setup ({inst.install_kind()} install)\n")
    ctx = prepare(args)
    plan = ctx.plan
    planlib.show(plan)
    install_now = bool(plan.chosen_packages()) and not args.no_packages
    if args.yes:
        pass
    elif planlib.interactive():
        if not planlib.ask(plan):
            inst.say("Stopped; nothing was changed.")
            return 1
        install_now = bool(plan.chosen_packages()) and not args.no_packages
    elif install_now:
        inst.say("Not a terminal and no --yes: the packages are left for you (the command is repeated at the end).")
        install_now = False
    outcome = execute(ctx, install_now)
    ops._offer_remote_password(args, ctx.xdg)
    fix = outcome.path_fix
    if fix is not None and fix.shell == "windows" and (args.yes or planlib.interactive()):
        # A Windows installer puts its commands on the user's PATH; this one
        # does too, after the plan was accepted (listed in ROLLBACK.md).
        inst.ok(add_path_line(ctx, outcome) + "; open a new terminal for it to apply")
        fix = None
    if fix is not None and not args.yes and planlib.interactive():
        inst.say(f"\n`pcbridge` is not found by name: {inst.LOCAL_BIN} is not on PATH.")
        inst.say(f"  Setup can add this line to {fix.file} (backed up, listed in ROLLBACK.md):")
        inst.say(f"    {fix.line}")
        if _ask_yes("  Add it? [y/N] "):
            inst.ok(add_path_line(ctx, outcome) + "; open a new terminal for it to apply")
        else:
            inst.say("  Left alone.")
    if outcome.rollback:
        inst.say(f"\nBackups and undo instructions: {outcome.rollback}")
    inst.say("\nSummary")
    plan.desktop_on = desktop_enabled(ctx.xdg)
    for line in planlib.summary(plan):
        inst.say(f"  {line}")
    if outcome.notes:
        inst.say("\nStill to do by hand:")
        for n_ in outcome.notes:
            inst.say(f"  {n_}")
    return 0 if outcome.ready else 1


__all__ = ["Context", "Outcome", "add_path_line", "desktop_enabled", "desktop_title", "execute", "parser", "prepare",
           "run_plain", "steps", "wants_screen", "write_rollback"]
