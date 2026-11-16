#!/usr/bin/env python3
"""Release notes that always open with the install commands.

The install block has one source, `.github/release-install.md`. Every path
that writes a release body goes through `compose()`, which puts the block
on top and the notes below it, so writing the notes can no longer replace
the block (from v2.10.0 to v2.10.3 it did: the written notes were pasted
over the whole draft body).

    scripts/release_notes.py block VERSION [--no-windows]     the block alone
    scripts/release_notes.py compose VERSION NOTES.md [--no-windows]
    scripts/release_notes.py publish VERSION NOTES.md         set a release's notes
    scripts/release_notes.py ensure VERSION                   add the block if it is missing
    scripts/release_notes.py check VERSION                    exit 1 if it is missing
    scripts/release_notes.py ensure-recent [N]                the same for the N newest releases,
                                                              leaving an older block as it is

The Windows lines appear only when the release carries a Windows wheel
(`*-win_amd64.whl`); `publish`, `ensure` and `check` read that from the
release's assets. They call `gh`, which must be signed in to the repository.
A body that already starts with an install block (an older wording too)
gets the current block in its place, never a second one.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".github" / "release-install.md"
SEPARATOR = "---"
_WINDOWS_PART = re.compile(r"<!-- windows -->\n(.*?)<!-- /windows -->\n", re.S)
_VERSION = re.compile(r"v\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?")


def install_block(version: str, windows: bool = True, template: Path = TEMPLATE) -> str:
    """The install block for VERSION, ending in a newline."""
    if not _VERSION.fullmatch(version):
        raise ValueError(f"not a release tag: {version!r} (expected vX.Y.Z)")
    text = template.read_text(encoding="utf-8")
    text = _WINDOWS_PART.sub(lambda m: m.group(1) if windows else "", text)
    return text.replace("{{VERSION}}", version).rstrip("\n") + "\n"


def strip_install_block(body: str) -> str:
    """BODY without a leading install block and its separator, if it has one."""
    text = body.replace("\r\n", "\n").lstrip("\n")
    if not text.startswith("## Install\n"):
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == SEPARATOR:
            return "\n".join(lines[i + 1:]).lstrip("\n")
    # A block with no separator is all there is: no notes were written yet.
    return ""


def compose(version: str, notes: str, windows: bool = True) -> str:
    """The release body: the install block, a rule, then the notes."""
    block = install_block(version, windows)
    rest = strip_install_block(notes).rstrip("\n")
    return f"{block}\n{SEPARATOR}\n\n{rest}\n" if rest else f"{block}\n{SEPARATOR}\n"


def starts_with_block(body: str, version: str, windows: bool) -> bool:
    return body.replace("\r\n", "\n").lstrip("\n").startswith(install_block(version, windows))


# -- GitHub ------------------------------------------------------------------

def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _release(version: str) -> tuple[str, bool]:
    """(body, has a Windows wheel) of a published or draft release."""
    data = json.loads(_gh("release", "view", version, "--json", "body,assets"))
    windows = any(a["name"].endswith("-win_amd64.whl") for a in data.get("assets", []))
    return data.get("body") or "", windows


def _edit(version: str, body: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        _gh("release", "edit", version, "--notes-file", path)
    finally:
        Path(path).unlink(missing_ok=True)


def _recent(count: int) -> list[str]:
    """The tags of the COUNT newest published (not draft) releases."""
    rows = json.loads(_gh("release", "list", "--limit", str(count * 2),
                          "--json", "tagName,isDraft"))
    return [r["tagName"] for r in rows if not r.get("isDraft")][:count]


def _ensure(version: str, notes: str | None = None, keep_older: bool = False) -> None:
    body, windows = _release(version)
    if keep_older and notes is None and body.replace("\r\n", "\n").lstrip("\n").startswith("## Install\n"):
        print(f"{version}: has an install block")
        return
    new = compose(version, body if notes is None else notes, windows)
    if new.rstrip() == body.replace("\r\n", "\n").strip():
        print(f"{version}: unchanged")
        return
    _edit(version, new)
    print(f"{version}: notes written, install block on top"
          + (" (with Windows)" if windows else " (Linux only: no Windows wheel)"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("block", "compose"):
        p = sub.add_parser(name)
        p.add_argument("version")
        if name == "compose":
            p.add_argument("notes", type=Path)
        p.add_argument("--no-windows", dest="windows", action="store_false",
                       help="leave the Windows lines out (a release without a Windows wheel)")
    p = sub.add_parser("publish")
    p.add_argument("version")
    p.add_argument("notes", type=Path)
    for name in ("ensure", "check"):
        sub.add_parser(name).add_argument("version")
    sub.add_parser("ensure-recent").add_argument("count", nargs="?", type=int, default=5)
    args = ap.parse_args(argv)

    if args.cmd == "block":
        sys.stdout.write(install_block(args.version, args.windows))
        return 0
    if args.cmd == "compose":
        sys.stdout.write(compose(args.version, args.notes.read_text(encoding="utf-8"), args.windows))
        return 0

    if args.cmd == "ensure-recent":
        for tag in _recent(args.count):
            _ensure(tag, keep_older=True)
        return 0
    if args.cmd == "check":
        body, windows = _release(args.version)
        ok = starts_with_block(body, args.version, windows)
        print(f"{args.version}: " + ("the install block is at the top" if ok
                                     else "the install block is missing"))
        return 0 if ok else 1
    _ensure(args.version, args.notes.read_text(encoding="utf-8") if args.cmd == "publish" else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
