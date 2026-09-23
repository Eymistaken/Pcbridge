"""Package hints name Debian or Arch packages, whichever this system is."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge import distro  # noqa: E402

UBUNTU = 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\n'
ZORIN = 'NAME="Zorin OS"\nID=zorin\nID_LIKE="ubuntu debian"\n'
ARCH = 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\n'
ENDEAVOUR = 'NAME="EndeavourOS"\nID="endeavouros"\nID_LIKE="arch"\n'
FEDORA = 'NAME="Fedora Linux"\nID=fedora\n'


class FamilyTests(unittest.TestCase):
    def test_the_family_comes_from_id_then_id_like(self) -> None:
        self.assertEqual(distro.family(UBUNTU), distro.DEBIAN)
        self.assertEqual(distro.family(ZORIN), distro.DEBIAN)
        self.assertEqual(distro.family(ARCH), distro.ARCH)
        self.assertEqual(distro.family(ENDEAVOUR), distro.ARCH)

    def test_anything_else_keeps_the_debian_names(self) -> None:
        self.assertEqual(distro.family(FEDORA), distro.DEBIAN)
        self.assertEqual(distro.family(""), distro.DEBIAN)


class HintTests(unittest.TestCase):
    def test_every_need_has_a_name_on_both_families(self) -> None:
        for need, (debian, arch) in distro.PACKAGES.items():
            with self.subTest(need=need):
                self.assertTrue(debian.strip())
                self.assertTrue(arch.strip())

    def test_the_install_command_uses_the_family_package_manager(self) -> None:
        self.assertEqual(distro.install_command("atspi", fam=distro.DEBIAN),
                         "sudo apt install python3-gi gir1.2-atspi-2.0")
        self.assertEqual(distro.install_command("atspi", fam=distro.ARCH),
                         "sudo pacman -S --needed python-gobject at-spi2-core")
        self.assertEqual(distro.install_command("script", "notify-send", fam=distro.ARCH),
                         "sudo pacman -S --needed util-linux libnotify")

    def test_repeats_are_dropped_and_order_is_kept(self) -> None:
        self.assertEqual(
            distro.packages("atspi", "screencast", fam=distro.ARCH),
            ["python-gobject", "at-spi2-core", "gst-plugin-pipewire",
             "gst-plugins-good", "gst-plugins-base"],
        )

    def test_tesseract_languages_are_separate_packages_on_both(self) -> None:
        self.assertEqual(distro.tesseract_language_package("tur", fam=distro.DEBIAN),
                         "tesseract-ocr-tur")
        self.assertEqual(distro.tesseract_language_package("tur", fam=distro.ARCH),
                         "tesseract-data-tur")
        self.assertEqual(
            distro.install_command(raw=("tesseract-data-tur",), fam=distro.ARCH),
            "sudo pacman -S --needed tesseract-data-tur",
        )


if __name__ == "__main__":
    unittest.main()
