"""Which code a pcbridge process runs, beyond its version string.

Unreleased builds keep the version of the last release, so comparing
versions could not tell a daemon started before an install from one running
the installed code: `pcbridge setup` said "daemon already running this
version", skipped the restart, and its readiness check then asked the old
process (2026-10-17, twice). The fingerprint is a hash of the package's own
files, contents included, so a reinstall of identical code matches and any
changed file does not. The daemon takes it once at startup, before the files
under it can change.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def fingerprint(root: Path | None = None) -> str:
    """SHA-256 over every file of the package (relative path and contents)."""
    root = root or PACKAGE
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts:
            continue
        h.update(str(rel).encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()
