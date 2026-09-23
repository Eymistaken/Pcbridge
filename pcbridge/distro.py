"""Package names and the install command for this Linux distribution.

pcbridge tells people what to install when something is missing: in
`pcbridge setup`, `pcbridge doctor`, and in the error of a tool that needs a
program. Debian and Ubuntu (with Zorin, Mint and the rest of the family)
and Arch Linux (with Manjaro and EndeavourOS) name the same software
differently, so every such hint goes through here instead of spelling out
one distribution's names.

The family comes from `/etc/os-release`: `ID`, then `ID_LIKE`. Anything
else gets the Debian names, which is what every hint said before Arch
support existed.
"""

from __future__ import annotations

import re
from pathlib import Path

DEBIAN = "debian"
ARCH = "arch"

OS_RELEASE = Path("/etc/os-release")

# logical name -> (Debian packages, Arch packages). A logical name is what
# pcbridge needs, not a package: "atspi" is the GObject bindings plus the
# AT-SPI typelib, whatever the distribution calls them.
PACKAGES: dict[str, tuple[str, str]] = {
    "tmux": ("tmux", "tmux"),
    "wl-clipboard": ("wl-clipboard", "wl-clipboard"),
    "notify-send": ("libnotify-bin", "libnotify"),
    "script": ("bsdutils", "util-linux"),
    "gnome-screenshot": ("gnome-screenshot", "gnome-screenshot"),
    "gtk-launch": ("libgtk-3-bin", "gtk3"),
    "atspi": ("python3-gi gir1.2-atspi-2.0", "python-gobject at-spi2-core"),
    "screencast": (
        "gstreamer1.0-pipewire gstreamer1.0-plugins-good gir1.2-gst-plugins-base-1.0 python3-gi",
        "gst-plugin-pipewire gst-plugins-good gst-plugins-base python-gobject",
    ),
    "tesseract": ("tesseract-ocr", "tesseract"),
    "libpipewire": ("libpipewire-0.3-0t64", "libpipewire"),
    "python-venv": ("python3-venv", "python"),
    "native-build": (
        "libpipewire-0.3-dev libspa-0.2-dev libclang-dev pkg-config",
        "libpipewire clang pkgconf",
    ),
    "readelf": ("binutils", "binutils"),
}

_family_cache: str | None = None


def _parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def family(os_release: str | None = None) -> str:
    """`DEBIAN` or `ARCH`: whose package names to use (Debian when unsure)."""
    global _family_cache
    if os_release is None and _family_cache is not None:
        return _family_cache
    if os_release is None:
        try:
            text = OS_RELEASE.read_text(encoding="utf-8")
        except OSError:
            text = ""
    else:
        text = os_release
    fields = _parse(text)
    ids = [fields.get("ID", "").lower(), *fields.get("ID_LIKE", "").lower().split()]
    result = ARCH if "arch" in ids else DEBIAN
    if os_release is None:
        _family_cache = result
    return result


def packages(*needs: str, fam: str | None = None) -> list[str]:
    """The package names for these logical needs, in order, without repeats."""
    column = 1 if (fam or family()) == ARCH else 0
    out: list[str] = []
    for need in needs:
        names = PACKAGES[need][column] if need in PACKAGES else need
        for name in names.split():
            if name not in out:
                out.append(name)
    return out


def tesseract_language_package(lang: str, fam: str | None = None) -> str:
    """One tesseract language: `tesseract-ocr-tur` or `tesseract-data-tur`."""
    return (f"tesseract-data-{lang}" if (fam or family()) == ARCH
            else f"tesseract-ocr-{lang}")


def install_command(*needs: str, fam: str | None = None, raw: tuple[str, ...] = ()) -> str:
    """`sudo apt install …` or `sudo pacman -S --needed …` for these needs.

    `raw` adds package names that are already distribution-specific (a
    tesseract language from `tesseract_language_package`).
    """
    fam = fam or family()
    names = packages(*needs, fam=fam)
    names += [n for n in raw if n not in names]
    if fam == ARCH:
        return "sudo pacman -S --needed " + " ".join(names)
    return "sudo apt install " + " ".join(names)
