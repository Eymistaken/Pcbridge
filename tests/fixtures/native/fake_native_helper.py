#!/usr/bin/python3
"""Deterministic executable fixture for the Python native supervisor tests."""

from __future__ import annotations

import json
import os
import signal
import sys
from typing import Any, BinaryIO


PROTOCOL = {"major": 1, "minor": 0}


def read_exact(stream: BinaryIO, size: int) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None if not data else bytes(data)
        data.extend(chunk)
    return bytes(data)


def read_frame() -> tuple[dict[str, Any], bytes] | None:
    prefix = read_exact(sys.stdin.buffer, 4)
    if prefix is None:
        return None
    header_size = int.from_bytes(prefix, "big")
    raw_header = read_exact(sys.stdin.buffer, header_size)
    if raw_header is None or len(raw_header) != header_size:
        raise SystemExit(2)
    header = json.loads(raw_header)
    binary_size = header["binary_len"]
    binary = read_exact(sys.stdin.buffer, binary_size)
    if binary is None or len(binary) != binary_size:
        raise SystemExit(2)
    return header, binary


def write_response(
    request_id: str,
    *,
    result: Any | None = None,
    error: dict[str, Any] | None = None,
    protocol: dict[str, int] | None = None,
) -> None:
    header: dict[str, Any] = {
        "protocol": protocol or PROTOCOL,
        "id": request_id,
        "binary_len": 0,
    }
    if error is None:
        header["result"] = result
    else:
        header["error"] = error
    raw = json.dumps(header, separators=(",", ":")).encode()
    sys.stdout.buffer.write(len(raw).to_bytes(4, "big"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main() -> None:
    mode = os.environ.get("PCBRIDGE_FAKE_NATIVE_MODE", "normal")
    held: list[dict[str, Any]] = []
    if mode == "ignore_shutdown":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    first = read_frame()
    if first is None:
        return
    initialize, _ = first
    initialize_result = {
        "instance_id": "fake-native-instance",
        "native_version": "fixture",
        "platform": "test",
        "features": ["test.fixture"],
    }
    if mode == "invalid_initialize":
        initialize_result["features"] = "test.fixture"
    write_response(initialize["id"], result=initialize_result)
    if mode == "stop_reading":
        signal.pause()

    while True:
        frame = read_frame()
        if frame is None:
            return
        request, _ = frame
        request_id = request["id"]
        method = request["method"]

        if mode == "crash":
            if method == "hang":
                held.append(request)
                continue
            if method == "crash":
                os._exit(17)
        if mode == "invalid_frame" and method == "invalid":
            sys.stdout.buffer.write((65537).to_bytes(4, "big"))
            sys.stdout.buffer.flush()
            return
        if mode == "bad_major" and method == "ping":
            write_response(
                request_id,
                result={"pong": True},
                protocol={"major": 2, "minor": 0},
            )
            continue
        if mode == "stderr_flood" and method == "ping":
            sys.stderr.buffer.write(b"diagnostic-" * 9000)
            sys.stderr.buffer.flush()
        if mode == "reverse" and method == "ping":
            held.append(request)
            if len(held) == 2:
                for pending in reversed(held):
                    write_response(
                        pending["id"],
                        result={"pong": True, "nonce": pending["params"]["nonce"]},
                    )
                held.clear()
            continue
        if mode == "timeout" and method == "slow":
            sys.stderr.buffer.write(b"S")
            sys.stderr.buffer.flush()
            held.append(request)
            continue
        if method == "cancel":
            write_response(
                request_id,
                result={
                    "target_id": request["params"]["target_id"],
                    "canceled": bool(held),
                },
            )
            held.clear()
            continue
        if method == "shutdown":
            if mode == "ignore_shutdown":
                continue
            write_response(request_id, result={"shutdown": True})
            return
        if method == "ping":
            if mode == "timeout" and held:
                continue
            result = {"pong": True}
            if "nonce" in request["params"]:
                result["nonce"] = request["params"]["nonce"]
            write_response(request_id, result=result)
            continue
        if method == "remote_error":
            write_response(
                request_id,
                error={
                    "code": "PERMISSION_REQUIRED",
                    "message": "fixture permission is required",
                    "retryable": False,
                    "category": "permission",
                },
            )
            continue
        if method == "environment":
            write_response(
                request_id,
                result={"value": os.environ.get(request["params"]["name"])},
            )
            continue
        write_response(request_id, result={"method": method})


if __name__ == "__main__":
    main()
