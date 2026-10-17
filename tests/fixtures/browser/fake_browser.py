#!/usr/bin/env python3
"""Stands in for a browser on --remote-debugging-pipe: reads NUL-separated
JSON from fd 3, answers on fd 4 with {"id": ..., "result": {"method": ...}},
and exits on Browser.close (writing a marker file first) or when fd 3
closes. argv[1]: the marker file."""

import json
import os
import sys
from pathlib import Path

marker = Path(sys.argv[1]) if len(sys.argv) > 1 else None
buf = b""
os.write(4, json.dumps({"method": "Fake.started"}).encode() + b"\0")
while True:
    chunk = os.read(3, 65536)
    if not chunk:
        sys.exit(3)
    buf += chunk
    while b"\0" in buf:
        raw, buf = buf.split(b"\0", 1)
        msg = json.loads(raw)
        os.write(4, json.dumps({"id": msg.get("id"), "result": {"method": msg.get("method")}}).encode() + b"\0")
        if msg.get("method") == "Browser.close":
            if marker:
                marker.write_text("closed cleanly")
            sys.exit(0)
