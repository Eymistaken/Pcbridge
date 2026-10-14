"""Package names and the install command for this Linux distribution.

pcbridge tells people what to install when something is missing: in
`pcbridge setup`, `pcbridge doctor`, and in the error of a tool that needs a
program. Debian and Ubuntu (with Zorin, Mint and the rest of the family)
and Arch Linux (with Manjaro and EndeavourOS) name the same software
differently, so every such hint goes through here instead of spelling out
one distribution's names. Fedora's names are here for the install plan
(`pcbridge setup`); Fedora itself is untested.

The family comes from `/etc/os-release`: `ID`, then `ID_LIKE`. Anything
else gets the Debian names, which is what every hint said before Arch
support existed; `detected_family` tells the two cases apart.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

DEBIAN = "debian"
ARCH = "arch"
FEDORA = "fedora"

FAMILY_NAMES = {DEBIAN: "Debian", ARCH: "Arch", FEDORA: "Fedora"}
# The package manager each family installs with.
PACKAGE_MANAGERS = {DEBIAN: "apt-get", ARCH: "pacman", FEDORA: "dnf"}
_COLUMN = {DEBIAN: 0, ARCH: 1, FEDORA: 2}

OS_RELEASE = Path("/etc/os-release")

# logical name -> (Debian packages, Arch packages, Fedora packages). A
# logical name is what pcbridge needs, not a package: "atspi" is the GObject
# bindings plus the AT-SPI typelib, whatever the distribution calls them.
PACKAGES: dict[str, tuple[str, str, str]] = {
    "tmux": ("tmux", "tmux", "tmux"),
    "wl-clipboard": ("wl-clipboard", "wl-clipboard", "wl-clipboard"),
    "notify-send": ("libnotify-bin", "libnotify", "libnotify"),
    "script": ("bsdutils", "util-linux", "util-linux"),
    "gnome-screenshot": ("gnome-screenshot", "gnome-screenshot", "gnome-screenshot"),
    "gtk-launch": ("libgtk-3-bin", "gtk3", "gtk3"),
    "atspi": ("python3-gi gir1.2-atspi-2.0", "python-gobject at-spi2-core",
              "python3-gobject at-spi2-core"),
    "screencast": (
        "gstreamer1.0-pipewire gstreamer1.0-plugins-good gir1.2-gst-plugins-base-1.0 python3-gi",
        "gst-plugin-pipewire gst-plugins-good gst-plugins-base python-gobject",
        "pipewire-gstreamer gstreamer1-plugins-good gstreamer1-plugins-base python3-gobject",
    ),
    # English data comes with the engine on Debian and Fedora; Arch makes
    # every language a choice, English included.
    "tesseract": ("tesseract-ocr", "tesseract tesseract-data-eng", "tesseract"),
    "libpipewire": ("libpipewire-0.3-0t64", "libpipewire", "pipewire-libs"),
    "python-venv": ("python3-venv", "python", "python3"),
    "native-build": (
        "libpipewire-0.3-dev libspa-0.2-dev libclang-dev pkg-config",
        "libpipewire clang pkgconf",
        "pipewire-devel clang-devel pkgconf-pkg-config",
    ),
    "readelf": ("binutils", "binutils", "binutils"),
    # evdev (the [desktop] extra) has no wheels on PyPI: pip builds it.
    "c-compiler": ("gcc libc6-dev", "gcc", "gcc glibc-devel"),
    "kernel-headers": ("linux-libc-dev", "linux-api-headers", "kernel-headers"),
}

# ISO 639-1 (the locale's language) -> tesseract's language code. English
# is left out: every OCR install has it.
TESSERACT_LANGUAGES = {
    "ar": "ara", "bg": "bul", "cs": "ces", "da": "dan", "de": "deu",
    "el": "ell", "es": "spa", "fa": "fas", "fi": "fin", "fr": "fra",
    "he": "heb", "hi": "hin", "hr": "hrv", "hu": "hun", "id": "ind",
    "it": "ita", "ja": "jpn", "ko": "kor", "nb": "nor", "nl": "nld",
    "nn": "nor", "no": "nor", "pl": "pol", "pt": "por", "ro": "ron",
    "ru": "rus", "sk": "slk", "sl": "slv", "sr": "srp", "sv": "swe",
    "th": "tha", "tr": "tur", "uk": "ukr", "vi": "vie", "zh": "chi_sim",
}

_family_cache: str | None = None


def _parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def os_release(text: str | None = None) -> dict[str, str]:
    """The fields of `/etc/os-release` (or of `text`); empty when unreadable."""
    if text is None:
        try:
            text = OS_RELEASE.read_text(encoding="utf-8")
        except OSError:
            text = ""
    return _parse(text)


def detected_family(text: str | None = None) -> str:
    """`DEBIAN`, `ARCH`, `FEDORA`, or "" when `ID`/`ID_LIKE` name none of them.

    Pardus, Zorin and Mint say `debian` or `ubuntu` in `ID_LIKE`; Manjaro and
    EndeavourOS say `arch`; RHEL and its rebuilds say `fedora`.
    """
    fields = os_release(text)
    ids = [fields.get("ID", "").lower(), *fields.get("ID_LIKE", "").lower().split()]
    for fam, names in ((ARCH, ("arch",)), (FEDORA, ("fedora",)),
                       (DEBIAN, ("debian", "ubuntu"))):
        if any(name in ids for name in names):
            return fam
    return ""


def family(os_release: str | None = None) -> str:
    """`DEBIAN`, `ARCH` or `FEDORA`: whose package names to use (Debian when unsure)."""
    global _family_cache
    if os_release is None and _family_cache is not None:
        return _family_cache
    result = detected_family(os_release) or DEBIAN
    if os_release is None:
        _family_cache = result
    return result


def package_manager(fam: str | None = None) -> str:
    """The family's package manager when it is installed here, else ""."""
    name = PACKAGE_MANAGERS.get(fam or family(), "")
    return name if name and shutil.which(name) else ""


def packages(*needs: str, fam: str | None = None) -> list[str]:
    """The package names for these logical needs, in order, without repeats."""
    column = _COLUMN.get(fam or family(), 0)
    out: list[str] = []
    for need in needs:
        names = PACKAGES[need][column] if need in PACKAGES else need
        for name in names.split():
            if name not in out:
                out.append(name)
    return out


def tesseract_language_package(lang: str, fam: str | None = None) -> str:
    """One tesseract language: `tesseract-ocr-tur`, `tesseract-data-tur` or
    `tesseract-langpack-tur` (Debian writes `chi_sim` as `chi-sim`)."""
    fam = fam or family()
    if fam == ARCH:
        return f"tesseract-data-{lang}"
    if fam == FEDORA:
        return f"tesseract-langpack-{lang}"
    return f"tesseract-ocr-{lang.replace('_', '-')}"


def tesseract_language(locale_language: str) -> str:
    """The tesseract code for a locale's language (`tr` -> `tur`), or ""."""
    return TESSERACT_LANGUAGES.get(locale_language.lower(), "")


def python_headers_package(version: str, fam: str | None = None) -> str:
    """The package with Python.h for this Python minor ("3.12")."""
    fam = fam or family()
    if fam == ARCH:
        return "python"
    if fam == FEDORA:
        return "python3-devel"
    return f"python{version}-dev"


def install_argv(names: list[str], fam: str | None = None) -> list[str]:
    """The command that installs these package names without a second question.

    The plan has already asked once; the package manager must not ask again.
    """
    fam = fam or family()
    if fam == ARCH:
        return ["sudo", "pacman", "-S", "--needed", "--noconfirm", *names]
    if fam == FEDORA:
        return ["sudo", "dnf", "install", "-y", *names]
    return ["sudo", "apt-get", "install", "-y", *names]


def install_command(*needs: str, fam: str | None = None, raw: tuple[str, ...] = ()) -> str:
    """`sudo apt install …`, `sudo pacman -S --needed …` or `sudo dnf install …`.

    `raw` adds package names that are already distribution-specific (a
    tesseract language from `tesseract_language_package`).
    """
    fam = fam or family()
    names = packages(*needs, fam=fam)
    names += [n for n in raw if n not in names]
    if fam == ARCH:
        return "sudo pacman -S --needed " + " ".join(names)
    if fam == FEDORA:
        return "sudo dnf install " + " ".join(names)
    return "sudo apt install " + " ".join(names)
