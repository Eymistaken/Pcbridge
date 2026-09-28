"""Validate real Hyprland doctor observations without sending input."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.cli.doctor import Doctor  # noqa: E402
from pcbridge.cli.hyprland import setup_notes  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import hyprland, idlewatch  # noqa: E402
from pcbridge.desktop.errors import ErrorCode  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402


def inspect(cfg):
    doctor = Doctor()
    doctor.cfg = cfg
    doctor.desktop()
    checks = {item.name: item for item in doctor.checks}
    assert not {"GNOME Shell", "GNOME extension", "KDE Plasma", "KWin screenshots"} & checks.keys()
    for name in ("Hyprland IPC", "Hyprland native helper", "Hyprland capture protocol", "Hyprland lock state", "monitors"):
        assert checks[name].status == "ok", dataclasses.asdict(checks[name])
    assert "2560x800" in checks["monitors"].detail
    assert "no capture started" in checks["Hyprland capture protocol"].detail
    assert all("independent of native image-copy capture" in item.detail
               for item in checks.values() if item.group == "desktop integration")
    return checks


def main():
    if not __debug__:
        raise RuntimeError("Live verification requires Python assertions; do not use -O")
    if os.environ.get("PCBRIDGE_TEST_HYPRLAND_DOCTOR") != "1" or os.uname().nodename != "pcbridge-hyprland":
        raise RuntimeError("Use only the explicitly enabled disposable Hyprland VM")
    assert hyprland.screen_locked() is False
    assert not layers() and idlewatch.read_idle_ms() is None, "Do not overlap a resident grant/watcher"
    idle = gate = None
    with tempfile.TemporaryDirectory(prefix="pcbridge-hyprland-doctor-") as temporary:
        state = Path(temporary)
        base = load_config(ROOT / "config.example.toml", check_state=False)
        cfg = dataclasses.replace(base, state_dir=state,
            desktop=dataclasses.replace(base.desktop, enabled=True, unlock_idle_seconds=0,
                                        unlock_notification=False))
        # Use actual packaged default discovery: no binary config or env override.
        assert not os.environ.get("PCBRIDGE_NATIVE_BIN") and cfg.native.binary_path is None
        binary = discover_native_binary(cfg.native)
        assert "_native" in binary.parts
        try:
            absent = inspect(cfg)
            assert absent["Hyprland idle time"].status == "fail"
            assert absent["Hyprland visible frame"].status == "info"
            assert list(state.iterdir()) == [] and not layers(), "Doctor must not open a grant"
            idle = subprocess.Popen([str(binary), "idle-watch"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: idlewatch.read_idle_ms() is not None, description="fresh packaged idle watcher")
            ready = inspect(cfg)
            assert ready["Hyprland idle time"].status == "ok"
            gate = SafetyGate(cfg)
            gate.unlock(1, reason="Isolated VM doctor visible-frame verification")
            wait_for(lambda: len(layers()) == 8, description="eight presented frame strips")
            active = inspect(cfg)
            assert active["Hyprland visible frame"].status == "ok", active["Hyprland visible frame"].detail
            idle.terminate()
            idle.wait(timeout=5)
            wait_for(lambda: idlewatch.read_idle_ms() is None, description="dead idle watcher becomes unknown")
            assert inspect(cfg)["Hyprland idle time"].status == "fail"
            decision = gate.check("doctor_probe", write=True, force=True)
            assert not decision.allowed and decision.code == ErrorCode.ACTIVITY_UNKNOWN, decision
            gate.lock()
            wait_for(lambda: not layers(), description="doctor grant cleanup")
            assert inspect(cfg)["Hyprland visible frame"].status == "info"
            setup_notes()
            print(json.dumps({"default_helper": "packaged_release", "capture_probe": "no_capture_started",
                "idle": "absent_refused_fresh_known_dead_refused", "visible_frame": "inactive_active_cleanup",
                "forced_write_without_idle": "refused", "glow_strips": 8, "canvas": [2560, 800]}))
        finally:
            if gate:
                gate.lock()
            if idle and idle.poll() is None:
                idle.terminate()
                idle.wait(timeout=5)
            wait_for(lambda: not layers(), description="final doctor frame cleanup")


if __name__ == "__main__":
    main()
