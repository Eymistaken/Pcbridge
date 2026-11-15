"""Talk to the Windows native helper through pcbridge's own NativeClient.

    python scripts\\dev\\windows\\probes\\native_probe.py PATH\\TO\\pcbridge-native.exe [OUT.png]

Checks, in the desktop session: the handshake and display.snapshot; a
capture with no grant (GRANT_REQUIRED); a grant opened through pcbridge's
lease store and a capture under it (the PNG is written to OUT.png); the
grant revoked and the capture refused (REVOKED).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from pcbridge.desktop.lease import LeaseStore
from pcbridge.native.client import NativeClient


def client_for(binary: Path, root: Path) -> NativeClient:
    return NativeClient(binary, state_dir=root / "state", runtime_dir=root / "run")


def capture(client: NativeClient, snapshot: dict, grant_id: str, epoch: int):
    monitor = snapshot["monitors"][0]
    return client.request("capture.frame", {
        "display_id": f"windows:{monitor['connector']}",
        "topology_id": snapshot["topology_id"],
        "session_id": "probe",
        "grant_id": grant_id,
        "revoke_epoch": epoch,
        "timeout_ms": 8000,
        "freshness": "after_request",
        "include_pointer": True,
    })


def show(label: str, call) -> object:
    try:
        response = call()
    except Exception as exc:  # noqa: BLE001 - a probe reports everything
        print(f"== {label}: {type(exc).__name__}: {getattr(exc, 'code', '')} {exc}")
        return None
    result = getattr(response, "result", response)
    print(f"== {label}: {json.dumps(result)[:700]}")
    return response


def main() -> int:
    binary = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        (root / "run").mkdir()
        ungranted = client_for(binary, root)
        try:
            snapshot = show("display.snapshot", lambda: ungranted.request("display.snapshot", {})).result
            show("capture without a grant", lambda: capture(ungranted, snapshot, "none", 0))
        finally:
            ungranted.close()

        store = LeaseStore(root / "state")
        now = time.time()
        lease = store.grant(until=now + 120, reason="native probe", granted=now, granted_by="probe")
        token = lease.token(now)
        granted = client_for(binary, root)
        try:
            print("== handshake", granted.handshake if granted.handshake else "(lazy)")
            started = time.perf_counter()
            response = show("capture with the grant",
                            lambda: capture(granted, snapshot, token.grant_id, token.revoke_epoch))
            print(f"   round trip {1000 * (time.perf_counter() - started):.1f} ms")
            payload = getattr(response, "binary", b"") if response is not None else b""
            print(f"   png bytes {len(payload)}, signature ok {payload[:8] == bytes.fromhex('89504e470d0a1a0a')}")
            if out is not None and payload:
                out.write_bytes(payload)
            store.revoke()
            show("capture after revoke", lambda: capture(granted, snapshot, token.grant_id, token.revoke_epoch))
        finally:
            granted.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
