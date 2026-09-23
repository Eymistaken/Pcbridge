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

from .. import __version__
from .. import assets as assetslib
from .. import paths as pathslib

HOME = Path.home()
USER_UNIT_DIR = HOME / ".config" / "systemd" / "user"
UNIT_NAMES = ("pcbridge.service", "pcbridge.socket")
LOCAL_BIN = HOME / ".local" / "bin"
LAUNCHERS = ("pcbridge", "pcb-shot", "pcb-do")
EXTENSIONS_DIR = HOME / ".local" / "share" / "gnome-shell" / "extensions"
SYSTEM_EXTENSIONS_DIR = Path("/usr/share/gnome-shell/extensions")
DEB_PREFIX = Path("/usr/lib/pcbridge")
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


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _mark(word: str, color: str) -> str:
    return f"\033[{color}m{word}\033[0m" if _COLOR else word


def ok(msg: str) -> None:
    say(f"  {_mark('ok', '32')}    {msg}")


def warn(msg: str) -> None:
    say(f"  {_mark('warn', '33')}  {msg}")


def fail(msg: str) -> None:
    say(f"  {_mark('fail', '31')}  {msg}")


def run(cmd: list[str], timeout: float = 30, check: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=check, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]}: timed out")


def systemctl(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], timeout=timeout)


# ---------------------------------------------------------------------------
# Install kind and launcher
# ---------------------------------------------------------------------------


def repo_root() -> Path | None:
    """The git checkout this pcbridge runs from, if any."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() and (root / "pyproject.toml").exists() else None


def install_kind() -> str:
    """"deb", "user" (setup's venv under ~/.local/share), "git" or "pip"."""
    prefix = Path(sys.prefix).resolve()
    if str(prefix).startswith(str(DEB_PREFIX)):
        return "deb"
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
    reinstalled venv keeps the same path), /usr/bin/pcbridge for the .deb,
    the venv's own script otherwise.
    """
    kind = install_kind()
    if kind == "deb":
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
    if install_kind() == "deb":
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


# ---------------------------------------------------------------------------
# systemd units
# ---------------------------------------------------------------------------


def render_unit(name: str, python: str | None = None) -> str:
    text = assetslib.asset_path(f"systemd/{name}").read_text(encoding="utf-8")
    return text.replace("__PYTHON__", python or sys.executable)


def units_managed_by_package() -> bool:
    return install_kind() == "deb"


def install_units(backup: Backup) -> bool:
    """Write the user units for this installation; True when something changed."""
    if units_managed_by_package():
        return False
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
        state = json.loads((Path(cfg.state_dir) / "desktop_unlock.json").read_text())
        import time

        if float(state.get("until", 0) or 0) > time.time():
            return "the desktop grant is open"
    except (OSError, ValueError):
        pass
    return ""


def version_stamp_path() -> Path:
    return pathslib.data_home() / "version-stamp"


def write_version_stamp() -> None:
    """Tell a running daemon that a newer pcbridge is installed (it restarts when idle)."""
    import time

    path = version_stamp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{__version__} {install_kind()} {int(time.time())}\n", encoding="utf-8")


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
    if install_kind() == "deb" and (SYSTEM_EXTENSIONS_DIR / assetslib.EXTENSION_UUID).exists():
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
