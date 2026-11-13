"""The install plan: detection, what is offered where, and who owns the profile.

Nothing here installs a package or reads the real session: logind, the user
manager and the installed programs are replaced by fakes.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge import distro, platforminfo  # noqa: E402
from pcbridge.cli import plan as planlib  # noqa: E402
from pcbridge.desktop.session import CINNAMON as CINNAMON_KIND, XFCE as XFCE_KIND  # noqa: E402
from pcbridge.desktop.session import GNOME, HYPRLAND, KDE, UNKNOWN  # noqa: E402
from pcbridge.native import discovery  # noqa: E402

DEBIAN12 = ('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\n'
            'VERSION_ID="12"\nID=debian\n')
PARDUS = 'NAME="Pardus"\nVERSION_ID="25.0"\nID=pardus\nID_LIKE=debian\n'
ARCH = 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\n'


def example_config(path: Path, **edits: str) -> None:
    """config.example.toml made valid, with `key = value` lines replaced."""
    import re

    text = (Path(__file__).resolve().parents[2] / "config.example.toml").read_text()
    text = re.sub(r'(?m)^public_url = .*$', 'public_url = "http://localhost:8765"', text, count=1)
    text = re.sub(r'(?m)^password = .*$', 'password = "test-password-123456789"', text, count=1)
    text = re.sub(r'(?m)^static_token = .*$', 'static_token = "test-token-0123456789"', text, count=1)
    for key, value in edits.items():
        text = re.sub(rf'(?m)^{key} = .*$', f'{key} = "{value}"', text, count=1)
    path.write_text(text)


def platform(**kw) -> platforminfo.Platform:
    base = dict(arch="x86_64", distro_name="Arch Linux", distro_id="arch",
                family=distro.ARCH, package_manager="pacman", session_type="wayland",
                desktop="KDE Plasma", desktop_kind=KDE, desktop_version="6.7.5",
                session_source="environment", glibc=(2, 42), language="tr")
    base.update(kw)
    return platforminfo.Platform(**base)


CINNAMON = platform(distro_name="Pardus 25.0", distro_id="pardus", family=distro.DEBIAN,
                    package_manager="apt-get", session_type="x11", desktop="Cinnamon",
                    desktop_kind=UNKNOWN, desktop_version="6.4.8", glibc=(2, 41))

# Nothing installed, but the Python extras present (the common case after install.sh).
# The native helper is present in both: test_native_fetch.py covers the download.
NOTHING = planlib.Probes(which=lambda name: None, a11y_bus=lambda: False,
                         system_gi=lambda: False, ocr_languages=set,
                         desktop_extras=lambda: True, own_venv=lambda: True,
                         native_fetchable=lambda: False, kind=lambda: "user")
EVERYTHING = planlib.Probes(which=lambda name: f"/usr/bin/{name}", a11y_bus=lambda: True,
                            system_gi=lambda: True,
                            ocr_languages=lambda: {"eng", "tur", "osd"},
                            desktop_extras=lambda: True, own_venv=lambda: True,
                            native_fetchable=lambda: False, kind=lambda: "user")


class SessionTests(unittest.TestCase):
    def test_a_graphical_environment_is_used_as_it_is(self) -> None:
        s = platforminfo.session_from_environment(
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "zorin:GNOME", "LANG": "tr_TR.UTF-8"})
        self.assertEqual((s.type, s.desktop, s.source), ("wayland", "zorin:GNOME", "environment"))

    def test_ssh_has_no_graphical_environment(self) -> None:
        self.assertIsNone(platforminfo.session_from_environment({"XDG_SESSION_TYPE": "tty"}))
        self.assertIsNone(platforminfo.session_from_environment({}))

    def test_logind_names_the_local_graphical_session(self) -> None:
        answers = {
            ("loginctl", "show-user"): "Sessions=7 2\nDisplay=2\n",
            ("loginctl", "show-session", "7"): "Type=tty\nDesktop=\nClass=user\nRemote=yes\nActive=yes\n",
            ("loginctl", "show-session", "2"): "Type=x11\nDesktop=cinnamon\nClass=user\nRemote=no\nActive=yes\n",
            ("systemctl",): "LANG=tr_TR.UTF-8\nXDG_CURRENT_DESKTOP=X-Cinnamon\n",
        }

        def run(cmd, timeout=3.0, env=None):
            for key, out in answers.items():
                if tuple(cmd[:len(key)]) == key or (key[0] == "loginctl" and len(key) == 3
                                                    and cmd[:2] == list(key[:2]) and cmd[2] == key[2]):
                    return out
            return ""

        with mock.patch.object(platforminfo, "_run", side_effect=run):
            s = platforminfo.session_from_logind(1000)
        self.assertEqual((s.type, s.desktop, s.language, s.source),
                         ("x11", "X-Cinnamon", "tr_TR.UTF-8", "loginctl"))

    def test_logind_falls_back_to_the_session_desktop_property(self) -> None:
        def run(cmd, timeout=3.0, env=None):
            if cmd[:2] == ["loginctl", "show-user"]:
                return "Sessions=3\nDisplay=3\n"
            if cmd[:2] == ["loginctl", "show-session"]:
                return "Type=wayland\nDesktop=plasma\nClass=user\nRemote=no\nActive=yes\n"
            return ""

        with mock.patch.object(platforminfo, "_run", side_effect=run):
            s = platforminfo.session_from_logind(1000)
        self.assertEqual((s.type, s.desktop), ("wayland", "plasma"))

    def test_no_graphical_session_is_reported_as_none(self) -> None:
        with mock.patch.object(platforminfo, "_run", return_value=""):
            self.assertEqual(platforminfo.session_from_logind(1000), platforminfo.GraphicalSession())


class DetectTests(unittest.TestCase):
    def detect(self, os_release: str, env: dict[str, str]) -> platforminfo.Platform:
        with mock.patch.object(platforminfo, "desktop_version", return_value="1.0"), \
                mock.patch.object(platforminfo, "session_from_logind",
                                  return_value=platforminfo.GraphicalSession()):
            return platforminfo.detect(env, os_release)

    def test_names_the_distribution_family_and_desktop(self) -> None:
        p = self.detect(PARDUS, {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "X-Cinnamon"})
        self.assertEqual(p.family, distro.DEBIAN)
        self.assertEqual(p.desktop, "Cinnamon")
        self.assertEqual(p.desktop_kind, CINNAMON_KIND)
        self.assertEqual(p.found_line(), f"Found: Pardus 25.0 (Debian family) · Cinnamon 1.0 · X11 · {p.arch}")
        if p.arch.lower() in platforminfo.HELPER_ARCHES:
            self.assertTrue(p.desktop_supported)
            self.assertTrue(p.x11)
            self.assertEqual(p.unsupported_reason(), "")

    def test_x11_desktops_need_x11_and_others_stay_unsupported(self) -> None:
        for desktop, session_type, kind in (("X-Cinnamon", "x11", CINNAMON_KIND),
                                            ("XFCE", "x11", XFCE_KIND)):
            with self.subTest(desktop=desktop):
                p = self.detect(DEBIAN12, {"XDG_SESSION_TYPE": session_type,
                                           "XDG_CURRENT_DESKTOP": desktop})
                self.assertEqual(p.desktop_kind, kind)
        p = self.detect(DEBIAN12, {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "X-Cinnamon"})
        self.assertFalse(p.desktop_supported)
        self.assertEqual(p.unsupported_reason(),
                         "Cinnamon on Wayland is not supported; log in to the Cinnamon X11 session")
        p = self.detect(DEBIAN12, {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "MATE"})
        self.assertFalse(p.desktop_supported)
        self.assertEqual(p.unsupported_reason(), "MATE on X11 is not supported yet")

    def test_the_family_is_not_repeated_for_its_own_distribution(self) -> None:
        p = self.detect(DEBIAN12, {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "X-Cinnamon"})
        self.assertTrue(p.found_line().startswith("Found: Debian GNU/Linux 12 · Cinnamon"))

    def test_supported_desktops_need_wayland(self) -> None:
        for desktop, kind in (("zorin:GNOME", GNOME), ("KDE", KDE), ("Hyprland", HYPRLAND)):
            with self.subTest(desktop=desktop):
                p = self.detect(ARCH, {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": desktop})
                self.assertEqual(p.desktop_kind, kind)
                self.assertTrue(p.desktop_supported)
        p = self.detect(ARCH, {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"})
        self.assertFalse(p.desktop_supported)
        self.assertIn("log in to the GNOME Wayland session", p.unsupported_reason())

    def test_a_server_without_a_session_says_so(self) -> None:
        p = self.detect(ARCH, {"XDG_SESSION_TYPE": "tty"})
        self.assertEqual(p.session_type, "")
        self.assertIn("no graphical session", p.found_line())
        self.assertIn("no graphical session was found", p.unsupported_reason())

    def test_logind_plasma_is_kde(self) -> None:
        with mock.patch.object(platforminfo, "desktop_version", return_value=""), \
                mock.patch.object(platforminfo, "session_from_logind",
                                  return_value=platforminfo.GraphicalSession("wayland", "plasma", "", "loginctl")):
            p = platforminfo.detect({}, ARCH)
        self.assertEqual((p.desktop, p.desktop_kind, p.session_source), ("KDE Plasma", KDE, "loginctl"))

    def test_the_locale_language(self) -> None:
        with mock.patch.dict(os.environ, {"LANG": "", "LC_ALL": "", "LC_MESSAGES": ""}), \
                mock.patch.object(Path, "read_text", side_effect=OSError):
            self.assertEqual(platforminfo.locale_language("tr_TR.UTF-8"), "tr")
            self.assertEqual(platforminfo.locale_language("C.UTF-8", "de_DE.UTF-8"), "de")
            self.assertEqual(platforminfo.locale_language("C"), "")


class PlanTests(unittest.TestCase):
    def test_an_unsupported_desktop_gets_only_what_works_there(self) -> None:
        plan = planlib.build(CINNAMON, probes=NOTHING)
        self.assertEqual(plan.packages, ["tmux", "bsdutils", "libnotify-bin"])
        self.assertEqual(plan.components, [])  # no OCR: find_text needs the grant
        for name in ("wl-clipboard", "python3-gi", "tesseract-ocr", "gstreamer1.0-pipewire"):
            self.assertNotIn(name, plan.chosen_packages())
        self.assertEqual(plan.profile_target, "core")

    def test_a_supported_desktop_offers_ocr_in_the_locale_language(self) -> None:
        plan = planlib.build(platform(), probes=NOTHING)
        self.assertEqual(plan.packages, ["tmux", "util-linux", "libnotify", "wl-clipboard",
                                         "at-spi2-core", "python-gobject"])
        [ocr] = plan.components
        self.assertEqual(ocr.languages, ["eng", "tur"])
        self.assertEqual(ocr.packages, ["tesseract", "tesseract-data-eng", "tesseract-data-tur"])
        self.assertEqual(plan.install_argv()[:5], ["sudo", "pacman", "-S", "--needed", "--noconfirm"])
        ocr.ticked = False
        self.assertNotIn("tesseract", plan.chosen_packages())

    def test_debian_ocr_brings_english_with_the_engine(self) -> None:
        p = platform(family=distro.DEBIAN, package_manager="apt-get", desktop="GNOME",
                     desktop_kind=GNOME)
        [ocr] = planlib.build(p, probes=NOTHING).components
        self.assertEqual(ocr.packages, ["tesseract-ocr", "tesseract-ocr-tur"])

    def test_an_english_locale_adds_no_language(self) -> None:
        [ocr] = planlib.build(platform(language="en"), probes=NOTHING).components
        self.assertEqual(ocr.languages, ["eng"])

    def test_missing_pillow_is_installed_by_pip_only_where_it_is_used(self) -> None:
        probes = replace(NOTHING, desktop_extras=lambda: False)
        gnome = platform(family=distro.DEBIAN, package_manager="apt-get",
                         desktop="GNOME", desktop_kind=GNOME)
        plan = planlib.build(gnome, probes=probes)
        self.assertTrue(plan.extras)
        # Pillow has wheels: nothing is compiled, so no compiler or headers.
        for name in ("gcc", "libc6-dev", "linux-libc-dev", "linux-api-headers"):
            self.assertNotIn(name, plan.packages)
        # Not on an unsupported desktop, and not into a package's venv.
        self.assertFalse(planlib.build(CINNAMON, probes=probes).extras)
        self.assertFalse(planlib.build(platform(), probes=replace(probes, own_venv=lambda: False)).extras)

    def test_the_atspi_bus_and_the_system_gi_are_asked_for_only_where_used(self) -> None:
        gnome = platform(family=distro.DEBIAN, package_manager="apt-get",
                         desktop="GNOME", desktop_kind=GNOME)
        # Every supported desktop reads the tree off the AT-SPI bus ...
        for where in (gnome, platform(), platform(desktop="Hyprland", desktop_kind=HYPRLAND)):
            with self.subTest(desktop=where.desktop):
                self.assertIn("atspi-bus", planlib.build(where, probes=NOTHING).needs)
                self.assertNotIn("atspi-bus", planlib.build(
                    where, probes=replace(NOTHING, a11y_bus=lambda: True)).needs)
        # ... but only KDE Plasma's window scripts run in the system python3.
        self.assertIn("python3-gi", planlib.build(platform(), probes=NOTHING).needs)
        self.assertNotIn("python3-gi", planlib.build(gnome, probes=NOTHING).needs)
        self.assertNotIn("python3-gi", planlib.build(
            platform(), probes=replace(NOTHING, system_gi=lambda: True)).needs)
        # Neither where desktop control is not supported.
        cinnamon = planlib.build(CINNAMON, probes=NOTHING).needs
        self.assertNotIn("atspi-bus", cinnamon)
        self.assertNotIn("python3-gi", cinnamon)

    def test_nothing_missing_means_nothing_to_run(self) -> None:
        plan = planlib.build(platform(), probes=EVERYTHING)
        self.assertEqual(plan.chosen_packages(), [])
        self.assertIsNone(plan.install_argv())
        self.assertTrue(plan.components[0].installed)

    def test_a_server_gets_no_notification_package(self) -> None:
        plan = planlib.build(replace(CINNAMON, session_type="", desktop=""), probes=NOTHING)
        self.assertNotIn("libnotify-bin", plan.packages)

    def test_no_package_manager_means_no_command_to_run(self) -> None:
        plan = planlib.build(replace(CINNAMON, package_manager=""), probes=NOTHING)
        self.assertIsNone(plan.install_argv())

    def test_the_glibc_shortfall_is_said(self) -> None:
        with mock.patch.object(discovery, "glibc_needed", return_value=(2, 39)), \
                mock.patch.object(Path, "is_file", return_value=True):
            note = planlib.native_note((2, 36))
            self.assertIn("needs glibc 2.39 and this system has 2.36", note)
            self.assertEqual(planlib.native_note((2, 39)), "")


class ProfileTests(unittest.TestCase):
    def test_setup_owns_the_default_and_its_own_value_only(self) -> None:
        self.assertEqual(planlib.decide_profile(False, "full", None)[0], "core")
        self.assertEqual(planlib.decide_profile(False, "core", "core")[0], None)
        # The desktop became supported: setup takes its own "core" back.
        self.assertEqual(planlib.decide_profile(True, "core", "core")[0], "full")
        # The user chose these; setup keeps out.
        self.assertIsNone(planlib.decide_profile(False, "full", "core")[0])
        self.assertIsNone(planlib.decide_profile(False, "readonly", None)[0])
        self.assertIsNone(planlib.decide_profile(True, "core", None)[0])
        self.assertIn("your choice", planlib.decide_profile(False, "desktop", None)[1])

    def test_apply_writes_the_profile_and_remembers_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            example_config(cfg)
            with mock.patch.object(planlib, "_state_path", return_value=Path(tmp) / "setup.json"):
                plan = planlib.build(CINNAMON, probes=NOTHING)
                self.assertEqual(planlib.apply_settings(plan, cfg), ['tools.profile = "core"'])
                self.assertRegex(cfg.read_text(), r'(?m)^profile = "core"$')
                self.assertEqual(planlib.read_state(), {"profile": "core"})
                # The user picks "full" by hand: the next run keeps it.
                example_config(cfg, profile="full")
                again = planlib.build(CINNAMON, profile_now="full",
                                      recorded_profile=planlib.read_state()["profile"], probes=NOTHING)
                self.assertIsNone(again.profile_target)
                planlib.apply_settings(again, cfg)
                self.assertRegex(cfg.read_text(), r'(?m)^profile = "full"$')

    def test_apply_adds_the_installed_ocr_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            example_config(cfg)
            with mock.patch.object(planlib, "_state_path", return_value=Path(tmp) / "setup.json"):
                plan = planlib.build(platform(), probes=NOTHING)
                self.assertIn('desktop.ocr_languages = "eng+tur"', planlib.apply_settings(plan, cfg))
                example_config(cfg, ocr_languages="eng+deu")
                self.assertEqual(planlib.apply_settings(plan, cfg), [])


class NativeGlibcTests(unittest.TestCase):
    def test_the_requirement_is_read_from_the_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "helper"
            binary.write_bytes(b"\x7fELF...GLIBC_2.2.5\x00GLIBC_2.34\x00GLIBC_2.39\x00")
            self.assertEqual(discovery.glibc_needed(binary), (2, 39))
            self.assertEqual(discovery.glibc_shortfall(binary, (2, 39)), "")
            self.assertEqual(discovery.glibc_shortfall(binary, (2, 41)), "")
            self.assertIn("2.36", discovery.glibc_shortfall(binary, (2, 36)))
            self.assertEqual(discovery.glibc_shortfall(binary, ()), "")  # musl: not judged

    def test_an_old_glibc_skips_only_the_packaged_helper(self) -> None:
        from pcbridge.config import NativeSpec
        from pcbridge.desktop.errors import DesktopError

        with tempfile.TemporaryDirectory() as tmp:
            target = discovery._target_triple()
            packaged = Path(tmp) / "_native" / target / "pcbridge-native"
            packaged.parent.mkdir(parents=True)
            packaged.write_bytes(b"GLIBC_2.39")
            packaged.chmod(0o755)
            with mock.patch("pcbridge.platforminfo.glibc_version", return_value=(2, 36)):
                with self.assertRaises(DesktopError) as caught:
                    discovery.discover_native_binary(NativeSpec(), environ={}, package_root=Path(tmp))
                self.assertIn("glibc 2.39", caught.exception.message)
                # One the user names is theirs to choose.
                self.assertEqual(
                    discovery.discover_native_binary(
                        NativeSpec(), environ={"PCBRIDGE_NATIVE_BIN": str(packaged)}),
                    packaged.resolve())
            with mock.patch("pcbridge.platforminfo.glibc_version", return_value=(2, 39)):
                self.assertEqual(discovery.discover_native_binary(
                    NativeSpec(), environ={}, package_root=Path(tmp)), packaged.resolve())


if __name__ == "__main__":
    unittest.main()
