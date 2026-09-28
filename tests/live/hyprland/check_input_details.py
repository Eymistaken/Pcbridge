"""Bounded, machine-observed native input details in the disposable VM only."""
from __future__ import annotations

import os

# These refusals precede every PcBridge, GUI, state, and device import/action.
if not __debug__:
    raise RuntimeError("Input details require assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Input details run only on the disposable pcbridge-hyprland VM")
if os.environ.get("PCBRIDGE_NATIVE_BIN"):
    raise RuntimeError("Native helper overrides are forbidden")

import dataclasses
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.config import load_config  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from pcbridge.desktop import hyprland, idlewatch, monitors  # noqa: E402
from pcbridge.desktop.backends.rust import RustInputProvider  # noqa: E402
from pcbridge.desktop.runtime import create_runtime  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402
from tests.live.test_input_parity import InputWindow, near  # noqa: E402


def main():
    assert hyprland.screen_locked() is False
    assert not layers() and idlewatch.read_idle_ms() is None, "Existing grant/idle writer"
    assert os.access("/dev/uinput", os.R_OK | os.W_OK)
    base = load_config(ROOT / "config.example.toml", check_state=False)
    assert base.native.binary_path is None
    binary = discover_native_binary(base.native)
    assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
    info = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
                                   capture_output=True, text=True, timeout=5).stdout)
    assert info["profile"] == "release" and info["test_harness"] is False, info
    idle = window = gate = runtime = pointer = None
    evidence = {"helper": str(binary), "build": info, "native": {}, "diagnostic_ipc": []}
    with tempfile.TemporaryDirectory(prefix="pcbridge-input-details-") as temporary:
        directory = Path(temporary)
        cfg = dataclasses.replace(base, state_dir=directory,
            desktop=dataclasses.replace(base.desktop, enabled=True, unlock_idle_seconds=0,
                unlock_notification=False, hold_max_seconds=5),
            native=dataclasses.replace(base.native, input="rust"))
        try:
            window = InputWindow(directory / "observer.stderr", timeout=120, details=True)
            ready = window.wait(lambda e: e.get("event") == "ready", 20)
            table = monitors.list_monitors(use_cache=False)
            assert ready and len(table) == 2
            assert ready[1]["monitors"] == [[m.x, m.y, m.width, m.height] for m in table]
            window.settle()

            def focus():
                assert hyprland.screen_locked() is False
                assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
                clients = [c for c in hyprland._query("clients", json_output=True)
                           if c.get("pid") == window.process.pid and c.get("mapped")]
                assert len(clients) == 2 and all(c.get("visible") for c in clients)
                for monitor in table:
                    assert any(c["at"] == [monitor.x, monitor.y]
                               and c["size"] == [monitor.width, monitor.height] for c in clients)

            focus()
            idle = subprocess.Popen([str(binary), "idle-watch"], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            wait_for(lambda: idlewatch.read_idle_ms() is not None, description="fresh idle proof")
            gate = SafetyGate(cfg)
            gate.unlock(5, reason="Disposable VM native input details acceptance")
            pointer = RustInputProvider(cfg, gate=gate)
            runtime = create_runtime(cfg, gate=gate, input_provider=pointer)
            decision = gate.check("hyprland_input_details", write=True, force=True)
            assert decision.allowed, decision.reason
            with runtime.write_sequence("hyprland_input_details") as guard:
                def action(name, callback):
                    focus()
                    guard(name)
                    return callback()

                def receive(kind, mark, point=None, predicate=lambda e: True, timeout=3):
                    result = window.wait(lambda e: e.get("event") == kind
                        and (point is None or near(e, point)) and predicate(e), timeout, mark)
                    assert result, {"missing": kind, "point": point, "events": window.since(mark)}
                    return result

                def move(point):
                    mark = window.mark()
                    action("move", lambda: pointer.move(*point))
                    receive("motion", mark, point)
                    window.settle()
                    assert near(window.last("motion"), point)

                action("ensure", lambda: pointer.ensure(keyboard=True, pointer=True))
                client = pointer._helper.client
                assert client and client.handshake and client.handshake.build_id == info["build_id"]
                evidence["handshake_build_id"] = client.handshake.build_id
                m = table[0]
                start = (m.x + m.width // 3, m.y + m.height * 2 // 3)
                other = (start[0] + 80, start[1] - 60)
                move(other)
                move(start)
                for button, number in (("right", 3), ("middle", 2)):
                    mark = window.mark()
                    action("click", lambda: pointer.click(button))
                    press = receive("press", mark, start, lambda e: e["button"] == number)
                    release = receive("release", mark, start, lambda e: e["button"] == number)
                    evidence["native"][button] = [press[1], release[1]]
                for count in (2, 3):
                    time.sleep(0.7)  # Separate GTK multi-click sequences.
                    mark = window.mark()
                    action("click", lambda: pointer.click("left", count=count))
                    counted = receive("click_count", mark, start,
                                      lambda e: e["button"] == 1 and e["n_press"] == count)
                    evidence["native"][f"click_{count}"] = counted[1]
                deltas = []
                for amount in (2, -2):
                    mark = window.mark()
                    action("scroll", lambda: pointer.scroll(amount, horizontal=True))
                    receive("scroll", mark, predicate=lambda e: e["dx"] != 0)
                    window.settle()
                    scrolls = [e for _, e in window.since(mark) if e.get("event") == "scroll"]
                    assert scrolls and all(e["dy"] == 0 for e in scrolls)
                    deltas.append({"sent": amount, "dx": sum(e["dx"] for e in scrolls),
                                   "events": scrolls})
                assert deltas[0]["dx"] * deltas[1]["dx"] < 0
                evidence["native"]["horizontal_scroll"] = deltas
                end = (m.x + m.width * 2 // 3, start[1])
                mark = window.mark()
                action("drag", lambda: pointer.drag(*start, *end))
                receive("press", mark, start)
                receive("release", mark, end)
                updates = [e for _, e in window.since(mark) if e.get("event") == "drag_update"
                           and start[0] < e["x"] < end[0] and abs(e["y"] - start[1]) <= 1
                           and 1 in e["buttons"]]
                assert updates, window.since(mark)
                evidence["native"]["drag"] = {"start": start, "end": end, "intermediate": updates}
                for device, down, up, press_kind, release_kind, predicate in (
                    ("key", lambda: pointer.key_down("shift"), lambda: pointer.key_up("shift"),
                     "key_press", "key_release", lambda e: e["keyval"].startswith("Shift")),
                    ("button", lambda: pointer.mouse_down("left"), lambda: pointer.mouse_up("left"),
                     "press", "release", lambda e: e["button"] == 1)):
                    mark = window.mark()
                    action("hold", down)
                    pressed = receive(press_kind, mark, predicate=predicate)
                    action("release", up)
                    released = receive(release_kind, mark, predicate=predicate)
                    assert action("held", pointer.held) == []
                    evidence["native"][f"manual_{device}"] = [pressed[1], released[1]]
                    mark = window.mark()
                    action("hold", down)
                    pressed = receive(press_kind, mark, predicate=predicate)
                    # No native requests until the observer sees watchdog release.
                    released = receive(release_kind, mark, predicate=predicate, timeout=7)
                    elapsed = released[0] - pressed[0]
                    assert 4.5 <= elapsed < 6.5, elapsed
                    assert action("held", pointer.held) == []
                    names = action("auto_released", pointer.take_auto_released)
                    assert len(names) == 1 and (names == ["shift"] if device == "key" else names == ["left"]), names
                    assert action("auto_released", pointer.take_auto_released) == []
                    evidence["native"][f"timeout_{device}"] = {"seconds": elapsed,
                        "names": names, "press": pressed[1], "release": released[1]}
                instance, mode = hyprland.focus_context()
                assert mode == "lua", "Diagnostic requires the observed current Lua provider"
                for monitor in table:
                    point = (monitor.x + monitor.width // 2, monitor.y + monitor.height * 3 // 4)
                    distinct = (point[0] + 60, point[1] - 40)
                    move(distinct)
                    mark = window.mark()
                    command = f"hl.dsp.cursor.move({{x={point[0]},y={point[1]}}})"
                    dispatched = time.monotonic()
                    ack = action("diagnostic_cursor", lambda: subprocess.run(
                        ["hyprctl", "-i", instance, "dispatch", command],
                        capture_output=True, text=True, timeout=3))
                    assert ack.returncode == 0 and ack.stdout.strip() == "ok"
                    cursor_result = action("diagnostic_cursorpos", lambda: subprocess.run(
                        ["hyprctl", "-i", instance, "-j", "cursorpos"],
                        check=True, capture_output=True, text=True, timeout=3))
                    cursor = json.loads(cursor_result.stdout)
                    assert near(cursor, point)
                    # IPC warp observation is diagnostic: GTK may deliver no motion here.
                    # Native recovery below still requires fresh application events.
                    observed = window.wait(lambda e: e.get("event") == "motion" and near(e, point),
                                           1, mark)
                    # Use a distinct native recovery point before verifying the target
                    # after the external warp.
                    recovery = (point[0] - 60, point[1] + 40)
                    move(recovery)
                    recovered = window.last("motion")
                    move(point)
                    evidence["diagnostic_ipc"].append({"output": monitor.connector, "target": point,
                        "ack": ack.stdout.strip(), "cursor": cursor,
                        "observer": observed[1] if observed else None,
                        "observer_latency_seconds": observed[0] - dispatched if observed else None,
                        "observer_timeout_seconds": 1,
                        "observer_timed_out": observed is None,
                        "native_recovery": recovered, "native_observer": window.last("motion")})
            print(json.dumps(evidence, sort_keys=True))
        except Exception:
            # GTK swallows signal exceptions; preserve bounded test-only diagnostics.
            print(json.dumps({"partial_evidence": evidence}, sort_keys=True), file=sys.stderr)
            raise
        finally:
            failed = sys.exc_info()[0] is not None
            # Run every cleanup even when a preceding cleanup fails.
            cleanups = []
            if pointer:
                cleanups.append(pointer.release_all)  # Emergency release bypasses admission.
            if gate:
                cleanups.append(gate.lock)
            if runtime:
                cleanups.append(runtime.close)
            if idle:
                def stop_idle():
                    if idle.poll() is None:
                        idle.terminate()
                    idle.wait(timeout=5)
                cleanups.append(stop_idle)
            if window:
                cleanups.append(window.close)
            cleanups.append(lambda: wait_for(lambda: not layers(),
                                            description="final input-details frame teardown"))
            errors = []
            for cleanup in cleanups:
                try:
                    cleanup()
                except Exception as error:
                    errors.append(type(error).__name__)
            if (failed or errors) and window:
                stderr = window.errors.read_text(encoding="utf-8", errors="replace")
                print(json.dumps({"observer_stderr_tail": stderr[-8000:]}, sort_keys=True),
                      file=sys.stderr)
            if errors:
                raise RuntimeError(f"Input details cleanup failed: {errors}")


if __name__ == "__main__":
    main()
