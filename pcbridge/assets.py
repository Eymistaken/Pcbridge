"""Find the non-Python files pcbridge installs: extension, units, udev rule.

An installed pcbridge carries them in ``pcbridge/_assets/`` (copied there by
``setup.py`` at build time). A git checkout has them at the repository root.
Every caller goes through :func:`asset_path`, so neither layout leaks into the
rest of the code.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_REPO = _PACKAGE.parent

# logical name -> location in a git checkout. Must match setup.py ASSETS.
ASSETS = {
    "gnome-extension": "gnome-extension/pcbridge-gorunur@eymistaken.local",
    "systemd": "systemd",
    "udev": "packaging/udev",
    "modules-load": "packaging/modules-load",
    "config.example.toml": "config.example.toml",
    "skills": "skills",
}

EXTENSION_UUID = "pcbridge-gorunur@eymistaken.local"


def installed_layout() -> bool:
    """True when running from an installed package rather than a checkout."""
    return (_PACKAGE / "_assets").is_dir()


def asset_path(name: str) -> Path:
    """Path of a logical asset, e.g. ``asset_path("systemd") / "pcbridge.service"``.

    Raises FileNotFoundError with an English message naming both places that
    were searched, so a broken install says what is missing.
    """
    top, _, rest = name.partition("/")
    if top not in ASSETS:
        raise KeyError(f"unknown pcbridge asset: {name!r}")
    candidates = [_PACKAGE / "_assets" / top, _REPO / ASSETS[top]]
    for base in candidates:
        path = base / rest if rest else base
        if path.exists():
            return path
    searched = ", ".join(str(c / rest if rest else c) for c in candidates)
    raise FileNotFoundError(f"pcbridge asset {name!r} is missing; looked in: {searched}")
