"""`pcbridge doctor [--fix] [--json]`: is this machine ready, and if not, why.

Each check has a status (ok, warn, fail, info), a one-line detail, and, when
something is wrong, what to do about it. `--fix` repairs only what is safe to
repair unattended: file modes, the user units, a stale socket file, client
registrations. Anything that needs root is printed as the exact command.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import __version__
from .. import paths as pathslib
from .. import distro as distrolib
from . import connect as connectlib
from . import install as inst


@dataclass
class Check:
    group: str
    name: str
    status: str  # ok | warn | fail | info
    detail: str
    fix: str = ""
    fixed: bool = False


# Job records are never deleted by pcbridge; doctor warns above this size.
JOBS_WARN_BYTES = 1_000_000_000


class Doctor:
    def __init__(self, fix: bool = False) -> None:
        self.fix = fix
        self.checks: list[Check] = []
        self.backup = inst.Backup()
        self.cfg = None

    def add(self, group: str, name: str, status: str, detail: str, fix: str = "") -> Check:
        c = Check(group, name, status, detail, fix)
        self.checks.append(c)
        return c

    # -- groups -------------------------------------------------------------
    def install(self) -> None:
        g = "install"
        kind = inst.install_kind()
        self.add(g, "version", "info", f"pcbridge {__version__} ({kind} install, Python {sys.version.split()[0]}, {sys.prefix})")
        launcher = inst.launcher_path()
        if launcher.exists():
            self.add(g, "launcher", "ok", str(launcher))
        else:
            c = self.add(g, "launcher", "fail", f"{launcher} is missing", "pcbridge setup")
            if self.fix:
                inst.install_launchers(self.backup)
                c.fixed = launcher.exists()
        on_path = shutil.which("pcbridge")
        if on_path and Path(on_path).resolve() != launcher.resolve():
            self.add(g, "PATH", "warn", f"`pcbridge` on PATH is {on_path}, not {launcher}",
                     "remove the other pcbridge from PATH or put ~/.local/bin first")

    def config(self) -> None:
        g = "config"
        from ..config import load_config

        try:
            self.cfg = load_config()
        except SystemExit as exc:
            self.add(g, "config file", "fail", str(exc), "pcbridge setup")
            return
        cfg = self.cfg
        kind = {"xdg": "ok", "env": "ok", "explicit": "ok", "legacy": "warn"}.get(cfg.source_kind, "info")
        self.add(g, "config file", kind, f"{cfg.source_path} ({cfg.source_kind}, config_version {cfg.config_version})",
                 "pcbridge setup  (moves it to ~/.config/pcbridge)" if kind == "warn" else "")
        for w in cfg.warnings:
            if "readable by other users" in w:
                continue
            self.add(g, "config warning", "warn", w)
        for path in (cfg.source_path, cfg.db_path):
            if path is None or not Path(path).exists():
                continue
            mode = stat.S_IMODE(Path(path).stat().st_mode)
            if mode & 0o077:
                c = self.add(g, f"mode of {Path(path).name}", "fail", f"{path} is mode {mode:o}; it holds secrets",
                             f"chmod 600 {path}")
                if self.fix:
                    os.chmod(path, 0o600)
                    c.fixed = True
            else:
                self.add(g, f"mode of {Path(path).name}", "ok", f"{mode:o}")
        if "DEGISTIR" in cfg.public_url or "CHANGE" in cfg.public_url:
            self.add(g, "public_url", "warn", "public_url is still the example value; remote access will not work",
                     f"edit {cfg.source_path}")
        self.add(g, "desktop control", "info",
                 "enabled" if cfg.desktop.enabled else "disabled ([desktop] enabled = false): desktop tools refuse")

    def daemon(self) -> None:
        g = "daemon"
        if inst.units_managed_by_package():
            self.add(g, "units", "info", "provided by the package in /usr/lib/systemd/user")
        else:
            for name in inst.UNIT_NAMES:
                path = inst.USER_UNIT_DIR / name
                want = inst.render_unit(name)
                if not path.exists():
                    c = self.add(g, name, "fail", "not installed", "pcbridge setup")
                elif path.read_text() != want:
                    c = self.add(g, name, "warn", f"{path} differs from this version's unit", "pcbridge doctor --fix")
                else:
                    c = self.add(g, name, "ok", str(path))
                    continue
                if self.fix:
                    inst.install_units(self.backup)
                    c.fixed = path.exists() and path.read_text() == want
        for name in inst.UNIT_NAMES:
            enabled = inst.systemctl("is-enabled", name).stdout.strip()
            active = inst.systemctl("is-active", name).stdout.strip()
            status = "ok" if enabled == "enabled" else "warn"
            c = self.add(g, f"{name} state", status, f"{enabled}, {active}",
                         "" if status == "ok" else f"systemctl --user enable --now {name}")
        env = inst.systemctl("show", "-p", "Environment", "pcbridge.service").stdout
        if "ANTHROPIC_MODEL" in env or "CLAUDE_CODE_EFFORT_LEVEL" in env:
            self.add(g, "unit environment", "fail",
                     "pcbridge.service sets ANTHROPIC_MODEL or CLAUDE_CODE_EFFORT_LEVEL; jobs inherit it and --effort stops working",
                     "remove those Environment= lines, then systemctl --user daemon-reload")
        try:
            sock = pathslib.socket_path()
        except pathslib.RuntimeDirError as exc:
            self.add(g, "socket", "fail", str(exc), "log in to a normal desktop session")
            return
        reply = handshake(sock)
        if isinstance(reply, dict):
            v = reply.get("version", "?")
            status = "ok" if v == __version__ else "warn"
            self.add(g, "socket", status, f"{sock}: daemon {v}, pid {reply.get('pid')}",
                     "" if status == "ok" else "the daemon restarts itself when idle; or: pcbridge update")
        elif sock.exists() and not inst.socket_active():
            c = self.add(g, "socket", "fail", f"{sock} exists but nothing listens (stale)", "pcbridge doctor --fix")
            if self.fix:
                sock.unlink(missing_ok=True)  # a dead socket inode, not user data
                c.fixed = not sock.exists()
        else:
            self.add(g, "socket", "warn", f"no daemon answers on {sock} ({reply}); clients fall back to in-process",
                     "systemctl --user start pcbridge.socket")
        if self.cfg is not None:
            url = f"http://{self.cfg.host}:{self.cfg.port}/healthz"
            try:
                import urllib.request

                with urllib.request.urlopen(url, timeout=3) as r:
                    ok = json.loads(r.read()).get("ok")
                self.add(g, "http", "ok" if ok else "warn", url)
            except OSError as exc:
                self.add(g, "http", "warn", f"{url}: {exc}", "systemctl --user start pcbridge.service")

    def clients(self) -> None:
        g = "clients"
        want = inst.client_command()
        for client in connectlib.CLIENTS:
            reg = connectlib.current(client)
            if not reg.present:
                if connectlib.client_available(client):
                    c = self.add(g, client, "warn", "not registered", f"pcbridge connect --client {client}")
                else:
                    self.add(g, client, "info", "client not installed")
                    continue
            elif reg.command == want:
                self.add(g, client, "ok", shlex.join(reg.command) + (f" ({reg.note})" if reg.note else ""))
                continue
            elif reg.command[1:] == ["-m", "pcbridge.server", "--stdio"]:
                c = self.add(g, client, "warn", f"pre-2.0 command {shlex.join(reg.command)} (still works through the relay)",
                             f"pcbridge connect --client {client}")
            else:
                c = self.add(g, client, "warn", f"runs {shlex.join(reg.command)}", f"pcbridge connect --client {client}")
            if self.fix:
                res = connectlib.connect([client], self.backup)
                c.fixed = res[0][1] == "ok"

    def readiness(self) -> None:
        g = "readiness"
        t0 = time.monotonic()
        res = probe(inst.client_command())
        ms = (time.monotonic() - t0) * 1000
        if isinstance(res, int):
            self.add(g, "fresh client", "ok" if res >= 36 else "fail",
                     f"`{shlex.join(inst.client_command())}` answered tools/list with {res} tools in {ms:.0f} ms")
        else:
            self.add(g, "fresh client", "fail", str(res), "pcbridge logs")

    def agents(self) -> None:
        g = "agents"
        from .. import executables as exelib

        if self.cfg is None:
            return
        for name, spec in self.cfg.agents.items():
            if not spec.enabled:
                self.add(g, name, "info", "disabled")
                continue
            exe = spec.command[0] if spec.command else ""
            found = exelib.find_executable(exe)
            self.add(g, name, "ok" if found else "warn", str(found) if found else f"`{exe}` not found",
                     "" if found else f"install it, or set its full path in [agents.{name}] command")
        for tool, pkg, why in (
            ("tmux", "tmux", "tmux tools"),
            ("script", "script", "agents with pty = true"),
            ("notify-send", "notify-send", "notifications"),
        ):
            have = shutil.which(tool)
            self.add(g, tool, "ok" if have else "warn", have or f"missing ({why})",
                     "" if have else distrolib.install_command(pkg))

    def desktop(self) -> None:
        from ..desktop.session import KDE, desktop_kind

        g = "desktop"
        kde = desktop_kind() == KDE
        session = os.environ.get("XDG_SESSION_TYPE", "")
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or session == "wayland"
        self.add(g, "session", "ok" if wayland else "warn",
                 f"XDG_SESSION_TYPE={session or '(unset)'}, WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '(unset)')}",
                 "" if wayland else "desktop tools need a GNOME or KDE Plasma on Wayland session")
        if kde:
            plasma = inst.run(["plasmashell", "--version"]).stdout.strip()
            self.add(g, "KDE Plasma", "ok" if plasma else "warn", plasma or "plasmashell not found")
        else:
            shell = inst.run(["gnome-shell", "--version"]).stdout.strip()
            self.add(g, "GNOME Shell", "ok" if shell else "warn", shell or "gnome-shell not found")
        dev = Path("/dev/uinput")
        if not dev.exists():
            self.add(g, "/dev/uinput", "fail", "missing (uinput module not loaded)", udev_hint())
        elif os.access(dev, os.R_OK | os.W_OK):
            self.add(g, "/dev/uinput", "ok", "writable by this user")
        else:
            self.add(g, "/dev/uinput", "fail", "not writable by this user", udev_hint())
        for mod, pkg in (("evdev", "evdev"), ("PIL", "Pillow")):
            try:
                __import__(mod)
                self.add(g, f"python {pkg}", "ok", "importable")
            except ImportError:
                self.add(g, f"python {pkg}", "fail", "missing", "pcbridge setup  (reinstalls the [desktop] extra)")
        sysgi = inst.run(["/usr/bin/python3", "-c", "import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi"])
        self.add(g, "AT-SPI (system python3-gi)", "ok" if sysgi.returncode == 0 else "fail",
                 "available" if sysgi.returncode == 0 else sysgi.stderr.strip()[-160:],
                 "" if sysgi.returncode == 0 else distrolib.install_command("atspi"))
        for tool, pkg, level in (("wl-copy", "wl-clipboard", "fail"), ("tesseract", "tesseract", "warn")):
            have = shutil.which(tool)
            self.add(g, tool, "ok" if have else level, have or "missing",
                     "" if have else distrolib.install_command(pkg))
        if kde:
            self.plasma(g)
        else:
            self.gnome_extension(g)
        if self.cfg is not None:
            try:
                from ..desktop import monitors

                mons = monitors.list_monitors(use_cache=False)
                w, h = monitors.canvas_size(mons)
                self.add(g, "monitors", "ok" if mons else "warn", f"{len(mons)} monitor(s), canvas {w}x{h}")
            except Exception as exc:  # noqa: BLE001
                self.add(g, "monitors", "warn", f"monitor table unreadable: {exc}"[:200])

    def plasma(self, g: str) -> None:
        """KDE Plasma's pieces: KWin screenshots, scripting, idle time, a11y."""
        from ..desktop import a11y, idlewatch, kwin

        binary = None
        if self.cfg is not None:
            try:
                from ..native import discover_native_binary

                binary = discover_native_binary(self.cfg.native)
            except Exception:  # noqa: BLE001 — the native group reports it
                binary = None
        entry = kwin.helper_authorized(binary) if binary else None
        self.add(g, "KWin screenshots", "ok" if entry else "fail",
                 f"the native helper is authorized by {entry}" if entry
                 else "the native helper is not authorized (KWin refuses its screenshots)",
                 "" if entry else "pcbridge setup")
        owned = inst.run(["busctl", "--user", "status", "org.kde.KWin"], timeout=5).returncode == 0
        self.add(g, "KWin scripting", "ok" if owned else "fail",
                 "org.kde.KWin answers (window focus and the focused window)" if owned
                 else "org.kde.KWin has no owner: this is not a KWin session")
        idle = idlewatch.read_idle_ms()
        self.add(g, "idle time", "ok" if idle is not None else "warn",
                 f"the idle watcher answers ({idle} ms since the last input)" if idle is not None
                 else "no idle watcher: desktop writes need force=true",
                 "" if idle is not None
                 else "the daemon starts it when [desktop] enabled = true; see pcbridge logs")
        state = a11y.is_enabled()
        self.add(g, "Qt accessibility", "info",
                 "on" if state else "off (desktop_unlock turns it on for the grant)"
                 if state is False else "unknown (org.a11y.Bus did not answer)")
        lock = inst.applications_dir() / inst.LOCK_ENTRY
        self.add(g, "lock entry", "ok" if lock.exists() else "info",
                 str(lock) if lock.exists() else "not installed", "" if lock.exists() else "pcbridge setup")

    def gnome_extension(self, g: str) -> None:
        ext = inst.extension_target()
        sys_ext = inst.SYSTEM_EXTENSIONS_DIR / inst.assetslib.EXTENSION_UUID
        where = ext if (ext.exists() or ext.is_symlink()) else (sys_ext if sys_ext.exists() else None)
        enabled = inst.assetslib.EXTENSION_UUID in inst.run(
            ["gsettings", "get", "org.gnome.shell", "enabled-extensions"]).stdout
        if where is None:
            self.add(g, "GNOME extension", "warn", "not installed (window focus falls back to GNOME search)", "pcbridge setup")
        else:
            kind = "symlink to " + str(where.resolve()) if where.is_symlink() else str(where)
            self.add(g, "GNOME extension", "ok" if enabled else "warn", f"{kind}; {'enabled' if enabled else 'not enabled'}",
                     "" if enabled else f"gnome-extensions enable {inst.assetslib.EXTENSION_UUID}")
            # What the RUNNING shell loaded: new files only load at the next
            # login. A 1.x extension has no Version property; both work.
            running = inst.run([
                "gdbus", "call", "--session", "--dest", "io.github.eymistaken.Pcbridge.WindowFocus",
                "--object-path", "/io/github/eymistaken/Pcbridge/WindowFocus",
                "--method", "org.freedesktop.DBus.Properties.Get",
                "io.github.eymistaken.Pcbridge.WindowFocus", "Version"], timeout=5)
            m = re.search(r"'([^']*)'", running.stdout) if running.returncode == 0 else None
            if m:
                self.add(g, "extension in this session", "ok", f"version {m.group(1)} (panel indicator on)")
            elif "UnknownProperty" in running.stderr or "No such property" in running.stderr \
                    or "InvalidArgs" in running.stderr:
                self.add(g, "extension in this session", "info",
                         "the 1.x extension is loaded (no panel indicator yet)",
                         "log out and back in to load the new one")
            else:
                self.add(g, "extension in this session", "info", "not running in this session")

    def native(self) -> None:
        g = "native helper"
        if self.cfg is None:
            return
        try:
            from ..native.diagnostics import diagnose

            for f in diagnose(self.cfg):
                line = f.line() if hasattr(f, "line") else str(f)
                level, _, msg = line.partition("\t")
                status = {"ok": "ok", "pass": "ok", "warn": "warn", "fail": "fail"}.get(level, "info")
                self.add(g, "native", status, msg or line)
        except Exception as exc:  # noqa: BLE001
            self.add(g, "native", "warn", f"diagnostics failed: {exc}"[:200])

    def remote(self) -> None:
        g = "remote"
        if not shutil.which("tailscale"):
            self.add(g, "tailscale", "info", "not installed (only needed for remote access)")
            return
        port = self.cfg.port if self.cfg else 8765
        st = inst.run(["tailscale", "funnel", "status"], timeout=10).stdout
        self.add(g, "funnel", "info", f"open on port {port}" if f":{port}" in st else "closed (`pcbridge remote start` opens it)")

    def logs(self) -> None:
        g = "logs"
        res = inst.run(["journalctl", "--user", "-u", "pcbridge.service", "--since", "-15min", "-p", "err", "--no-pager", "-q"])
        n = len([l for l in res.stdout.splitlines() if l.strip()])
        self.add(g, "errors (15 min)", "ok" if n == 0 else "warn", f"{n} error line(s) in the journal",
                 "" if n == 0 else "pcbridge logs")
        # The daemon logs to the journal (journald rotates it) and audit.log
        # rotates itself at [limits] audit_max_bytes. Job records are agent
        # transcripts, so pcbridge never deletes them; it says when they grow.
        cfg = getattr(self, "cfg", None)
        if cfg is not None:
            jobs = Path(cfg.state_dir) / "jobs"
            total = count = 0
            for root, _dirs, files in os.walk(jobs):
                for name in files:
                    try:
                        total += (Path(root) / name).stat().st_size
                    except OSError:
                        pass
            count = sum(1 for p in jobs.iterdir() if p.is_dir()) if jobs.is_dir() else 0
            big = total > JOBS_WARN_BYTES
            self.add(g, "job records", "warn" if big else "ok",
                     f"{count} job(s), {total / 1_000_000:.1f} MB in {jobs}",
                     f"move finished jobs you no longer need to the trash: gio trash {jobs}/<job-id>" if big else "")

    def run_all(self) -> list[Check]:
        for step in (self.install, self.config, self.daemon, self.clients, self.readiness,
                     self.agents, self.desktop, self.native, self.remote, self.logs):
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
                self.add(step.__name__, "check crashed", "fail", f"{type(exc).__name__}: {exc}"[:200])
        if self.backup.entries:
            self.backup.write_rollback()
        return self.checks


def udev_hint() -> str:
    try:
        rule = inst.assetslib.asset_path("udev/60-pcbridge-uinput.rules")
        mod = inst.assetslib.asset_path("modules-load/pcbridge-uinput.conf")
    except FileNotFoundError:
        return "install the pcbridge udev rule for /dev/uinput"
    return (
        f"sudo install -m 644 {rule} /etc/udev/rules.d/ && sudo install -m 644 {mod} /etc/modules-load.d/ "
        "&& sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=uinput"
    )


def handshake(sock: Path, timeout: float = 3.0) -> dict | str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock))
        s.sendall(json.dumps({"pcbridge_relay": {"version": __version__, "protocol": 1, "pid": os.getpid(), "env": {}}}).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return json.loads(data)["pcbridge_daemon"]
    except (OSError, ValueError, KeyError) as exc:
        return str(exc) or type(exc).__name__
    finally:
        s.close()


def probe(cmd: list[str], timeout: float = 30.0) -> int | str:
    """Spawn a fresh client command; initialize + tools/list. Returns the tool count."""
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return f"cannot start {cmd[0]}: {exc}"
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                        "clientInfo": {"name": "pcbridge-doctor", "version": __version__}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    # Keep stdin open until the answer arrives: EOF ends the MCP session, and
    # a server may end it before answering what is still queued.
    import threading

    result: list = []

    def read() -> None:
        assert p.stdout is not None
        for line in p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == 2:
                result.append(len(m.get("result", {}).get("tools", [])))
                return

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        assert p.stdin is not None
        p.stdin.write("".join(json.dumps(m) + "\n" for m in msgs).encode())
        p.stdin.flush()
        reader.join(timeout)
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            p.kill()
    if result:
        return result[0]
    return f"no tools/list answer within {timeout:.0f} s"


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="pcbridge doctor", description="Check that pcbridge is installed and ready.")
    p.add_argument("--fix", action="store_true", help="Repair what is safe to repair unattended.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = p.parse_args(argv)
    doc = Doctor(fix=args.fix)
    checks = doc.run_all()
    counts = {s: sum(1 for c in checks if c.status == s and not c.fixed) for s in ("ok", "warn", "fail", "info")}
    if args.json:
        print(json.dumps({"version": __version__, "summary": counts, "backup": str(doc.backup.root) if doc.backup.entries else None,
                          "checks": [asdict(c) for c in checks]}, indent=2))
    else:
        group = None
        for c in checks:
            if c.group != group:
                group = c.group
                inst.say(f"\n{group}")
            fn = {"ok": inst.ok, "warn": inst.warn, "fail": inst.fail, "info": inst.ok}[c.status]
            text = f"{c.name}: {c.detail}"
            if c.fixed:
                text += "  [fixed]"
            elif c.fix and c.status in ("warn", "fail"):
                text += f"\n          fix: {c.fix}"
            fn(text)
        inst.say(f"\n{counts['ok']} ok, {counts['warn']} warning(s), {counts['fail']} failure(s)")
    return 1 if counts["fail"] else 0
