"""Find the agent CLIs (claude, codex, agy, ...) wherever they are installed.

A daemon started by systemd does not see the PATH of an interactive shell:
nvm, npm's user prefix and bun all add their bin directories in ~/.bashrc.
So a bare command name is looked up in three places, first hit wins:

    1. an absolute or relative path given in config.toml, as is;
    2. the process PATH;
    3. the usual per-user install directories listed in WELL_KNOWN_DIRS,
       plus nvm's node versions (newest first).

On Windows a file counts as a program only with one of PATHEXT's extensions
(`os.access(X_OK)` is true for every file there), the directories are the
Windows ones (WINDOWS_DIRS), and a name with a backslash or a drive is a
path. An npm `.cmd` shim is never handed a prompt: cmd.exe would parse it
(quotes, `&`, `%VAR%`), so `direct_argv` reads the shim and calls node with
the script itself.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

WELL_KNOWN_DIRS = (
    "~/.local/bin",
    "~/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.cargo/bin",
    "~/.deno/bin",
    "/usr/local/bin",
)


# %VAR% is expanded when the directory is searched.
WINDOWS_DIRS = (
    "~/.local/bin",                     # Claude Code's native installer: claude.exe
    "%APPDATA%/npm",                    # npm's global prefix
    "%LOCALAPPDATA%/Programs",          # per-user installers
    "%LOCALAPPDATA%/Microsoft/WindowsApps",  # winget and Store command aliases
    "%NVM_SYMLINK%",                    # nvm-windows' active node
    "~/.bun/bin",
    "~/.cargo/bin",
    "~/.deno/bin",
)


def _pathext() -> list[str]:
    return [e.lower() for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", path.parent.name)) or (0,)


def nvm_bin_dirs() -> list[Path]:
    root = Path(os.environ.get("NVM_DIR") or Path.home() / ".nvm")
    dirs: list[Path] = []
    current = root / "current" / "bin"
    if current.is_dir():
        dirs.append(current)
    versions = sorted((root / "versions" / "node").glob("*/bin"), key=_version_key, reverse=True)
    dirs.extend(versions)
    return dirs


def search_dirs() -> list[Path]:
    if IS_WINDOWS:
        dirs = []
        for d in WINDOWS_DIRS:
            expanded = os.path.expandvars(os.path.expanduser(d))
            if "%" not in expanded:  # an unset variable stays unexpanded
                dirs.append(Path(expanded))
        return dirs
    dirs = [Path(os.path.expanduser(d)) for d in WELL_KNOWN_DIRS]
    return dirs + nvm_bin_dirs()


def _executable(path: Path) -> bool:
    if IS_WINDOWS:
        return path.is_file() and path.suffix.lower() in _pathext()
    return path.is_file() and os.access(path, os.X_OK)


def _is_path(name: str) -> bool:
    if IS_WINDOWS:
        return "/" in name or "\\" in name or (len(name) > 1 and name[1] == ":")
    return "/" in name


def find_executable(name: str, path: str | None = None) -> Path | None:
    """Absolute path of `name`, or None when it is not installed anywhere known.

    `path` is the PATH to search first (a client's, when the daemon starts a
    process on its behalf); default is this process's PATH.
    """
    if not name:
        return None
    if _is_path(name):
        path = Path(os.path.expanduser(name))
        return path.resolve() if _executable(path) else None
    hit = shutil.which(name, path=path)
    if hit:
        return Path(hit)
    names = [name]
    if IS_WINDOWS and not Path(name).suffix:
        names = [name + ext for ext in _pathext()]
    for directory in search_dirs():
        for candidate_name in names:
            candidate = directory / candidate_name
            if _executable(candidate):
                return candidate
    return None


# npm's cmd-shim: `"%_prog%"  "%dp0%\node_modules\pkg\cli.js" %*`, with
# _prog the node.exe next to the shim or the node on PATH.
_SHIM_SCRIPT = re.compile(r'"%_prog%"\s+"%dp0%\\([^"]+)"\s+%\*', re.IGNORECASE)


def direct_argv(path: Path) -> list[str] | None:
    """For a Windows `.cmd`/`.bat` npm shim: [node, script] to run instead,
    so no argument passes through cmd.exe. None when it is not such a shim
    (or node cannot be found); the caller then refuses to run it."""
    if path.suffix.lower() not in (".cmd", ".bat"):
        return [str(path)]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _SHIM_SCRIPT.search(text)
    if match is None:
        return None
    script = path.parent.joinpath(*match.group(1).split("\\"))
    if not script.is_file():
        return None
    local_node = path.parent / "node.exe"
    node = str(local_node) if local_node.is_file() else shutil.which("node")
    if node is None:
        return None
    return [node, str(script)]


def refused_shim_message(path: Path) -> str:
    return (
        f"`{path}` is a batch file that is not an npm shim pcbridge can read. Running it would "
        "pass the prompt through cmd.exe, which interprets quotes, `&` and `%VAR%` in it, so "
        "pcbridge does not. Point the agent's `command` at the program itself (an .exe, or "
        "node with the script)."
    )


def not_found_message(agent: str, name: str, config_path: object) -> str:
    places = ", ".join((WINDOWS_DIRS if IS_WINDOWS else WELL_KNOWN_DIRS)[:4])
    return (
        f"Agent '{agent}': the command '{name}' was not found on PATH or in {places} "
        "or nvm's node versions. Install it, or set its full path as the first element "
        f"of `command` in [agents.{agent}] of {config_path}."
    )
