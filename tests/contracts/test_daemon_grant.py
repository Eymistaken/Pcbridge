"""Resident CLI grant routing over actual bounded Unix socket framing."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from pcbridge.cli import daemon_grant, grant
from pcbridge.desktop import session
from pcbridge.relay import SocketBackend


class ResidentGrantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cfg = SimpleNamespace(state_dir=Path(temporary.name), desktop=SimpleNamespace(
            enabled=True, unlock_default_minutes=15, unlock_max_minutes=120))
        self.context = {"version": 1, "compositor": "hyprland", "state_dir": temporary.name,
                        "wayland_display": "wayland-1", "hyprland_instance": "selected"}
        self.received = []

    def connect(self, *, context=None, result=None, oversized=False):
        local, remote = socket.socketpair()
        backend = SocketBackend(local)
        self.addCleanup(backend.close)
        self.addCleanup(remote.close)
        self.server_error = None

        def serve():
            try:
                with remote.makefile("rb") as reader:
                    pre = json.loads(reader.readline())
                    self.received.append(pre)
                    if oversized:
                        remote.sendall(b"x" * 65537 + b"\n")
                        return
                    reply = {"protocol": 1, "desktop_context": self.context if context is None else context}
                    remote.sendall(json.dumps({"pcbridge_daemon": reply}).encode() + b"\n")
                    while line := reader.readline():
                        request = json.loads(line)
                        self.received.append(request)
                        if "id" not in request:
                            continue
                        answer = {} if request["method"] == "initialize" else result or {
                            "content": [{"type": "text", "text": "Visible grant confirmed."}],
                            "structuredContent": {"type": "pcbridge.desktop.grant"}}
                        remote.sendall(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": answer}).encode() + b"\n")
            except (OSError, ValueError) as error:
                self.server_error = error

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 1)
        return backend

    def run_unlock(self, backend, minutes=5):
        with mock.patch.object(session, "grant_context", return_value=self.context), \
                mock.patch.object(daemon_grant, "connect_daemon", return_value=backend), \
                mock.patch.object(daemon_grant.paths, "socket_path", return_value=Path("/fixture.sock")):
            return daemon_grant.unlock(self.cfg, minutes, "VM control", "pcbridge ui")

    def test_same_context_routes_existing_tool_and_closes_only_the_connection(self):
        backend = self.connect()
        self.assertEqual(self.run_unlock(backend), "Visible grant confirmed.")
        self.assertTrue(self.received[0]["pcbridge_relay"]["desktop_context"])
        self.assertEqual(self.received[1]["params"]["clientInfo"]["name"], "pcbridge ui")
        self.assertEqual(self.received[-1]["params"], {"name": "desktop_unlock", "arguments": {
            "minutes": 5, "reason": "VM control"}})
        self.assertEqual(backend.sock.fileno(), -1)

    def test_state_or_session_mismatch_never_sends_an_unlock(self):
        for field in ("state_dir", "wayland_display", "hyprland_instance", "compositor", "version"):
            with self.subTest(field=field):
                self.received.clear()
                backend = self.connect(context={**self.context, field: "foreign"})
                with self.assertRaisesRegex(grant.GrantError, "different desktop"):
                    self.run_unlock(backend)
                self.assertEqual(len(self.received), 1)

    def test_old_daemon_and_oversized_handshake_refuse(self):
        for options in ({"context": {}}, {"oversized": True}):
            backend = self.connect(**options)
            with self.assertRaises(grant.GrantError):
                self.run_unlock(backend)

    def test_unavailable_daemon_never_creates_a_local_runtime(self):
        with mock.patch("pcbridge.desktop.compositor.current", return_value=SimpleNamespace(kind="hyprland")), \
                mock.patch("pcbridge.cli.runtime_of") as runtime, \
                mock.patch.object(session, "grant_context", return_value=self.context), \
                mock.patch.object(daemon_grant, "connect_daemon", return_value=None), \
                mock.patch.object(daemon_grant.paths, "socket_path", return_value=Path("/fixture.sock")):
            with self.assertRaisesRegex(grant.GrantError, "resident desktop owner is unavailable"):
                grant.unlock(self.cfg, 5)
            runtime.assert_not_called()

    def test_typed_tool_refusal_and_missing_confirmation_raise(self):
        for result in (
            {"isError": True, "content": [{"type": "text", "text": "Frame unavailable."}]},
            {"content": [{"type": "text", "text": "Unconfirmed."}], "structuredContent": {}},
        ):
            backend = self.connect(result=result)
            with self.assertRaises(grant.GrantError):
                self.run_unlock(backend)

    def test_default_duration_and_bounded_reply(self):
        backend = self.connect()
        self.run_unlock(backend, minutes=None)
        self.assertEqual(self.received[-1]["params"]["arguments"]["minutes"], 15)
        backend = self.connect(result={"content": [{"type": "text", "text": "x" * daemon_grant.MAX_REPLY_BYTES}]})
        with self.assertRaisesRegex(grant.GrantError, "oversized"):
            self.run_unlock(backend)


if __name__ == "__main__":
    unittest.main()
