#!/usr/bin/env python3
"""Serve the capture-delivery fixture over real stdio.

A test helper, not a tool: `test_mcp_capture_delivery.py` starts it as a child
process so the JSON-RPC encoding of image blocks is exercised over real pipes,
not only through FastMCP's in-memory transport.

    python tests/integration/delivery_server.py <scratch-directory>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import delivery_fixture  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: delivery_server.py <scratch-directory>", file=sys.stderr)
        return 2
    server = delivery_fixture.build(Path(argv[1]), transport="stdio")
    server.mcp.run(transport="stdio", show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
