"""Real hyprlock transitions in the dedicated VM, including locker failure."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time

from pcbridge.desktop import hyprland, session


def wait_lock(expected, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if hyprland.screen_locked() is expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"No authoritative lock transition to {expected}")


def native_lock(expected):
    subprocess.run([
        "cargo", "test", "-p", "pcbridge-native", "--locked", "--test", "hyprland_live",
        "selected_hyprland_session_has_authoritative_lock_state", "--", "--nocapture",
    ], cwd="rust", check=True, timeout=30, env={
        **os.environ, "PCBRIDGE_TEST_HYPRLAND_LOCKED": str(expected).lower(),
    })


def main():
    assert os.environ.get("PCBRIDGE_TEST_LIVE_HYPRLAND") == "1"
    assert os.uname().nodename == "pcbridge-hyprland", "Lock tests require the dedicated VM"
    assert session.desktop_kind() == session.HYPRLAND
    initial = hyprland.screen_locked()
    assert initial is False, f"Require a known unlocked session, observed {initial}"
    native_lock(False)
    evidence = {"initial": "unlocked"}
    locker = None
    with tempfile.TemporaryDirectory(prefix="pcbridge-lock-test-") as temporary:
        config = Path(temporary) / "hyprlock.conf"
        config.write_text("animations {\n enabled = false\n}\nbackground {\n monitor =\n color = rgba(202020ff)\n}\n", encoding="utf-8")
        with (Path(temporary) / "locker.log").open("w") as log:
            offset = 0
            def start():
                nonlocal offset
                log.seek(0, os.SEEK_END)
                offset = log.tell()
                return subprocess.Popen(["hyprlock", "--config", str(config), "--grace", "0", "--verbose"],
                                        stdout=log, stderr=log)
            def wait_presented():
                until = time.monotonic() + 20
                while time.monotonic() < until:
                    text = (Path(temporary) / "locker.log").read_text(encoding="utf-8")
                    if "onLockLocked called" in text[offset:]:
                        return
                    assert locker.poll() is None, "Locker exited before the locked callback"
                    time.sleep(0.1)
                raise AssertionError("No hyprlock locked callback after presenting its surfaces")
            try:
                locker = start()
                wait_lock(True)
                wait_presented()
                native_lock(True)
                evidence["lock_transition"] = "locked"
                locker.send_signal(signal.SIGUSR1)
                locker.wait(timeout=5)
                wait_lock(False)
                native_lock(False)
                evidence["normal_unlock"] = "unlocked"

                locker = start()
                wait_lock(True)
                wait_presented()
                locker.kill()
                locker.wait(timeout=5)
                time.sleep(0.5)
                assert hyprland.screen_locked() is True, "Locker death must not imply unlock"
                native_lock(True)
                evidence["locker_crash"] = "still_locked"

                # Stock Hyprland has allow_session_lock_restore=false. A new
                # locker cannot reclaim this dead lock. Preserve that security
                # setting and let the VM harness reset its session afterward.
                evidence["cleanup"] = "requires_vm_session_reset"
                print(json.dumps(evidence, sort_keys=True))
            except BaseException:
                log.flush()
                shutil.copyfile(Path(temporary) / "locker.log", "/tmp/pcbridge-hyprlock-failure.log")
                print("Hyprlock evidence: /tmp/pcbridge-hyprlock-failure.log", flush=True)
                raise
            finally:
                if locker and locker.poll() is None:
                    locker.send_signal(signal.SIGUSR1)
                    try:
                        locker.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        locker.kill()
                        locker.wait()


if __name__ == "__main__":
    main()
