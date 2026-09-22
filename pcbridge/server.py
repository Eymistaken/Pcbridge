"""`python -m pcbridge.server [--stdio]`: the pre-2.0 entry point, kept working.

Every client registered before 2.0 runs `<venv>/bin/python -m pcbridge.server
--stdio`, and the old systemd unit runs it without `--stdio`. This module is
deliberately tiny so the stdio path starts fast:

    --stdio              -> the relay (pcbridge.relay): a thin pipe to the
                            resident daemon, or the in-process server when the
                            daemon cannot be reached
    (no --stdio)         -> the resident server (pcbridge.daemon), serving
                            HTTP and, when systemd passes one, the unix socket
    PCBRIDGE_IN_PROCESS  -> the classic single-client server (pcbridge.app)

Everything else that used to live here (build_app, INSTRUCTIONS, the ASGI
shims) is in pcbridge.app and is still importable from this module.
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("PCBRIDGE_IN_PROCESS") or "--check" in args:
        from .app import main as app_main

        return app_main(args)
    if "--stdio" in args:
        from .relay import main as relay_main

        return relay_main(args)
    from .daemon import main as daemon_main

    return daemon_main(args, bind_socket=False)


def __getattr__(name: str):
    # `from pcbridge.server import build_app` and friends keep working.
    from . import app

    return getattr(app, name)


if __name__ == "__main__":
    raise SystemExit(main())
