#!/usr/bin/env python3
"""Task 4.2: the Rust capture backend against the legacy one, on the real desktop.

Skipped unless `PCBRIDGE_TEST_CAPTURE=1`. It opens screen shares and covers
every monitor with a static test pattern for a few minutes. It sends no input:
nothing is clicked, typed or moved. Every grant lives in a scratch state
directory, so the user's real desktop grant is never read or written, and the
captured images stay in a scratch directory that is removed at the end.

The evidence is what Gate 4 asks for (`PLAN.md` Task 4.2): monitor identity,
offset, size and mapping parity; static pixel parity at full size and at 1536;
pointer modes; freshness after a request; warm latency p95 against the legacy
path; session startups; revoke and expiry; the Python host dying; the native
helper crashing; delivery to an HTTP MCP client while `shell_run` keeps
working. The numbers are printed as JSON on stderr at the end, and written to
`$PCBRIDGE_PARITY_REPORT` when that is set.

Not covered here, because they need a person: locking the screen (unlocking
takes the user's password) and seeing the sharing indicator disappear.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageChops  # noqa: E402

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop import screencast as screencastlib  # noqa: E402
from pcbridge.desktop.backends.python import PythonCaptureProvider  # noqa: E402
from pcbridge.desktop.backends.rust import NativeScreenCast, RustCaptureProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.native.diagnostics import read_build_info  # noqa: E402


LIVE = os.environ.get("PCBRIDGE_TEST_CAPTURE") == "1"
PACKAGED = ROOT / "pcbridge" / "_native" / "x86_64-unknown-linux-gnu" / "pcbridge-native"
SYSTEM_PYTHON = "/usr/bin/python3"
VENV_PYTHON = str(ROOT / ".venv" / "bin" / "python")

# Shared with pattern_window.py; change both.
MARKERS = ((255, 0, 255), (0, 255, 255), (255, 255, 0), (255, 128, 0))
MARKER_BOX = (60, 60, 200, 200)
STRIP_ORIGIN = (60, 820)
CELL = 48
BITS = 16

WARM_CAPTURES = 30
STARTUPS = 5
PARITY_FLOOR = 99.5
P95_RATIO = 1.5


# ------------------------------------------------------------------ helpers
def release_binary() -> tuple[Path | None, str]:
    candidates = [Path(p) for p in (os.environ.get("PCBRIDGE_NATIVE_BIN", "").strip(),) if p]
    candidates.append(PACKAGED)
    for candidate in candidates:
        if not os.access(candidate, os.X_OK):
            continue
        info = read_build_info(candidate)
        if (
            isinstance(info, dict)
            and info.get("profile") == "release"
            and info.get("test_harness") is False
        ):
            return candidate, ""
    return None, "no release helper: run scripts/build-native.sh"


def screen_locked() -> bool:
    proc = subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
         "--object-path", "/org/gnome/ScreenSaver",
         "--method", "org.gnome.ScreenSaver.GetActive"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return "true" in proc.stdout


def screencast_sessions() -> int:
    proc = subprocess.run(
        ["gdbus", "introspect", "--session", "--dest", "org.gnome.Mutter.ScreenCast",
         "--object-path", "/org/gnome/Mutter/ScreenCast/Session"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return len(re.findall(r"^\s+node\s+\S+", proc.stdout, re.M))


def _argv(pid_dir: Path) -> list[str]:
    raw = (pid_dir / "cmdline").read_bytes()
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def _parent(pid_dir: Path) -> int:
    stat = (pid_dir / "stat").read_text()
    return int(stat.rsplit(")", 1)[1].split()[1])


def processes(match, *, parent: int | None = None) -> set[int]:
    """This user's live processes whose argv satisfies `match`."""
    found: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = _argv(entry)
            if not argv or not match(argv):
                continue
            if (entry / "stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                continue
            if parent is not None and _parent(entry) != parent:
                continue
        except (OSError, IndexError, ValueError):
            continue
        found.add(int(entry.name))
    return found - {os.getpid()}


def native_pids(binary: Path, **kwargs) -> set[int]:
    return processes(lambda argv: argv[0] == str(binary), **kwargs)


def legacy_helpers() -> set[int]:
    return processes(
        lambda argv: Path(argv[0]).name.startswith("python")
        and any(Path(part).name == "screencast_helper.py" for part in argv[1:3])
    )


def wait_until(predicate, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")


def agreement(left: Image.Image, right: Image.Image) -> float:
    if left.size != right.size:
        return 0.0
    red, green, blue = ImageChops.difference(left, right).split()
    worst = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return 100.0 * worst.histogram()[0] / (left.width * left.height)


def marker_color(image: Image.Image) -> tuple[int, int, int]:
    x, y, width, height = MARKER_BOX
    sx, sy = image.width / 1920, image.height / 1080
    return image.getpixel((int((x + width / 2) * sx), int((y + height / 2) * sy)))[:3]


def close_to(color, expected, tolerance: int = 12) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(color, expected))


def read_counter(image: Image.Image) -> int:
    sx, sy = image.width / 1920, image.height / 1080
    value = 0
    for bit in range(BITS):
        cx = int((STRIP_ORIGIN[0] + bit * CELL + CELL / 2) * sx)
        cy = int((STRIP_ORIGIN[1] + CELL / 2) * sy)
        red, green, blue = image.getpixel((cx, cy))[:3]
        value = (value << 1) | (1 if (red + green + blue) / 3 > 127 else 0)
    return value


def summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else float("nan")
    return {
        "n": len(ordered),
        "min": round(ordered[0], 1) if ordered else float("nan"),
        "p50": round(statistics.median(ordered), 1) if ordered else float("nan"),
        "p95": round(p95, 1),
        "max": round(ordered[-1], 1) if ordered else float("nan"),
    }


def scratch_config(root: Path, binary: Path, *, port: int | None = None,
                   token: str | None = None) -> Path:
    """The example config, pointed at scratch state and the Rust backend."""
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    replacements = [
        (r"^state_dir = .*$", f'state_dir = "{root / "state"}"'),
        (r'^capture = "python"$', 'capture = "rust"'),
        (r'^binary_path = ""$', f'binary_path = "{binary}"'),
        (r'^agent_shot_dir = ""$', f'agent_shot_dir = "{root / "agent-shots"}"'),
        (r"^include_pointer = true$", "include_pointer = false"),
    ]
    if port is not None:
        replacements.append((r"^port = 8765$", f"port = {port}"))
    if token is not None:
        replacements.append((r'^static_token = ""$', f'static_token = "{token}"'))
    for pattern, value in replacements:
        text, count = re.subn(pattern, lambda _match, v=value: v, text, flags=re.M)
        if count != 1:
            raise AssertionError(f"config.example.toml changed shape: {pattern}")
    header = text.index("\n[desktop]\n")
    enabled = re.compile(r"^enabled = false$", re.M).search(text, header)
    if enabled is None or "\n[" in text[header + 1 : enabled.start()]:
        raise AssertionError("config.example.toml: [desktop] enabled not found")
    text = text[: enabled.start()] + "enabled = true" + text[enabled.end():]
    path = root / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class LineReader:
    """Lines from a child's stdout, with a timeout."""

    def __init__(self, stream) -> None:
        self.lines: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=self._pump, args=(stream,), daemon=True).start()

    def _pump(self, stream) -> None:
        for line in stream:
            self.lines.put(line.rstrip("\n"))

    def expect(self, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no line starting with {prefix!r} in {timeout} s")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line


class PatternWindow:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [SYSTEM_PYTHON, str(HERE / "pattern_window.py"), "--timeout", "900"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self.reader = LineReader(self.process.stdout)
        ready = self.reader.expect("ready ", 30)
        self.connectors = ready.split(" ", 1)[1].split(",")
        time.sleep(0.5)  # let the compositor present the fullscreen windows

    def show(self, value: int) -> None:
        self.process.stdin.write(f"show {value}\n")
        self.process.stdin.flush()
        self.reader.expect(f"shown {value}", 10)

    def close(self) -> None:
        try:
            self.process.stdin.write("quit\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)


HOST_PROGRAM = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from pcbridge.config import load_config
    from pcbridge.desktop.safety import SafetyGate
    from pcbridge.desktop.backends.rust import RustCaptureProvider
    cfg = load_config({config!r})
    provider = RustCaptureProvider(cfg, gate=SafetyGate(cfg))
    provider.start(cursor=False)
    provider.capture(1, out_dir=Path({out!r}), scale_long_edge=0, include_pointer=False)
    print("captured", flush=True)
    time.sleep(120)
    """
)


# -------------------------------------------------------------------- tests
@unittest.skipUnless(
    LIVE, "set PCBRIDGE_TEST_CAPTURE=1: opens screen shares and covers the monitors"
)
class CaptureParityGate(unittest.TestCase):
    report: dict[str, Any] = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary, why = release_binary()
        if binary is None:
            raise unittest.SkipTest(why)
        if screen_locked():
            raise unittest.SkipTest("the screen is locked")
        if legacy_helpers():
            raise unittest.SkipTest("a Python screencast helper is running (a real grant)")
        cls.binary = binary
        cls.root = Path(tempfile.mkdtemp(prefix="pcb-parity-"))
        cls.pattern = None
        cls.legacy = None
        cls.native = None
        cls.report = {"binary": read_build_info(binary)}
        try:
            cls.config_path = scratch_config(cls.root, binary)
            cls.cfg = load_config(str(cls.config_path))
            cls.gate = SafetyGate(cls.cfg)
            cls.gate.unlock(30, reason="capture parity gate")
            cls.sessions_before = screencast_sessions()
            cls.natives_before = native_pids(binary)
            cls.pattern = PatternWindow()
            cls.monitors = monitorslib.list_monitors(use_cache=False)
            cls.report["monitors"] = [
                [m.index, m.connector, m.x, m.y, m.width, m.height] for m in cls.monitors
            ]
            cls.legacy = PythonCaptureProvider(cls.cfg, screencastlib.ScreenCast())
            cls.legacy.start(cursor=False)
            cls.native = RustCaptureProvider(cls.cfg, gate=cls.gate)
            cls.native.start(cursor=False)
        except BaseException:
            cls._cleanup(raise_problems=False)
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._cleanup(raise_problems=True)

    @classmethod
    def _cleanup(cls, *, raise_problems: bool) -> None:
        problems = []
        for provider in (cls.native, cls.legacy):
            if provider is not None:
                try:
                    provider.close()
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"close: {exc}")
        if cls.pattern is not None:
            cls.pattern.close()
        try:
            cls.gate.lock()
        except Exception:  # noqa: BLE001
            pass
        if hasattr(cls, "sessions_before"):
            if not wait_until(lambda: screencast_sessions() <= cls.sessions_before, 10):
                problems.append(
                    f"Mutter sessions {screencast_sessions()} > {cls.sessions_before}"
                )
            leftover = native_pids(cls.binary) - cls.natives_before
            if not wait_until(lambda: not (native_pids(cls.binary) - cls.natives_before), 10):
                problems.append(f"native helpers left: {sorted(leftover)}")
            if legacy_helpers():
                problems.append(f"legacy helpers left: {sorted(legacy_helpers())}")
        cls.report["cleanup_problems"] = problems
        rendered = json.dumps(cls.report, indent=2, ensure_ascii=False, default=str)
        print("\ncapture parity report:\n" + rendered, file=sys.stderr)
        target = os.environ.get("PCBRIDGE_PARITY_REPORT")
        if target:
            Path(target).write_text(rendered + "\n", encoding="utf-8")
        shutil.rmtree(cls.root, ignore_errors=True)
        if problems and raise_problems:
            raise AssertionError("; ".join(problems))

    # ---------------------------------------------------------------- utils
    def providers(self):
        return (("legacy", type(self).legacy), ("native", type(self).native))

    def capture(self, provider, spec, *, scale: int, pointer: bool, label: str):
        out = self.root / label
        return provider.capture(
            spec, out_dir=out, scale_long_edge=scale, include_pointer=pointer,
            reserved_dirs=[out],
        )

    @staticmethod
    def describe(shots) -> list[tuple]:
        rows = []
        for shot in shots:
            width, height = shot.scaled
            rows.append((
                shot.id.split("-")[0], shot.monitor.index, shot.monitor.connector,
                shot.offset, shot.size, shot.scaled, round(shot.scale, 6),
                shot.to_global(0, 0), shot.to_global(width // 2, height // 2),
                shot.to_global(width - 1, height - 1),
            ))
        return rows

    def fresh_native(self) -> RustCaptureProvider:
        provider = RustCaptureProvider(self.cfg, gate=self.gate)
        provider.start(cursor=False)
        return provider

    # ---------------------------------------------------------------- tests
    def test_01_the_pattern_is_on_every_monitor_for_both_backends(self) -> None:
        for name, provider in self.providers():
            shots = self.capture(provider, "all", scale=0, pointer=False, label=f"t01-{name}")
            self.assertEqual(len(shots), len(self.monitors))
            for shot in shots:
                observed = marker_color(rgb(shot.path))
                expected = MARKERS[(shot.monitor.index - 1) % len(MARKERS)]
                self.assertTrue(
                    close_to(observed, expected),
                    f"{name} {shot.monitor.connector}: marker {observed}, expected "
                    f"{expected} -- pattern missing, or a frame from the wrong monitor",
                )

    def test_02_identity_offset_size_and_mapping_match(self) -> None:
        specs = [1, 2, "all"] if len(self.monitors) > 1 else [1, "all"]
        for spec in specs:
            for scale in (1536, 0):
                legacy = self.describe(self.capture(
                    self.legacy, spec, scale=scale, pointer=False, label=f"t02-l-{spec}-{scale}"))
                native = self.describe(self.capture(
                    self.native, spec, scale=scale, pointer=False, label=f"t02-n-{spec}-{scale}"))
                self.assertEqual(legacy, native, f"spec={spec} scale={scale}")
        self.report["mapping_cases"] = [f"{spec}@{scale}" for spec in specs for scale in (1536, 0)]

    def test_03_static_pixels_agree_at_full_size_and_1536(self) -> None:
        results = {}
        for scale in (0, 1536):
            legacy = {s.monitor.connector: rgb(s.path) for s in self.capture(
                self.legacy, "all", scale=scale, pointer=False, label=f"t03-l-{scale}")}
            native = {s.monitor.connector: rgb(s.path) for s in self.capture(
                self.native, "all", scale=scale, pointer=False, label=f"t03-n-{scale}")}
            for connector, image in legacy.items():
                key = f"{connector}@{scale or 'full'}"
                results[key] = round(agreement(image, native[connector]), 3)
        self.report["pixel_parity_percent"] = results
        for key, value in results.items():
            self.assertGreaterEqual(value, PARITY_FLOOR, f"{key}: {value}%")

    def test_04_pointer_modes_keep_sizes_and_parity(self) -> None:
        with_pointer = {}
        for name, provider in self.providers():
            with_pointer[name] = {s.monitor.connector: s for s in self.capture(
                provider, "all", scale=0, pointer=True, label=f"t04-{name}-pointer")}
        plain = {s.monitor.connector: s for s in self.capture(
            self.native, "all", scale=0, pointer=False, label="t04-native-plain")}
        parity, cursor_effect = {}, {}
        for connector, legacy_shot in with_pointer["legacy"].items():
            native_shot = with_pointer["native"][connector]
            self.assertEqual(legacy_shot.size, native_shot.size)
            self.assertEqual(legacy_shot.scaled, native_shot.scaled)
            parity[connector] = round(
                agreement(rgb(legacy_shot.path), rgb(native_shot.path)), 3)
            cursor_effect[connector] = round(
                agreement(rgb(native_shot.path), rgb(plain[connector].path)), 3)
        self.report["pointer_parity_percent"] = parity
        self.report["native_pointer_vs_plain_percent"] = cursor_effect
        for connector, value in parity.items():
            self.assertGreaterEqual(value, PARITY_FLOOR, f"{connector} with pointer: {value}%")

    def test_05_every_native_frame_follows_its_request(self) -> None:
        results = {}
        for name, provider in self.providers():
            stale = []
            for value in range(1, 13):
                self.pattern.show(value + (100 if name == "native" else 0))
                expected = value + (100 if name == "native" else 0)
                time.sleep(0.15)
                spec = 1 if value % 2 or len(self.monitors) == 1 else 2
                shots = self.capture(provider, spec, scale=0, pointer=False,
                                     label=f"t05-{name}-{value}")
                seen = read_counter(rgb(shots[0].path))
                if seen != expected:
                    stale.append([expected, seen])
            results[name] = {"requests": 12, "stale": stale}
        self.report["freshness"] = results
        self.assertEqual(results["native"]["stale"], [], "a native frame older than its request")

    def test_06_warm_latency_p95_stays_within_1_5x_of_legacy(self) -> None:
        samples: dict[str, list[float]] = {"legacy": [], "native": []}
        errors = []
        for name, provider in self.providers():
            self.capture(provider, 1, scale=1536, pointer=False, label=f"t06-warmup-{name}")
        for iteration in range(WARM_CAPTURES):
            for name, provider in self.providers():
                started = time.perf_counter()
                try:
                    self.capture(provider, 1, scale=1536, pointer=False,
                                 label=f"t06-{name}-{iteration}")
                except Exception as exc:  # noqa: BLE001 - counted, not hidden
                    errors.append(f"{name}#{iteration}: {exc}")
                    continue
                samples[name].append(1000 * (time.perf_counter() - started))
        raw: dict[str, list[float]] = {"legacy": [], "native": []}
        connector = self.monitors[0].connector
        for iteration in range(WARM_CAPTURES):
            for name, provider in self.providers():
                path = self.root / f"t06-raw-{name}-{iteration}.png"
                started = time.perf_counter()
                try:
                    provider.screencast.capture(connector, path)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"raw {name}#{iteration}: {exc}")
                    continue
                raw[name].append(1000 * (time.perf_counter() - started))
        stats = {name: summarize(values) for name, values in samples.items()}
        self.report["warm_capture_ms"] = stats
        self.report["raw_frame_ms"] = {name: summarize(values) for name, values in raw.items()}
        self.report["capture_errors"] = errors
        self.report["load_average"] = Path("/proc/loadavg").read_text().split()[:3]
        self.assertEqual(errors, [])
        self.assertLessEqual(
            stats["native"]["p95"], P95_RATIO * stats["legacy"]["p95"],
            f"native p95 {stats['native']['p95']} ms > {P95_RATIO} x legacy "
            f"{stats['legacy']['p95']} ms",
        )

    def test_07_session_startups(self) -> None:
        connector = self.monitors[0].connector
        connectors = [monitor.connector for monitor in self.monitors]
        startups: dict[str, list[float]] = {"legacy": [], "native": []}
        errors = []
        for iteration in range(STARTUPS):
            for name in ("legacy", "native"):
                handle = (
                    screencastlib.ScreenCast() if name == "legacy"
                    else NativeScreenCast(self.cfg, gate=self.gate)
                )
                path = self.root / f"t07-{name}-{iteration}.png"
                started = time.perf_counter()
                try:
                    handle.start(connectors, cursor=False)
                    handle.capture(connector, path)
                    startups[name].append(1000 * (time.perf_counter() - started))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{name}#{iteration}: {exc}")
                finally:
                    handle.close()
        self.report["session_startup_ms"] = {
            name: summarize(values) for name, values in startups.items()
        }
        self.assertEqual(errors, [])

    def test_08_revoke_and_expiry_deliver_no_frame(self) -> None:
        outcomes = {}
        provider = self.fresh_native()
        try:
            self.capture(provider, 1, scale=0, pointer=False, label="t08-before")
            self.gate.lock()
            with self.assertRaises(DesktopError) as raised:
                self.capture(provider, 1, scale=0, pointer=False, label="t08-revoked")
            outcomes["revoked"] = [raised.exception.code.value, raised.exception.category.value]
            self.assertEqual(list((self.root / "t08-revoked").glob("*.png")), [])
        finally:
            provider.close()

        store = LeaseStore(self.cfg.state_dir)
        granted = time.time()
        store.grant(until=granted + 4.0, reason="expiry", granted=granted,
                    granted_by="capture-parity")
        provider = self.fresh_native()
        try:
            self.capture(provider, 1, scale=0, pointer=False, label="t08-expiring")
            time.sleep(max(0.0, granted + 4.0 - time.time()) + 1.0)
            with self.assertRaises(DesktopError) as raised:
                self.capture(provider, 1, scale=0, pointer=False, label="t08-expired")
            outcomes["expired"] = [raised.exception.code.value, raised.exception.category.value]
            self.assertEqual(list((self.root / "t08-expired").glob("*.png")), [])
        finally:
            provider.close()
        self.report["refusals"] = outcomes

        # The shared helper was bound to the revoked grant; give the remaining
        # tests a live grant and a helper bound to it.
        self.gate.unlock(30, reason="capture parity gate")
        type(self).native.close()
        type(self).native = self.fresh_native()
        for kind, (_code, category) in outcomes.items():
            self.assertEqual(category, ErrorCategory.SAFETY.value, f"{kind}: {outcomes[kind]}")

    def test_09_the_python_host_dying_releases_the_native_session(self) -> None:
        sessions_before = screencast_sessions()
        natives_before = native_pids(self.binary)
        out = self.root / "t09"
        host = subprocess.Popen(
            [VENV_PYTHON, "-c", HOST_PROGRAM.format(
                root=str(ROOT), config=str(self.config_path), out=str(out))],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            LineReader(host.stdout).expect("captured", 60)
            spawned = native_pids(self.binary, parent=host.pid)
            self.assertTrue(spawned, "the host should have started a helper")
            self.assertGreater(screencast_sessions(), sessions_before)
            killed = time.monotonic()
            host.kill()
            host.wait(10)
            gone = wait_until(lambda: not (native_pids(self.binary) & spawned), 10)
            gone_after = time.monotonic() - killed
            closed = wait_until(lambda: screencast_sessions() <= sessions_before, 10)
            closed_after = time.monotonic() - killed
        finally:
            if host.poll() is None:
                host.kill()
                host.wait(5)
            host.stdout.close()
        self.report["host_exit"] = {
            "helper_exited": gone, "helper_exit_s": round(gone_after, 2),
            "session_closed": closed, "session_close_s": round(closed_after, 2),
        }
        self.assertTrue(gone, "the native helper outlived its Python host")
        self.assertTrue(closed, "the Mutter session outlived the Python host")
        self.assertEqual(native_pids(self.binary) - natives_before, set())

    def test_10_a_crashed_native_helper_is_reported_and_replaced(self) -> None:
        provider = self.fresh_native()
        try:
            self.capture(provider, 1, scale=0, pointer=False, label="t10-before")
            pid = provider.screencast._client.pid
            self.assertIsNotNone(pid)
            sessions_before = screencast_sessions()
            os.kill(pid, signal.SIGKILL)
            died = wait_until(lambda: pid not in native_pids(self.binary), 5)
            released = wait_until(lambda: screencast_sessions() < sessions_before, 10)
            errors, recovered = [], False
            for attempt in range(3):
                try:
                    self.capture(provider, 1, scale=0, pointer=False, label=f"t10-after-{attempt}")
                    recovered = True
                    break
                except DesktopError as exc:
                    errors.append(exc.code.value)
            replacement = provider.screencast._client.pid
        finally:
            provider.close()
        self.report["native_crash"] = {
            "died": died, "session_released": released,
            "errors_before_recovery": errors, "recovered": recovered,
            "replaced_process": replacement != pid,
        }
        self.assertTrue(died)
        self.assertTrue(released, "the crashed helper's Mutter session was not released")
        self.assertTrue(recovered, errors)
        self.assertTrue(all(code == "NATIVE_CRASHED" for code in errors), errors)
        self.assertNotEqual(replacement, pid)

    def test_11_an_http_client_decodes_frames_and_shell_survives_a_crash(self) -> None:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        root = self.root / "http"
        root.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        token = secrets.token_urlsafe(24)
        config = scratch_config(root, self.binary, port=port, token=token)
        shim = root / "no-screenshot"
        shim.mkdir()
        (shim / "gnome-screenshot").write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        (shim / "gnome-screenshot").chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
        env.pop("PCBRIDGE_NATIVE_BIN", None)
        log = (root / "server.log").open("w")
        server = subprocess.Popen(
            [VENV_PYTHON, "-m", "pcbridge.server", "-c", str(config)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )

        def healthy() -> bool:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                    return r.status == 200
            except OSError:
                return False

        async def scenario():
            transport = StreamableHttpTransport(
                f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": f"Bearer {token}"},
            )
            async with Client(transport) as client:
                unlock = await client.call_tool(
                    "desktop_unlock", {"minutes": 2, "reason": "http parity"},
                    raise_on_error=False)
                first = await client.call_tool(
                    "screen_capture", {"monitor": "all"}, raise_on_error=False)
                killed = native_pids(self.binary, parent=server.pid)
                for pid in killed:
                    os.kill(pid, signal.SIGKILL)
                wait_until(lambda: not (native_pids(self.binary) & killed), 5)
                shell = await client.call_tool(
                    "shell_run", {"command": "echo native-down-ok"}, raise_on_error=False)
                jobs = await client.call_tool("job_list", {}, raise_on_error=False)
                second = await client.call_tool(
                    "screen_capture", {"monitor": "all"}, raise_on_error=False)
                third = await client.call_tool(
                    "screen_capture", {"monitor": "all"}, raise_on_error=False)
                lock = await client.call_tool("desktop_lock", raise_on_error=False)
                return unlock, first, killed, shell, jobs, second, third, lock

        try:
            self.assertTrue(wait_until(healthy, 30), (root / "server.log").read_text())
            unlock, first, killed, shell, jobs, second, third, lock = asyncio.run(scenario())
        finally:
            server.terminate()
            try:
                server.wait(15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(5)
            log.close()

        def text(result) -> str:
            return "\n".join(b.text for b in result.content if b.type == "text")

        def check_images(result) -> list[str]:
            self.assertFalse(result.is_error, text(result))
            ids = re.findall(r"shot: `(m\d+)-[0-9a-f]{6}`", text(result))
            images = [b for b in result.content if b.type == "image"]
            self.assertEqual(len(images), len(ids))
            for shot_id, block in zip(ids, images):
                image = Image.open(io.BytesIO(base64.b64decode(block.data))).convert("RGB")
                index = int(shot_id[1:])
                self.assertTrue(
                    close_to(marker_color(image), MARKERS[(index - 1) % len(MARKERS)]),
                    f"{shot_id} over HTTP shows {marker_color(image)}",
                )
            return ids

        self.assertIn("Ekran yayını açık", text(unlock), text(unlock))
        first_ids = check_images(first)
        self.assertEqual(len(first_ids), len(self.monitors))
        self.assertTrue(killed, "the server should have a native helper to crash")
        self.assertFalse(shell.is_error, text(shell))
        self.assertIn("native-down-ok", text(shell))
        self.assertFalse(jobs.is_error, text(jobs))
        after_crash = []
        for result in (second, third):
            if result.is_error:
                after_crash.append(result.structured_content["error"]["code"])
            else:
                check_images(result)
                after_crash.append("delivered")
        self.report["http"] = {
            "first_capture_images": len(first_ids),
            "helpers_crashed": len(killed),
            "shell_during_crash": "ok",
            "captures_after_crash": after_crash,
            "lock_error": lock.is_error,
        }
        self.assertIn("delivered", after_crash, "the server never recovered capture")
        for outcome in after_crash:
            self.assertIn(outcome, {"delivered", "NATIVE_CRASHED"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
