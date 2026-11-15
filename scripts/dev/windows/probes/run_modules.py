"""Run every test module on its own, with a time limit, and print one line each.

Used to see which contract and integration modules already pass on Windows
(`windows-vm.sh ssh` then this script). A module runs in its own process, so
one that kills or hangs its interpreter does not take the others down.

    python scripts\\dev\\windows\\probes\\run_modules.py tests\\contracts [tests\\integration]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> None:
    env = {**os.environ, "PCBRIDGE_NO_UPGRADE_CHECK": "1", "PYTHONUTF8": "1"}
    for folder in sys.argv[1:]:
        for path in sorted(Path(folder).glob("test_*.py")):
            module = ".".join(path.with_suffix("").parts)
            try:
                result = subprocess.run([sys.executable, "-m", "unittest", module], capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=180,
                                        env=env)
            except subprocess.TimeoutExpired:
                print(f"TIMEOUT {module}", flush=True)
                continue
            tail = result.stderr.strip().splitlines()[-1:] or [""]
            ran = re.search(r"Ran (\d+) test", result.stderr)
            status = "PASS" if result.returncode == 0 else "FAIL"
            print(f"{status} {module} ran={ran.group(1) if ran else '?'} {tail[0][:120]}", flush=True)


if __name__ == "__main__":
    main()
