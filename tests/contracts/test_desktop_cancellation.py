"""Canceled real MCP calls stop desktop input; no devices or GUI are used."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import dataclasses
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware

from pcbridge import app as serverlib  # Install the normal late-response compatibility shim.
from pcbridge import tools as toolslib
from pcbridge.desktop.execution import ExecutionLock
from pcbridge.desktop import batch as batchlib, ops as opslib
from pcbridge.desktop.runtime import DesktopRuntime
from pcbridge.desktop.safety import SafetyGate
from pcbridge.jobs import JobManager
from pcbridge.shots import ShotStore
from tests.contracts.test_batch_safety import ClosedCapture, FakeDesktopState
from tests.contracts.test_runtime_contract import make_config


class RequestObserver(Middleware):
    """Record the real request identity for the public MCP cancel API."""

    def __init__(self):
        self.ids = {}

    async def on_call_tool(self, context, call_next):
        self.ids[context.message.name] = context.fastmcp_context.request_context.request_id
        return await call_next(context)


class ObservedExecutionLock(ExecutionLock):
    """Observe blocked flock polling and worker unwind through test-only hooks."""

    def __init__(self, root):
        self.waiting = threading.Event()
        self.unwound = threading.Event()
        def observed_sleep(seconds):
            self.waiting.set()
            time.sleep(seconds)
        super().__init__(root, sleep=observed_sleep)

    @contextmanager
    def hold(self, *args, **kwargs):
        try:
            with super().hold(*args, **kwargs) as slot:
                yield slot
        finally:
            self.unwound.set()


class RecordingInput:
    """The provider boundary records input without accessing any OS device."""

    def __init__(self):
        self.down = threading.Event()
        self.released = threading.Event()
        self.keys = []
        self.sent = []
        self.release_times = []
        self.on_down = None
        self.on_release = None
        self.ensure_calls = 0
        self.moved = threading.Event()
        self.on_move = None
        self.clicks = []

    def available(self):
        return True, ""

    def ensure(self, **kwargs):
        self.ensure_calls += 1
        return 0.0

    def key_down(self, keys):
        self.keys.append(keys)
        self.down.set()
        if self.on_down:
            self.on_down()

    def key(self, keys):
        self.sent.append(keys)

    def move(self, x, y, **kwargs):
        self.moved.set()
        if self.on_move:
            self.on_move()
        return x, y

    def click(self, button, count=1, **kwargs):
        self.clicks.append((button, count))

    def held(self):
        return list(self.keys)

    def release_all(self):
        if self.on_release:
            self.on_release()
        released, self.keys = self.keys, []
        self.release_times.append(time.monotonic())
        self.released.set()
        return released

    def take_auto_released(self):
        return []

    def close(self):
        self.release_all()


class RecordingCapture(ClosedCapture):
    def to_global(self, x, y, **kwargs):
        return x, y


class DesktopCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cfg = make_config(self.root)
        self.cfg = dataclasses.replace(self.cfg, desktop=dataclasses.replace(
            self.cfg.desktop, unlock_notification=False, batch_check_focus=False,
            max_actions_per_second=0, unlock_idle_seconds=10))
        self.state = FakeDesktopState()
        self.gate = SafetyGate(self.cfg, state_provider=self.state)
        self.gate.unlock(1)
        self.input = RecordingInput()
        self.execution = ObservedExecutionLock(self.root)
        self.runtime = DesktopRuntime(capture_provider=RecordingCapture(),
            input_provider=self.input, accessibility_provider=object(),
            gate=self.gate, desktop_state_provider=self.state,
            execution_lock=self.execution)
        self.addCleanup(self.runtime.close)
        self.mcp = FastMCP("desktop-cancellation-contract")
        self.requests = RequestObserver()
        self.mcp.add_middleware(self.requests)
        @self.mcp.tool()
        def readiness() -> str:
            return "alive"

        toolslib.register(self.mcp, self.cfg,
            JobManager(self.cfg.jobs_dir, default_timeout=60), ShotStore(self.cfg),
            transport="stdio", runtime=self.runtime)
        self.focus_patch = mock.patch.object(toolslib.appslib,
            "extension_focus_available", return_value=False)
        self.focus_patch.start()
        self.addCleanup(self.focus_patch.stop)


    async def test_canceled_mcp_batch_releases_promptly_and_sends_no_later_key(self):
        async with Client(self.mcp) as client:
            task = asyncio.create_task(client.call_tool("computer_batch", {
                "actions": json.dumps([{"a": "hold", "keys": "shift"},
                    {"a": "wait", "ms": 1200}, {"a": "key", "keys": "a"}]),
                "final": "none", "force": True}, raise_on_error=False))
            self.assertTrue(await asyncio.to_thread(self.input.down.wait, 2))
            canceled = time.monotonic()
            await client.cancel(self.requests.ids["computer_batch"], reason="Contract cancellation")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            released = await asyncio.to_thread(self.input.released.wait, 0.5)
            # Keep the actual MCP server alive past the canceled batch deadline.
            await asyncio.sleep(max(0, canceled + 1.5 - time.monotonic()))
            ready = await asyncio.wait_for(client.call_tool("readiness", {}), 2)
            self.assertEqual(ready.data, "alive")
            self.assertTrue(released, "Canceled batch still holds Shift after 500 ms")
            self.assertEqual(self.input.keys, [])
            self.assertEqual(self.input.sent, [], "Canceled batch sent its sentinel")

    async def test_normal_completed_hold_is_retained(self):
        async with Client(self.mcp) as client:
            result = await client.call_tool("computer_batch", {
                "actions": json.dumps([{"a": "hold", "keys": "shift"}]),
                "final": "none", "force": True}, raise_on_error=False)
            self.assertFalse(result.is_error)
            self.assertEqual(self.input.keys, ["shift"])
            self.assertFalse(self.input.released.is_set())

    async def test_unexpected_single_action_exception_releases_held_input(self):
        def fail_after_hold():
            raise RuntimeError("Recording provider failed after holding Shift")
        self.input.on_down = fail_after_hold
        async with Client(self.mcp) as client:
            result = await client.call_tool("keyboard", {
                "action": "hold", "keys": "shift", "force": True}, raise_on_error=False)
            self.assertTrue(result.is_error)
            self.assertEqual(self.input.keys, [], "Escaping action exception left Shift held")
            self.assertTrue(self.input.released.is_set())

    async def test_cancellation_during_last_hold_releases_at_sequence_exit(self):
        finish_action = threading.Event()
        self.input.on_down = lambda: finish_action.wait(2)
        try:
            async with Client(self.mcp) as client:
                task = asyncio.create_task(client.call_tool("keyboard", {
                    "action": "hold", "keys": "shift", "force": True}, raise_on_error=False))
                self.assertTrue(await asyncio.to_thread(self.input.down.wait, 2))
                await client.cancel(self.requests.ids["keyboard"], reason="Cancel last action")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                # Process the notification while the bounded provider call finishes.
                await client.call_tool("readiness", {})
                finish_action.set()
                self.assertTrue(await asyncio.to_thread(self.input.released.wait, 0.5))
                self.assertEqual(self.input.keys, [])
        finally:
            finish_action.set()

    def test_escaping_wait_failure_releases_before_execution_slot_is_available(self):
        class WaitAborted(BaseException):
            pass
        self.assertTrue(self.gate.check("wait-contract", write=True, force=True).allowed)
        cleanup_slot_owned = []
        def observe_cleanup():
            # A different writer must still be excluded during input release.
            from pcbridge.desktop.execution import SequenceRefused
            try:
                with ExecutionLock(self.root).hold("competing", timeout=0):
                    cleanup_slot_owned.append(False)
            except SequenceRefused:
                cleanup_slot_owned.append(True)
        self.input.on_release = observe_cleanup
        def failed_wait(seconds):
            raise WaitAborted("Wait aborted after hold")
        try:
            with self.assertRaises(WaitAborted):
                with self.runtime.write_sequence("wait-contract") as guard:
                    batchlib.run(batchlib.parse(json.dumps([
                        {"a": "hold", "keys": "shift"}, {"a": "wait", "ms": 10}])),
                        opslib.DeviceOps(self.input, object(), self.cfg, ClosedCapture()),
                        budget=10, check_focus=False, before_action=guard, sleep=failed_wait)
            self.assertEqual(self.input.keys, [])
            self.assertEqual(cleanup_slot_owned, [True])
            with ExecutionLock(self.root).hold("after-cleanup", timeout=0):
                pass
        finally:
            self.input.on_release = None

    async def test_canceling_a_waiting_writer_preserves_the_current_owners_hold(self):
        self.input.keys = ["shift"]  # A completed intentional hold belongs to the owner.
        async with Client(self.mcp) as client:
            with ExecutionLock(self.root).hold("current-owner"):
                task = asyncio.create_task(client.call_tool("computer_batch", {
                    "actions": json.dumps([{"a": "key", "keys": "a"}]),
                    "final": "none", "force": True}, raise_on_error=False))
                self.assertTrue(await asyncio.to_thread(self.execution.waiting.wait, 2),
                    "Writer never entered a blocked flock poll")
                self.assertIn("computer_batch", self.requests.ids)
                await client.cancel(self.requests.ids["computer_batch"], reason="Cancel waiting writer")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(await asyncio.to_thread(self.execution.unwound.wait, 0.5),
                    "Canceled blocked writer did not unwind while the owner held its slot")
                self.assertEqual(self.input.keys, ["shift"])
                self.assertFalse(self.input.released.is_set())
                self.assertEqual(self.input.ensure_calls, 0, "Waiting writer rebound input before ownership")
            self.assertEqual(self.input.sent, [])
            self.assertEqual(self.input.keys, ["shift"])

    async def test_canceled_cleanup_finishes_before_replacement_writer_can_hold(self):
        cleanup_started = threading.Event()
        cleanup_finish = threading.Event()
        def slow_release():
            cleanup_started.set()
            cleanup_finish.wait(2)
        self.input.on_release = slow_release
        try:
            async with Client(self.mcp) as client:
                first = asyncio.create_task(client.call_tool("computer_batch", {
                    "actions": json.dumps([{"a": "hold", "keys": "shift"},
                        {"a": "wait", "ms": 1200}]), "final": "none", "force": True},
                    raise_on_error=False))
                self.assertTrue(await asyncio.to_thread(self.input.down.wait, 2))
                await client.cancel(self.requests.ids["computer_batch"], reason="Cancel old grant sequence")
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                self.assertTrue(await asyncio.to_thread(cleanup_started.wait, 0.5))
                self.gate.unlock(1)  # A legitimate replacement grant, through the real gate.
                replacement = asyncio.create_task(client.call_tool("computer_batch", {
                    "actions": json.dumps([{"a": "hold", "keys": "ctrl"}]),
                    "final": "none", "force": True}, raise_on_error=False))
                await asyncio.sleep(0.15)
                self.assertEqual(self.input.ensure_calls, 1)
                self.assertEqual(self.input.keys, ["shift"])
                cleanup_finish.set()
                result = await asyncio.wait_for(replacement, 2)
                self.assertFalse(result.is_error)
                self.assertEqual(self.input.keys, ["ctrl"])
                self.assertEqual(self.input.ensure_calls, 2)
                await asyncio.sleep(0.15)
                self.assertEqual(self.input.keys, ["ctrl"], "Stale cleanup released the replacement hold")
        finally:
            cleanup_finish.set()
            self.input.on_release = None

    def test_wait_does_not_extend_the_sliding_lease(self):
        self.assertTrue(self.gate.check("wait-contract", write=True, force=True).allowed)
        with self.runtime.write_sequence("wait-contract") as guard:
            deadline = self.gate.unlocked_until()
            checks = guard.checks
            with (mock.patch.object(self.gate, "verify", wraps=self.gate.verify) as verify,
                  mock.patch.object(self.gate, "touch", wraps=self.gate.touch) as touch):
                guard.sleep(0.12)
            verify.assert_not_called()
            touch.assert_not_called()
            self.assertEqual(guard.checks, checks)
            self.assertEqual(self.gate.unlocked_until(), deadline)

    async def test_canceled_general_shell_keeps_server_alive_and_does_not_release_input(self):
        self.input.keys = ["shift"]
        async with Client(self.mcp) as client:
            task = asyncio.create_task(client.call_tool("shell_run", {
                "command": "sleep 0.2; printf contract-survived"}, raise_on_error=False))
            deadline = time.monotonic() + 2
            while "shell_run" not in self.requests.ids and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            self.assertIn("shell_run", self.requests.ids)
            await client.cancel(self.requests.ids["shell_run"], reason="Cancel general shell")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.4)
            ready = await asyncio.wait_for(client.call_tool("readiness", {}), 2)
            self.assertEqual(ready.data, "alive")
            self.assertEqual(self.input.keys, ["shift"])
            self.assertFalse(self.input.released.is_set())

    async def test_canceling_rate_wait_releases_without_sending_the_next_key(self):
        # Construct a public runtime with an actual shared one-action rate window.
        runtime = DesktopRuntime(capture_provider=ClosedCapture(), input_provider=self.input,
            accessibility_provider=object(), gate=self.gate, desktop_state_provider=self.state,
            execution_lock=ExecutionLock(self.root), rate_limit=1)
        self.addCleanup(runtime.close)
        mcp = FastMCP("rate-cancellation-contract")
        requests = RequestObserver()
        mcp.add_middleware(requests)
        toolslib.register(mcp, self.cfg,
            JobManager(self.cfg.jobs_dir, default_timeout=60), ShotStore(self.cfg),
            transport="stdio", runtime=runtime)
        async with Client(mcp) as client:
            task = asyncio.create_task(client.call_tool("computer_batch", {
                "actions": json.dumps([{"a": "hold", "keys": "shift"},
                    {"a": "key", "keys": "a"}]), "final": "none", "force": True},
                raise_on_error=False))
            self.assertTrue(await asyncio.to_thread(self.input.down.wait, 2))
            # The sentinel cannot start until the shared one-second rate window opens.
            await asyncio.sleep(0.1)
            await client.cancel(requests.ids["computer_batch"], reason="Cancel rate waiting")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(await asyncio.to_thread(self.input.released.wait, 0.5))
            await asyncio.sleep(0.6)
            self.assertEqual(self.input.sent, [])
            self.assertEqual(self.input.keys, [])

    async def test_canceling_during_batch_move_sends_no_following_click(self):
        finish_move = threading.Event()
        self.input.on_move = lambda: finish_move.wait(2)
        try:
            async with Client(self.mcp) as client:
                task = asyncio.create_task(client.call_tool("computer_batch", {
                    "actions": json.dumps([{"a": "hold", "keys": "shift"},
                        {"a": "click", "x": 100, "y": 100}]),
                    "final": "none", "force": True}, raise_on_error=False))
                self.assertTrue(await asyncio.to_thread(self.input.moved.wait, 2))
                await client.cancel(self.requests.ids["computer_batch"], reason="Cancel during move")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await client.call_tool("readiness", {})
                finish_move.set()
                self.assertTrue(await asyncio.to_thread(self.input.released.wait, 0.5))
                self.assertEqual(self.input.clicks, [], "Canceled move still dispatched its click")
        finally:
            finish_move.set()


if __name__ == "__main__":
    unittest.main()
