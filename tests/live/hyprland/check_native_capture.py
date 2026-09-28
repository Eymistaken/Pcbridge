"""Measure grant-bound native image-copy frames in the dedicated Hyprland VM."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.config import DesktopSpec, NativeSpec  # noqa: E402
from pcbridge.desktop import glowstate, hyprland, idlewatch, monitors  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.native import NativeClient  # noqa: E402
from pcbridge.native.client import helper_environment  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, monitor_rule, wait_for  # noqa: E402
from tests.live.test_capture_parity import LineReader, MARKERS, close_to, marker_color, read_counter  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("PCBRIDGE_TEST_HYPRLAND_NATIVE_CAPTURE") == "1"
    assert os.uname().nodename == "pcbridge-hyprland"
    assert hyprland.screen_locked() is False
    assert not layers() and idlewatch.read_idle_ms() is None, "Refuse to overlap resident desktop resources"
    original = hyprland.monitors()
    binary = (ROOT / "rust/target/debug/pcbridge-native").resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pattern = idle = client = gate = None
    with tempfile.TemporaryDirectory(prefix="pcbridge-native-capture-") as temporary, tempfile.TemporaryFile() as errors:
        directory = Path(temporary).resolve()
        cfg = SimpleNamespace(state_dir=directory, audit_log=directory / "audit.log",
            desktop=DesktopSpec(enabled=True, unlock_idle_seconds=20), native=NativeSpec(binary_path=binary))
        evidence = []
        try:
            idle = subprocess.Popen([str(binary), "idle-watch"], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=errors, env=helper_environment())
            wait_for(lambda: idlewatch.read_idle_ms() is not None, description="fresh native idle")
            pattern = subprocess.Popen(["/usr/bin/python3", str(ROOT / "tests/live/pattern_window.py"), "--timeout", "120"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True)
            reader = LineReader(pattern.stdout)
            connectors = reader.expect("ready ", 30).split(" ", 1)[1].split(",")
            assert len(connectors) == 2
            gate = SafetyGate(cfg)
            gate.unlock(1, "Native screenshot acceptance in the disposable VM")
            token = gate.last_token()
            runtime = directory / "native-runtime"
            runtime.mkdir(mode=0o700)
            client = NativeClient(binary, state_dir=directory, runtime_dir=runtime)

            def capture(connector, counter, label):
                admission = gate.check("screen_capture", write=False)
                assert admission.allowed, admission
                snapshot = client.request("display.snapshot").result
                assert gate.verify(token).allowed
                params = {"grant_id": token.grant_id, "revoke_epoch": token.revoke_epoch,
                    "session_id": "hyprland-native-capture-probe", "topology_id": snapshot["topology_id"],
                    "display_id": "hyprland:" + connector, "include_pointer": False,
                    "freshness": "after_request", "timeout_ms": 8000}
                started = time.monotonic()
                response = client.request("capture.frame", params=params)
                assert gate.verify(token).allowed
                with Image.open(io.BytesIO(response.binary)) as raw:
                    image = raw.convert("RGB")
                assert response.result["backend"] == "linux.hyprland.image-copy"
                table = monitors.list_monitors(use_cache=False)
                monitor = next(row for row in table if row.connector == connector)
                assert image.size == monitor.source_pixel_size, (image.size, monitor.source_pixel_size)
                assert response.result["desktop_rect"] == [monitor.x, monitor.y, monitor.width, monitor.height]
                assert snapshot["topology_id"] == monitors.topology_id(table), "The native request reused old topology"
                assert read_counter(image) == counter, (connector, read_counter(image), counter)
                marker = marker_color(image)
                index = connectors.index(connector)
                assert close_to(marker, MARKERS[index]), (connector, marker, MARKERS[index])
                image.save(args.out_dir / f"{label}-{connector}-{counter}.png")
                evidence.append({"output": connector, "counter": counter, "marker": marker,
                    "pixels": image.size, "wait_ms": response.result["wait_ms"],
                    "total_ms": round((time.monotonic() - started) * 1000), "label": label})

            for counter in (631, 632):
                pattern.stdin.write(f"show {counter}\n")
                pattern.stdin.flush()
                reader.expect(f"shown {counter}", 10)
                # Window draw acknowledgments precede fullscreen animations.
                time.sleep(0.7)
                for connector in connectors:
                    capture(connector, counter, "normal")
            monitor_rule(original[1], scale=1.25, transform=1)
            wait_for(lambda: glowstate.read_on_current_outputs(directory, token, binary=binary),
                     description="fresh rotated fractional frame")
            pattern.stdin.write("show 633\n")
            pattern.stdin.flush()
            reader.expect("shown 633", 10)
            time.sleep(0.7)
            capture(original[1]["name"], 633, "rotated-fractional")
            gate.lock()
            try:
                client.request("capture.frame", params={"grant_id": token.grant_id,
                    "revoke_epoch": token.revoke_epoch, "session_id": "hyprland-native-capture-probe",
                    "topology_id": "v1|revoked", "display_id": "hyprland:" + connectors[0],
                    "include_pointer": False, "freshness": "after_request", "timeout_ms": 8000})
            except DesktopError as error:
                assert error.code == ErrorCode.REVOKED, error.to_dict()
            else:
                raise AssertionError("Native pixels escaped after explicit desktop_lock")
            print(json.dumps({"native_image_copy": evidence, "after_revoke": "refused", "input_sent": False}, sort_keys=True))
        finally:
            if gate:
                gate.lock()
                gate.close()
            if client:
                client.close()
            if pattern and pattern.poll() is None:
                pattern.stdin.write("quit\n")
                pattern.stdin.flush()
                pattern.wait(timeout=5)
            if idle and idle.poll() is None:
                idle.terminate()
                idle.wait(timeout=3)
            for row in original:
                monitor_rule(row)
            errors.seek(0)
            diagnostic = errors.read(8192).decode(errors="replace")
            if diagnostic:
                print(diagnostic, file=sys.stderr)


if __name__ == "__main__":
    main()
