"""VM-only observer lifecycle checks; no desktop input or grant is sent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from pcbridge.desktop import idlewatch, session


def wait_known(timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = idlewatch.read_idle_ms()
        if value is not None:
            return value
        time.sleep(0.1)
    raise AssertionError("No fresh session-bound idle observation")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("PCBRIDGE_TEST_LIVE_HYPRLAND") != "1":
        raise SystemExit("Set PCBRIDGE_TEST_LIVE_HYPRLAND=1 in the isolated VM")
    assert session.desktop_kind() == session.HYPRLAND
    # Do not replace a resident daemon's observer. Stop it explicitly in the VM
    # before this test if it is running.
    assert idlewatch.read_idle_ms() is None, "An idle observer is already running"
    watcher = subprocess.Popen([str(args.binary.resolve()), "idle-watch"])
    evidence = {}
    try:
        first = wait_known()
        time.sleep(2)
        second = wait_known()
        assert second > first + 500, (first, second)
        evidence["idle_advances"] = [first, second]
        watcher.send_signal(signal.SIGSTOP)
        time.sleep(3.5)
        assert idlewatch.read_idle_ms() is None, "Stopped observer record remained trusted"
        evidence["stopped_observer"] = "unknown"
        watcher.send_signal(signal.SIGCONT)
        wait_known()
        original = os.environ["HYPRLAND_INSTANCE_SIGNATURE"]
        try:
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = "different-session"
            assert idlewatch.read_idle_ms() is None
        finally:
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = original
        evidence["different_session"] = "unknown"
        watcher.terminate()
        watcher.wait(timeout=3)
        assert idlewatch.read_idle_ms() is None, "Dead observer record remained trusted"
        evidence["dead_observer"] = "unknown"
        print(json.dumps(evidence, sort_keys=True))
    finally:
        if watcher.poll() is None:
            watcher.send_signal(signal.SIGCONT)
            watcher.terminate()
            try:
                watcher.wait(timeout=3)
            except subprocess.TimeoutExpired:
                watcher.kill()
                watcher.wait()


if __name__ == "__main__":
    main()
