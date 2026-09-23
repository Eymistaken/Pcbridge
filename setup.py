"""Build glue that pyproject.toml cannot express on its own.

Two things are copied or declared here:

* Non-Python assets that live at the repository root (the GNOME extension,
  the systemd units, the udev rule and config.example.toml) are copied into
  ``pcbridge/_assets/`` inside the built package, so an installed pcbridge
  can install them. A git checkout reads them in place; ``pcbridge.assets``
  knows both layouts.
* When the native helper has been built into ``pcbridge/_native``, the wheel
  is marked platform-specific, because it then carries an x86_64 binary.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

try:
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # pragma: no cover
    _bdist_wheel = None

ROOT = Path(__file__).resolve().parent

# Must match pcbridge/assets.py ASSETS.
ASSETS = {
    "gnome-extension": "gnome-extension/pcbridge-gorunur@eymistaken.local",
    "systemd": "systemd",
    "udev": "packaging/udev",
    "modules-load": "packaging/modules-load",
    "config.example.toml": "config.example.toml",
    "skills": "skills",
}


class build_py(_build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "pcbridge" / "_assets"
        for logical, rel in ASSETS.items():
            src = ROOT / rel
            dst = target / logical
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "gschemas.compiled"))
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)


cmdclass = {"build_py": build_py}

if _bdist_wheel is not None:

    class bdist_wheel(_bdist_wheel):
        def finalize_options(self):
            super().finalize_options()
            if any((ROOT / "pcbridge" / "_native").glob("*/pcbridge-native")):
                self.root_is_pure = False

        def get_tag(self):
            python, abi, plat = super().get_tag()
            if not self.root_is_pure:
                return "py3", "none", plat
            return python, abi, plat

    cmdclass["bdist_wheel"] = bdist_wheel

setup(cmdclass=cmdclass)
