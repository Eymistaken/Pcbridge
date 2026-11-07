#!/usr/bin/env python3
"""Check that a built wheel carries every asset the checkout has.

    packaging/check-wheel.py dist/pcbridge-*.whl

`python -m build` builds the wheel from the sdist, so a file MANIFEST.in
leaves out never reaches the wheel. That shipped 2.6.0 and 2.7.0 without
skills/, and computer_task failed on every install. For each entry of ASSETS
in pcbridge/assets.py, every file in the checkout must be in the wheel under
pcbridge/_assets/. Exits 1 and names what is missing.
"""

from __future__ import annotations

import ast
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# setup.py's build_py leaves these out on purpose.
SKIPPED = {"__pycache__", "gschemas.compiled"}


def assets() -> dict[str, str]:
    tree = ast.parse((ROOT / "pcbridge" / "assets.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ASSETS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("ASSETS not found in pcbridge/assets.py")


def expected() -> set[str]:
    out: set[str] = set()
    for logical, rel in assets().items():
        src = ROOT / rel
        if src.is_file():
            out.add(logical)
            continue
        if not src.is_dir():
            raise SystemExit(f"the asset {rel!r} is missing from the checkout")
        for path in src.rglob("*"):
            if path.is_file() and not SKIPPED & set(path.relative_to(src).parts):
                out.add(f"{logical}/{path.relative_to(src).as_posix()}")
    return out


def carried(wheel: Path) -> set[str]:
    # A platform wheel keeps the package under pcbridge-X.data/purelib/.
    marker = "pcbridge/_assets/"
    return {name.split(marker, 1)[1] for name in zipfile.ZipFile(wheel).namelist()
            if marker in name and not name.endswith("/")}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    want = expected()
    failed = False
    for arg in argv:
        missing = sorted(want - carried(Path(arg)))
        if missing:
            failed = True
            print(f"{arg}: {len(missing)} asset file(s) missing:")
            for name in missing:
                print(f"  pcbridge/_assets/{name}")
        else:
            print(f"{arg}: all {len(want)} asset files present")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
