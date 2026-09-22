"""Locate the private pcbridge-native executable without searching PATH."""

from __future__ import annotations

import os
import platform
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
            return packaged.resolve()

    raise DesktopError(
        code=ErrorCode.NATIVE_NOT_FOUND,
        message="The pcbridge native helper was not found.",
        category=ErrorCategory.IPC,
        retryable=False,
        suggested_action="configure_native_binary",
        backend="pcbridge-native",
    )


__all__ = ["discover_native_binary"]
