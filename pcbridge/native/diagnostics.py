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
        return f"yardimci calistirilamadi: {exc}"
    if proc.returncode != 0:
        if "unsupported command-line arguments" in (proc.stderr or ""):
            return "--build-info yok: Task 4.1'den eski bir derleme (scripts/build-native.sh)"
        return f"--build-info cikis {proc.returncode}: {(proc.stderr or '').strip()[:160]}"
    try:
        info = json.loads(proc.stdout)
    except ValueError:
        return "--build-info JSON dondurmedi"
    return info if isinstance(info, dict) else "--build-info bir JSON nesnesi dondurmedi"


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
        return f"kutuphane listesi okunamadi: {exc}"
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
        return "kutuphane listesi okunamadi (ELF degil mi?)"
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
            return f"handshake basarisiz: {exc.message}"
        except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
            return f"handshake basarisiz: {exc}"
        finally:
            client.close()
    if response.error:
        return f"capabilities hata dondurdu: {response.error.get('code')}"
    result = response.result if isinstance(response.result, dict) else {}
    return (handshake.build_id if handshake is not None else ""), result


def _legacy_note() -> Finding:
    return Finding(
        "info",
        "native capture python3-gi, GStreamer ve pipewiresrc istemez; "
        "erisilebilirlik (ui_dump/ui_click) ve Python ekran yayini hala python3-gi ister",
    )


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
    # The helper is needed as much as the strictest of the two settings says.
    selected = (
        "rust" if "rust" in (capture, typing)
        else "auto" if "auto" in (capture, typing)
        else "python"
    )
    findings = [
        Finding(
            "info",
            f"[native] capture = {capture}"
            + {
                "python": " (kareyi Python yardimcisi aliyor)",
                "rust": " (native yardimci ZORUNLU)",
                "auto": " (varsayilan: varsa native, yoksa Python)",
            }.get(capture, ""),
        ),
        Finding(
            "info",
            f"[native] input = {typing}"
            + {
                "python": " (klavye, fare ve pano Python yolunda)",
                "rust": " (native yardimci ZORUNLU)",
                "auto": " (varsayilan: varsa native, yoksa Python)",
            }.get(typing, ""),
        ),
    ]

    try:
        binary = discover_native_binary(
            cfg.native, environ=environment, package_root=package_root
        )
    except DesktopError as exc:
        level = {"rust": "fail", "auto": "warn"}.get(selected, "info")
        findings.append(Finding(
            level, f"native yardimci yok: {exc.message} Derlemek icin: scripts/build-native.sh"
        ))
        findings.append(_legacy_note())
        return findings
    findings.append(Finding("pass", f"bulundu: {binary} ({_source(cfg.native, environment)})"))

    info = read_build_info(binary, run)
    if isinstance(info, str):
        findings.append(Finding("warn", info))
    else:
        protocol = info.get("protocol") if isinstance(info.get("protocol"), dict) else {}
        findings.append(Finding(
            "pass",
            f"surum {info.get('version')} · build {info.get('build_id')} · protokol "
            f"{protocol.get('major')}.{protocol.get('minor')} · {info.get('target')} · "
            f"{info.get('profile')}",
        ))
        if info.get("test_harness") is not False:
            findings.append(Finding(
                "fail",
                "test-harness derlemesi: sahte backend, gercek ekran okumaz "
                "(scripts/build-native.sh ile yeniden derleyin)",
            ))
        if protocol.get("major") != PROTOCOL_MAJOR:
            findings.append(Finding(
                "fail", f"protokol {protocol.get('major')}, bu pcbridge {PROTOCOL_MAJOR} konusuyor"
            ))
        expected = _target_triple()
        if expected is not None and info.get("target") != expected:
            findings.append(Finding(
                "fail", f"hedef {info.get('target')}, bu makine {expected}"
            ))
        if info.get("profile") != "release":
            findings.append(Finding(
                "warn",
                "release degil: PNG kodlama debug derlemede ~9 kat yavas (olculdu) "
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
                "cozulemeyen kutuphaneler: " + ", ".join(missing)
                + " (Ubuntu 24.04: sudo apt install libpipewire-0.3-0t64)",
            ))
        else:
            findings.append(Finding(
                "pass", "calisma zamani kutuphaneleri tamam: " + ", ".join(needed)
            ))
        unexpected = sorted(set(needed) - EXPECTED_LIBRARIES)
        if unexpected:
            findings.append(Finding(
                "warn", "belgelenmemis kutuphane bagimliligi: " + ", ".join(unexpected)
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
                reason = capability.get("reason") or "neden bildirilmedi"
                findings.append(Finding("warn", f"{text} — {reason}"))
        if not isinstance(info, str) and build_id and build_id != info.get("build_id"):
            findings.append(Finding(
                "warn",
                f"handshake build {build_id}, --build-info {info.get('build_id')}: "
                "binary arada degismis olabilir",
            ))

    if selected == "python":
        findings.append(Finding(
            "info",
            "yardimci hazir ama kullanilmiyor; secmek icin [native] capture ve "
            'input icin "auto" ya da "rust"',
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
        print(f"fail\tyapilandirma okunamadi: {exc}")
        return 0
    for finding in diagnose(cfg):
        print(finding.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
