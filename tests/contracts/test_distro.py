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
ROCKY = 'NAME="Rocky Linux"\nID="rocky"\nID_LIKE="rhel centos fedora"\n'
PARDUS = 'NAME="Pardus"\nID=pardus\nID_LIKE=debian\nVERSION_ID="23.4"\n'
OPENSUSE = 'NAME="openSUSE Tumbleweed"\nID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n'


class FamilyTests(unittest.TestCase):
    def test_the_family_comes_from_id_then_id_like(self) -> None:
        self.assertEqual(distro.family(UBUNTU), distro.DEBIAN)
        self.assertEqual(distro.family(ZORIN), distro.DEBIAN)
        self.assertEqual(distro.family(ARCH), distro.ARCH)
        self.assertEqual(distro.family(ENDEAVOUR), distro.ARCH)

    def test_pardus_counts_as_debian_and_rhel_rebuilds_as_fedora(self) -> None:
        self.assertEqual(distro.detected_family(PARDUS), distro.DEBIAN)
        self.assertEqual(distro.family(FEDORA), distro.FEDORA)
        self.assertEqual(distro.family(ROCKY), distro.FEDORA)

    def test_anything_else_keeps_the_debian_names(self) -> None:
        self.assertEqual(distro.family(OPENSUSE), distro.DEBIAN)
        self.assertEqual(distro.family(""), distro.DEBIAN)
        # ...but the plan can tell that it only guessed.
        self.assertEqual(distro.detected_family(OPENSUSE), "")
        self.assertEqual(distro.detected_family(""), "")


class HintTests(unittest.TestCase):
    def test_every_need_has_a_name_in_every_family(self) -> None:
        for need, names in distro.PACKAGES.items():
            with self.subTest(need=need):
                self.assertEqual(len(names), 3)
                self.assertTrue(all(n.strip() for n in names))

    def test_the_install_command_uses_the_family_package_manager(self) -> None:
        self.assertEqual(distro.install_command("atspi", fam=distro.DEBIAN),
                         "sudo apt install python3-gi gir1.2-atspi-2.0")
        self.assertEqual(distro.install_command("atspi", fam=distro.ARCH),
                         "sudo pacman -S --needed python-gobject at-spi2-core")
        self.assertEqual(distro.install_command("script", "notify-send", fam=distro.ARCH),
                         "sudo pacman -S --needed util-linux libnotify")
        self.assertEqual(distro.install_command("atspi", fam=distro.FEDORA),
                         "sudo dnf install python3-gobject at-spi2-core")

    def test_the_plan_runs_the_manager_without_a_second_question(self) -> None:
        self.assertEqual(distro.install_argv(["tmux"], distro.DEBIAN),
                         ["sudo", "apt-get", "install", "-y", "tmux"])
        self.assertEqual(distro.install_argv(["tmux"], distro.ARCH),
                         ["sudo", "pacman", "-S", "--needed", "--noconfirm", "tmux"])
        self.assertEqual(distro.install_argv(["tmux"], distro.FEDORA),
                         ["sudo", "dnf", "install", "-y", "tmux"])

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
        self.assertEqual(distro.tesseract_language_package("tur", fam=distro.FEDORA),
                         "tesseract-langpack-tur")
        self.assertEqual(
            distro.install_command(raw=("tesseract-data-tur",), fam=distro.ARCH),
            "sudo pacman -S --needed tesseract-data-tur",
        )

    def test_debian_writes_script_variants_with_a_dash(self) -> None:
        self.assertEqual(distro.tesseract_language_package("chi_sim", fam=distro.DEBIAN),
                         "tesseract-ocr-chi-sim")
        self.assertEqual(distro.tesseract_language_package("chi_sim", fam=distro.ARCH),
                         "tesseract-data-chi_sim")

    def test_the_locale_language_names_its_tesseract_data(self) -> None:
        self.assertEqual(distro.tesseract_language("tr"), "tur")
        self.assertEqual(distro.tesseract_language("de"), "deu")
        self.assertEqual(distro.tesseract_language("en"), "")
        self.assertEqual(distro.tesseract_language(""), "")

    def test_arch_makes_english_ocr_data_a_choice(self) -> None:
        self.assertEqual(distro.packages("tesseract", fam=distro.ARCH),
                         ["tesseract", "tesseract-data-eng"])
        self.assertEqual(distro.packages("tesseract", fam=distro.DEBIAN), ["tesseract-ocr"])


if __name__ == "__main__":
    unittest.main()
