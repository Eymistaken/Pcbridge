"""The install plan of `pcbridge setup`: what was found, what will work, what
to install, asked once.

Built from `platforminfo.detect()` so the install script and setup share one
detection. System packages are planned only for capabilities that will work
on this machine: an unsupported desktop gets no AT-SPI, clipboard or OCR
packages, and the tool profile leaves out the desktop tools that would only
refuse there. Every command that needs sudo is printed before it runs.

`pcbridge doctor --fix` offers the same missing parts again.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import distro as distrolib
from .. import paths as pathslib
from ..desktop.session import KDE, X11_KINDS
from ..platforminfo import Platform
from . import install as inst

STATE_FILE = "setup.json"


# Where D-Bus looks for activatable session services; at-spi2-core puts the
# accessibility bus there (`org.a11y.Bus`, measured on Zorin OS 18).
DBUS_SERVICE_DIRS = (Path("/usr/share/dbus-1/services"), Path("/usr/local/share/dbus-1/services"))


def _has_a11y_bus() -> bool:
    return any((d / "org.a11y.Bus.service").is_file() for d in DBUS_SERVICE_DIRS)


def _has_library(name: str) -> bool:
    """A shared library the dynamic linker would find (the helper's libpipewire)."""
    probe = inst.run([shutil.which("ldconfig") or "/sbin/ldconfig", "-p"])
    if probe.returncode == 0:
        return any(line.strip().startswith(name + " ") for line in probe.stdout.splitlines())
    return any(Path(d, name).exists() for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib64",
                                                 "/usr/lib", "/lib/x86_64-linux-gnu"))


def _has_system_gi() -> bool:
    """The system python3 imports PyGObject (`kwin_helper.py` runs there)."""
    probe = inst.run(["/usr/bin/python3", "-c",
                      "import gi; gi.require_version('Gio', '2.0'); from gi.repository import Gio"])
    return probe.returncode == 0


def _tesseract_languages() -> set[str]:
    if not shutil.which("tesseract"):
        return set()
    res = inst.run(["tesseract", "--list-langs"], timeout=10)
    return {line.strip() for line in (res.stdout + res.stderr).splitlines() if line.strip()}


def _has_desktop_extras() -> bool:
    importlib.invalidate_caches()  # pip may have just installed it
    return importlib.util.find_spec("PIL") is not None


def _native_fetchable() -> bool:
    """No native helper discovery would find, and a release builds one for here.

    A helper is found when it is packaged under `pcbridge/_native/<target>/`
    or named by `PCBRIDGE_NATIVE_BIN` or `[native] binary_path`. Releases
    build only x86_64, so another architecture has nothing to fetch.
    """
    import os

    from ..native.discovery import _target_triple
    from ..native.fetch import packaged_path

    target = _target_triple()
    if target is None or os.environ.get("PCBRIDGE_NATIVE_BIN", "").strip():
        return False
    path = packaged_path(target)
    if path.is_file() and os.access(path, os.X_OK):
        return False
    try:
        from .. import settings as settingslib

        named = settingslib.ConfigEditor(pathslib.config_file()).value("native.binary_path")
    except Exception:  # noqa: BLE001 - no config yet, so nothing is named
        named = ""
    return not str(named or "").strip()


@dataclass
class Probes:
    """What is installed; replaceable in tests."""

    which: Callable[[str], str | None] = shutil.which
    a11y_bus: Callable[[], bool] = _has_a11y_bus
    system_gi: Callable[[], bool] = _has_system_gi
    library: Callable[[str], bool] = _has_library
    ocr_languages: Callable[[], set[str]] = _tesseract_languages
    desktop_extras: Callable[[], bool] = _has_desktop_extras
    # Only a venv pcbridge owns can take the extras; a package's venv has them.
    own_venv: Callable[[], bool] = lambda: inst.install_kind() in ("user", "git", "pip")
    native_fetchable: Callable[[], bool] = _native_fetchable
    kind: Callable[[], str] = lambda: inst.install_kind()


@dataclass
class Component:
    """One optional part, offered in the checklist."""

    key: str
    title: str
    packages: list[str]      # what is still missing for it
    ticked: bool = True
    languages: list[str] = field(default_factory=list)  # tesseract codes it adds

    @property
    def installed(self) -> bool:
        return not self.packages


@dataclass
class Plan:
    platform: Platform
    family: str                     # whose package names (Debian when unknown)
    needs: list[str]                # logical needs that are missing
    packages: list[str]             # their package names
    components: list[Component]
    profile_now: str
    profile_target: str | None      # the profile setup will set, None = leave it
    profile_owned: bool             # False: the user chose the profile, setup keeps out
    profile_note: str
    native_note: str
    extras: bool = False            # pip still has to install Pillow
    native_fetch: bool = False      # download the release's native helper (no Rust here)

    def chosen_packages(self) -> list[str]:
        out = list(self.packages)
        for c in self.components:
            if c.ticked:
                out += [p for p in c.packages if p not in out]
        return out

    def install_argv(self) -> list[str] | None:
        names = self.chosen_packages()
        if not names or not self.platform.package_manager:
            return None
        return distrolib.install_argv(names, self.family)


# ---------------------------------------------------------------------------
# building the plan
# ---------------------------------------------------------------------------


def _state_path() -> Path:
    return pathslib.state_home() / STATE_FILE


def read_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def owns_profile(current: str, recorded: str | None) -> bool:
    return current == recorded if recorded is not None else current == "full"


def decide_profile(supported: bool, current: str, recorded: str | None) -> tuple[str | None, str]:
    """(profile to set or None, why). Setup changes only a profile it owns.

    Setup owns the profile when the value is the one it wrote last time, or,
    with no record, when it is still the default "full". Anything else is
    the user's choice and stays, with a note when it cannot work here.
    """
    owned = owns_profile(current, recorded)
    target = "full" if supported else "core"
    if not owned:
        if not supported and current in ("full", "desktop"):
            return None, (f'"{current}" (your choice) is kept; its desktop tools refuse here. '
                          '`pcbridge set tools.profile core` leaves them out')
        return None, f'"{current}" (your choice) is kept'
    if current == target:
        return None, (f'"{current}"' + ("" if supported else ": desktop tools are left out, "
                                        "they would only refuse here"))
    if supported:
        return target, f'"{target}": desktop control is available here again'
    return target, f'"{target}": desktop tools are left out, they would only refuse here'


def native_note(glibc: tuple[int, ...] | None = None) -> str:
    """Why the packaged native helper will not be used, "" when it will (or is absent)."""
    from ..native.discovery import _target_triple, glibc_shortfall

    target = _target_triple()
    if target is None:
        return ""
    packaged = Path(__file__).resolve().parents[1] / "_native" / target / "pcbridge-native"
    if not packaged.is_file():
        return ""
    why = glibc_shortfall(packaged, glibc)
    return why[0].upper() + why[1:] if why else ""


def build(platform: Platform, *, profile_now: str = "full", recorded_profile: str | None = None,
          probes: Probes | None = None) -> Plan:
    probes = probes or Probes()
    fam = platform.family or distrolib.DEBIAN
    needs: list[str] = []
    if not probes.which("tmux"):
        needs.append("tmux")
    if not probes.which("script"):
        needs.append("script")
    if platform.session_type and not probes.which("notify-send"):
        needs.append("notify-send")  # notifications need a desktop to show them on
    components: list[Component] = []
    if platform.desktop_supported:
        if platform.desktop_kind in X11_KINDS:
            # X11: xclip is the clipboard program, and the helper still links
            # libpipewire, which Xfce does not install (measured).
            if not probes.which("xclip"):
                needs.append("xclip")
            if not probes.library("libpipewire-0.3.so.0"):
                needs.append("libpipewire")
        elif not probes.which("wl-copy"):
            needs.append("wl-clipboard")
        # The native helper reads the accessibility tree off the AT-SPI bus.
        if not probes.a11y_bus():
            needs.append("atspi-bus")
        # KDE Plasma's window scripts run in the system python3 with PyGObject.
        if platform.desktop_kind == KDE and not probes.system_gi():
            needs.append("python3-gi")
        # Pillow crops and scales the frames; it has wheels, so nothing is
        # compiled (evdev was, for the Python input path that is gone).
        extras = not probes.desktop_extras() and probes.own_venv()
        # OCR for find_text / wait_for_text: English plus the locale's language.
        langs = ["eng"]
        extra = distrolib.tesseract_language(platform.language)
        if extra and extra not in langs:
            langs.append(extra)
        have = probes.ocr_languages()
        missing: list[str] = []
        if not probes.which("tesseract"):
            missing += distrolib.packages("tesseract", fam=fam)
        for lang in langs:
            if lang not in have:
                name = distrolib.tesseract_language_package(lang, fam=fam)
                # English data comes with the engine on Debian and Fedora.
                if lang == "eng" and fam != distrolib.ARCH:
                    continue
                if name not in missing:
                    missing.append(name)
        title = "OCR for find_text / wait_for_text (" + " + ".join(langs) + ")"
        components.append(Component("ocr", title, missing, ticked=True, languages=langs))
    else:
        extras = False
    # A distribution package carries the helper. Any other installation that
    # has none takes the release's prebuilt one; a checkout with Rust can
    # build its own instead (install.sh tries that first, so getting here
    # with Rust usually means the build failed).
    native_fetch, build_note = False, ""
    if platform.desktop_supported and probes.native_fetchable():
        kind = probes.kind()
        if kind in ("git", "pip", "user"):
            native_fetch = True
            if kind == "git" and probes.which("cargo"):
                build_note = ("Rust is installed: scripts/build-native.sh builds the helper "
                              "from this checkout instead of the download")
    target, note = decide_profile(platform.desktop_supported, profile_now, recorded_profile)
    names = distrolib.packages(*needs, fam=fam)
    return Plan(
        platform=platform,
        family=fam,
        needs=needs,
        packages=names,
        components=components,
        profile_now=profile_now,
        profile_target=target,
        profile_owned=owns_profile(profile_now, recorded_profile),
        profile_note=note,
        native_note=native_note(platform.glibc or None) or build_note,
        extras=extras,
        native_fetch=native_fetch,
    )


# ---------------------------------------------------------------------------
# showing it, asking once, running it
# ---------------------------------------------------------------------------

CHECK = "✓"
CROSS = "✗"


def capability_lines(plan: Plan) -> list[str]:
    p = plan.platform
    lines = [f"{CHECK} Terminal, files, tmux, coding agents"]
    if p.desktop_supported:
        lines.append(f"{CHECK} Desktop control (off until you turn it on: "
                     "pcbridge set desktop.enabled true)")
    else:
        lines.append(f"{CROSS} Desktop control: {p.unsupported_reason()}")
    if plan.native_note:
        lines.append(f"  {plan.native_note}")
    return lines


def show(plan: Plan) -> None:
    inst.say(plan.platform.found_line())
    for line in capability_lines(plan):
        inst.say(line)
    if plan.packages:
        inst.say("System packages: " + " ".join(plan.packages) + "  (needs sudo)")
    else:
        inst.say("System packages: nothing to install")
    if plan.components:
        inst.say("Optional components:")
        for i, c in enumerate(plan.components, 1):
            state = "installed" if c.installed else " ".join(c.packages)
            mark = "x" if (c.ticked or c.installed) else " "
            inst.say(f"  [{mark}] {i}. {c.title}: {state}")
    if plan.extras:
        inst.say("Python package for desktop control: Pillow (no sudo)")
    if plan.native_fetch:
        inst.say("Native helper: downloaded from the pcbridge release and checked against "
                 "its sha256sums.txt (no sudo)")
    inst.say(f"Tool profile: {plan.profile_note}")
    argv = plan.install_argv()
    if argv:
        inst.say("Runs: " + shlex.join(argv))
    elif plan.chosen_packages():
        inst.say("No known package manager here: install those packages yourself "
                 "(the names above are Debian's).")


def ask(plan: Plan) -> bool:
    """The one confirmation. A number toggles a component; Enter goes ahead."""
    toggles = [c for c in plan.components if not c.installed]
    while True:
        hint = "Enter to go ahead"
        if toggles:
            hint += ", a number to tick or untick a component"
        try:
            answer = _read_tty(f"{hint}, n to stop: ").strip().lower()
        except EOFError:
            return False
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no", "q"):
            return False
        if answer.isdigit() and 1 <= int(answer) <= len(plan.components):
            c = plan.components[int(answer) - 1]
            if c.installed:
                inst.say(f"  {c.title} is already installed.")
                continue
            c.ticked = not c.ticked
            inst.say(f"  [{'x' if c.ticked else ' '}] {c.title}")
            argv = plan.install_argv()
            inst.say("Runs: " + (shlex.join(argv) if argv else "nothing"))


def _read_tty(prompt: str) -> str:
    return input(prompt)


def interactive() -> bool:
    return sys.stdin.isatty()


def run_sudo(argv: list[str]) -> bool:
    """Print the command, then run it with the terminal (sudo asks there)."""
    if not interactive() and argv[:1] == ["sudo"]:
        argv = ["sudo", "-n", *argv[1:]]  # no terminal to ask a password on
    if not interactive() and argv[:3] == ["sudo", "-n", "apt-get"]:
        # No terminal for debconf to ask on: take the package defaults quietly.
        argv = ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", *argv[2:]]
    inst.say("  $ " + shlex.join(argv))
    try:
        return subprocess.run(argv, check=False).returncode == 0
    except OSError as exc:
        inst.fail(f"{argv[0]}: {exc}")
        return False


def install_packages(plan: Plan) -> tuple[bool, str]:
    """Install what the plan chose. (done, the command when it is left to do)."""
    argv = plan.install_argv()
    if argv is None:
        names = plan.chosen_packages()
        return (not names), (distrolib.install_command(raw=tuple(names), fam=plan.family) if names else "")
    if not shutil.which("sudo"):
        return False, shlex.join(argv)
    if run_sudo(argv):
        return True, ""
    if plan.family == distrolib.DEBIAN:
        # A fresh or long-idle system has stale package lists (a cloud image
        # has none at all); refresh them once and try again.
        inst.warn("the install failed; refreshing the package lists and trying once more")
        if run_sudo(["sudo", "apt-get", "update"]) and run_sudo(argv):
            return True, ""
    return False, shlex.join(argv)


def constraints_file() -> Path | None:
    """The dependency versions this pcbridge was tested with, when known."""
    root = inst.repo_root()
    for cand in ((root / "packaging" / "constraints.txt") if root else None,
                 pathslib.data_home() / "constraints.txt"):
        if cand is not None and cand.is_file():
            return cand
    return None


def install_extras() -> bool:
    """pip-install the [desktop] extra (Pillow) into this venv."""
    argv = [sys.executable, "-m", "pip", "install", "-q"]
    constraints = constraints_file()
    if constraints is not None:
        argv += ["-c", str(constraints)]
    argv += ["Pillow"]
    inst.say("  $ " + shlex.join(argv))
    try:
        return subprocess.run(argv, check=False, timeout=600).returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        inst.fail(str(exc))
        return False


def fetch_native() -> tuple[bool, str]:
    """Download the release's native helper. (installed, the line to show)."""
    from .. import __version__
    from ..native import fetch as fetchlib

    inst.say("  downloading pcbridge-native from the pcbridge release ...")
    try:
        got = fetchlib.fetch(version=__version__)
    except fetchlib.FetchError as exc:
        return False, f"native helper not installed: {exc}"
    return True, (f"native helper from release {got.tag} ({got.size / 1_000_000:.1f} MB, "
                  f"sha256 {got.sha256[:12]}, handshake ok): {got.path}")


def apply_settings(plan: Plan, config_path: Path) -> list[str]:
    """Set the tool profile and the OCR languages setup decided on. Returns lines to show."""
    from .. import settings as S

    out: list[str] = []
    state = read_state()
    ed = S.ConfigEditor(config_path)
    if plan.profile_target is not None:
        ed.set("tools.profile", plan.profile_target)
        out.append(f"tools.profile = \"{plan.profile_target}\"")
    # Remember the value setup owns: a later run changes it only while it is
    # still this value, so a profile the user picks afterwards stays theirs.
    if plan.profile_owned:
        state["profile"] = plan.profile_target or plan.profile_now
    for c in plan.components:
        if c.key == "ocr" and (c.ticked or c.installed) and len(c.languages) > 1:
            want = "+".join(c.languages)
            if ed.value("desktop.ocr_languages") == "eng" and want != "eng":
                ed.set("desktop.ocr_languages", want)
                out.append(f"desktop.ocr_languages = \"{want}\"")
    if ed.changed:
        ed.save()
    write_state(state)
    return out


def summary(plan: Plan, probes: Probes | None = None) -> list[str]:
    """What works, what does not and why, measured again after the install."""
    probes = probes or Probes()
    p = plan.platform
    lines = []
    missing = [name for name in ("tmux",) if not probes.which(name)]
    if missing:
        lines.append(f"{CROSS} tmux tools: tmux is not installed ({distrolib.install_command('tmux', fam=plan.family)})")
        lines.append(f"{CHECK} Terminal, files, coding agents")
    else:
        lines.append(f"{CHECK} Terminal, files, tmux, coding agents")
    if p.desktop_supported and plan.extras and not probes.desktop_extras():
        lines.append(f"{CROSS} Desktop control: supported here, but Pillow is not "
                     "installed (`pcbridge setup` tries again)")
    elif p.desktop_supported and plan.native_fetch and probes.native_fetchable():
        lines.append(f"{CROSS} Desktop control: supported here, but the native helper is not "
                     "installed and desktop control needs it; `pcbridge doctor --fix` "
                     "downloads it")
    elif p.desktop_supported:
        lines.append(f"{CHECK} Desktop control: supported here, off until you turn it on "
                     "(pcbridge set desktop.enabled true)")
    if p.desktop_supported:
        for c in plan.components:
            if c.key == "ocr":
                have = bool(probes.which("tesseract")) and set(c.languages) <= probes.ocr_languages()
                lines.append(f"{CHECK} OCR ({'+'.join(c.languages)})" if have else
                             f"{CROSS} OCR: not installed; `pcbridge doctor --fix` offers it again")
    else:
        lines.append(f"{CROSS} Desktop control: {p.unsupported_reason()}")
    if plan.native_note:
        lines.append(f"  {plan.native_note}")
    profile = plan.profile_target or plan.profile_now
    lines.append(f"Tool profile: \"{profile}\"")
    lines.append("Run `pcbridge setup` again after a system or desktop update: it detects everything again.")
    return lines


__all__ = ["Component", "Plan", "Probes", "apply_settings", "ask", "build", "decide_profile",
           "fetch_native", "install_extras", "install_packages", "owns_profile", "read_state",
           "show", "summary"]
