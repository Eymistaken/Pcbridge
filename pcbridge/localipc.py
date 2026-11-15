"""The local channel between `pcbridge stdio` and the daemon.

Linux: a Unix socket in the private runtime directory (mode 0600 in a 0700
directory), the code pcbridge always had.

Windows: CPython has no AF_UNIX there (measured), and anyio serves no named
pipes, so the daemon listens on 127.0.0.1 at a port the system picks and
writes `endpoint.json` next to where the socket would be: the port and a
32-byte secret made at each start. The runtime directory has a DACL for the
user only, so the file is the user's alone, as the socket file is on Linux.
Before any MCP byte, both sides prove they hold the secret without sending
it:

    client -> {"pcbridge_auth": {"v": 1, "nonce": C}}
    daemon -> {"pcbridge_auth": {"nonce": S, "proof": HMAC(secret, "daemon" C S)}}
    client    checks the proof; a process squatting the port learns nothing
    client -> {"pcbridge_auth": {"proof": HMAC(secret, "client" C S)}}
    daemon    checks it, then the relay preamble follows as on Linux

Standard library only: the relay imports this on every client's start.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

IS_WINDOWS = sys.platform == "win32"
ENDPOINT_FILE = "endpoint.json"
AUTH_VERSION = 1
_MAX_AUTH_LINE = 1024


class AuthError(OSError):
    """The other side does not hold the endpoint's secret."""


def _proof(secret: bytes, role: bytes, client_nonce: str, server_nonce: str) -> str:
    message = role + b"\0" + client_nonce.encode() + b"\0" + server_nonce.encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def read_endpoint(path: Path) -> dict[str, Any]:
    """The daemon's port, secret and pid from endpoint.json (Windows)."""
    with open(path, "rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise OSError(f"{path} is too large to be an endpoint file")
    try:
        data = json.loads(raw)
        port = int(data["port"])
        secret = bytes.fromhex(data["secret"])
    except (ValueError, KeyError, TypeError) as exc:
        raise OSError(f"{path} is not a valid endpoint file") from exc
    if not 0 < port < 65536 or len(secret) < 32:
        raise OSError(f"{path} is not a valid endpoint file")
    return {"port": port, "secret": secret, "pid": data.get("pid")}


def _readline(sock: socket.socket) -> bytes:
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
        if len(data) > _MAX_AUTH_LINE:
            raise AuthError("the authentication line is too long")
    return data


def client_authenticate(sock: socket.socket, secret: bytes) -> None:
    """Prove the secret to the daemon after the daemon proved it first."""
    client_nonce = secrets.token_hex(16)
    sock.sendall(json.dumps({"pcbridge_auth": {"v": AUTH_VERSION, "nonce": client_nonce}}).encode() + b"\n")
    try:
        reply = json.loads(_readline(sock))["pcbridge_auth"]
        server_nonce = str(reply["nonce"])
        proof = str(reply["proof"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError("the endpoint did not answer the authentication") from exc
    if not hmac.compare_digest(proof, _proof(secret, b"daemon", client_nonce, server_nonce)):
        raise AuthError("the process on the endpoint's port is not the pcbridge daemon")
    answer = _proof(secret, b"client", client_nonce, server_nonce)
    sock.sendall(json.dumps({"pcbridge_auth": {"proof": answer}}).encode() + b"\n")


async def server_authenticate(readline: Callable[[], Awaitable[bytes]],
                              send: Callable[[bytes], Awaitable[None]], secret: bytes) -> bool:
    """The daemon's side of the exchange; False means close the connection."""
    try:
        hello = json.loads(await readline())["pcbridge_auth"]
        client_nonce = str(hello["nonce"])
        if int(hello.get("v", 0)) != AUTH_VERSION or not 16 <= len(client_nonce) <= 128:
            return False
    except (ValueError, KeyError, TypeError):
        return False
    server_nonce = secrets.token_hex(16)
    proof = _proof(secret, b"daemon", client_nonce, server_nonce)
    await send(json.dumps({"pcbridge_auth": {"nonce": server_nonce, "proof": proof}}).encode() + b"\n")
    try:
        answer = str(json.loads(await readline())["pcbridge_auth"]["proof"])
    except (ValueError, KeyError, TypeError):
        return False
    return hmac.compare_digest(answer, _proof(secret, b"client", client_nonce, server_nonce))


def server_authenticate_sync(sock: socket.socket, secret: bytes) -> bool:
    """`server_authenticate` for a blocking socket (the terminal host)."""
    try:
        hello = json.loads(_readline(sock))["pcbridge_auth"]
        client_nonce = str(hello["nonce"])
        if int(hello.get("v", 0)) != AUTH_VERSION or not 16 <= len(client_nonce) <= 128:
            return False
        server_nonce = secrets.token_hex(16)
        proof = _proof(secret, b"daemon", client_nonce, server_nonce)
        sock.sendall(json.dumps({"pcbridge_auth": {"nonce": server_nonce, "proof": proof}}).encode() + b"\n")
        answer = str(json.loads(_readline(sock))["pcbridge_auth"]["proof"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return hmac.compare_digest(answer, _proof(secret, b"client", client_nonce, server_nonce))


def connect(path: Path, timeout: float | None = None) -> socket.socket:
    """A connected, authenticated socket to the daemon; OSError when there is none.

    `path` is the socket on Linux and endpoint.json on Windows."""
    if not IS_WINDOWS:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
        except OSError:
            sock.close()
            raise
        sock.settimeout(None)
        return sock
    endpoint = read_endpoint(path)
    sock = socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=timeout or 5.0)
    try:
        sock.settimeout(timeout or 5.0)
        client_authenticate(sock, endpoint["secret"])
        sock.settimeout(None)
    except (OSError, ValueError):
        sock.close()
        raise
    return sock


def bind_endpoint(path: Path) -> tuple[socket.socket, bytes] | None:
    """Windows: listen on 127.0.0.1 and publish endpoint.json. None when a
    live daemon already answers on the published endpoint."""
    try:
        connect(path, timeout=0.5).close()
        return None
    except OSError:
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Exclusive: another process cannot bind the same port alongside us.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    secret = secrets.token_bytes(32)
    payload = json.dumps({"port": sock.getsockname()[1], "secret": secret.hex(), "pid": os.getpid()})
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(payload)
    os.replace(temporary, path)
    return sock, secret


def remove_endpoint(path: Path) -> None:
    """Remove endpoint.json if this process published it."""
    try:
        if read_endpoint(path).get("pid") == os.getpid():
            path.unlink()
    except OSError:
        pass
