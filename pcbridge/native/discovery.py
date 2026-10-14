"""Locate the private pcbridge-native executable without searching PATH."""

from __future__ import annotations

import os
import platform
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from pcbridge.config import NativeSpec
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode


def _expanded(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def _target_triple() -> str | None:
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine in ("amd64", "x86_64"):
        return "x86_64-unknown-linux-gnu"
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return "aarch64-apple-darwin"
    if sys.platform == "darwin" and machine in ("amd64", "x86_64"):
        return "x86_64-apple-darwin"
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "x86_64-pc-windows-msvc"
    return None


def _require_executable(path: Path, source: str) -> Path:
    if path.is_file() and os.access(path, os.X_OK):
        return path
    raise DesktopError(
        code=ErrorCode.NATIVE_NOT_FOUND,
        message=f"The native helper is missing or not executable: {path}",
        category=ErrorCategory.IPC,
        retryable=False,
        suggested_action=f"check_{source}_native_binary",
        backend="pcbridge-native",
    )


_GLIBC_SYMBOL = re.compile(rb"GLIBC_2\.(\d+)")
_needs_cache: dict[tuple[str, int, int], tuple[int, int]] = {}


def glibc_needed(path: Path) -> tuple[int, int]:
    """The newest glibc symbol version the binary links against, (0, 0) if none.

    Read from the GLIBC_2.N version strings in the file (5 ms for the 7 MB
    helper, and the same answer as `objdump -T`), so a helper built on this
    machine is judged by what it really needs, not by a fixed number.
    """
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key not in _needs_cache:
            found = [int(m) for m in _GLIBC_SYMBOL.findall(path.read_bytes())]
            _needs_cache[key] = (2, max(found)) if found else (0, 0)
        return _needs_cache[key]
    except OSError:
        return (0, 0)


def glibc_shortfall(path: Path, have: tuple[int, ...] | None = None) -> str:
    """Why this helper cannot start on this system's glibc, "" when it can."""
    if have is None:
        from pcbridge.platforminfo import glibc_version

        have = glibc_version()
    need = glibc_needed(path)
    if not have or need == (0, 0) or tuple(have[:2]) >= need:
        return ""
    return (f"the packaged native helper needs glibc {need[0]}.{need[1]} and this system has "
            f"{have[0]}.{have[1]}, so it was skipped and the Python paths are used")


def discover_native_binary(
    spec: NativeSpec,
    *,
    environ: Mapping[str, str] | None = None,
    package_root: Path | None = None,
) -> Path:
    """Return the configured helper using the fixed discovery priority."""

    environment = os.environ if environ is None else environ
    explicit = environment.get("PCBRIDGE_NATIVE_BIN", "").strip()
    if explicit:
        return _require_executable(_expanded(explicit), "environment")

    if spec.binary_path is not None:
        return _require_executable(spec.binary_path.resolve(), "configured")

    target = _target_triple()
    if target is not None:
        root = package_root or Path(__file__).resolve().parents[1]
        suffix = ".exe" if sys.platform == "win32" else ""
        packaged = root / "_native" / target / f"pcbridge-native{suffix}"
        if packaged.is_file() and os.access(packaged, os.X_OK):
            # Only the packaged helper is judged: one named by the user or
            # the environment is theirs to choose.
            why = glibc_shortfall(packaged)
            if why:
                raise DesktopError(
                    code=ErrorCode.NATIVE_NOT_FOUND,
                    message=why[0].upper() + why[1:] + ".",
                    category=ErrorCategory.IPC,
                    retryable=False,
                    suggested_action="build_native_helper_or_use_python",
                    backend="pcbridge-native",
                )
            return packaged.resolve()

    raise DesktopError(
        code=ErrorCode.NATIVE_NOT_FOUND,
        message="The pcbridge native helper was not found.",
        category=ErrorCategory.IPC,
        retryable=False,
        suggested_action="configure_native_binary",
        backend="pcbridge-native",
    )


__all__ = ["discover_native_binary", "glibc_needed", "glibc_shortfall"]
