#!/usr/bin/env python3
"""Contracts for the private Python-to-native process boundary."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import BytesIO
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import NativeSpec, load_config  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.native.discovery import discover_native_binary  # noqa: E402
from pcbridge.native.client import NativeClient  # noqa: E402
from pcbridge.native.registry import NativeRegistry  # noqa: E402
from pcbridge.native.protocol import (  # noqa: E402
    MAX_BINARY_BYTES,
    MAX_HEADER_BYTES,
    NativeFrameError,
    read_frame,
    write_frame,
)


class OneByteReader:
    def __init__(self, data: bytes) -> None:
        self._stream = BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(min(size, 1))


class TwoByteWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.flush_count = 0

    def write(self, data: bytes | memoryview) -> int:
        chunk = bytes(data[:2])
        self.data.extend(chunk)
        return len(chunk)

    def flush(self) -> None:
        self.flush_count += 1


class NativeConfigContractTests(unittest.TestCase):
    def test_config_defaults_to_python_and_explicit_binary_has_priority(self) -> None:
        public = load_config(str(ROOT / "config.example.toml"))
        self.assertEqual(public.native, NativeSpec(capture="python"))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            configured = root / "configured-native"
            configured.touch(mode=0o700)
            selected = discover_native_binary(
                NativeSpec(capture="rust", binary_path=configured),
                environ={},
                package_root=root / "package",
            )

        self.assertEqual(selected, configured.resolve())

    @unittest.skipUnless(
        sys.platform.startswith("linux")
        and platform.machine().lower() in ("amd64", "x86_64"),
        "packaged fixture targets Linux x86_64",
    )
    def test_environment_then_config_then_packaged_discovery_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            environment_binary = root / "environment-native"
            configured_binary = root / "configured-native"
            packaged_binary = (
                root
                / "package/_native/x86_64-unknown-linux-gnu/pcbridge-native"
            )
            for binary in (environment_binary, configured_binary, packaged_binary):
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.touch(mode=0o700)

            configured = NativeSpec(capture="rust", binary_path=configured_binary)
            self.assertEqual(
                discover_native_binary(
                    configured,
                    environ={"PCBRIDGE_NATIVE_BIN": str(environment_binary)},
                    package_root=root / "package",
                ),
                environment_binary.resolve(),
            )
            self.assertEqual(
                discover_native_binary(
                    configured,
                    environ={},
                    package_root=root / "package",
                ),
                configured_binary.resolve(),
            )
            self.assertEqual(
                discover_native_binary(
                    NativeSpec(capture="rust"),
                    environ={},
                    package_root=root / "package",
                ),
                packaged_binary.resolve(),
            )

    def test_missing_explicit_binary_is_typed_and_does_not_fall_through(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            packaged = root / "_native/x86_64-unknown-linux-gnu/pcbridge-native"
            packaged.parent.mkdir(parents=True)
            packaged.touch(mode=0o700)

            with self.assertRaises(DesktopError) as raised:
                discover_native_binary(
                    NativeSpec(capture="rust"),
                    environ={"PCBRIDGE_NATIVE_BIN": str(root / "missing")},
                    package_root=root,
                )

        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_NOT_FOUND)

    def test_native_toml_values_are_parsed_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            binary = root / "native"
            text = (ROOT / "config.example.toml").read_text()
            text = text.replace('capture = "python"', 'capture = "rust"', 1)
            text = text.replace(
                'binary_path = ""',
                f'binary_path = "{binary}"',
                1,
            )
            path = root / "config.toml"
            path.write_text(text)

            loaded = load_config(str(path))
            self.assertEqual(
                loaded.native,
                NativeSpec(capture="rust", binary_path=binary.resolve()),
            )

            path.write_text(text.replace('capture = "rust"', 'capture = "magic"', 1))
            with self.assertRaisesRegex(SystemExit, "python, rust ya da auto"):
                load_config(str(path))


class NativeProtocolContractTests(unittest.TestCase):
    def test_short_reads_and_writes_preserve_header_and_binary(self) -> None:
        writer = TwoByteWriter()
        write_frame(writer, {"kind": "fixture", "binary_len": 3}, b"abc")

        frame = read_frame(OneByteReader(bytes(writer.data)))

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.header, {"kind": "fixture", "binary_len": 3})
        self.assertEqual(frame.binary, b"abc")
        self.assertEqual(writer.flush_count, 1)

    def test_limits_are_rejected_before_payload_allocation(self) -> None:
        oversized_header = (MAX_HEADER_BYTES + 1).to_bytes(4, "big")
        with self.assertRaises(NativeFrameError):
            read_frame(BytesIO(oversized_header))

        header = b'{"binary_len":%d}' % (MAX_BINARY_BYTES + 1)
        oversized_binary = len(header).to_bytes(4, "big") + header
        with self.assertRaises(NativeFrameError):
            read_frame(BytesIO(oversized_binary))


@unittest.skipUnless(sys.platform.startswith("linux"), "helper fixture targets Linux")
class NativeClientContractTests(unittest.TestCase):
    def make_client(
        self,
        root: Path,
        *,
        mode: str = "normal",
        **kwargs,
    ) -> NativeClient:
        helper = ROOT / "tests/fixtures/native/fake_native_helper.py"
        client = NativeClient(
            helper,
            state_dir=root / "state",
            runtime_dir=root / "runtime",
            environment={"PCBRIDGE_FAKE_NATIVE_MODE": mode},
            **kwargs,
        )
        self.addCleanup(client.close)
        return client

    def test_initialize_ping_and_shutdown_are_isolated_and_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            client = self.make_client(root)
            output = StringIO()
            with redirect_stdout(output):
                response = client.request("ping", {"nonce": "contract"})
            pid = client.pid

            self.assertEqual(response.result, {"pong": True, "nonce": "contract"})
            self.assertEqual(response.binary, b"")
            self.assertEqual(client.instance_id, "fake-native-instance")
            self.assertEqual(client.handshake.native_version, "fixture")
            self.assertEqual(client.handshake.platform, "test")
            self.assertEqual(client.features, frozenset({"test.fixture"}))
            self.assertEqual(output.getvalue(), "")
            self.assertIsNotNone(pid)

            client.close()

            self.assertFalse(client.is_running)
            assert pid is not None
            with self.assertRaises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)

    def test_live_helper_is_registered_and_close_removes_exact_record(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            client = self.make_client(root)
            client.request("ping")
            entries = NativeRegistry(root / "runtime").entries()

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].pid, client.pid)
            self.assertEqual(entries[0].instance_id, client.instance_id)

            client.close()
            self.assertEqual(NativeRegistry(root / "runtime").entries(), [])

    def test_out_of_order_responses_are_correlated_by_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(Path(raw), mode="reverse")
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(client.request, "ping", {"nonce": "first"})
                second = pool.submit(client.request, "ping", {"nonce": "second"})

            self.assertEqual(first.result().result["nonce"], "first")
            self.assertEqual(second.result().result["nonce"], "second")

    def test_stderr_is_drained_without_logging_and_keeps_only_64_kib(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(Path(raw), mode="stderr_flood")
            output = StringIO()
            with redirect_stdout(output):
                response = client.request("ping")

            deadline = time.monotonic() + 1.0
            while len(client.stderr_tail.encode()) < 64 * 1024:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)

            self.assertTrue(response.result["pong"])
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(len(client.stderr_tail.encode()), 64 * 1024)

    def test_timeout_sends_cancel_and_later_requests_stay_usable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(Path(raw), mode="timeout")

            with self.assertRaises(DesktopError) as raised:
                client.request("slow", timeout=0.05)
            response = client.request("ping", {"nonce": "after-timeout"})

            self.assertEqual(raised.exception.code, ErrorCode.TIMEOUT)
            self.assertEqual(response.result["nonce"], "after-timeout")

    def test_blocked_pipe_write_cannot_overrun_the_request_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(
                Path(raw),
                mode="stop_reading",
                shutdown_timeout=0.05,
            )

            started = time.monotonic()
            with self.assertRaises(DesktopError) as raised:
                client.request("blocked", binary=b"x" * (4 * 1024 * 1024), timeout=0.05)
            elapsed = time.monotonic() - started
            pid = client.pid
            client.close()

        self.assertEqual(raised.exception.code, ErrorCode.TIMEOUT)
        self.assertLess(elapsed, 0.5)
        assert pid is not None
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_crash_fails_all_pending_requests_and_next_call_does_not_replay(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(Path(raw), mode="crash", default_timeout=1.0)
            with ThreadPoolExecutor(max_workers=1) as pool:
                waiting = pool.submit(client.request, "hang")
                deadline = time.monotonic() + 1.0
                while client.pid is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                first_pid = client.pid
                time.sleep(0.03)

                with self.assertRaises(DesktopError) as crashed:
                    client.request("crash")
                with self.assertRaises(DesktopError) as pending:
                    waiting.result()

            self.assertIsNone(client.instance_id)
            response = client.request("ping", {"nonce": "fresh-process"})

            self.assertEqual(crashed.exception.code, ErrorCode.NATIVE_CRASHED)
            self.assertEqual(pending.exception.code, ErrorCode.NATIVE_CRASHED)
            self.assertNotEqual(client.pid, first_pid)
            self.assertEqual(response.result["nonce"], "fresh-process")

    def test_invalid_frame_and_protocol_version_are_distinct_typed_errors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            invalid = self.make_client(root / "invalid", mode="invalid_frame")
            with self.assertRaises(DesktopError) as invalid_error:
                invalid.request("invalid")
            invalid.close()

            mismatch = self.make_client(root / "mismatch", mode="bad_major")
            with self.assertRaises(DesktopError) as mismatch_error:
                mismatch.request("ping")

        self.assertEqual(invalid_error.exception.code, ErrorCode.INVALID_FRAME)
        self.assertEqual(
            mismatch_error.exception.code,
            ErrorCode.PROTOCOL_MISMATCH,
        )

    def test_invalid_initialize_result_is_rejected_and_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(Path(raw), mode="invalid_initialize")
            with self.assertRaises(DesktopError) as raised:
                client.request("ping")

        self.assertEqual(raised.exception.code, ErrorCode.INVALID_FRAME)
        self.assertFalse(client.is_running)

    def test_invalid_local_request_does_not_stop_a_healthy_helper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(Path(raw))
            client.request("ping")
            pid = client.pid

            with self.assertRaises(DesktopError) as raised:
                client.request("ping", {"invalid": object()})
            response = client.request("ping", {"nonce": "still-running"})

        self.assertEqual(raised.exception.code, ErrorCode.INVALID_FRAME)
        self.assertEqual(client.pid, pid)
        self.assertEqual(response.result["nonce"], "still-running")

    def test_pending_limit_returns_busy_without_writing_an_extra_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(
                Path(raw),
                mode="timeout",
                default_timeout=1.0,
                pending_limit=2,
            )
            pool = ThreadPoolExecutor(max_workers=2)
            first = pool.submit(client.request, "slow")
            second = pool.submit(client.request, "slow")
            deadline = time.monotonic() + 1.0
            while client.stderr_tail.count("S") < 2 and time.monotonic() < deadline:
                time.sleep(0.01)

            with self.assertRaises(DesktopError) as raised:
                client.request("third")
            client.close()
            for future in (first, second):
                with self.assertRaises(DesktopError):
                    future.result()
            pool.shutdown()

        self.assertEqual(raised.exception.code, ErrorCode.BUSY)

    def test_shutdown_escalates_and_reaps_an_uncooperative_helper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(
                Path(raw),
                mode="ignore_shutdown",
                shutdown_timeout=0.05,
            )
            client.request("ping")
            pid = client.pid

            started = time.monotonic()
            client.close()
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.5)
            self.assertFalse(client.is_running)
            assert pid is not None
            with self.assertRaises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)

    def test_ipc_descriptors_and_parent_secrets_are_not_inherited(self) -> None:
        marker = "contract-secret-must-not-cross-native-boundary"
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(os.environ, {"PCBRIDGE_PASSWORD": marker}):
                client = self.make_client(Path(raw))
                secret = client.request(
                    "environment",
                    {"name": "PCBRIDGE_PASSWORD"},
                )
                pid = client.pid
                assert pid is not None
                helper_pipes = {
                    os.readlink(f"/proc/{pid}/fd/{number}") for number in range(3)
                }
                probe = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import json,os; "
                        "print(json.dumps([os.readlink('/proc/self/fd/'+n) "
                        "for n in os.listdir('/proc/self/fd') "
                        "if n not in ('0','1','2') "
                        "and os.path.exists('/proc/self/fd/'+n)]))",
                    ],
                    check=True,
                    close_fds=False,
                    capture_output=True,
                    text=True,
                )

            inherited = set(json.loads(probe.stdout))
            self.assertIsNone(secret.result["value"])
            self.assertTrue(helper_pipes.isdisjoint(inherited))

    def test_missing_helper_is_typed_and_does_not_break_the_parent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            client = NativeClient(
                root / "missing-helper",
                state_dir=root / "state",
                runtime_dir=root / "runtime",
            )
            with self.assertRaises(DesktopError) as raised:
                client.request("ping")
            client.close()

        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_NOT_FOUND)

    def test_structured_helper_error_maps_to_desktop_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            client = self.make_client(Path(raw))
            with self.assertRaises(DesktopError) as raised:
                client.request("remote_error")

        self.assertEqual(raised.exception.code, ErrorCode.PERMISSION_REQUIRED)
        self.assertEqual(raised.exception.category.value, "permission")
        self.assertEqual(raised.exception.backend, "pcbridge-native")


if __name__ == "__main__":
    unittest.main()
