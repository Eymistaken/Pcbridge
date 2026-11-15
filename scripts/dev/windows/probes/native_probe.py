"""Talk to the Windows native helper through pcbridge's own NativeClient.

    python scripts\\dev\\windows\\probes\\native_probe.py PATH\\TO\\pcbridge-native.exe

Prints the handshake, display.snapshot, capabilities, and what a method with
no Windows backend yet answers. Run it in the desktop session.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from pcbridge.native.client import NativeClient


def main() -> int:
    binary = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        client = NativeClient(binary, state_dir=Path(tmp) / "state", runtime_dir=Path(tmp) / "run")
        (Path(tmp) / "state").mkdir()
        (Path(tmp) / "run").mkdir()
        try:
            for method, params in (("display.snapshot", {}), ("capabilities", {}),
                                   ("capture.frame", {"display_id": "x"})):
                try:
                    response = client.request(method, params)
                    print(f"== {method}\n{json.dumps(getattr(response, 'result', response), indent=1)[:1500]}")
                except Exception as exc:  # noqa: BLE001 - a probe reports everything
                    print(f"== {method}\n{type(exc).__name__}: {exc}")
            print("== handshake", client.handshake)
        finally:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
