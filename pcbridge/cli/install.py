"""Installation pieces shared by `pcbridge setup`, `doctor --fix`, `update`
and `uninstall`: install kind, launcher, systemd units, extension, aliases,
backups and the version stamp.

Everything here is idempotent: running it twice changes nothing the second
time. Every file that is about to be rewritten or removed is first copied
into one backup directory per run, `$XDG_STATE_HOME/pcbridge/backup-<ts>/`,
which also gets a ROLLBACK.md with the exact commands to undo the run.
Nothing is ever deleted outright: files are moved into that backup
directory (or to the trash with `gio trash`).
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import oscompat
from .. import __version__
from .. import assets as assetslib
from .. import distro as distrolib
from .. import paths as pathslib

HOME = Path.home()
USER_UNIT_DIR = HOME / ".config" / "systemd" / "user"
UNIT_NAMES = ("pcbridge.service", "pcbridge.socket")
LOCAL_BIN = HOME / ".local" / "bin"
LAUNCHERS = ("pcbridge", "pcb-shot", "pcb-do")
EXTENSIONS_DIR = HOME / ".local" / "share" / "gnome-shell" / "extensions"
SYSTEM_EXTENSIONS_DIR = Path("/usr/share/gnome-shell/extensions")
# Where the .deb and the Arch package put their venv.
SYSTEM_PREFIX = Path("/usr/lib/pcbridge")
SYSTEM_KINDS = ("deb", "pacman")
ALIAS_BEGIN = "# >>> pcbridge >>>"
ALIAS_END = "# <<< pcbridge <<<"
# The shell aliases the maintainer has used since 1.x, now pointing at the CLI.
ALIASES = {
    "bridgeac": "remote start",
    "bridgekapat": "remote stop",
    "bridgedurum": "remote status",
    "bridgekilit": "lock",
}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

# Where the lines go instead of stdout: `pcbridge setup`'s terminal UI sets
# it to (kind, text) with kind say, ok, warn, fail, step or out, and draws
# them as rows; nothing may print while Textual owns the terminal.
Sink = Callable[[str, str], None]
_sink: Sink | None = None


def set_sink(sink: Sink | None) -> None:
    global _sink
    _sink = sink


def _emit(kind: str, msg: str) -> bool:
    if _sink is None:
        return False
    _sink(kind, msg)
    return True


def say(msg: str = "") -> None:
    if not _emit("say", msg):
        print(msg, flush=True)


def _mark(word: str, color: str) -> str:
    return f"\033[{color}m{word}\033[0m" if _COLOR else word


def ok(msg: str) -> None:
    if not _emit("ok", msg):
        say(f"  {_mark('ok', '32')}    {msg}")


def warn(msg: str) -> None:
    if not _emit("warn", msg):
        say(f"  {_mark('warn', '33')}  {msg}")


def fail(msg: str) -> None:
    if not _emit("fail", msg):
        say(f"  {_mark('fail', '31')}  {msg}")


def step(key: str, title: str, number: int | None = None) -> None:
    """A step of `pcbridge setup` begins: "\\n1. System packages" on a terminal,
    a row of the setup screen in the terminal UI. Without a number the
    printed output shows nothing (a part of the numbered step before it)."""
    if not _emit("step", f"{key}\t{title}") and number is not None:
        say(f"\n{number}. {title}")


def run_streamed(argv: list[str], timeout: float | None = None,
                 env: dict[str, str] | None = None) -> int:
    """Run a command whose output a person may want to see; its exit status.

    Printed straight to the terminal, as before; under the setup screen
    every line goes to the sink instead (it must not reach the terminal).
    """
    if _sink is None:
        try:
            return subprocess.run(argv, check=False, timeout=timeout, env=env).returncode
        except subprocess.TimeoutExpired:
            return 124
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", env=env)
    except OSError as exc:
        _emit("out", f"{argv[0]}: {exc}")
        return 127
    assert proc.stdout is not None
    with proc.stdout:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _emit("out", line)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return 124


# The setup screen asks for the sudo password with the terminal handed back
# for a moment: called with the command that needs it, True once sudo has
# the password cached (`sudo -v`). None: sudo asks on the terminal itself.
SudoPrompt = Callable[[list[str]], bool]
sudo_prompt: SudoPrompt | None = None


def run(cmd: list[str], timeout: float = 30, check: bool = False,
        input: str | None = None) -> subprocess.CompletedProcess:
    """Run a command with no terminal; `input`, when given, is its stdin."""
    try:
        if input is not None:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check,
                                  input=input)
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=check, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]}: timed out")


def systemctl(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    if oscompat.IS_WINDOWS:
        return _windows_systemctl(list(args), timeout)
    return run(["systemctl", "--user", *args], timeout=timeout)


def _windows_systemctl(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """The systemctl verbs pcbridge uses, answered by the Windows supervisor.

    pcbridge.service is the daemon under its supervisor (winservice);
    pcbridge.socket has no counterpart (the supervisor starts at logon, and
    the relay starts it when nothing answers), so it reports what the
    service does. Job scopes do not exist: jobs are stopped one by one."""
    from .. import winservice

    cmd = ["systemctl", "--user", *args]
    verb = args[0] if args else ""
    names = [a for a in args[1:] if not a.startswith("-")]
    ours = [n for n in names if n in UNIT_NAMES]
    if verb in ("daemon-reload", "reset-failed") or (verb in ("start", "stop") and names == ["pcbridge.socket"]):
        return subprocess.CompletedProcess(cmd, 0, "", "")
    if verb == "list-units":
        return subprocess.CompletedProcess(cmd, 0, "", "")
    if verb == "show":
        return subprocess.CompletedProcess(cmd, 0, "Environment=\n" if "Environment" in args else "", "")
    if verb == "is-active":
        state = winservice.status()
        active = state["daemon"] or bool(state["supervisor_pid"])
        return subprocess.CompletedProcess(cmd, 0 if active else 3, "active\n" if active else "inactive\n", "")
    if verb == "is-enabled":
        enabled = winservice.is_enabled()
        return subprocess.CompletedProcess(cmd, 0 if enabled else 1, "enabled\n" if enabled else "disabled\n", "")
    ok = True
    if verb == "enable" and ours:
        winservice.enable()
        if "--now" in args:
            ok = winservice.start(wait=timeout)
    elif verb == "disable" and ours:
        if "--now" in args:
            ok = winservice.stop(wait=timeout)
        winservice.disable()
    elif verb == "start" and ours:
        ok = winservice.start(wait=timeout)
    elif verb == "stop" and ours:
        ok = winservice.stop(wait=timeout)
    elif verb == "restart" and ours:
        ok = winservice.stop(wait=timeout / 2) and winservice.start(wait=timeout / 2)
    elif verb in ("enable", "disable", "start", "stop", "restart"):
        pass  # job scopes and other units have no Windows counterpart
    else:
        return subprocess.CompletedProcess(cmd, 1, "", f"systemctl {verb}: not available on Windows")
    if ok:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.CompletedProcess(cmd, 1, "", "the pcbridge daemon did not answer in time; see `pcbridge logs`")


# ---------------------------------------------------------------------------
# Install kind and launcher
# ---------------------------------------------------------------------------


def repo_root() -> Path | None:
    """The git checkout this pcbridge runs from, if any."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() and (root / "pyproject.toml").exists() else None


def install_kind() -> str:
    """"deb" or "pacman" (a distribution package), "user" (setup's venv
    under ~/.local/share), "git" or "pip"."""
    prefix = Path(sys.prefix).resolve()
    if str(prefix).startswith(str(SYSTEM_PREFIX)):
        return "pacman" if distrolib.family() == distrolib.ARCH else "deb"
    if prefix == (pathslib.data_home() / "venv").resolve():
        return "user"
    if repo_root() is not None:
        return "git"
    return "pip"


def bin_path(name: str) -> Path:
    """The entry point of this installation (not a symlink to it)."""
    return Path(sys.prefix) / "bin" / name


def launcher_path() -> Path:
    """The stable path clients and aliases use to run pcbridge.

    ~/.local/bin/pcbridge for a user install (a symlink setup maintains, so a
    reinstalled venv keeps the same path), /usr/bin/pcbridge for a
    distribution package, the venv's own script otherwise.
    """
    kind = install_kind()
    if kind in SYSTEM_KINDS:
        return Path("/usr/bin/pcbridge")
    if kind == "user":
        return LOCAL_BIN / "pcbridge"
    return bin_path("pcbridge")


def client_command() -> list[str]:
    return [str(launcher_path()), "stdio"]


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


@dataclass
class Backup:
    """One backup directory per run, created on first use."""

    root: Path = field(
        default_factory=lambda: pathslib.state_home()
        / f"backup-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    entries: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, original, saved)

    def _dest(self, original: Path) -> Path:
        rel = str(original).lstrip("/").replace("/", "__")
        return self.root / rel

    def save(self, original: Path) -> Path | None:
        """Copy a file (or a symlink, as a symlink) before it is changed."""
        if not (original.exists() or original.is_symlink()):
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        dest = self._dest(original)
        if original.is_symlink():
            os.symlink(os.readlink(original), dest)
        elif original.is_dir():
            shutil.copytree(original, dest, symlinks=True)
        else:
            shutil.copy2(original, dest)
        self.entries.append(("copy", str(original), str(dest)))
        return dest

    def move(self, original: Path) -> Path | None:
        """Move a file or directory out of the way (never deleted)."""
        if not (original.exists() or original.is_symlink()):
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self._dest(original)
        shutil.move(str(original), str(dest))
        self.entries.append(("move", str(original), str(dest)))
        return dest

    def write_rollback(self, extra: list[str] | None = None) -> Path | None:
        if not self.entries and not extra:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Rollback for `pcbridge setup` ({__version__}), {self.root.name}",
            "",
            "Every file this run changed or moved is saved in this directory.",
            "To undo the run, stop pcbridge and put the saved files back:",
            "",
            "```bash",
            "systemctl --user stop pcbridge.service pcbridge.socket",
            "systemctl --user disable pcbridge.socket",
        ]
        for kind, original, saved in self.entries:
            if kind == "create":
                lines.append(f"rm -f '{original}'   # did not exist before")
            else:
                lines.append(f"rm -rf '{original}' 2>/dev/null; cp -a '{saved}' '{original}'   # was {kind}d")
        lines += [
            "systemctl --user daemon-reload",
            "systemctl --user restart pcbridge.service",
            "```",
        ]
        if extra:
            lines += ["", *extra]
        path = self.root / "ROLLBACK.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def trash(path: Path) -> bool:
    """Move to the desktop trash; never a permanent delete."""
    if not (path.exists() or path.is_symlink()):
        return True
    return run(["gio", "trash", str(path)]).returncode == 0


def write_if_changed(path: Path, text: str, backup: Backup, mode: int | None = None) -> bool:
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        if mode is not None:
            os.chmod(path, mode)
        return False
    backup.save(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".pcbridge-tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


# ---------------------------------------------------------------------------
# Launchers
# ---------------------------------------------------------------------------


def install_launchers(backup: Backup) -> list[str]:
    """~/.local/bin/{pcbridge,pcb-shot,pcb-do} -> this installation."""
    notes = []
    if is_system_package():
        return notes
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    for name in LAUNCHERS:
        target = bin_path(name)
        link = LOCAL_BIN / name
        if link.is_symlink() and Path(os.readlink(link)) == target:
            continue
        if link.exists() or link.is_symlink():
            backup.move(link)
            notes.append(f"moved the previous {link} to the backup")
        link.symlink_to(target)
        notes.append(f"{link} -> {target}")
    return notes


def skill_source() -> Path:
    """The computer-use skill directory of this installation."""
    return assetslib.asset_path("skills/computer-use")


def install_skill_link(backup: Backup, claude_dir: Path | None = None) -> str:
    """~/.claude/skills/computer-use -> this installation's skill.

    Claude Code finds skills only there; before 2.0 install.sh made this
    link, and the 2.0 setup dropped it. Only when Claude Code's directory
    exists. A link that already leads to a skill is the user's choice (the
    maintainer's points at a git checkout) and stays; a broken one, such as
    one into a venv that was replaced, is moved to the backup and made again.
    """
    claude = claude_dir if claude_dir is not None else HOME / ".claude"
    if not claude.is_dir():
        return "Claude Code's ~/.claude does not exist; no skill link needed"
    link = claude / "skills" / "computer-use"
    target = skill_source()
    if link.is_symlink():
        if (link / "SKILL.md").is_file():
            return f"skill: {link} -> {os.readlink(link)}"
        backup.move(link)
    elif link.exists():
        return f"skill: {link} is a directory of its own; left as it is"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    return f"skill: {link} -> {target}"


def remove_skill_link(backup: Backup, claude_dir: Path | None = None) -> bool:
    """Undo install_skill_link: only a link that leads into this installation."""
    link = (claude_dir if claude_dir is not None else HOME / ".claude") / "skills" / "computer-use"
    if not link.is_symlink() or Path(os.readlink(link)) != skill_source():
        return False
    backup.move(link)
    return True


def path_note(path: str | None = None) -> str:
    """What to do when ~/.local/bin is not on PATH, "" when it is.

    Arch's /etc/profile does not add it (measured on the Arch VM, 2026-10-14):
    the launchers are there and MCP clients use full paths, but typing
    `pcbridge` finds nothing. The shell's files are the user's; this says
    what to add instead of editing them.
    """
    if is_system_package():
        return ""
    dirs = [d for d in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep) if d]
    if any(Path(d).expanduser().resolve() == LOCAL_BIN.resolve() for d in dirs):
        return ""
    try:
        profile = (HOME / ".profile").read_text(encoding="utf-8")
    except OSError:
        profile = ""
    if ".local/bin" in profile:
        # Debian's and Ubuntu's ~/.profile add it at login once it exists.
        return (f"{LOCAL_BIN} is not on PATH in this shell yet; your ~/.profile adds it at "
                f"the next login (until then: {LOCAL_BIN}/pcbridge)")
    return (f"{LOCAL_BIN} is not on PATH, so `pcbridge` is not found by name: add "
            "'export PATH=\"$HOME/.local/bin:$PATH\"' to ~/.bashrc (or ~/.profile)")


PATH_MARK = "# added by pcbridge setup"


@dataclass
class PathFix:
    """The one line that puts ~/.local/bin on PATH for the user's login shell."""

    shell: str      # "bash", "zsh" or "fish"
    file: Path
    line: str


def path_fix(path: str | None = None, shell: str | None = None) -> PathFix | None:
    """What `pcbridge setup` can offer when `pcbridge` is not found by name.

    None when nothing is needed (on PATH already, a package install, or a
    ~/.profile that adds it at the next login) or the shell is not one setup
    knows how to change. The file is changed only after the user says yes.
    """
    if not path_note(path):
        return None
    try:
        profile = (HOME / ".profile").read_text(encoding="utf-8")
    except OSError:
        profile = ""
    if ".local/bin" in profile:
        return None
    name = Path(shell if shell is not None else os.environ.get("SHELL", "")).name
    if name == "bash":
        fix = PathFix("bash", HOME / ".bashrc", 'export PATH="$HOME/.local/bin:$PATH"')
    elif name == "zsh":
        fix = PathFix("zsh", Path(os.environ.get("ZDOTDIR") or HOME) / ".zshrc",
                      'export PATH="$HOME/.local/bin:$PATH"')
    elif name == "fish":
        fix = PathFix("fish", HOME / ".config" / "fish" / "conf.d" / "pcbridge-path.fish",
                      "fish_add_path -g $HOME/.local/bin")
    else:
        return None
    try:
        if ".local/bin" in fix.file.read_text(encoding="utf-8"):
            return None  # already there: a new terminal has it
    except OSError:
        pass
    return fix


def apply_path_fix(fix: PathFix, backup: Backup) -> str:
    """Add the line (the file is backed up first, or noted as new for ROLLBACK.md)."""
    existed = fix.file.exists()
    text = fix.file.read_text(encoding="utf-8") if existed else ""
    if fix.line in text:
        return f"already in {fix.file}"
    new = text + ("" if text.endswith("\n") or not text else "\n") + f"{fix.line}   {PATH_MARK}\n"
    write_if_changed(fix.file, new, backup)
    if not existed:
        backup.entries.append(("create", str(fix.file), ""))
    return f"added to {fix.file}"


# ---------------------------------------------------------------------------
# systemd units
# ---------------------------------------------------------------------------


def render_unit(name: str, python: str | None = None) -> str:
    text = assetslib.asset_path(f"systemd/{name}").read_text(encoding="utf-8")
    return text.replace("__PYTHON__", python or sys.executable)


def is_system_package() -> bool:
    """Installed by the .deb or the Arch package (owns /usr/bin and the units)."""
    return install_kind() in SYSTEM_KINDS


def units_managed_by_package() -> bool:
    return is_system_package()


def install_units(backup: Backup) -> bool:
    """Write the user units for this installation; True when something changed."""
    if units_managed_by_package():
        # Units in ~/.config/systemd/user override the package's
        # /usr/lib/systemd/user ones; a leftover from an earlier user install
        # would keep starting the old venv. Moved to the backup, not deleted.
        changed = False
        for name in UNIT_NAMES:
            leftover = USER_UNIT_DIR / name
            if leftover.exists() or leftover.is_symlink():
                backup.move(leftover)
                changed = True
        if changed:
            # Without this systemd keeps the removed units in memory, and the
            # restart that follows starts the old venv again (seen in the Arch
            # VM: the package's setup restarted the checkout's daemon).
            systemctl("daemon-reload")
        return changed
    changed = False
    for name in UNIT_NAMES:
        changed |= write_if_changed(USER_UNIT_DIR / name, render_unit(name), backup, 0o644)
    if changed:
        systemctl("daemon-reload")
    return changed


def enable_units() -> None:
    systemctl("enable", *UNIT_NAMES)


def service_active() -> bool:
    return systemctl("is-active", "pcbridge.service").stdout.strip() == "active"


def socket_active() -> bool:
    return systemctl("is-active", "pcbridge.socket").stdout.strip() == "active"


def idle_reason(cfg) -> str:
    """Why restarting pcbridge now would interrupt something ("" = idle)."""
    from ..jobs import JobManager

    try:
        running = JobManager(cfg.jobs_dir).list_jobs(limit=200, only_running=True)
    except Exception:  # noqa: BLE001
        running = []
    if running:
        return f"{len(running)} job(s) running"
    try:
        state = json.loads((Path(cfg.state_dir) / "desktop_unlock.json").read_text(encoding="utf-8"))
        import time

        if float(state.get("until", 0) or 0) > time.time():
            return "the desktop grant is open"
    except (OSError, ValueError):
        pass
    return ""


def version_stamp_path() -> Path:
    return pathslib.data_home() / "version-stamp"


def write_version_stamp() -> None:
    """Tell a running daemon what is installed now; it restarts when idle if
    that is not the code it runs.

    The third field is the build fingerprint (`build.py`). It used to be a
    timestamp, so reinstalling identical code still restarted the daemon.
    """
    from .. import build as buildlib

    path = version_stamp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{__version__} {install_kind()} {buildlib.fingerprint()}\n",
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# GNOME extension
# ---------------------------------------------------------------------------


def extension_target() -> Path:
    return EXTENSIONS_DIR / assetslib.EXTENSION_UUID


def _compile_schemas(ext_dir: Path) -> None:
    """The kill-switch shortcut's setting needs a compiled schema. Without
    glib-compile-schemas only the shortcut is missing; the indicator works."""
    schemas = ext_dir / "schemas"
    if schemas.is_dir() and shutil.which("glib-compile-schemas"):
        run(["glib-compile-schemas", str(schemas)])


def install_extension(backup: Backup) -> str:
    """Copy the extension into the user's extension directory.

    The running GNOME Shell keeps the version it loaded until the next login,
    so this never disturbs the current session; the new files are picked up
    at the next login.
    """
    if is_system_package() and (SYSTEM_EXTENSIONS_DIR / assetslib.EXTENSION_UUID).exists():
        return "provided by the package"
    src = assetslib.asset_path("gnome-extension")
    dst = extension_target()
    if dst.is_symlink():
        if dst.resolve() == src.resolve():
            _compile_schemas(dst)
            return "already linked to this installation"
        backup.move(dst)
    if dst.is_dir():
        same = all(
            (dst / f.relative_to(src)).is_file()
            and (dst / f.relative_to(src)).read_bytes() == f.read_bytes()
            for f in src.rglob("*")
            if f.is_file() and f.name != "gschemas.compiled" and "__pycache__" not in f.parts
        )
        if same:
            _compile_schemas(dst)
            return "up to date"
        backup.save(dst)
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "gschemas.compiled"))
    _compile_schemas(dst)
    run(["gnome-extensions", "enable", assetslib.EXTENSION_UUID])
    return "installed (active after the next login)"


# ---------------------------------------------------------------------------
# Cinnamon applet
# ---------------------------------------------------------------------------

SYSTEM_APPLETS_DIR = Path("/usr/share/cinnamon/applets")


def applet_target() -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
    return data / "cinnamon" / "applets" / assetslib.CINNAMON_APPLET_UUID


def enabled_applets_with(current: str, uuid: str) -> str | None:
    """`org.cinnamon enabled-applets` with `uuid` added to the panel's right
    end, or None when it is already there or the value is unreadable.

    Entries read `panel1:right:0:uuid:instance`; the new instance id is one
    above the highest in use, as Cinnamon numbers them.
    """
    import ast

    text = current.strip()
    if text.startswith("@as "):
        text = text[4:]
    try:
        entries = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        return None
    if any(e.split(":")[3:4] == [uuid] for e in entries):
        return None
    ids = [int(e.split(":")[4]) for e in entries
           if len(e.split(":")) > 4 and e.split(":")[4].isdigit()]
    panel = next((e.split(":")[0] for e in entries if e.count(":") >= 4), "panel1")
    entries.append(f"{panel}:right:0:{uuid}:{max(ids, default=0) + 1}")
    return "[" + ", ".join(repr(e) for e in entries) + "]"


def install_cinnamon_applet(backup: Backup) -> str:
    """Copy the applet into the user's Cinnamon applets and add it to the panel.

    Cinnamon loads an applet as soon as `enabled-applets` names it: no new
    login is needed, unlike the GNOME extension.
    """
    uuid = assetslib.CINNAMON_APPLET_UUID
    if is_system_package() and (SYSTEM_APPLETS_DIR / uuid).exists():
        state = "provided by the package"
    else:
        src = assetslib.asset_path("cinnamon-applet")
        dst = applet_target()
        same = dst.is_dir() and all(
            (dst / f.relative_to(src)).is_file()
            and (dst / f.relative_to(src)).read_bytes() == f.read_bytes()
            for f in src.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
        if same:
            state = "up to date"
        else:
            if dst.exists() or dst.is_symlink():
                backup.move(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
            state = "installed"
    current = run(["gsettings", "get", "org.cinnamon", "enabled-applets"])
    value = enabled_applets_with(current.stdout if current.returncode == 0 else "", uuid)
    if value is None:
        return f"{state}; in the panel" if current.returncode == 0 else \
            f"{state}; add it to the panel with Applets > pcbridge"
    done = run(["gsettings", "set", "org.cinnamon", "enabled-applets", value])
    if done.returncode != 0:
        return f"{state}; add it to the panel with Applets > pcbridge"
    return f"{state}; added to the panel"


# ---------------------------------------------------------------------------
# KDE Plasma entries
# ---------------------------------------------------------------------------

LOCK_ENTRY = "pcbridge-lock.desktop"


def applications_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share") / "applications"


def lock_entry(launcher: Path) -> str:
    """A launcher entry for the kill switch, so a shortcut can be bound to it.

    Plasma has no pcbridge extension with a lock shortcut; System Settings >
    Shortcuts can bind any application entry. No shortcut is set here, as the
    GNOME one is off by default too.
    """
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=pcbridge: lock desktop control\n"
        "Comment=End pcbridge's desktop grant now (the kill switch)\n"
        f"Exec={launcher} lock\n"
        "Icon=system-lock-screen\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )


def install_kde_entries(native_spec: object) -> list[str]:
    """Plasma: authorize the native helper for KWin screenshots, add the lock entry."""
    from ..desktop import kwin
    from ..native import discover_native_binary

    notes = []
    try:
        binary = discover_native_binary(native_spec)
    except Exception:  # noqa: BLE001 — said below, not raised
        notes.append("native helper not found: screen capture on Plasma needs it")
    else:
        path, changed = kwin.install_helper_entry(binary, dest_dir=applications_dir())
        notes.append(f"{path.name}: {'installed' if changed else 'up to date'} "
                     "(KWin screenshots for the native helper)")
    notes.append(install_lock_entry("System Settings > Shortcuts"))
    return notes


def install_lock_entry(where: str) -> str:
    """The kill switch's launcher entry; `where` names the shortcut settings."""
    lock_path = applications_dir() / LOCK_ENTRY
    text = lock_entry(launcher_path())
    try:
        current = lock_path.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != text:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(text, encoding="utf-8")
    return (f"{LOCK_ENTRY}: {'up to date' if current == text else 'installed'} "
            f"(bind a shortcut to it in {where})")


def remove_cinnamon_applet(backup: Backup) -> None:
    """Take the applet off the panel and move its files to the backup."""
    uuid = assetslib.CINNAMON_APPLET_UUID
    current = run(["gsettings", "get", "org.cinnamon", "enabled-applets"])
    if current.returncode == 0 and uuid in current.stdout:
        import ast

        text = current.stdout.strip()
        text = text[4:] if text.startswith("@as ") else text
        try:
            entries = [e for e in ast.literal_eval(text) if e.split(":")[3:4] != [uuid]]
        except (ValueError, SyntaxError, AttributeError):
            entries = None
        if entries is not None:
            run(["gsettings", "set", "org.cinnamon", "enabled-applets",
                 "[" + ", ".join(repr(e) for e in entries) + "]"])
    target = applet_target()
    if target.exists() or target.is_symlink():
        backup.move(target)


def remove_kde_entries(backup: Backup) -> None:
    from ..desktop import kwin

    for name in (kwin.HELPER_ENTRY, LOCK_ENTRY):
        path = applications_dir() / name
        if path.exists():
            backup.move(path)


# ---------------------------------------------------------------------------
# Shell aliases
# ---------------------------------------------------------------------------


def alias_block() -> str:
    exe = str(launcher_path())
    lines = [ALIAS_BEGIN, "# Written by `pcbridge setup`. Edit through `pcbridge setup`, not by hand."]
    lines += [f"alias {name}='{exe} {cmd}'" for name, cmd in ALIASES.items()]
    lines.append(ALIAS_END)
    return "\n".join(lines) + "\n"


def write_aliases(backup: Backup, rc: Path | None = None, create: bool = False) -> str:
    """Rewrite the pcbridge alias block in ~/.bashrc (only if one exists, unless create)."""
    rc = rc or HOME / ".bashrc"
    text = rc.read_text(encoding="utf-8") if rc.exists() else ""
    start, end = text.find(ALIAS_BEGIN), text.find(ALIAS_END)
    if start < 0 or end < start:
        if not create:
            return "no pcbridge alias block; left alone"
        new = text + ("" if text.endswith("\n") or not text else "\n") + alias_block()
    else:
        end += len(ALIAS_END)
        if end < len(text) and text[end] == "\n":
            end += 1
        new = text[:start] + alias_block() + text[end:]
    if new == text:
        return "up to date"
    write_if_changed(rc, new, backup)
    return "updated (open a new terminal to use them)"


def remove_aliases(backup: Backup, rc: Path | None = None) -> bool:
    rc = rc or HOME / ".bashrc"
    if not rc.exists():
        return False
    text = rc.read_text(encoding="utf-8")
    start, end = text.find(ALIAS_BEGIN), text.find(ALIAS_END)
    if start < 0 or end < start:
        return False
    end += len(ALIAS_END) + 1
    return write_if_changed(rc, text[:start] + text[end:], backup)
