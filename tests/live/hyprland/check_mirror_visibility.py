"""Measure how Hyprland exposes a mirrored output in the disposable VM."""
from __future__ import annotations

import os

if not __debug__:
    raise RuntimeError("Mirror acceptance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_MIRROR") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_MIRROR=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Mirror acceptance runs only on the disposable pcbridge-hyprland VM")

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.desktop import hyprland, idlewatch, monitors  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402


def evaluate(source):
    subprocess.run(["hyprctl", "eval", source], check=True,
                   capture_output=True, timeout=3)


def all_outputs():
    return json.loads(subprocess.run(["hyprctl", "-j", "monitors", "all"],
        check=True, capture_output=True, text=True, timeout=5).stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"passed": False, "cleanup": {}}
    failure = None
    try:
        assert hyprland.screen_locked() is False
        assert not layers() and idlewatch.read_idle_ms() is None
        original = hyprland.monitors()
        assert [(row["name"], row["x"], row["y"], row["mirrorOf"], row["disabled"])
                for row in original] == [
            ("Virtual-1", 0, 0, "none", False),
            ("Virtual-2", 1280, 0, "none", False)]
        try:
            evaluate('hl.monitor({ output = "Virtual-2", mirror = "Virtual-1" })')
            mirrored = wait_for(lambda: next((row for row in all_outputs()
                if row["name"] == "Virtual-2" and row["mirrorOf"] not in ("none", "")), None),
                description="mirror reported by compositor")
            evidence["mirror_of"] = mirrored["mirrorOf"]
            evidence["active_outputs"] = [row["name"] for row in hyprland.monitors()]
            evidence["canvas_outputs"] = [row.connector for row in
                                           monitors.list_monitors(use_cache=False)]
            assert evidence["active_outputs"] == ["Virtual-1"]
            assert evidence["canvas_outputs"] == ["Virtual-1"]
            evidence["passed"] = True
        finally:
            mode = f'{original[1]["width"]}x{original[1]["height"]}@{original[1]["refreshRate"]:.2f}'
            source = ('hl.monitor({ output = "Virtual-2", mirror = "", disabled = false, '
                      + 'mode = ' + json.dumps(mode) + ', position = "1280x0", '
                      + 'scale = 1, transform = 0 })')
            evaluate(source)
            wait_for(lambda: [(row["name"], row["x"], row["y"], row["mirrorOf"], row["disabled"])
                for row in hyprland.monitors()] == [
                ("Virtual-1", 0, 0, "none", False),
                ("Virtual-2", 1280, 0, "none", False)],
                description="original nonmirrored outputs restored")
            evidence["cleanup"]["monitors"] = "restored"
            assert hyprland.screen_locked() is False
            assert not layers() and idlewatch.read_idle_ms() is None
            evidence["cleanup"]["screen_locked"] = False
    except BaseException as error:
        failure = error
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)[:1600]}
        evidence["passed"] = False
    finally:
        (args.out_dir / "mirror-visibility.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print({"passed": evidence["passed"], "failure": evidence.get("failure"),
               "cleanup": evidence["cleanup"]})
    if failure:
        raise failure


if __name__ == "__main__":
    main()
