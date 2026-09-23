"""The resident pcbridge server (`pcbridge serve`).

One process owns everything that used to be duplicated in every client's
stdio server: the native helper, the screencast session, the desktop grant
bookkeeping, the execution lock and the jobs. It serves:

    * local MCP sessions on a unix socket, one connection per session, raw
      MCP JSON-RPC in stdio framing (one message per line). The thin relay
      (pcbridge.relay) connects every local client to it;
    * the HTTP + OAuth endpoint on 127.0.0.1:<port> for remote clients and
      the PcBridgeDesktop app, exactly as before.

Local sessions skip OAuth, as stdio always did: the socket is mode 0600 in a
0700 directory, so the boundary is the same user account. Each connection
starts with one preamble line from the relay (version, client pid, cwd and an
allow-listed environment subset) and one answer line from the daemon; after
that the bytes are MCP.

The socket normally comes from systemd (pcbridge.socket, socket activation,
LISTEN_FDS). `pcbridge serve` binds it itself when started by hand.

When a newer pcbridge is installed, the install writes a new version stamp;
the daemon notices and restarts itself, but only while idle: no running job,
no open desktop grant, no call in flight. The relays reconnect on their own.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from anyio.abc import SocketListener, SocketStream

from . import __version__
from . import paths as pathslib
from . import sessionctx

log = logging.getLogger("pcbridge")

RELAY_PROTOCOL = 1
RESTART_EXIT_CODE = 75  # pcbridge.service: RestartForceExitStatus=75
# How often the version stamp is read; tests shorten it.
STAMP_POLL_S = float(os.environ.get("PCBRIDGE_STAMP_POLL_S") or 5.0)
# status.json for the GNOME extension's panel indicator (Step 7 of 2.0).
STATUS_FILE = "status.json"
STATUS_POLL_S = float(os.environ.get("PCBRIDGE_STATUS_POLL_S") or 5.0)
STATUS_SCHEMA = 1
THREAD_LIMIT = 128
MAX_LINE = 64 * 1024 * 1024


@dataclass
class DaemonState:
    """What `system_status` and `pcbridge status` report about this process."""

    started: float = field(default_factory=time.time)
    socket_path: str = ""
    socket_source: str = ""  # "systemd" | "bound" | ""
    http: str = ""
    sessions: dict[str, dict] = field(default_factory=dict)
    in_flight: int = 0
    restart_pending: str = ""


STATE: DaemonState | None = None


def describe() -> str:
    """One line for system_status: how this server process runs."""
    mode = os.environ.get("PCBRIDGE_MODE", "")
    if STATE is None:
        return mode or "in-process (one server per client)"
    uptime = int(time.time() - STATE.started)
    parts = [f"daemon pid {os.getpid()}, version {__version__}, up {uptime} s"]
    parts.append(f"{len(STATE.sessions)} local session(s)")
    if STATE.socket_path:
        parts.append(f"socket {STATE.socket_path} ({STATE.socket_source})")
    if STATE.http:
        parts.append(f"http {STATE.http}")
    if STATE.restart_pending:
        parts.append(f"restart pending: {STATE.restart_pending}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Socket
# ---------------------------------------------------------------------------


def _systemd_socket() -> socket.socket | None:
    """The listening socket systemd passed us, if any (sd_listen_fds)."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    try:
        count = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        return None
    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(key, None)
    if count < 1:
        return None
    sock = socket.socket(fileno=3)
    if sock.family != socket.AF_UNIX:
        log.warning("systemd passed a non-unix socket; ignoring it")
        return None
    return sock


def _bind_socket(path: Path) -> socket.socket | None:
    """Bind the socket path ourselves, unless a live server already owns it."""
    if path.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(str(path))
            log.warning("another pcbridge server already listens on %s; not binding it", path)
            return None
        except OSError:
            path.unlink(missing_ok=True)  # stale: nobody listens
        finally:
            probe.close()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(str(path))
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    sock.listen(64)
    return sock


# ---------------------------------------------------------------------------
# One local MCP session
# ---------------------------------------------------------------------------


class _Lines:
    """Newline-framed reader over an anyio byte stream."""

    def __init__(self, stream: SocketStream) -> None:
        self.stream = stream
        self.buf = bytearray()

    async def readline(self) -> bytes:
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line = bytes(self.buf[: i + 1])
                del self.buf[: i + 1]
                return line
            if len(self.buf) > MAX_LINE:
                raise ValueError("line too long")
            try:
                chunk = await self.stream.receive(65536)
            except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
                chunk = b""
            if not chunk:
                rest = bytes(self.buf)
                self.buf.clear()
                return rest
            self.buf += chunk


class Daemon:
    def __init__(self, mcp, cfg, jm) -> None:
        self.mcp = mcp
        self.cfg = cfg
        self.jm = jm
        self.runtime = getattr(mcp, "_pcbridge_desktop_runtime", None)
        self.counter = 0
        self.state = DaemonState()
        self._last_status: dict[str, Any] | None = None

    # -- session -----------------------------------------------------------
    async def handle(self, stream: SocketStream) -> None:
        from mcp import types
        from mcp.server.lowlevel.server import NotificationOptions
        from mcp.shared.message import SessionMessage
        from fastmcp.server.context import reset_transport, set_transport

        self.counter += 1
        key = f"sock:{self.counter}"
        lines = _Lines(stream)
        try:
            with anyio.fail_after(10):
                first = await lines.readline()
            pre = json.loads(first)["pcbridge_relay"]
        except (TimeoutError, ValueError, KeyError, TypeError):
            log.warning("local connection without a valid relay preamble; closed")
            await stream.aclose()
            return
        reply = {"pcbridge_daemon": {"version": __version__, "protocol": RELAY_PROTOCOL, "pid": os.getpid()}}
        await stream.send(json.dumps(reply).encode() + b"\n")
        if int(pre.get("protocol", 0)) != RELAY_PROTOCOL:
            await stream.aclose()
            return

        info = sessionctx.SessionInfo(
            key=key,
            transport="stdio",
            pid=pre.get("pid") if isinstance(pre.get("pid"), int) else None,
            cwd=str(pre.get("cwd") or "") or None,
            env=sessionctx.filter_env(pre.get("env") or {}),
            relay_version=str(pre.get("version") or ""),
        )
        record = {"pid": info.pid, "client": "", "since": time.time()}
        self.state.sessions[key] = record
        in_flight: set[str] = set()

        read_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_reader = anyio.create_memory_object_stream(0)

        async def reader() -> None:
            async with read_writer:
                while True:
                    line = await lines.readline()
                    if not line:
                        return
                    if not line.strip():
                        continue
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
                    except Exception as exc:  # noqa: BLE001 - the session reports it
                        await read_writer.send(exc)
                        continue
                    root = message.root
                    method = getattr(root, "method", None)
                    rid = getattr(root, "id", None)
                    if method == "initialize":
                        params = getattr(root, "params", None) or {}
                        name = ((params.get("clientInfo") or {}).get("name") or "")[:60]
                        info.client = name
                        record["client"] = name
                    if method is not None and rid is not None:
                        in_flight.add(json.dumps(rid))
                        self.state.in_flight += 1
                    await read_writer.send(SessionMessage(message))

        async def writer() -> None:
            async with write_reader:
                async for session_message in write_reader:
                    root = session_message.message.root
                    if getattr(root, "method", None) is None:
                        rid = json.dumps(getattr(root, "id", None))
                        if rid in in_flight:
                            in_flight.discard(rid)
                            self.state.in_flight -= 1
                    data = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    await stream.send(data.encode() + b"\n")

        session_token = sessionctx.bind(info)
        transport_token = set_transport("stdio")
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(reader)
                tg.start_soon(writer)
                await self.mcp._mcp_server.run(
                    read_stream,
                    write_stream,
                    self.mcp._mcp_server.create_initialization_options(
                        notification_options=NotificationOptions(tools_changed=True),
                    ),
                    stateless=False,
                )
                tg.cancel_scope.cancel()
        except Exception:  # noqa: BLE001 - one broken session must not stop the daemon
            log.exception("local session %s ended with an error", key)
        finally:
            reset_transport(transport_token)
            sessionctx.reset(session_token)
            self.state.in_flight -= len(in_flight)
            self.state.sessions.pop(key, None)
            await anyio.to_thread.run_sync(self._session_ended, key)
            try:
                await stream.aclose()
            except Exception:  # noqa: BLE001
                pass

    def _session_ended(self, key: str) -> None:
        sessionctx.session_ended(key)
        # A client that went away while holding a key or button down cannot
        # release it any more. Before 2.0 its own server process exited and
        # took the virtual devices with it; now let go explicitly.
        if self.runtime is None:
            return
        try:
            held = self.runtime.input_provider.held()
            if held and not self.state.sessions:
                self.runtime.input_provider.release_all()
                log.info("released held input after the last local session ended: %s", held)
        except Exception as exc:  # noqa: BLE001
            log.warning("held input check after session end failed: %s", exc)

    # -- idle restart --------------------------------------------------------
    def _busy_reason(self) -> str:
        if self.state.in_flight > 0:
            return f"{self.state.in_flight} call(s) in flight"
        try:
            running = self.jm.list_jobs(limit=200, only_running=True) if self.jm else []
        except Exception:  # noqa: BLE001
            running = []
        if running:
            return f"{len(running)} job(s) running"
        gate = getattr(self.runtime, "gate", None)
        try:
            if gate is not None and gate.is_unlocked():
                return "the desktop grant is open"
        except Exception:  # noqa: BLE001
            pass
        return ""

    # -- status for the panel indicator -------------------------------------
    def _status(self, cfg: Any, running: bool = True) -> dict[str, Any]:
        """What the extension shows. Read-only facts; nothing here is a switch.

        The grant is NOT copied: the extension reads desktop_unlock.json
        itself, as it always has, so there is a single source for it.
        """
        from .cli import install as inst

        jobs = 0
        if running and self.jm is not None:
            try:
                jobs = len(self.jm.list_jobs(limit=200, only_running=True))
            except Exception:  # noqa: BLE001
                jobs = 0
        remote = None
        if running and shutil.which("tailscale"):
            try:
                out = subprocess.run(["tailscale", "funnel", "status"], capture_output=True,
                                     text=True, timeout=5).stdout
                remote = f":{cfg.port}" in out
            except (OSError, subprocess.SubprocessError):
                remote = None
        try:
            cli = str(inst.launcher_path())
        except Exception:  # noqa: BLE001
            cli = ""
        return {
            "schema": STATUS_SCHEMA,
            "version": __version__,
            "daemon": "running" if running else "stopped",
            "pid": os.getpid() if running else 0,
            "jobs_running": jobs,
            "remote": remote,
            "cli": cli,
        }

    def write_status(self, cfg: Any, running: bool = True) -> bool:
        """Write status.json atomically when it changed; True if written."""
        data = self._status(cfg, running)
        if data == self._last_status:
            return False
        path = Path(cfg.state_dir) / STATUS_FILE
        tmp = path.with_name(f".{STATUS_FILE}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not write %s: %s", path, exc)
            return False
        self._last_status = data
        return True

    async def watch_status(self, cfg: Any) -> None:
        while True:
            await anyio.to_thread.run_sync(self.write_status, cfg)
            await anyio.sleep(STATUS_POLL_S)

    async def watch_stamp(self, done: anyio.Event) -> None:
        path = pathslib.data_home() / "version-stamp"

        def read() -> str:
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""

        initial = read()
        while True:
            await anyio.sleep(STAMP_POLL_S)
            current = read()
            if not current or current == initial:
                continue
            reason = await anyio.to_thread.run_sync(self._busy_reason)
            if reason:
                if self.state.restart_pending != reason:
                    log.info("a newer pcbridge is installed; restart deferred: %s", reason)
                self.state.restart_pending = reason
                continue
            log.info("a newer pcbridge is installed (%s); restarting while idle", current)
            done.set()
            return


# ---------------------------------------------------------------------------


async def _serve(args: argparse.Namespace, bind_socket: bool) -> int:
    global STATE
    from fastmcp.server.context import set_transport

    from . import app as applib
    from .config import load_config
    from .desktop import session as sessionlib

    cfg = load_config(args.config)
    for warning in cfg.warnings:
        log.warning("config: %s", warning)
    fixed = sessionlib.ensure_session_env()
    if fixed:
        log.warning("Session environment was incomplete; repaired: %s", ", ".join(fixed))

    mcp, _provider = applib.build_app(cfg, transport="http")
    runtime = mcp._pcbridge_desktop_runtime
    jm = getattr(mcp, "_pcbridge_jobs", None)
    daemon = Daemon(mcp, cfg, jm)
    STATE = daemon.state

    # Every tool is synchronous and runs in anyio's worker threads, shared by
    # all sessions; some hold a thread for up to 110 s.
    anyio.to_thread.current_default_thread_limiter().total_tokens = THREAD_LIMIT

    listener: SocketListener | None = None
    sock = _systemd_socket()
    if sock is not None:
        daemon.state.socket_source = "systemd"
    elif bind_socket:
        try:
            path = Path(args.socket) if args.socket else pathslib.socket_path()
            if not args.socket:
                pathslib.runtime_dir(create=True)
            sock = _bind_socket(path)
            if sock is not None:
                daemon.state.socket_source = "bound"
        except (OSError, pathslib.RuntimeDirError) as exc:
            log.warning("local socket unavailable (%s); serving HTTP only", exc)
    if sock is not None:
        daemon.state.socket_path = str(sock.getsockname())
        # anyio's UNIX listener calls accept() directly: a blocking socket
        # would stall the whole event loop until the next client connects.
        sock.setblocking(False)
        listener = await SocketListener.from_socket(sock)
        # The jobs this daemon starts live in their own systemd scopes, so a
        # daemon restart or crash does not take them down.
        if jm is not None and os.environ.get("INVOCATION_ID"):
            jm.use_scopes = True

    port = args.port if args.port is not None else cfg.port
    restart = anyio.Event()
    code = 0
    log.info("pcbridge %s daemon: %s", __version__, describe())
    try:
        async with mcp._lifespan_manager():
            async with anyio.create_task_group() as tg:

                async def http() -> None:
                    if args.no_http:
                        return
                    set_transport("http")
                    daemon.state.http = f"http://{cfg.host}:{port}{cfg.mcp_path}"
                    try:
                        await mcp.run_http_async(transport="http", **applib.http_run_kwargs(cfg, port))
                    except (OSError, SystemExit) as exc:
                        daemon.state.http = ""
                        log.error("HTTP listener on %s:%s failed (%s); local sessions still work", cfg.host, port, exc)

                async def local() -> None:
                    assert listener is not None
                    async with listener:
                        await listener.serve(daemon.handle)

                async def stop_on_restart() -> None:
                    await restart.wait()
                    tg.cancel_scope.cancel()

                async def stop_on_signal() -> None:
                    # `systemctl stop` sends SIGTERM. Without this the process
                    # died on the spot: held keys were not released by us, the
                    # socket file stayed and status.json kept saying running.
                    with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
                        async for signum in signals:
                            log.info("received %s; shutting down", signal.Signals(signum).name)
                            tg.cancel_scope.cancel()
                            return

                tg.start_soon(http)
                if listener is not None:
                    tg.start_soon(local)
                tg.start_soon(daemon.watch_stamp, restart)
                tg.start_soon(daemon.watch_status, cfg)
                tg.start_soon(stop_on_restart)
                tg.start_soon(stop_on_signal)
        if restart.is_set():
            code = RESTART_EXIT_CODE
    finally:
        STATE = None
        daemon.write_status(cfg, running=False)
        if daemon.state.socket_source == "bound" and daemon.state.socket_path:
            try:
                p = Path(daemon.state.socket_path)
                if p.exists() and stat.S_ISSOCK(p.lstat().st_mode):
                    p.unlink()
            except OSError:
                pass
        runtime.close()
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pcbridge serve", description="Run the resident pcbridge server.")
    p.add_argument("-c", "--config", help="Path to config.toml.")
    p.add_argument("--socket", help="Unix socket to listen on (default: $XDG_RUNTIME_DIR/pcbridge/mcp.sock).")
    p.add_argument("--port", type=int, help="HTTP port (default: `port` in config.toml).")
    p.add_argument("--no-http", action="store_true", help="Serve only the local socket.")
    p.add_argument("--no-socket", action="store_true", help="Serve only HTTP.")
    return p


def main(argv: list[str] | None = None, bind_socket: bool = True) -> int:
    args = build_parser().parse_args(argv)
    if args.no_socket:
        bind_socket = False
    from .config import ConfigError, exit_on_config_error

    try:
        return anyio.run(_serve, args, bind_socket)
    except KeyboardInterrupt:
        return 0
    except ConfigError as exc:
        return exit_on_config_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
