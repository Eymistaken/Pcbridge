"""A small asyncio CDP client over the keeper's Unix socket.

Messages are JSON separated by NUL bytes, as on `--remote-debugging-pipe`
(the keeper relays them unchanged). Calls are matched to replies by id;
events are dropped unless a waiter asked for them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

# A full-page screenshot comes back as one base64 message of several MB.
READ_LIMIT = 64 << 20


class CDPError(Exception):
    """The browser answered a call with an error."""

    def __init__(self, method: str, error: dict) -> None:
        self.method = method
        self.code = error.get("code")
        self.text = str(error.get("message", error))
        super().__init__(f"{method}: {self.text}")


class Disconnected(Exception):
    """The keeper's socket closed: the browser is gone."""


class Connection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self.loop = asyncio.get_running_loop()
        self.next_id = 1000
        self.pending: dict[int, asyncio.Future] = {}
        self.waiters: list[tuple[str, str | None, asyncio.Future]] = []
        # Lasting listeners (a screencast's frames): (method, session, queue).
        self.listeners: list[tuple[str, str | None, asyncio.Queue]] = []
        self.closed = False
        self.task = self.loop.create_task(self._read())

    @classmethod
    async def open(cls, path: str, timeout: float = 5.0) -> "Connection":
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path, limit=READ_LIMIT), timeout)
        return cls(reader, writer)

    @property
    def usable(self) -> bool:
        """Open, and bound to the loop that is running now."""
        try:
            return not self.closed and asyncio.get_running_loop() is self.loop
        except RuntimeError:
            return False

    async def _read(self) -> None:
        try:
            while True:
                raw = await self.reader.readuntil(b"\0")
                msg = json.loads(raw[:-1])
                mid = msg.get("id")
                if mid is not None:
                    fut = self.pending.pop(mid, None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    continue
                method, session = msg.get("method"), msg.get("sessionId")
                for want, want_session, queue in list(self.listeners):
                    if want == method and want_session in (None, session):
                        queue.put_nowait(msg.get("params") or {})
                for entry in list(self.waiters):
                    want, want_session, fut = entry
                    if want == method and want_session in (None, session) and not fut.done():
                        fut.set_result(msg.get("params") or {})
                        self.waiters.remove(entry)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, asyncio.LimitOverrunError):
            pass
        finally:
            self.closed = True
            for fut in list(self.pending.values()) + [w[2] for w in self.waiters]:
                if not fut.done():
                    fut.set_exception(Disconnected("the browser closed"))
            self.pending.clear()
            self.waiters.clear()
            for _m, _s, queue in self.listeners:
                queue.put_nowait(None)

    async def call(self, method: str, params: dict | None = None, session: str | None = None,
                   timeout: float = 30.0) -> dict:
        if self.closed:
            raise Disconnected("the browser closed")
        self.next_id += 1
        mid = self.next_id
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut = self.loop.create_future()
        self.pending[mid] = fut
        self.writer.write(json.dumps(msg).encode() + b"\0")
        try:
            await self.writer.drain()
            reply = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"{method} got no answer within {timeout:g} s") from None
        except ConnectionError as exc:
            raise Disconnected(str(exc)) from None
        finally:
            self.pending.pop(mid, None)
        if "error" in reply:
            raise CDPError(method, reply["error"])
        return reply.get("result") or {}

    def expect(self, method: str, session: str | None = None) -> asyncio.Future:
        """A future for the next `method` event (of `session`); register it
        before the call that causes the event."""
        fut = self.loop.create_future()
        self.waiters.append((method, session, fut))
        return fut

    def subscribe(self, method: str, session: str | None = None) -> asyncio.Queue:
        """Every `method` event (of `session`) from now on, until unsubscribe.
        The queue gets None when the browser goes away."""
        queue: asyncio.Queue = asyncio.Queue()
        self.listeners.append((method, session, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.listeners = [entry for entry in self.listeners if entry[2] is not queue]

    def forget(self, fut: asyncio.Future) -> None:
        self.waiters = [w for w in self.waiters if w[2] is not fut]
        if not fut.done():
            fut.cancel()

    async def close(self) -> None:
        self.closed = True
        self.task.cancel()
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, RuntimeError):
            pass
