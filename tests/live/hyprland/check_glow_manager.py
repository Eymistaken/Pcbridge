"""Measure Python ownership of native drawing leases before gate integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.desktop import hyprland  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.glowowner import FrameOwner  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402


def main():
    assert os.environ.get("PCBRIDGE_TEST_LIVE_HYPRLAND") == "1"
    assert os.uname().nodename == "pcbridge-hyprland"
    assert hyprland.screen_locked() is False
    assert not layers(), "Refuse to overlap a resident frame"
    binary = (ROOT / "rust/target/debug/pcbridge-native").resolve()
    with tempfile.TemporaryDirectory(prefix="pcbridge-frame-manager-") as temporary:
        directory = Path(temporary).resolve()
        store = LeaseStore(directory)
        first_owner, replacement_owner = FrameOwner(directory, binary), FrameOwner(directory, binary)
        dying_owner, invalid_owner = FrameOwner(directory, binary), FrameOwner(directory, Path("/bin/false"))
        def grant():
            now = time.time()
            return store.grant(until=now + 60, reason="Drawing-only frame manager probe",
                               granted=now, granted_by="vm-test").token()
        try:
            first = grant()
            record = first_owner.open(first)
            assert record["owner_pid"] == os.getpid() and record["strip_count"] == 8
            assert first_owner.open(first)["pid"] == record["pid"], "Idempotent open restarted the frame"
            replacement = grant()
            new_record = replacement_owner.open(replacement)
            assert new_record["pid"] != record["pid"]
            first_owner.close()
            assert store.snapshot().token() == replacement
            assert replacement_owner.health(replacement), "Old close erased the new frame"
            replacement_owner.close()
            assert store.snapshot().token() is None
            wait_for(lambda: not layers(), description="explicit owner cleanup")

            dying = grant()
            dying_owner.open(dying)
            started = time.monotonic()
            dying_owner._process.kill()
            wait_for(lambda: store.snapshot().token() is None, timeout=1, description="dead helper lease retirement")
            death_ms = round((time.monotonic() - started) * 1000)
            assert death_ms <= 250, death_ms
            assert store.snapshot().revoke_epoch == dying.revoke_epoch + 1
            wait_for(lambda: not layers(), description="dead owner surfaces removed")

            invalid = grant()
            try:
                invalid_owner.open(invalid)
            except DesktopError as error:
                assert error.code == ErrorCode.BACKEND_UNAVAILABLE
            else:
                raise AssertionError("Unavailable executable reported visible grant success")
            assert store.snapshot().token() is None
            assert not layers()
            print(json.dumps({"python_native_frame_owner": {"first_strip_count": record["strip_count"],
                "idempotent_open": "passed", "old_owner_close_preserves_replacement": "passed",
                "dead_helper_retirement_ms": death_ms, "unavailable_start": "closed",
                "production_gate_opened": False}}, sort_keys=True))
        finally:
            for owner in (first_owner, replacement_owner, dying_owner, invalid_owner):
                owner.close()
            store.revoke()


if __name__ == "__main__":
    main()
