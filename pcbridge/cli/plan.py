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
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import distro as distrolib
from .. import paths as pathslib
from ..desktop.session import GNOME
from ..platforminfo import Platform
from . import install as inst

STATE_FILE = "setup.json"


def _has_atspi() -> bool:
    probe = inst.run(["/usr/bin/python3", "-c",
                      "import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi"])
    return probe.returncode == 0


def _tesseract_languages() -> set[str]:
    if not shutil.which("tesseract"):
        return set()
    res = inst.run(["tesseract", "--list-langs"], timeout=10)
    return {line.strip() for line in (res.stdout + res.stderr).splitlines() if line.strip()}


def _has_desktop_extras() -> bool:
    importlib.invalidate_caches()  # pip may have just installed them
    return all(importlib.util.find_spec(m) is not None for m in ("evdev", "PIL"))


def _can_build_evdev() -> dict[str, bool]:
    include = Path(sysconfig.get_path("include") or "/nonexistent")
    return {
        "compiler": bool(shutil.which("gcc") or shutil.which("cc")),
        "kernel-headers": Path("/usr/include/linux/input.h").exists(),
        "python-headers": (include / "Python.h").exists(),
    }


@dataclass
class Probes:
    """What is installed; replaceable in tests."""

    which: Callable[[str], str | None] = shutil.which
    atspi: Callable[[], bool] = _has_atspi
    ocr_languages: Callable[[], set[str]] = _tesseract_languages
    desktop_extras: Callable[[], bool] = _has_desktop_extras
    evdev_build: Callable[[], dict[str, bool]] = _can_build_evdev
    # Only a venv pcbridge owns can take the extras; a package's venv has them.
    own_venv: Callable[[], bool] = lambda: inst.install_kind() in ("user", "git", "pip")


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
    extras: bool = False            # pip still has to install evdev and Pillow

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
        if not probes.which("wl-copy"):
            needs.append("wl-clipboard")
        if platform.desktop_kind == GNOME and not probes.which("gnome-screenshot"):
            needs.append("gnome-screenshot")
        if not probes.atspi():
            needs.append("atspi")
        extras = not probes.desktop_extras() and probes.own_venv()
        raw: list[str] = []
        if extras:
            build = probes.evdev_build()
            if not build["compiler"]:
                needs.append("c-compiler")
            if not build["kernel-headers"]:
                needs.append("kernel-headers")
            if not build["python-headers"]:
                raw.append(distrolib.python_headers_package(
                    f"{sys.version_info[0]}.{sys.version_info[1]}", fam))
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
        extras, raw = False, []
    target, note = decide_profile(platform.desktop_supported, profile_now, recorded_profile)
    names = distrolib.packages(*needs, fam=fam)
    names += [n for n in raw if n not in names]
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
        native_note=native_note(platform.glibc or None),
        extras=extras,
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
        inst.say("Python packages for desktop control: evdev Pillow (pip builds evdev; no sudo)")
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
    """pip-install the [desktop] extra (evdev, Pillow) into this venv."""
    argv = [sys.executable, "-m", "pip", "install", "-q"]
    constraints = constraints_file()
    if constraints is not None:
        argv += ["-c", str(constraints)]
    argv += ["evdev", "Pillow"]
    inst.say("  $ " + shlex.join(argv))
    try:
        return subprocess.run(argv, check=False, timeout=600).returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        inst.fail(str(exc))
        return False


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
        lines.append(f"{CROSS} Desktop control: supported here, but evdev and Pillow are not "
                     "installed (`pcbridge setup` tries again)")
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
           "install_extras", "install_packages", "owns_profile", "read_state", "show", "summary"]
