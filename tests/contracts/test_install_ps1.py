"""install.ps1 installs the Windows build. PowerShell 7 also runs on Linux and
macOS; there the script must name the right installer and stop before it
touches anything. The order is checked everywhere; where `pwsh` is installed
(GitHub's Ubuntu runners have it) the script is also run."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "install.ps1"
LINUX_COMMAND = "curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh"


@unittest.skipUnless(SCRIPT.is_file(), "install.ps1 is not next to the tests (a package's test copy)")
class InstallPs1PlatformTests(unittest.TestCase):
    def test_the_guard_comes_before_anything_that_changes_the_machine(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        guard = text.index('-and -not $IsWindows')
        for first_use in ("$env:LOCALAPPDATA", "New-Item", "Invoke-WebRequest", "Join-Path"):
            with self.subTest(first_use=first_use):
                self.assertLess(guard, text.index(first_use))
        self.assertIn(LINUX_COMMAND, text)

    @unittest.skipIf(sys.platform == "win32" or not shutil.which("pwsh"), "needs PowerShell 7 off Windows")
    def test_pwsh_off_windows_names_the_linux_installer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "HOME": tmp, "TMPDIR": tmp}
            runs = {
                "as a file": ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)],
                # `irm ... | iex` runs the text, not a file: there it must throw, not exit.
                "piped": ["pwsh", "-NoProfile", "-NonInteractive", "-Command",
                          f"Get-Content -Raw '{SCRIPT}' | Invoke-Expression"],
            }
            for how, argv in runs.items():
                with self.subTest(how=how):
                    proc = subprocess.run(argv, cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                                          capture_output=True, text=True, timeout=60)
                    out = proc.stdout + proc.stderr
                    self.assertNotEqual(proc.returncode, 0, out)
                    if sys.platform == "darwin":
                        self.assertIn("macOS is not supported", out)
                    else:
                        self.assertIn("this script installs pcbridge on Windows", out)
                        self.assertIn(LINUX_COMMAND, out)
                    self.assertNotIn("pcbridge-install-", out)
            self.assertFalse([p for p in Path(tmp).iterdir() if p.name.startswith("pcbridge-install-")])


if __name__ == "__main__":
    unittest.main()
