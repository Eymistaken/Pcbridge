"""Route the existing Hyprland grant action to its resident frame owner."""

from __future__ import annotations

import json
import time

from .. import __version__, paths
from ..desktop import session
from ..relay import CONNECT_WAIT_S, HandshakeError, connect_daemon

MAX_REPLY_BYTES = 256 * 1024
REQUEST_TIMEOUT_SECONDS = 30.0


def unlock(cfg, minutes: int | None, reason: str, granted_by: str) -> str:
    from .grant import GrantError

    expected = session.grant_context(cfg.state_dir)
    if not expected["hyprland_instance"] or not expected["wayland_display"]:
        raise GrantError("No unambiguous Hyprland session is available for desktop control.")
    backend = None
    try:
        backend = connect_daemon(paths.socket_path(), CONNECT_WAIT_S, activate=True)
        if backend is None:
            raise GrantError("The resident desktop owner is unavailable. Run pcbridge setup and pcbridge doctor.")
        handshake = backend.handshake(5, desktop_context=True)
        if handshake.get("desktop_context") != expected:
            raise GrantError("The resident daemon belongs to a different desktop session or state directory. Run pcbridge doctor.")
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS

        def send(message: dict) -> None:
            backend.sock.settimeout(max(0.01, deadline - time.monotonic()))
            backend.send(json.dumps(message).encode() + b"\n")

        def result(request_id: int) -> dict:
            # Ignore bounded notifications, never wait forever for an answer.
            for _ in range(64):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GrantError("The resident desktop owner did not answer before the deadline.")
                backend.sock.settimeout(remaining)
                line = backend.rfile.readline(MAX_REPLY_BYTES + 1)
                if not line.endswith(b"\n") or len(line) > MAX_REPLY_BYTES:
                    raise GrantError("The resident desktop owner returned an invalid or oversized reply.")
                reply = json.loads(line)
                if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
                    raise GrantError("The resident desktop owner returned an invalid reply.")
                if reply.get("id") != request_id:
                    continue
                if "error" in reply:
                    raise GrantError("The resident desktop owner rejected the grant request. Run pcbridge doctor.")
                response = reply.get("result")
                if not isinstance(response, dict):
                    raise GrantError("The resident desktop owner returned an invalid result.")
                return response
            raise GrantError("The resident desktop owner sent too many notifications without an answer.")

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": granted_by, "version": __version__}}})
        result(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "desktop_unlock", "arguments": {
                "minutes": max(1, min(cfg.desktop.unlock_max_minutes,
                    cfg.desktop.unlock_default_minutes if minutes is None else int(minutes))),
                "reason": reason}}})
        response = result(2)
        text = "\n".join(block.get("text", "") for block in response.get("content", [])
                         if isinstance(block, dict) and block.get("type") == "text")
        if response.get("isError"):
            raise GrantError(text or "The resident desktop owner refused the grant.")
        structured = response.get("structuredContent")
        if not text or not isinstance(structured, dict) or structured.get("type") != "pcbridge.desktop.grant":
            raise GrantError("The resident desktop owner did not confirm the grant.")
        return text
    except (OSError, HandshakeError, ValueError, TypeError) as error:
        raise GrantError("The resident desktop owner connection failed. Run pcbridge doctor.") from error
    finally:
        if backend is not None:
            backend.close()
