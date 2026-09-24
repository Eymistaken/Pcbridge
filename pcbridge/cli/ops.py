"""The operating subcommands of `pcbridge`: setup, status, lock, unlock, stop,
remote, logs, report, update and uninstall. Output is English; `--json`
where a program might read it; `--yes` never asks.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path

from .. import __version__
from .. import assets as assetslib
from .. import distro as distrolib
from .. import paths as pathslib
from . import connect as connectlib
from . import install as inst

REQUIRED_COMMANDS = {
    # command -> what provides it (a logical name in pcbridge.distro)
    "tmux": "tmux",
    "wl-copy": "wl-clipboard",
    "notify-send": "notify-send",
    "script": "script",
}


def _load_cfg():
    from ..config import load_config

    return load_config()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status(argv: list[str]) -> int:
    from .doctor import handshake

    p = argparse.ArgumentParser(prog="pcbridge status", description="Show whether pcbridge is running and what it is doing.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    out: dict = {"version": __version__, "install": inst.install_kind()}
    try:
        sock = pathslib.socket_path()
        reply = handshake(sock)
    except pathslib.RuntimeDirError as exc:
        sock, reply = None, str(exc)
    out["daemon"] = reply if isinstance(reply, dict) else {"reachable": False, "why": reply}
    out["service"] = inst.systemctl("is-active", "pcbridge.service").stdout.strip()
    out["socket_unit"] = inst.systemctl("is-active", "pcbridge.socket").stdout.strip()
    try:
        cfg = _load_cfg()
    except SystemExit as exc:
        cfg = None
        out["config"] = str(exc)
    if cfg is not None:
        from .grant import read_state

        grant = read_state(cfg)
        out["desktop_grant"] = {"open": grant.open, "seconds_left": grant.seconds_left,
                                "enabled_in_config": grant.enabled_in_config}
        from ..jobs import JobManager

        running = JobManager(cfg.jobs_dir).list_jobs(limit=50, only_running=True)
        out["jobs_running"] = [{"id": j["job_id"], "kind": j["kind"], "label": j["label"][:60]} for j in running]
        st = inst.run(["tailscale", "funnel", "status"], timeout=10).stdout if shutil.which("tailscale") else ""
        out["remote_tunnel"] = "open" if f":{cfg.port}" in st else "closed"
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    d = out["daemon"]
    if d.get("reachable") is False:
        inst.warn(f"daemon: not reachable ({d['why']}); clients use an in-process server")
    else:
        inst.ok(f"daemon: pcbridge {d.get('version')} pid {d.get('pid')} (service {out['service']}, socket {out['socket_unit']})")
    if "desktop_grant" in out:
        g = out["desktop_grant"]
        inst.ok("desktop grant: " + (f"OPEN, {g['seconds_left']} s left" if g["open"] else "closed")
                + ("" if g["enabled_in_config"] else " (desktop control disabled in config)"))
        inst.ok(f"jobs running: {len(out['jobs_running'])}")
        for j in out["jobs_running"]:
            inst.say(f"          {j['id']}  {j['kind']}  {j['label']}")
        inst.ok(f"remote tunnel: {out['remote_tunnel']}")
    return 0


# ---------------------------------------------------------------------------
# lock / unlock / stop
# ---------------------------------------------------------------------------


def lock(argv: list[str]) -> int:
    argparse.ArgumentParser(
        prog="pcbridge lock",
        description="Emergency stop for desktop control: close the grant and stop every screen share.",
    ).parse_args(argv)
    from .lock import main as lock_main

    return lock_main([])


def unlock(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge unlock", description="Open desktop control for agents (for humans).")
    p.add_argument("--minutes", type=int, default=15)
    p.add_argument("--reason", default="opened with pcbridge unlock")
    args = p.parse_args(argv)
    from . import load
    from .grant import GrantError, unlock as open_grant

    try:
        inst.say(open_grant(load(), args.minutes, args.reason, granted_by="pcbridge unlock"))
    except GrantError as exc:
        inst.fail(str(exc))
        return 1
    return 0


def stop(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="pcbridge stop",
        description="Stop everything: close the desktop grant, stop the daemon and end every running job.",
    )
    p.add_argument("--keep-jobs", action="store_true", help="Leave running jobs alone.")
    args = p.parse_args(argv)
    lock([])
    inst.systemctl("stop", "pcbridge.service")
    inst.ok("daemon stopped (the socket stays; the next client starts it again)")
    if args.keep_jobs:
        return 0
    units = inst.systemctl("list-units", "--plain", "--no-legend", "pcbridge-job-*.scope").stdout.split()
    scopes = [u for u in units if u.startswith("pcbridge-job-")]
    if scopes:
        inst.systemctl("stop", *scopes)
    try:
        cfg = _load_cfg()
        from ..jobs import JobManager

        jm = JobManager(cfg.jobs_dir)
        rest = jm.list_jobs(limit=200, only_running=True)
        for j in rest:
            jm.cancel(j["job_id"])
    except SystemExit:
        rest = []
    inst.ok(f"jobs stopped: {len(scopes)} scoped, {len(rest)} other")
    return 0


# ---------------------------------------------------------------------------
# remote
# ---------------------------------------------------------------------------


def remote(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge remote", description="Open or close remote access through Tailscale Funnel.")
    p.add_argument("action", choices=["start", "stop", "status"])
    args = p.parse_args(argv)
    cfg = _load_cfg()
    if not shutil.which("tailscale"):
        inst.fail("tailscale is not installed: https://tailscale.com/download/linux")
        return 1
    port = str(cfg.port)

    def funnel_open() -> bool:
        return f":{port}" in inst.run(["tailscale", "funnel", "status"], timeout=10).stdout

    def health() -> bool:
        import urllib.request

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                return r.status == 200
        except OSError:
            return False

    if args.action == "status":
        (inst.ok if health() else inst.warn)(f"local endpoint http://127.0.0.1:{port}/healthz {'answers' if health() else 'does not answer'}")
        (inst.ok if funnel_open() else inst.warn)(f"funnel {'open' if funnel_open() else 'closed'}; remote address {cfg.mcp_url}")
        return 0
    if args.action == "start":
        inst.systemctl("start", "pcbridge.service")
        for _ in range(25):
            if health():
                break
            time.sleep(0.4)
        if not health():
            inst.fail("pcbridge does not answer locally; see `pcbridge logs`")
            return 1
        if funnel_open():
            inst.ok(f"funnel already open: {cfg.mcp_url}")
            return 0
        res = inst.run(["tailscale", "funnel", "--bg", port], timeout=30)
        if res.returncode != 0:
            inst.fail("tailscale refused: " + (res.stderr or res.stdout).strip()[:300])
            inst.say(f"  If it needs root, run: sudo tailscale funnel --bg {port}")
            inst.say("  If HTTPS is not enabled: https://login.tailscale.com/admin/dns")
            return 1
        inst.ok(f"remote access open: give remote clients {cfg.mcp_url}")
        return 0
    # stop: close the tunnel only. The daemon keeps serving local clients.
    res = inst.run(["tailscale", "funnel", "--bg", port, "off"], timeout=30)
    if res.returncode != 0 and funnel_open():
        inst.fail("tailscale refused: " + (res.stderr or res.stdout).strip()[:300])
        inst.say(f"  If it needs root, run: sudo tailscale funnel --bg {port} off")
        return 1
    inst.ok("remote access closed (local clients keep working)")
    return 0


# ---------------------------------------------------------------------------
# logs / report
# ---------------------------------------------------------------------------


def logs(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge logs", description="Show the daemon's log (systemd journal).")
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("-n", "--lines", type=int, default=200)
    args = p.parse_args(argv)
    cmd = ["journalctl", "--user", "-u", "pcbridge.service", "-u", "pcbridge.socket", "-n", str(args.lines), "--no-pager"]
    if args.follow:
        cmd.append("-f")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


_SECRET_KEYS = re.compile(r'(?im)^(\s*(password|static_token|token|secret)[\w]*\s*=\s*)(".*?"|\'.*?\'|\S+)')


def _redact(text: str, secrets_: list[str]) -> str:
    for s in secrets_:
        if s and len(s) >= 6:
            text = text.replace(s, "<redacted>")
    text = _SECRET_KEYS.sub(r'\1"<redacted>"', text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1<redacted>", text)
    return text.replace(str(Path.home()), "~")


def report(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge report", description="Write a sanitized bug-report bundle.")
    p.add_argument("-o", "--output", help="Where to write the .tar.gz (default: the current directory).")
    args = p.parse_args(argv)
    from .doctor import Doctor

    try:
        cfg = _load_cfg()
        secret_values = [cfg.password, cfg.static_token]
        raw_cfg = Path(cfg.source_path).read_text(encoding="utf-8")
    except (SystemExit, OSError):
        cfg, secret_values, raw_cfg = None, [], ""
    doc = Doctor(fix=False)
    checks = [c.__dict__ for c in doc.run_all()]
    journal = inst.run(["journalctl", "--user", "-u", "pcbridge.service", "-n", "2000", "--no-pager"], timeout=30).stdout
    files = {
        "versions.txt": "\n".join([
            f"pcbridge {__version__} ({inst.install_kind()})",
            f"python {sys.version.split()[0]}",
            inst.run(["gnome-shell", "--version"]).stdout.strip(),
            inst.run(["plasmashell", "--version"]).stdout.strip(),
            open("/etc/os-release").read() if os.path.exists("/etc/os-release") else "",
        ]),
        "doctor.json": json.dumps(checks, indent=2),
        "journal.txt": journal,
        "config.toml": raw_cfg,
    }
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.output) if args.output else Path.cwd() / f"pcbridge-report-{stamp}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for name, text in files.items():
            data = _redact(text, secret_values).encode("utf-8")
            info = tarfile.TarInfo(f"pcbridge-report-{stamp}/{name}")
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    os.chmod(out, 0o600)
    inst.ok(f"report written: {out} (passwords, tokens and your home path are redacted; read it before sharing)")
    return 0


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def _legacy_config_candidates() -> list[Path]:
    """Config files of a pre-2.0 git install, found through what points at it."""
    found: list[Path] = []

    def add_from_python(py: str) -> None:
        # <repo>/.venv/bin/python -> <repo>/config.toml
        path = Path(py)
        if path.parent.name == "bin" and path.parent.parent.name == ".venv":
            cand = path.parent.parent.parent / "config.toml"
            if cand.exists() and cand not in found:
                found.append(cand)

    for client in connectlib.CLIENTS:
        reg = connectlib.current(client)
        if reg.present and reg.command[1:3] == ["-m", "pcbridge.server"]:
            add_from_python(reg.command[0])
    unit = inst.USER_UNIT_DIR / "pcbridge.service"
    if unit.exists():
        m = re.search(r"^ExecStart=(\S+)", unit.read_text(), re.M)
        if m:
            add_from_python(m.group(1))
    root = inst.repo_root()
    if root and (root / "config.toml").exists() and (root / "config.toml") not in found:
        found.append(root / "config.toml")
    return found


def _fresh_config(dest: Path) -> None:
    text = assetslib.asset_path("config.example.toml").read_text(encoding="utf-8")
    text = re.sub(r'(?m)^public_url = .*$', 'public_url = "http://localhost:8765"', text, count=1)
    text = re.sub(r'(?m)^password = .*$', f'password = "{secrets.token_urlsafe(24)}"', text, count=1)
    text = re.sub(r'(?m)^static_token = .*$', f'static_token = "{secrets.token_urlsafe(32)}"', text, count=1)
    tomllib.loads(text)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _deps_report() -> list[str]:
    """The logical needs (see `pcbridge.distro`) that are not installed."""
    missing = [need for cmd, need in REQUIRED_COMMANDS.items() if not shutil.which(cmd)]
    if session_desktop() == "gnome" and not shutil.which("gnome-screenshot"):
        missing.append("gnome-screenshot")
    probe = inst.run(["/usr/bin/python3", "-c", "import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi"])
    if probe.returncode != 0:
        missing.append("atspi")
    if not shutil.which("tesseract"):
        missing.append("tesseract")
    return missing


def session_desktop() -> str:
    from ..desktop.session import desktop_kind

    return desktop_kind()


def _restart_when_idle(cfg, wait_s: float) -> str:
    """Restart pcbridge.service into the daemon, only when nothing is running (I5)."""
    deadline = time.monotonic() + wait_s
    while True:
        why = inst.idle_reason(cfg)
        if not why:
            break
        if time.monotonic() >= deadline:
            return f"deferred: {why}. Run `pcbridge update` when it is done."
        time.sleep(5)
    inst.systemctl("start", "pcbridge.socket")
    res = inst.systemctl("restart", "pcbridge.service", timeout=60)
    if res.returncode != 0:
        return "restart failed: " + (res.stderr or res.stdout).strip()[:200]
    from .doctor import handshake

    for _ in range(40):
        reply = handshake(pathslib.socket_path())
        if isinstance(reply, dict):
            return f"daemon {reply.get('version')} running, pid {reply.get('pid')}"
        time.sleep(0.25)
    return "restarted, but the daemon does not answer on the socket yet; see `pcbridge logs`"


def setup(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge setup", description="Install or update pcbridge for this user.")
    p.add_argument("--yes", action="store_true", help="Do not ask; take the defaults.")
    p.add_argument("--from-config", help="Migrate this pre-2.0 config.toml instead of the one found automatically.")
    p.add_argument("--no-connect", action="store_true", help="Do not register MCP clients.")
    p.add_argument("--no-aliases", action="store_true", help="Leave ~/.bashrc alone.")
    p.add_argument("--no-extension", action="store_true")
    p.add_argument("--idle-wait", type=float, default=600, help="Seconds to wait for running jobs before restarting (default 600).")
    args = p.parse_args(argv)
    from .. import config as configlib

    backup = inst.Backup()
    notes: list[str] = []
    inst.say(f"pcbridge {__version__} setup ({inst.install_kind()} install)\n")

    inst.say("1. System packages")
    missing = _deps_report()
    if missing:
        command = distrolib.install_command(*missing)
        inst.warn("missing: " + ", ".join(distrolib.packages(*missing)))
        inst.say(f"          install with: {command}")
        notes.append(command)
    else:
        inst.ok("all present")
    if not os.access("/dev/uinput", os.R_OK | os.W_OK):
        from .doctor import udev_hint

        inst.warn("/dev/uinput is not writable: keyboard and pointer control will not work")
        inst.say(f"          fix with: {udev_hint()}")
        notes.append(udev_hint())

    inst.say("\n2. Configuration")
    xdg = pathslib.config_file()
    if xdg.exists():
        res = configlib.migrate_config(xdg, xdg)
        inst.ok(f"{xdg} " + ("migrated to config_version %d" % res.to_version if res.changed else "is current"))
        if res.backup:
            backup.entries.append(("copy", str(xdg), str(res.backup)))
    else:
        src = Path(args.from_config).expanduser() if args.from_config else None
        cands = [src] if src else _legacy_config_candidates()
        if cands and cands[0] and cands[0].exists():
            res = configlib.migrate_config(cands[0], xdg)
            inst.ok(f"migrated {cands[0]} -> {xdg} (the old file is left in place)")
        else:
            _fresh_config(xdg)
            inst.ok(f"created {xdg} with a new password and token; desktop control is OFF until you enable it there")
    cfg = configlib.load_config(str(xdg))
    db = Path(cfg.db_path)
    if db.exists():
        os.chmod(db, 0o600)

    inst.say("\n3. Commands")
    for n in inst.install_launchers(backup):
        inst.ok(n)
    inst.ok(f"launcher: {inst.launcher_path()}")

    inst.say("\n4. Background service")
    changed = inst.install_units(backup)
    if inst.units_managed_by_package():
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
        reply = handshake(pathslib.socket_path())
        running_daemon = isinstance(reply, dict) and reply.get("version") == __version__
    except pathslib.RuntimeDirError:
        pass
    if running_daemon and not changed:
        inst.systemctl("enable", *inst.UNIT_NAMES)
        inst.ok("daemon already running this version")
    else:
        msg = _restart_when_idle(cfg, args.idle_wait)
        if msg.startswith("daemon"):
            inst.systemctl("enable", *inst.UNIT_NAMES)
            inst.ok(msg + "; socket and service enabled (start at login)")
        else:
            inst.warn(msg)
            notes.append("pcbridge update   # finishes the service switch when no job is running")

    if not args.no_extension:
        from ..desktop.session import KDE, desktop_kind

        if desktop_kind() == KDE:
            inst.say("\n5. KDE Plasma")
            for line in inst.install_kde_entries(cfg.native):
                (inst.warn if line.startswith("native helper not found") else inst.ok)(line)
        else:
            inst.say("\n5. GNOME Shell extension")
            inst.ok(inst.install_extension(backup))

    if not args.no_connect:
        inst.say("\n6. MCP clients")
        for client, st, detail in connectlib.connect(list(connectlib.CLIENTS), backup):
            (inst.warn if st == "warn" else inst.ok)(f"{client}: {detail}")

    if not args.no_aliases:
        inst.say("\n7. Shell aliases")
        inst.ok(inst.write_aliases(backup))

    inst.say("\n8. Readiness")
    from .doctor import probe

    t0 = time.monotonic()
    n = probe(inst.client_command())
    if isinstance(n, int) and n >= 37:
        inst.ok(f"a fresh client got {n} tools in {(time.monotonic() - t0) * 1000:.0f} ms")
    else:
        inst.fail(f"a fresh client failed: {n}")
    rb = backup.write_rollback(extra=["Commands that still need you:", *[f"    {n}" for n in notes]] if notes else None)
    if rb:
        inst.say(f"\nBackups and undo instructions: {rb}")
    if notes:
        inst.say("\nStill to do by hand:")
        for n_ in notes:
            inst.say(f"  {n_}")
    return 0 if isinstance(n, int) and n >= 37 else 1


# ---------------------------------------------------------------------------
# update / uninstall
# ---------------------------------------------------------------------------


def update(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge update", description="Load the installed version into the daemon (waits for idle).")
    p.add_argument("--idle-wait", type=float, default=600)
    args = p.parse_args(argv)
    kind = inst.install_kind()
    if kind == "git":
        root = inst.repo_root()
        res = inst.run(["git", "-C", str(root), "pull", "--ff-only"], timeout=120)
        inst.ok("git pull: " + (res.stdout.strip().splitlines() or ["done"])[-1])
    elif kind == "deb":
        inst.say("Package install: update with `sudo apt install ./pcbridge_<version>_amd64.deb`; the daemon then restarts itself when idle.")
    elif kind == "pacman":
        inst.say("Package install: update with `sudo pacman -U pcbridge-<version>-x86_64.pkg.tar.zst`; the daemon then restarts itself when idle.")
    elif kind == "user":
        inst.say("User install: download the new wheel and run `pcbridge setup` from it; this command then restarts the daemon when idle.")
    inst.write_version_stamp()
    cfg = _load_cfg()
    msg = _restart_when_idle(cfg, args.idle_wait)
    (inst.ok if msg.startswith("daemon") else inst.warn)(msg)
    return 0 if msg.startswith("daemon") else 1


def uninstall(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge uninstall", description="Remove pcbridge for this user (config and state are kept).")
    p.add_argument("--purge", action="store_true", help="Also move config, state and the user venv to the trash.")
    p.add_argument("--yes", action="store_true")
    args = p.parse_args(argv)
    if not args.yes:
        inst.say("This stops pcbridge and removes its units, commands, extension, aliases and client registrations.")
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            return 1
    backup = inst.Backup()
    lock([])
    inst.systemctl("disable", "--now", *inst.UNIT_NAMES)
    for name in inst.UNIT_NAMES:
        backup.move(inst.USER_UNIT_DIR / name)
    inst.systemctl("daemon-reload")
    for name in inst.LAUNCHERS:
        link = inst.LOCAL_BIN / name
        if link.is_symlink():
            backup.move(link)
    ext = inst.extension_target()
    if ext.exists() or ext.is_symlink():
        inst.run(["gnome-extensions", "disable", assetslib.EXTENSION_UUID])
        backup.move(ext)
    inst.remove_kde_entries(backup)
    inst.remove_aliases(backup)
    for client in connectlib.CLIENTS:
        reg = connectlib.current(client)
        if not reg.present:
            continue
        if client == "claude-code":
            backup.save(connectlib.CLAUDE_JSON)
            inst.run(["claude", "mcp", "remove", "pcbridge", "-s", "user"], timeout=30)
        elif client == "codex":
            text = connectlib.CODEX_TOML.read_text()
            new = re.sub(r"(?ms)^\[mcp_servers\.pcbridge(\.[^\]]+)?\]\n.*?(?=^\[(?!mcp_servers\.pcbridge)|\Z)", "", text)
            inst.write_if_changed(connectlib.CODEX_TOML, new, backup)
        else:
            data = json.loads(connectlib.DESKTOP_JSON.read_text())
            data.get("mcpServers", {}).pop("pcbridge", None)
            inst.write_if_changed(connectlib.DESKTOP_JSON, json.dumps(data, indent=2) + "\n", backup)
    rb = backup.write_rollback()
    inst.ok(f"pcbridge removed. Everything removed is saved in {backup.root}" + (f" ({rb.name})" if rb else ""))
    if args.purge:
        # The backup directory lives in the state directory, so it goes to
        # the trash with it and can still be restored from there.
        for path in (pathslib.config_home(), pathslib.state_home(), pathslib.data_home()):
            inst.trash(path)
        inst.ok("config, state (including that backup) and data moved to the trash")
    return 0
