"""The local channel's endpoint authentication (Windows) and connect (both).

The exchange runs over a real socket pair on both systems; the endpoint
file and the loopback listener only exist on Windows.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from pcbridge import localipc


def _serve_once(sock: socket.socket, secret: bytes) -> list[bool]:
    """Run the daemon side on a blocking socket; returns [trusted]."""
    result: list[bool] = []
    reader = sock.makefile("rb")

    async def readline() -> bytes:
        return reader.readline(1025)

    async def send(data: bytes) -> None:
        sock.sendall(data)

    result.append(asyncio.run(localipc.server_authenticate(readline, send, secret)))
    return result


class ExchangeTest(unittest.TestCase):
    def _run(self, client_secret: bytes, server_secret: bytes) -> tuple[Exception | None, list[bool]]:
        client, server = socket.socketpair()
        client.settimeout(5)
        server.settimeout(5)
        outcome: list[bool] = []
        thread = threading.Thread(target=lambda: outcome.extend(_serve_once(server, server_secret)))
        thread.start()
        error: Exception | None = None
        try:
            localipc.client_authenticate(client, client_secret)
        except localipc.AuthError as exc:
            error = exc
            client.close()  # the client gives up: the daemon's read ends
        thread.join(10)
        client.close()
        server.close()
        return error, outcome

    def test_the_same_secret_passes_both_ways(self) -> None:
        secret = secrets.token_bytes(32)
        error, outcome = self._run(secret, secret)
        self.assertIsNone(error)
        self.assertEqual(outcome, [True])

    def test_a_squatter_without_the_secret_is_refused_by_the_client(self) -> None:
        error, outcome = self._run(secrets.token_bytes(32), secrets.token_bytes(32))
        self.assertIsInstance(error, localipc.AuthError)
        # The client refused before sending its own proof.
        self.assertEqual(outcome, [False])

    def test_a_client_without_the_secret_is_refused_by_the_daemon(self) -> None:
        secret = secrets.token_bytes(32)
        client, server = socket.socketpair()
        client.settimeout(5)
        outcome: list[bool] = []
        thread = threading.Thread(target=lambda: outcome.extend(_serve_once(server, secret)))
        thread.start()
        try:
            client.sendall(json.dumps({"pcbridge_auth": {"v": 1, "nonce": "a" * 32}}).encode() + b"\n")
            client.makefile("rb").readline()
            client.sendall(json.dumps({"pcbridge_auth": {"proof": "0" * 64}}).encode() + b"\n")
            thread.join(10)
        finally:
            client.close()
            server.close()
        self.assertEqual(outcome, [False])


@unittest.skipUnless(localipc.IS_WINDOWS, "the loopback endpoint exists on Windows only")
class WindowsEndpointTest(unittest.TestCase):
    def test_bind_connect_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / localipc.ENDPOINT_FILE
            bound = localipc.bind_endpoint(path)
            self.assertIsNotNone(bound)
            listener, secret = bound
            self.assertEqual(listener.getsockname()[0], "127.0.0.1")
            endpoint = localipc.read_endpoint(path)
            self.assertEqual(endpoint["secret"], secret)
            self.assertEqual(endpoint["pid"], os.getpid())

            def accept() -> None:
                conn, _ = listener.accept()
                with conn:
                    _serve_once(conn, secret)
                    conn.sendall(b"hello\n")

            thread = threading.Thread(target=accept)
            thread.start()
            with localipc.connect(path, timeout=5) as sock:
                self.assertEqual(sock.makefile("rb").readline(), b"hello\n")
            thread.join(10)
            listener.close()
            localipc.remove_endpoint(path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
