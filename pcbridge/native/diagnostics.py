"""The native helper section of `doctor.sh`: find it, describe it, ask nothing.

Every check is read-only. The binary describes itself with `--build-info`,
which opens neither the session bus nor PipeWire. The capability check runs a
real handshake in a throwaway state directory where no grant exists, so the
helper can say whether capture could work here without a screen share, a
permission prompt, or the user's real grant ever being touched.

Output is one finding per line, `level<TAB>message`, for `doctor.sh` to color.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pcbridge.config import Config, NativeSpec
from pcbridge.desktop.errors import DesktopError

from .discovery import _target_triple, discover_native_binary
from .protocol import PROTOCOL_MAJOR

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# What a release helper links against, measured 2026-09-13 with `readelf -d`.
# Anything outside this set is a dependency the packaging notes do not list.
# `libm.so.6` since Task 5.3 (measured 2026-09-19): its only symbol is `hypot`,
# the pointer path's distance, and it ships with `libc6` like `libc.so.6`.
EXPECTED_LIBRARIES = frozenset(
    {
        "libpipewire-0.3.so.0",
        "libgcc_s.so.1",
        "libm.so.6",
        "libc.so.6",
        "ld-linux-x86-64.so.2",
    }
)

Runner = Callable[..., subprocess.CompletedProcess]
Probe = Callable[[Path], "tuple[str, dict[str, Any]] | str"]


@dataclass(frozen=True)
class Finding:
    level: str  # pass | warn | fail | info
    message: str

    def line(self) -> str:
        return f"{self.level}\t{self.message}"


def _source(spec: NativeSpec, environ: Mapping[str, str]) -> str:
    if environ.get("PCBRIDGE_NATIVE_BIN", "").strip():
        return "PCBRIDGE_NATIVE_BIN"
    if spec.binary_path is not None:
        return "config: [native] binary_path"
    return "paket: pcbridge/_native"


def read_build_info(binary: Path, run: Runner = subprocess.run) -> dict[str, Any] | str:
    """The helper's own description, or why it could not give one."""
    try:
        proc = run(
            [str(binary), "--build-info"],
            capture_output=True, text=True, timeout=5, check=False, cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the helper could not be run: {exc}"
    if proc.returncode != 0:
        if "unsupported command-line arguments" in (proc.stderr or ""):
            return "no --build-info: a build older than Task 4.1 (scripts/build-native.sh)"
        return f"--build-info exit {proc.returncode}: {(proc.stderr or '').strip()[:160]}"
    try:
        info = json.loads(proc.stdout)
    except ValueError:
        return "--build-info returned no JSON"
    return info if isinstance(info, dict) else "--build-info returned no JSON object"


def linked_libraries(
    binary: Path, run: Runner = subprocess.run
) -> tuple[list[str], list[str]] | str:
    """(NEEDED kutuphaneleri, cozulemeyenler) ya da neden okunamadigi."""
    env = {**os.environ, "LC_ALL": "C"}
    try:
        needed = run(["readelf", "-d", str(binary)], capture_output=True, text=True,
                     timeout=10, check=False, env=env)
        resolved = run(["ldd", str(binary)], capture_output=True, text=True,
                       timeout=10, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the library list could not be read: {exc}"
    libraries = [
        line.split("[", 1)[1].split("]", 1)[0]
        for line in needed.stdout.splitlines()
        if "(NEEDED)" in line and "[" in line
    ]
    missing = [
        line.split("=>", 1)[0].strip()
        for line in resolved.stdout.splitlines()
        if "not found" in line
    ]
    if not libraries:
        return "the library list could not be read (not an ELF file?)"
    return libraries, missing


def handshake_capabilities(binary: Path) -> tuple[str, dict[str, Any]] | str:
    """Handshake plus `capabilities` in a throwaway state directory."""
    from .client import NativeClient

    with tempfile.TemporaryDirectory(prefix="pcbridge-doctor-") as raw:
        root = Path(raw)
        (root / "state").mkdir()
        (root / "runtime").mkdir()
        client = NativeClient(binary, state_dir=root / "state", runtime_dir=root / "runtime")
        try:
            response = client.request("capabilities", {}, timeout=10)
            handshake = client.handshake
        except DesktopError as exc:
            return f"handshake failed: {exc.message}"
        except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
            return f"handshake failed: {exc}"
        finally:
            client.close()
    if response.error:
        return f"capabilities returned an error: {response.error.get('code')}"
    result = response.result if isinstance(response.result, dict) else {}
    return (handshake.build_id if handshake is not None else ""), result


def _legacy_note() -> Finding:
    return Finding(
        "info",
        "native capture and accessibility need neither python3-gi, GStreamer nor "
        "pipewiresrc; the window list before a grant (screen_info), the Python "
        "accessibility path and the Python screen share still need python3-gi",
    )


# What a helper must offer for `[native] accessibility`: reads since Task 6.2,
# clicks and text since Task 6.3. An older build answers neither.
ACCESSIBILITY_CAPABILITIES = ("accessibility.read", "accessibility.action")


def diagnose(
    cfg: Config,
    *,
    environ: Mapping[str, str] | None = None,
    run: Runner = subprocess.run,
    probe: Probe = handshake_capabilities,
    package_root: Path = PACKAGE_ROOT,
) -> list[Finding]:
    environment = os.environ if environ is None else environ
    capture = cfg.native.capture
    typing = cfg.native.input
    reading = cfg.native.accessibility
    # The helper is needed as much as the strictest setting says.
    choices = (capture, typing, reading)
    selected = (
        "rust" if "rust" in choices
        else "auto" if "auto" in choices
        else "python"
    )
    findings = [
        Finding(
            "info",
            f"[native] capture = {capture}"
            + {
                "python": " (frames come from the Python helper)",
                "rust": " (native helper REQUIRED)",
                "auto": " (default: native when available, otherwise Python)",
            }.get(capture, ""),
        ),
        Finding(
            "info",
            f"[native] input = {typing}"
            + {
                "python": " (keyboard, pointer and clipboard on the Python path)",
                "rust": " (native helper REQUIRED)",
                "auto": " (default: native when available, otherwise Python)",
            }.get(typing, ""),
        ),
        Finding(
            "info",
            f"[native] accessibility = {reading}"
            + {
                "python": " (ui_dump/ui_click in the Python helper)",
                "rust": " (native helper REQUIRED)",
                "auto": " (default: native when available, otherwise Python)",
            }.get(reading, ""),
        ),
    ]

    try:
        binary = discover_native_binary(
            cfg.native, environ=environment, package_root=package_root
        )
    except DesktopError as exc:
        level = {"rust": "fail", "auto": "warn"}.get(selected, "info")
        findings.append(Finding(
            level, f"no native helper: {exc.message} To build it: scripts/build-native.sh"
        ))
        findings.append(_legacy_note())
        return findings
    findings.append(Finding("pass", f"found: {binary} ({_source(cfg.native, environment)})"))

    info = read_build_info(binary, run)
    if isinstance(info, str):
        findings.append(Finding("warn", info))
    else:
        protocol = info.get("protocol") if isinstance(info.get("protocol"), dict) else {}
        findings.append(Finding(
            "pass",
            f"version {info.get('version')} · build {info.get('build_id')} · protocol "
            f"{protocol.get('major')}.{protocol.get('minor')} · {info.get('target')} · "
            f"{info.get('profile')}",
        ))
        if info.get("test_harness") is not False:
            findings.append(Finding(
                "fail",
                "test-harness build: a fake backend that reads no real screen "
                "(rebuild with scripts/build-native.sh)",
            ))
        if protocol.get("major") != PROTOCOL_MAJOR:
            findings.append(Finding(
                "fail", f"protocol {protocol.get('major')}, this pcbridge speaks {PROTOCOL_MAJOR}"
            ))
        expected = _target_triple()
        if expected is not None and info.get("target") != expected:
            findings.append(Finding(
                "fail", f"target {info.get('target')}, this machine is {expected}"
            ))
        if info.get("profile") != "release":
            findings.append(Finding(
                "warn",
                "not a release build: PNG encoding is ~9 times slower in a debug build (measured) "
                "— scripts/build-native.sh",
            ))

    libraries = linked_libraries(binary, run)
    if isinstance(libraries, str):
        findings.append(Finding("info", libraries))
    else:
        needed, missing = libraries
        if missing:
            findings.append(Finding(
                "fail",
                "unresolved libraries: " + ", ".join(missing)
                + " (Ubuntu 24.04: sudo apt install libpipewire-0.3-0t64)",
            ))
        else:
            findings.append(Finding(
                "pass", "runtime libraries present: " + ", ".join(needed)
            ))
        unexpected = sorted(set(needed) - EXPECTED_LIBRARIES)
        if unexpected:
            findings.append(Finding(
                "warn", "undocumented library dependency: " + ", ".join(unexpected)
            ))

    probed = probe(binary)
    if isinstance(probed, str):
        findings.append(Finding("fail", probed))
    else:
        build_id, result = probed
        for capability in result.get("capabilities", []):
            text = (
                f"{capability.get('name')}: {capability.get('status')} "
                f"({result.get('backend')})"
            )
            if capability.get("status") == "supported":
                findings.append(Finding("pass", text))
            else:
                # A degraded entry may carry its limitation instead of a reason
                # (`window.list`: only accessibility-visible applications).
                reason = (
                    capability.get("reason")
                    or "; ".join(capability.get("limitations") or [])
                    or "no reason given"
                )
                findings.append(Finding("warn", f"{text} — {reason}"))
        if not isinstance(info, str) and build_id and build_id != info.get("build_id"):
            findings.append(Finding(
                "warn",
                f"handshake build {build_id}, --build-info {info.get('build_id')}: "
                "the binary may have changed in between",
            ))
        offered = {capability.get("name") for capability in result.get("capabilities", [])}
        lacking = [name for name in ACCESSIBILITY_CAPABILITIES if name not in offered]
        if reading in ("auto", "rust") and lacking:
            # `auto` takes the helper whenever it is there, old or new: an old
            # one then fails every ui_dump instead of falling back.
            findings.append(Finding(
                "fail" if reading == "rust" else "warn",
                "the helper is an older build: " + ", ".join(lacking) + " missing; "
                "ui_dump/ui_click fail on the native path. Rebuild it: "
                "scripts/build-native.sh",
            ))

    if selected == "python":
        findings.append(Finding(
            "info",
            "the helper is ready but unused; to use it set [native] capture, "
            'input and accessibility to "auto" or "rust"',
        ))
    findings.append(_legacy_note())
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    from pcbridge.config import load_config
    from pcbridge.desktop import session as sessionlib

    sessionlib.ensure_session_env()
    try:
        cfg = load_config()
    except SystemExit as exc:
        print(f"fail\tthe config could not be read: {exc}")
        return 0
    for finding in diagnose(cfg):
        print(finding.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
