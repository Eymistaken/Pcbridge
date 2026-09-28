"""Real uinput and all-edge glow transparency in the disposable Hyprland VM.

Each event must reach fullscreen observer windows. All production actions use
the normal native provider, shared gate, and cross-process execution lock.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import glowstate, hyprland, idlewatch, monitors  # noqa: E402
from pcbridge.desktop.backends.rust import RustCaptureProvider, RustInputProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.hyprland_windows import HyprlandWindowProvider  # noqa: E402
from pcbridge.desktop.runtime import create_runtime  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402
from tests.live.test_input_parity import InputWindow, near  # noqa: E402


def main():
    if not __debug__:
        raise RuntimeError("Real input verification requires Python assertions; do not use -O")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("PCBRIDGE_TEST_HYPRLAND_INPUT") == "1"
    assert os.uname().nodename == "pcbridge-hyprland", "Use only the disposable VM"
    assert hyprland.screen_locked() is False
    assert not layers() and idlewatch.read_idle_ms() is None, "Do not overlap another resident grant"
    assert os.access("/dev/uinput", os.R_OK | os.W_OK), "Install the existing pcbridge udev rule in the VM"
    binary = (ROOT / "rust/target/debug/pcbridge-native").resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    idle = window = gate = runtime = None
    with tempfile.TemporaryDirectory(prefix="pcbridge-hyprland-input-") as temporary:
        directory = Path(temporary)
        base = load_config(ROOT / "config.example.toml")
        cfg = dataclasses.replace(base, state_dir=directory,
            desktop=dataclasses.replace(base.desktop, enabled=True, unlock_idle_seconds=0,
                                        unlock_notification=False, hold_max_seconds=5),
            native=dataclasses.replace(base.native, input="rust", capture="rust", binary_path=binary))
        try:
            window = InputWindow(directory / "window.stderr", timeout=180)
            ready = window.wait(lambda event: event.get("event") == "ready", 20)
            assert ready, "Both input observers must have painted before any input"
            table = monitors.list_monitors(use_cache=False)
            assert ready[1]["monitors"] == [[m.x, m.y, m.width, m.height] for m in table]
            assert len(table) == 2
            window.settle()
            original_focus = hyprland._query("activewindow", json_output=True)
            assert original_focus["pid"] == window.process.pid
            clients = [item for item in hyprland._query("clients", json_output=True)
                       if item["pid"] == window.process.pid and item["mapped"]]
            assert len(clients) == 2 and all(item["visible"] for item in clients)
            original_geometry = {item["stableId"]: (item["at"], item["size"]) for item in clients}
            original_reserved = {item["name"]: item["reserved"] for item in hyprland.monitors()}
            idle = subprocess.Popen([str(binary), "idle-watch"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: idlewatch.read_idle_ms() is not None, description="fresh idle proof")
            gate = SafetyGate(cfg)
            gate.unlock(5, reason="Isolated VM uinput and glow transparency acceptance")
            pointer = RustInputProvider(cfg, gate=gate)
            capture = RustCaptureProvider(cfg, gate=gate)
            runtime = create_runtime(cfg, gate=gate, input_provider=pointer, capture_provider=capture)
            token = gate.current_token()
            assert glowstate.read(directory, token, binary=binary)["strip_count"] == 8
            assert hyprland._query("activewindow", json_output=True)["stableId"] == original_focus["stableId"]
            assert {item["name"]: item["reserved"] for item in hyprland.monitors()} == original_reserved
            assert gate.check("screen_capture", write=False).allowed
            shots = capture.capture("all", out_dir=args.out_dir, scale_long_edge=0, include_pointer=False)
            assert len(shots) == len(table)
            for shot in shots:
                with Image.open(shot.path) as image:
                    assert image.size == shot.size
                    assert len(image.convert("RGB").getcolors(image.width * image.height)) > 40
            decision = gate.check("hyprland_input_probe", write=True, force=True)
            assert decision.allowed, decision.reason
            evidence = {"edges": [], "screenshots": len(shots), "input_backend": "linux.uinput.native"}
            with runtime.write_sequence("hyprland_input_probe") as guard:
                guard("ensure")
                pointer.ensure(keyboard=True, pointer=True, relative=True)

                def received(kind, mark, point=None):
                    result = window.wait(lambda event: event.get("event") == kind
                        and (point is None or near(event, point)), 3, mark)
                    assert result, {"missing": kind, "point": point,
                                    "last_motion": window.last("motion"), "events": window.since(mark),
                                    "cursor": subprocess.run(["hyprctl", "cursorpos"],
                                        capture_output=True, text=True, timeout=3).stdout.strip()}
                    return result

                def move(point):
                    previous = window.last("motion")
                    mark = window.mark()
                    guard("move")
                    pointer.move(*point)
                    if not (previous and near(previous, point)):
                        received("motion", mark, point)
                    window.settle()
                    observed = window.last("motion")
                    assert observed and near(observed, point), (point, observed)
                    assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
                    return observed

                # Moving to the existing cursor position need not produce a
                # GTK motion event. Establish an observed position with two
                # distinct safe points before testing screenshot coordinates.
                guard("move")
                pointer.move(table[0].x + 3, table[0].y + table[0].height // 2)
                move((table[0].x + 43, table[0].y + table[0].height // 2))
                evidence["shot_mapping"] = []
                for shot in shots:
                    point = capture.to_global(shot.size[0] // 2, shot.size[1] // 2,
                                              shot=shot.id, dirs=[args.out_dir])
                    move(point)
                    mark = window.mark()
                    guard("click")
                    pointer.click("left")
                    received("release", mark, point)
                    evidence["shot_mapping"].append({"shot": shot.id, "global": point,
                                                     "observed": "within_one_pixel"})

                for monitor in table:
                    x, y, width, height = monitor.x, monitor.y, monitor.width, monitor.height
                    edges = [("left", (x + 3, y + height // 2), (x + 3, y + height // 2 + 40)),
                             ("right", (x + width - 4, y + height // 2), (x + width - 4, y + height // 2 + 40)),
                             ("top", (x + width // 2, y + 3), (x + width // 2 + 40, y + 3)),
                             ("bottom", (x + width // 2, y + height - 4), (x + width // 2 + 40, y + height - 4))]
                    for edge, point, endpoint in edges:
                        move(point)
                        mark = window.mark()
                        guard("click")
                        pointer.click("left")
                        press = received("press", mark, point)
                        release = received("release", mark, point)
                        assert press[1]["button"] == release[1]["button"] == 1
                        mark = window.mark()
                        guard("scroll")
                        pointer.scroll(-1)
                        scroll = received("scroll", mark)
                        assert scroll[1]["dy"] > 0
                        mark = window.mark()
                        guard("drag")
                        pointer.drag(*point, *endpoint)
                        received("press", mark, point)
                        received("release", mark, endpoint)
                        evidence["edges"].append({"output": monitor.connector, "edge": edge,
                            "click": "received", "scroll": "received", "drag_end": endpoint})

                park = (table[0].width // 2, table[0].height // 2)
                before = move(park)
                mark = window.mark()
                guard("move_by")
                assert pointer.move_by(100, 0) == (100, 0)
                received("motion", mark)
                window.settle()
                after = window.last("motion")
                assert after["x"] > before["x"] and after["y"] == before["y"]
                assert pointer.position is None
                move(park)
                evidence["relative"] = {"sent": [100, 0], "observed_delta": after["x"] - before["x"],
                                        "absolute_recovery": "within_one_pixel"}

                x, y, width, height = ready[1]["field"]
                center = (x + min(width, table[-1].width - 100) // 2, y + height // 2)
                move(center)
                mark = window.mark()
                guard("click")
                pointer.click("left")
                received("release", mark, center)
                assert window.wait(lambda event: event.get("event") == "focus" and event["field"], 3)
                assert window.last("active")["active"] and window.last("focus")["field"]
                original_clipboard = pointer.clipboard.save()
                try:
                    guard("clipboard")
                    pointer.clipboard.put_text("PcBridge clipboard sentinel")
                    mark = window.mark()
                    guard("type")
                    text = "PcBridge Hyprland Türkçe 42"
                    pointer.type_text(text)
                    assert window.wait(lambda event: event.get("event") == "text" and event["value"] == text, 5, mark)
                    assert pointer.clipboard.save().data == b"PcBridge clipboard sentinel"
                    evidence["clipboard_typing"] = "exact_text_and_restored_clipboard"
                finally:
                    pointer.clipboard.restore(original_clipboard)
                mark = window.mark()
                guard("key")
                pointer.key("shift+a")
                assert window.wait(lambda event: event.get("event") == "key_press"
                    and event["keyval"] == "A" and event["shift"], 3, mark)
                evidence["keyboard"] = "shift_modifier_received"
                # Hyprland's stock autogenerated-config warning moves its
                # reserved area with monitor focus. Compare the same focus
                # before grant, during glow, and after teardown.
                guard("focus")
                HyprlandWindowProvider().activate(f"stableid:{original_focus['stableId']}",
                                                 checkpoint=runtime.compositor_checkpoint)
                window.settle()
                assert {item["name"]: item["reserved"] for item in hyprland.monitors()} == original_reserved
                assert {item["stableId"]: (item["at"], item["size"])
                        for item in hyprland._query("clients", json_output=True)
                        if item["pid"] == window.process.pid} == original_geometry
                mark = window.mark()
                guard("hold")
                pointer.key_down("shift")
                assert window.wait(lambda event: event.get("event") == "key_press"
                    and event["keyval"].startswith("Shift"), 3, mark)
                released = time.monotonic()
                gate.lock()
                up = window.wait(lambda event: event.get("event") == "key_release"
                    and event["keyval"].startswith("Shift"), 1, mark)
                assert up and up[0] - released <= 0.25, "Held Shift did not release within 250 ms"
                evidence["revoke_key_release_ms"] = round((up[0] - released) * 1000)
                try:
                    pointer.key("a")
                except DesktopError as error:
                    assert error.code in (ErrorCode.REVOKED, ErrorCode.GRANT_REQUIRED)
                    evidence["after_revoke"] = error.code.value
                else:
                    raise AssertionError("Native input accepted a revoked grant")
            current = {item["stableId"]: (item["at"], item["size"])
                       for item in hyprland._query("clients", json_output=True) if item["pid"] == window.process.pid}
            assert current == original_geometry
            current_reserved = {item["name"]: item["reserved"] for item in hyprland.monitors()}
            assert current_reserved == original_reserved, {"before": original_reserved, "after": current_reserved}
            wait_for(lambda: not layers(), description="locked frame teardown")
            assert hyprland._query("activewindow", json_output=True)["stableId"] == original_focus["stableId"]
            evidence["geometry_and_reserved_space"] = "unchanged"
            print(json.dumps(evidence, sort_keys=True))
        finally:
            if gate:
                gate.lock()
            if runtime:
                runtime.close()
            if idle and idle.poll() is None:
                idle.terminate()
                idle.wait(timeout=5)
            if window:
                window.close()
            wait_for(lambda: not layers(), description="final frame teardown")


if __name__ == "__main__":
    main()
