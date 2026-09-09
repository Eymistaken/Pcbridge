"""Versioned framed transport shared by the Python native supervisor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, BinaryIO


PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
MAX_HEADER_BYTES = 64 * 1024
MAX_BINARY_BYTES = 128 * 1024 * 1024


class NativeFrameError(ValueError):
    """The helper sent or was asked to send an invalid frame."""


class NativeProtocolMismatch(NativeFrameError):
    """The helper speaks a different major or negotiated minor version."""


@dataclass(frozen=True)
class NativeFrame:
    header: dict[str, Any]
    binary: bytes


@dataclass(frozen=True)
class NativeResponse:
    """A response validated at the private transport boundary."""

    request_id: str
    result: Any
    binary: bytes
    error: dict[str, Any] | None = None


def _read_exact(
    stream: BinaryIO,
    size: int,
    *,
    section: str,
    allow_initial_eof: bool = False,
) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            chunk = stream.read(size - len(chunks))
        except OSError as error:
            raise NativeFrameError(f"I/O error while reading {section}") from error
        if not chunk:
            if allow_initial_eof and not chunks:
                return None
            raise NativeFrameError(
                f"frame ended during {section}: expected {size} bytes, "
                f"received {len(chunks)}"
            )
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(stream: BinaryIO) -> NativeFrame | None:
    """Read one complete frame, or return ``None`` for clean initial EOF."""

    prefix = _read_exact(
        stream,
        4,
        section="header length",
        allow_initial_eof=True,
    )
    if prefix is None:
        return None

    header_size = int.from_bytes(prefix, "big", signed=False)
    if header_size == 0:
        raise NativeFrameError("header length must be greater than zero")
    if header_size > MAX_HEADER_BYTES:
        raise NativeFrameError(
            f"header length exceeds the {MAX_HEADER_BYTES}-byte limit"
        )

    raw_header = _read_exact(stream, header_size, section="JSON header")
    assert raw_header is not None
    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativeFrameError("frame header is not valid UTF-8 JSON") from error
    if not isinstance(header, dict):
        raise NativeFrameError("frame header must be a JSON object")

    binary_size = header.get("binary_len")
    if (
        isinstance(binary_size, bool)
        or not isinstance(binary_size, int)
        or binary_size < 0
    ):
        raise NativeFrameError(
            "frame header must include an unsigned integer binary_len"
        )
    if binary_size > MAX_BINARY_BYTES:
        raise NativeFrameError(
            f"binary length exceeds the {MAX_BINARY_BYTES}-byte limit"
        )

    binary = _read_exact(stream, binary_size, section="binary payload")
    assert binary is not None
    return NativeFrame(header=header, binary=binary)


def encode_frame(
    header: dict[str, Any],
    binary: bytes = b"",
) -> bytes:
    """Validate and encode one complete frame without touching the stream."""

    if not isinstance(binary, bytes):
        raise NativeFrameError("binary payload must be bytes")
    if len(binary) > MAX_BINARY_BYTES:
        raise NativeFrameError(
            f"binary length exceeds the {MAX_BINARY_BYTES}-byte limit"
        )
    declared = header.get("binary_len")
    if (
        isinstance(declared, bool)
        or not isinstance(declared, int)
        or declared != len(binary)
    ):
        raise NativeFrameError("declared binary length does not match the payload")

    try:
        raw_header = json.dumps(
            header,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise NativeFrameError("frame header is not JSON serializable") from error
    if not raw_header:
        raise NativeFrameError("header length must be greater than zero")
    if len(raw_header) > MAX_HEADER_BYTES:
        raise NativeFrameError(
            f"header length exceeds the {MAX_HEADER_BYTES}-byte limit"
        )

    return len(raw_header).to_bytes(4, "big") + raw_header + binary


def write_encoded_frame(stream: BinaryIO, encoded: bytes) -> None:
    """Write a validated encoded frame while handling short writes."""

    view = memoryview(encoded)
    written = 0
    while written < len(view):
        try:
            count = stream.write(view[written:])
        except OSError as error:
            raise NativeFrameError("I/O error while writing frame") from error
        if count is None or count <= 0:
            raise NativeFrameError("stream stopped during frame write")
        written += count
    try:
        stream.flush()
    except OSError as error:
        raise NativeFrameError("I/O error while flushing frame") from error


def write_frame(
    stream: BinaryIO,
    header: dict[str, Any],
    binary: bytes = b"",
) -> None:
    """Validate, encode, and write one complete frame."""

    write_encoded_frame(stream, encode_frame(header, binary))


def decode_response(frame: NativeFrame) -> NativeResponse:
    """Validate the versioned response envelope before dispatch by ID."""

    header = frame.header
    protocol = header.get("protocol")
    if not isinstance(protocol, dict):
        raise NativeFrameError("response protocol must be an object")
    major = protocol.get("major")
    minor = protocol.get("minor")
    if (
        isinstance(major, bool)
        or isinstance(minor, bool)
        or not isinstance(major, int)
        or not isinstance(minor, int)
    ):
        raise NativeFrameError("response protocol versions must be integers")
    if major != PROTOCOL_MAJOR or minor != PROTOCOL_MINOR:
        raise NativeProtocolMismatch("response protocol version does not match")

    request_id = header.get("id")
    if not isinstance(request_id, str) or not request_id:
        raise NativeFrameError("response id must be a nonempty string")
    has_result = "result" in header
    has_error = "error" in header
    if has_result == has_error:
        raise NativeFrameError("response must contain exactly one result or error")
    error = header.get("error") if has_error else None
    if error is not None:
        if not isinstance(error, dict):
            raise NativeFrameError("response error must be an object")
        if not isinstance(error.get("code"), str) or not isinstance(
            error.get("message"), str
        ):
            raise NativeFrameError("response error fields are invalid")
        if not isinstance(error.get("retryable"), bool) or not isinstance(
            error.get("category"), str
        ):
            raise NativeFrameError("response error fields are invalid")
    return NativeResponse(
        request_id=request_id,
        result=header.get("result"),
        binary=frame.binary,
        error=error,
    )


__all__ = [
    "MAX_BINARY_BYTES",
    "MAX_HEADER_BYTES",
    "NativeFrame",
    "NativeFrameError",
    "NativeProtocolMismatch",
    "NativeResponse",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "decode_response",
    "encode_frame",
    "read_frame",
    "write_encoded_frame",
    "write_frame",
]
