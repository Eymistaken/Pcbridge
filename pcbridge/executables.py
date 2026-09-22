"""Find the agent CLIs (claude, codex, agy, ...) wherever they are installed.

A daemon started by systemd does not see the PATH of an interactive shell:
nvm, npm's user prefix and bun all add their bin directories in ~/.bashrc.
So a bare command name is looked up in three places, first hit wins:

    1. an absolute or relative path given in config.toml, as is;
    2. the process PATH;
    3. the usual per-user install directories listed in WELL_KNOWN_DIRS,
       plus nvm's node versions (newest first).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

WELL_KNOWN_DIRS = (
    "~/.local/bin",
    "~/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.cargo/bin",
    "~/.deno/bin",
    "/usr/local/bin",
)


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
    dirs = [Path(os.path.expanduser(d)) for d in WELL_KNOWN_DIRS]
    return dirs + nvm_bin_dirs()


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_executable(name: str) -> Path | None:
    """Absolute path of `name`, or None when it is not installed anywhere known."""
    if not name:
        return None
    if "/" in name:
        path = Path(os.path.expanduser(name))
        return path.resolve() if _executable(path) else None
    hit = shutil.which(name)
    if hit:
        return Path(hit)
    for directory in search_dirs():
        candidate = directory / name
        if _executable(candidate):
            return candidate
    return None


def not_found_message(agent: str, name: str, config_path: object) -> str:
    places = ", ".join(WELL_KNOWN_DIRS[:4])
    return (
        f"Agent '{agent}': the command '{name}' was not found on PATH or in {places} "
        "or nvm's node versions. Install it, or set its full path as the first element "
        f"of `command` in [agents.{agent}] of {config_path}."
    )
