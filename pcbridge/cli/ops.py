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


def _load_cfg():
    from ..config import load_config

    return load_config()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status_data() -> dict:
    """What `pcbridge status` reports, as data (the terminal UI shows it too)."""
    from .doctor import handshake

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
        out["keep_awake"] = keep_awake_state(cfg, out["daemon"])
    return out


def keep_awake_state(cfg, daemon: dict | None = None) -> dict:
    """`[power] keep_awake` as set in the file, plus what the daemon holds.

    The daemon writes what it holds to status.json every few seconds; when it
    is not running nothing is held.
    """
    state: dict = {"enabled": bool(cfg.keep_awake), "held": False}
    if daemon is not None and daemon.get("reachable") is False:
        return state
    try:
        written = json.loads((Path(cfg.state_dir) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    held = written.get("keep_awake") if written.get("daemon") == "running" else None
    if isinstance(held, dict):
        state.update({k: v for k, v in held.items() if k != "enabled"})
    return state


def describe_keep_awake(state: dict) -> tuple[bool, str]:
    """(fine, text) for `pcbridge status` and the terminal UI."""
    if not state.get("enabled"):
        return True, "off (`pcbridge keep-awake on` to stop idle suspend while jobs run)"
    if state.get("held"):
        return True, (f"on, holding a {state.get('method')} {state.get('what')} inhibitor"
                      f" ({state.get('reason')})")
    if state.get("error"):
        return False, f"on, but the last attempt to hold an inhibitor failed: {state['error']}"
    return True, "on, nothing held (no job running)"


def status(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge status", description="Show whether pcbridge is running and what it is doing.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    out = status_data()
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
        fine, text = describe_keep_awake(out["keep_awake"])
        (inst.ok if fine else inst.warn)(f"keep awake: {text}")
    return 0


def keep_awake(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="pcbridge keep-awake",
        description="Keep the machine from suspending on idle while jobs run "
                    "([power] keep_awake; off by default). Alone: show the state.",
    )
    p.add_argument("state", nargs="?", choices=("on", "off", "status"), default="status")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if args.state != "status":
        from .. import settings as S

        want = args.state == "on"
        try:
            ed = S.ConfigEditor()
            if ed.value("power.keep_awake") is want:
                inst.ok(f"keep awake is already {args.state}")
                return 0
            ed.set("power.keep_awake", want)
            result = ed.save()
        except S.SettingsError as exc:
            inst.fail(str(exc))
            return 1
        inst.ok(f"power.keep_awake = {'true' if want else 'false'}")
        if result.backup:
            inst.say(f"          backup: {result.backup}")
        inst.say("          The running daemon applies it within a few seconds; no restart.")
        return 0
    try:
        cfg = _load_cfg()
    except SystemExit as exc:
        inst.fail(str(exc))
        return 1
    state = keep_awake_state(cfg)
    if args.json:
        print(json.dumps(state, indent=2))
        return 0
    fine, text = describe_keep_awake(state)
    (inst.ok if fine else inst.warn)(f"keep awake: {text}")
    return 0 if fine else 1


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
    if argv and argv[0] == "clients":
        return remote_clients(argv[1:])
    if argv and argv[0] == "revoke":
        return remote_revoke(argv[1:])
    p = argparse.ArgumentParser(
        prog="pcbridge remote",
        description="Open or close remote access through Tailscale Funnel; list and revoke remote clients.",
        epilog="`pcbridge remote clients` lists the remote clients that hold tokens; "
               "`pcbridge remote revoke NAME` (or --all) cuts them off.",
    )
    p.add_argument("action", choices=["start", "stop", "status", "clients", "revoke"])
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
        if not cfg.has_password:
            inst.fail("remote access needs a password first, and none is set")
            inst.say("  Local clients never use it; remote clients type it on the approval page.")
            inst.say("  Create one with `pcbridge set auth.password`, then run this again.")
            return 1
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


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def remote_clients(argv: list[str]) -> int:
    from .. import remoteclients as rc

    p = argparse.ArgumentParser(
        prog="pcbridge remote clients",
        description="List the remote clients (OAuth apps such as ChatGPT or Gemini) that hold tokens. "
                    "One line per app name; an app that connected several times has several registrations.",
    )
    p.add_argument("--all", action="store_true",
                   help="Also list names whose every token has expired.")
    p.add_argument("--ids", action="store_true", help="Show each registration's client id.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    cfg = _load_cfg()
    groups = rc.clients(cfg.db_path, include_stale=args.all)
    if args.json:
        print(json.dumps({"clients": [g.as_dict() for g in groups],
                          "static_token": bool(cfg.static_token)}, indent=2))
        return 0
    if not groups:
        inst.ok("no remote client holds a token" + ("" if args.all else " (--all lists expired ones too)"))
    for g in groups:
        live = len(g.connected)
        state = (f"{live} connected" if live else "not connected")
        line = f"{g.name}: {state}, {len(g.registrations)} registration(s), last {_when(g.last_registered)}"
        if live:
            line += f", tokens expire {rc.describe_expiry(g.expires)}"
        if live:
            inst.ok(line)
        else:
            inst.say(f"        {line}")
        if args.ids:
            for r in g.registrations:
                mark = "connected" if r.connected else "expired"
                inst.say(f"          {r.client_id}  {_when(r.registered)}  {mark}")
    if groups:
        inst.say("        `pcbridge remote revoke NAME` cuts one off; --all cuts off every one.")
    if cfg.static_token:
        inst.say("        The static token (auth.static_token) is not listed here: anyone who has it can call "
                 "pcbridge. Replace it with `pcbridge set auth.static_token --generate`.")
    return 0


def _confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        inst.fail("not a terminal, so nothing was asked; add --yes to go ahead")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def remote_revoke(argv: list[str]) -> int:
    from .. import remoteclients as rc

    p = argparse.ArgumentParser(
        prog="pcbridge remote revoke",
        description="Cut off a remote client: delete its registration and every token issued to it. "
                    "Its next call fails, its refresh token stops working, and the app has to "
                    "connect again with the password. Works whether or not the daemon runs.",
    )
    p.add_argument("client", nargs="?",
                   help="An app name from `pcbridge remote clients` (every registration of that name), "
                        "or the start of one client id (--ids shows them).")
    p.add_argument("--all", action="store_true", help="Revoke every remote client.")
    p.add_argument("--yes", action="store_true", help="Do not ask.")
    args = p.parse_args(argv)
    if bool(args.client) == bool(args.all):
        p.error("give one client name or id, or --all")
    cfg = _load_cfg()
    if args.all:
        regs = rc.registrations(cfg.db_path)
        if not regs:
            inst.ok("no remote client is registered; nothing to revoke")
            return 0
        live = sum(1 for r in regs if r.connected)
        if not _confirm(f"Revoke every remote client ({live} connected, {len(regs)} registration(s))?",
                        args.yes):
            return 1
        res = rc.revoke_all(cfg.db_path, cfg.audit_log)
    else:
        try:
            regs = rc.select(cfg.db_path, args.client)
        except rc.RevokeError as exc:
            inst.fail(str(exc))
            return 1
        name = regs[0].name
        live = sum(1 for r in regs if r.connected)
        if not _confirm(f"Revoke {name} ({live} connected, {len(regs)} registration(s))?", args.yes):
            return 1
        res = rc.revoke(cfg.db_path, [r.client_id for r in regs], cfg.audit_log)
    inst.ok(f"revoked {', '.join(res.names) or 'nothing'}: {res.registrations} registration(s), "
            f"{res.access_tokens} access and {res.refresh_tokens} refresh token(s)")
    inst.say("        The next call from a revoked app fails; to use pcbridge again it must connect "
             "again and enter the password.")
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
    # No password and no static token: local clients use neither, and remote
    # access stays off until the user creates a password for it
    # (`pcbridge remote start`, the Remote tab, or the end of setup).
    text = re.sub(r'(?m)^password = .*$', 'password = ""', text, count=1)
    text = re.sub(r'(?m)^static_token = .*$', 'static_token = ""', text, count=1)
    tomllib.loads(text)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


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


def expected_tools(profile: str) -> int:
    """How many tools a fresh local client must get with this profile."""
    from ..tools import tools_in_profile

    return len(tools_in_profile(profile))


def setup(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="pcbridge setup", description="Install or update pcbridge for this user. "
        "Detects the machine, shows the plan and asks once; run it again after an update.")
    p.add_argument("--yes", action="store_true", help="Do not ask; take the defaults (installs the packages).")
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
    args = p.parse_args(argv)
    from .. import config as configlib
    from .. import platforminfo
    from .. import settings as settingslib
    from . import plan as planlib

    backup = inst.Backup()
    notes: list[str] = []
    inst.say(f"pcbridge {__version__} setup ({inst.install_kind()} install)\n")

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
    if args.optional is not None:
        wanted = {w.strip().lower() for w in args.optional.split(",") if w.strip()}
        unknown = wanted - {c.key for c in plan.components} - {"none"}
        if unknown:
            inst.warn("not offered on this machine: " + ", ".join(sorted(unknown)))
        for c in plan.components:
            c.ticked = c.key in wanted
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

    inst.say("\n1. System packages")
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
        if planlib.install_extras():
            inst.ok("Python packages for desktop control: evdev, Pillow")
        else:
            inst.warn("evdev and Pillow were not installed; desktop control needs them")
            notes.append("pcbridge setup   # tries evdev and Pillow again")
    if found.desktop_supported and not os.access("/dev/uinput", os.R_OK | os.W_OK):
        from .doctor import udev_hint

        inst.warn("/dev/uinput is not writable: keyboard and pointer control will not work")
        inst.say(f"          fix with: {udev_hint()}")
        notes.append(udev_hint())

    inst.say("\n2. Configuration")
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
            inst.ok(f"created {xdg}; desktop control is OFF and remote access has no password yet")
    for line in planlib.apply_settings(plan, xdg):
        inst.ok(line)
    settings_changed = plan.profile_target is not None
    cfg = configlib.load_config(str(xdg))
    db = Path(cfg.db_path)
    if db.exists():
        os.chmod(db, 0o600)

    inst.say("\n3. Commands")
    for n in inst.install_launchers(backup):
        inst.ok(n)
    inst.ok(f"launcher: {inst.launcher_path()}")
    path_note = inst.path_note()
    if path_note:
        inst.warn(path_note)
        notes.append(path_note)

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
    if running_daemon and not changed and not settings_changed:
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

    setup_desktop(cfg.native, backup, no_extension=args.no_extension, kind=found.desktop_kind)

    if not args.no_connect:
        inst.say("\n6. MCP clients")
        for client, st, detail in connectlib.connect(list(connectlib.CLIENTS), backup):
            (inst.warn if st == "warn" else inst.ok)(f"{client}: {detail}")

    if not args.no_aliases:
        inst.say("\n7. Shell aliases")
        inst.ok(inst.write_aliases(backup))

    inst.say("\n8. Readiness")
    from .doctor import probe

    want = expected_tools(cfg.tools_profile)
    t0 = time.monotonic()
    n = probe(inst.client_command())
    ready = isinstance(n, int) and n >= want
    if ready:
        inst.ok(f"a fresh client got {n} tools (profile \"{cfg.tools_profile}\") in {(time.monotonic() - t0) * 1000:.0f} ms")
    else:
        inst.fail(f"a fresh client failed: {n}" + (f" (expected {want})" if isinstance(n, int) else ""))
    rb = backup.write_rollback(extra=["Commands that still need you:", *[f"    {n}" for n in notes]] if notes else None)
    if rb:
        inst.say(f"\nBackups and undo instructions: {rb}")
    inst.say("\nSummary")
    for line in planlib.summary(plan):
        inst.say(f"  {line}")
    if notes:
        inst.say("\nStill to do by hand:")
        for n_ in notes:
            inst.say(f"  {n_}")
    return 0 if ready else 1


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
    for client in connectlib.ALL_CLIENTS:
        if not connectlib.current(client).present:
            continue
        try:
            connectlib.remove_entry(client, backup)
        except Exception as exc:  # noqa: BLE001 - uninstall goes on; the rest is still removed
            inst.warn(f"{client}: pcbridge's entry was not removed: {exc}")
    rb = backup.write_rollback()
    inst.ok(f"pcbridge removed. Everything removed is saved in {backup.root}" + (f" ({rb.name})" if rb else ""))
    if args.purge:
        # The backup directory lives in the state directory, so it goes to
        # the trash with it and can still be restored from there.
        for path in (pathslib.config_home(), pathslib.state_home(), pathslib.data_home()):
            inst.trash(path)
        inst.ok("config, state (including that backup) and data moved to the trash")
    return 0


def setup_desktop(native, backup, *, no_extension: bool = False, kind: str | None = None) -> None:
    """Install only the integration belonging to the selected desktop.

    `kind` comes from the install plan, which also reads the session over
    SSH; without it, this process's environment decides.
    """
    if no_extension:
        return
    from ..desktop.session import GNOME, KDE, HYPRLAND, desktop_kind

    kind = kind or desktop_kind()
    if kind == KDE:
        inst.say("\n5. KDE Plasma")
        for line in inst.install_kde_entries(native):
            (inst.warn if line.startswith("native helper not found") else inst.ok)(line)
    elif kind == GNOME:
        inst.say("\n5. GNOME Shell extension")
        inst.ok(inst.install_extension(backup))
    elif kind == HYPRLAND:
        from .hyprland import setup_notes

        inst.say("\n5. Hyprland")
        setup_notes()
    else:
        inst.say("\n5. Desktop integration")
        inst.ok("none for this desktop; the terminal, file, tmux and agent tools need none")
