"""Exercise production native visibility checks using a drawing-only VM lease.

The helper is built with test-harness, but runs its production session and
health providers. Only a test resource flag is opened: no capture or input.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.desktop import glowstate, hyprland  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from pcbridge.native import NativeClient  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402


def main():
    assert os.environ.get("PCBRIDGE_TEST_HYPRLAND_FRAME_GUARD") == "1"
    assert os.uname().nodename == "pcbridge-hyprland"
    assert hyprland.screen_locked() is False
    assert not layers(), "Refuse to overlap a resident frame"
    binary = (ROOT / "rust/target/debug/pcbridge-native").resolve()
    owners = []
    with tempfile.TemporaryDirectory(prefix="pcbridge-native-visibility-") as temporary, \
            tempfile.TemporaryFile() as errors:
        directory = Path(temporary).resolve()
        runtime = directory / "native-runtime"
        runtime.mkdir(mode=0o700)
        store = LeaseStore(directory)
        client = None
        try:
            now = time.time()
            token = store.grant(until=now + 90, reason="Native visibility test resource only",
                                granted=now, granted_by="vm-test").token()
            client = NativeClient(binary, state_dir=directory, runtime_dir=runtime)

            def refused(code):
                try:
                    client.request("test.hold_resource")
                except DesktopError as error:
                    assert error.code == code, error.to_dict()
                else:
                    raise AssertionError("Resource opened without its required guard")

            refused(ErrorCode.BACKEND_UNAVAILABLE)
            snapshot = client.request("display.snapshot").result
            assert len(snapshot["monitors"]) == 2
            assert all(row["primary_is_focus"] for row in snapshot["monitors"])
            owner = subprocess.Popen([str(binary), "glow-watch", str(directory), token.grant_id,
                                      str(token.revoke_epoch)], stdout=subprocess.DEVNULL, stderr=errors)
            owners.append(owner)
            wait_for(lambda: glowstate.read(directory, token, binary=binary), description="visible native owner")
            assert client.request("test.hold_resource").result["open"] is True
            owner.send_signal(signal.SIGSTOP)
            started = time.monotonic()
            wait_for(lambda: client.request("test.resource_status").result["open"] is False,
                     timeout=2, description="stale presentation native watchdog")
            stale_ms = round((time.monotonic() - started) * 1000)
            assert stale_ms <= 1400, stale_ms
            refused(ErrorCode.BACKEND_UNAVAILABLE)
            owner.send_signal(signal.SIGCONT)
            wait_for(lambda: glowstate.read(directory, token, binary=binary), description="fresh resumed presentation")
            assert client.request("test.hold_resource").result["open"] is True
            owner.kill()
            owner.wait(timeout=3)
            started = time.monotonic()
            wait_for(lambda: client.request("test.resource_status").result["open"] is False,
                     timeout=1, description="dead frame native watchdog")
            dead_ms = round((time.monotonic() - started) * 1000)
            assert dead_ms <= 250, dead_ms
            refused(ErrorCode.BACKEND_UNAVAILABLE)
            wait_for(lambda: not layers(), description="dead surfaces removed")

            now = time.time()
            replacement = store.grant(until=now + 90, reason="Replacement visibility resource probe",
                                      granted=now, granted_by="vm-test").token()
            new_owner = subprocess.Popen([str(binary), "glow-watch", str(directory), replacement.grant_id,
                                          str(replacement.revoke_epoch)], stdout=subprocess.DEVNULL, stderr=errors)
            owners.append(new_owner)
            wait_for(lambda: glowstate.read(directory, replacement, binary=binary), description="replacement presentation")
            refused(ErrorCode.REVOKED)
            client.close()
            client = NativeClient(binary, state_dir=directory, runtime_dir=runtime)
            assert client.request("test.hold_resource").result["open"] is True
            store.revoke()
            wait_for(lambda: client.request("test.resource_status").result["open"] is False,
                     timeout=1, description="revoke resource closure")
            refused(ErrorCode.REVOKED)
            assert new_owner.wait(timeout=3) == 0
            wait_for(lambda: not layers(), description="revoked frame teardown")
            print(json.dumps({"native_visibility_guard": {"missing_frame": "refused",
                "stale_frame_close_ms": stale_ms, "dead_frame_close_ms": dead_ms,
                "fresh_resumption": "passed", "replacement_does_not_rebind": "passed",
                "revoked_resource_closed": "passed", "input_and_capture_opened": False}}, sort_keys=True))
        finally:
            store.revoke()
            if client:
                client.close()
            for owner in owners:
                if owner.poll() is None:
                    owner.send_signal(signal.SIGCONT)
                    owner.terminate()
                    try:
                        owner.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        owner.kill()
                        owner.wait()
            errors.seek(0)
            diagnostic = errors.read(8192).decode(errors="replace")
            if diagnostic:
                print(diagnostic, file=sys.stderr)


if __name__ == "__main__":
    main()
