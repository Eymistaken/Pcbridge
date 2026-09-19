#!/usr/bin/env python3
"""A native helper serves one desktop grant; a new grant gets a new helper.

The real helper binds the grant it reads at `initialize` and never rebinds
(`native_revoke.rs`: `revoke_releases_resource_and_session_never_rebinds`),
while every `desktop_unlock` writes a new grant id. Measured 2026-09-19 before
the fix: a second `desktop_unlock` with the grant still open closed the native
share and left capture REVOKED until `desktop_lock`; native input stayed
REVOKED after a second unlock, after an expiry, and after a second lock,
because `close()` only worked once.

No helper process, no D-Bus, no `/dev/uinput`: `BoundHelper` reproduces the
one rule that matters -- the first grant it is asked under is the only one it
answers -- and everything else is recorded.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop.backends.rust import (  # noqa: E402
    GrantBoundHelper,
    NativeScreenCast,
    RustInputProvider,
)
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402
from pcbridge.desktop.lease import LeaseToken  # noqa: E402
from pcbridge.native import NativeResponse  # noqa: E402


MONITORS = [
    monitorslib.Monitor(
        index=1, connector="DP-4", x=0, y=0, width=1920, height=1080,
        scale=1.0, primary=False, name="left",
    ),
    monitorslib.Monitor(
        index=2, connector="DP-3", x=1920, y=0, width=1920, height=1080,
        scale=1.0, primary=True, name="right",
    ),
]

RESULTS = {
    "capture.session_open": {"outcome": "opened"},
    "capture.frame": {"wait_ms": 1.0},
    "input.keyboard.key": {"held": []},
    "input.keyboard.release_all": {"released": []},
    "input.pointer.click": {"held": []},
    "input.pointer.release_all": {"released": []},
}


class BoundHelper:
    """Answers only the first grant it is asked under, like the real helper."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.bound: tuple[str, int] | None = None
        self.requests: list[tuple[str, dict]] = []
        self.closed = False
        self.is_running = True

    def request(self, method, params=None, *, binary=b"", timeout=None):
        params = dict(params or {})
        self.requests.append((method, params))
        if self.closed:
            raise AssertionError(f"{self.name} was asked {method} after it was stopped")
        if "grant_id" in params:
            grant = (params["grant_id"], params["revoke_epoch"])
            if self.bound is None:
                self.bound = grant
            if grant != self.bound:
                raise DesktopError(
                    code=ErrorCode.REVOKED,
                    message="request does not match the bound desktop grant",
                    category=ErrorCategory.SAFETY,
                    retryable=False,
                    suggested_action="desktop_unlock",
                )
        return NativeResponse(
            request_id=self.name,
            result=dict(RESULTS.get(method, {})),
            binary=b"png" if method == "capture.frame" else b"",
            error=None,
        )

    def methods(self) -> list[str]:
        return [method for method, _params in self.requests]

    def close(self) -> None:
        self.closed = True
        self.is_running = False


class Helpers:
    """A client factory that remembers every helper it started."""

    def __init__(self) -> None:
        self.started: list[BoundHelper] = []

    def __call__(self) -> BoundHelper:
        helper = BoundHelper(f"helper-{len(self.started) + 1}")
        self.started.append(helper)
        return helper


class Grants:
    """The gate's view of the lease file; `unlock()` is what desktop_unlock does."""

    def __init__(self) -> None:
        self.count = 0
        self.token: LeaseToken | None = None
        self.unlock()

    def unlock(self) -> None:
        self.count += 1
        self.token = LeaseToken(grant_id=f"grant-{self.count}", revoke_epoch=5)

    def current_token(self):
        return self.token

    def last_token(self):
        return self.token


class GrantBoundHelperTests(unittest.TestCase):
    def test_the_same_grant_keeps_one_helper(self) -> None:
        helpers = Helpers()
        bound = GrantBoundHelper(helpers)
        first = bound.for_grant(("grant-1", 0))
        self.assertIs(bound.for_grant(("grant-1", 0)), first)
        self.assertEqual(len(helpers.started), 1)
        self.assertFalse(first.closed)

    def test_a_new_grant_retires_the_old_helper_before_starting_one(self) -> None:
        helpers = Helpers()
        bound = GrantBoundHelper(helpers)
        first = bound.for_grant(("grant-1", 0))
        seen: list[tuple[object, object, bool]] = []
        second = bound.for_grant(
            ("grant-2", 0),
            retire=lambda stale: seen.append((stale, bound.client, stale.closed)),
        )
        self.assertEqual(seen, [(first, first, False)], "retire runs while the old one is current")
        self.assertTrue(first.closed)
        self.assertIsNot(second, first)
        self.assertEqual(len(helpers.started), 2)

    def test_a_new_revoke_epoch_is_a_new_grant_too(self) -> None:
        helpers = Helpers()
        bound = GrantBoundHelper(helpers)
        first = bound.for_grant(("grant-1", 0))
        self.assertIsNot(bound.for_grant(("grant-1", 1)), first)
        self.assertTrue(first.closed)

    def test_a_failed_cleanup_still_stops_the_old_helper_and_serves_the_new_grant(self) -> None:
        helpers = Helpers()
        bound = GrantBoundHelper(helpers)
        first = bound.for_grant(("grant-1", 0))

        def broken(_stale) -> None:
            raise RuntimeError("release failed")

        with self.assertLogs("pcbridge.desktop.backends.rust", level="WARNING"):
            second = bound.for_grant(("grant-2", 0), retire=broken)
        self.assertTrue(first.closed)
        self.assertIs(bound.client, second)

    def test_close_can_be_followed_by_a_new_helper(self) -> None:
        helpers = Helpers()
        bound = GrantBoundHelper(helpers)
        first = bound.for_grant(("grant-1", 0))
        bound.close()
        self.assertTrue(first.closed)
        self.assertIsNone(bound.client)
        second = bound.for_grant(("grant-1", 0))
        self.assertIsNot(second, first)
        bound.close()
        self.assertTrue(second.closed)

    def test_an_injected_helper_adopts_the_first_grant(self) -> None:
        helpers = Helpers()
        injected = BoundHelper("injected")
        bound = GrantBoundHelper(helpers, injected)
        self.assertIs(bound.for_grant(("grant-1", 0)), injected)
        self.assertEqual(helpers.started, [])


class NativeCaptureRebindTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.grants = Grants()
        self.helpers = Helpers()
        self.handle = NativeScreenCast(
            self.cfg, gate=self.grants, client_factory=self.helpers
        )
        patcher = mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def start(self) -> None:
        self.handle.start([monitor.connector for monitor in MONITORS], cursor=False)

    def test_a_second_unlock_while_open_reopens_the_share_under_the_new_grant(self) -> None:
        self.start()
        self.handle.capture("DP-4", Path(self.tmp.name) / "a.png")
        self.grants.unlock()
        self.start()
        frame = self.handle.capture("DP-4", Path(self.tmp.name) / "b.png")

        self.assertTrue(frame["ok"])
        first, second = self.helpers.started
        self.assertTrue(first.closed)
        self.assertEqual(first.methods(), ["capture.session_open", "capture.frame"])
        self.assertEqual(second.methods(), ["capture.session_open", "capture.frame"])
        self.assertEqual(second.requests[0][1]["grant_id"], "grant-2")
        self.assertTrue(self.handle.is_open())

    def test_a_grant_changed_by_another_process_is_served_on_the_next_frame(self) -> None:
        self.start()
        self.grants.unlock()  # another process's desktop_unlock: no start() here
        frame = self.handle.capture("DP-3", Path(self.tmp.name) / "c.png")

        self.assertTrue(frame["ok"])
        first, second = self.helpers.started
        self.assertTrue(first.closed)
        self.assertEqual(second.methods(), ["capture.frame"])
        self.assertEqual(second.requests[0][1]["grant_id"], "grant-2")

    def test_the_same_grant_reuses_the_helper_and_its_session(self) -> None:
        self.start()
        for name in ("a", "b", "c"):
            self.handle.capture("DP-4", Path(self.tmp.name) / f"{name}.png")
        self.assertEqual(len(self.helpers.started), 1)

    def test_close_then_a_new_grant_starts_a_new_helper(self) -> None:
        for lock in range(1, 3):
            self.start()
            self.handle.close()
            self.assertTrue(self.helpers.started[-1].closed, f"lock {lock} left its helper running")
            self.grants.unlock()
        self.start()
        self.assertEqual([helper.closed for helper in self.helpers.started],
                         [True, True, False])


class NativeInputRebindTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.grants = Grants()
        self.helpers = Helpers()
        self.provider = RustInputProvider(
            self.cfg, gate=self.grants, client_factory=self.helpers
        )
        patcher = mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_second_unlock_while_open_moves_input_to_a_new_helper(self) -> None:
        self.provider.key("a")
        self.grants.unlock()
        self.provider.key("b")

        first, second = self.helpers.started
        self.assertEqual(
            first.methods(),
            ["input.keyboard.key", "input.keyboard.release_all"],
            "the superseded helper is asked to release before it stops",
        )
        self.assertTrue(first.closed)
        self.assertEqual(second.methods(), ["input.keyboard.key"])
        self.assertEqual(second.requests[0][1]["grant_id"], "grant-2")

    def test_a_pointer_write_under_a_new_grant_moves_to_a_new_helper_too(self) -> None:
        self.provider.key("a")
        self.grants.unlock()
        self.provider.click("left")

        first, second = self.helpers.started
        self.assertTrue(first.closed)
        self.assertEqual(second.methods(), ["input.pointer.click"])
        self.assertEqual(second.requests[0][1]["grant_id"], "grant-2")

    def test_every_desktop_lock_stops_the_helper_it_finds(self) -> None:
        for lock in range(1, 4):
            self.provider.key("a")
            helper = self.helpers.started[-1]
            self.provider.close()  # what desktop_lock reaches via release_resources
            # Immediately, not at the next write: a helper left running here
            # outlives the grant it was bound to.
            self.assertTrue(helper.closed, f"lock {lock} left its helper running")
            self.assertEqual(helper.methods()[-1], "input.keyboard.release_all")
            self.grants.unlock()
        self.provider.key("b")

        self.assertEqual(len(self.helpers.started), 4)
        self.assertEqual([helper.closed for helper in self.helpers.started],
                         [True, True, True, False])
        self.assertEqual(self.helpers.started[-1].requests[0][1]["grant_id"], "grant-4")

    def test_after_expiry_a_new_unlock_gets_a_new_helper(self) -> None:
        self.provider.key("a")
        self.grants.token = None  # expired: no current grant, nothing remembered
        with self.assertRaises(DesktopError) as raised:
            self.provider.key("b")
        self.assertIs(raised.exception.code, ErrorCode.GRANT_REQUIRED)
        self.assertEqual(len(self.helpers.started), 1, "a refused call starts nothing")

        self.grants.unlock()
        self.provider.key("c")
        first, second = self.helpers.started
        self.assertTrue(first.closed)
        self.assertEqual(second.requests[0][1]["grant_id"], "grant-2")

    def test_a_write_is_still_sent_once_under_the_new_grant(self) -> None:
        self.provider.key("a")
        self.grants.unlock()
        self.provider.key("b")
        sent = [
            params["combo"]
            for helper in self.helpers.started
            for method, params in helper.requests
            if method == "input.keyboard.key"
        ]
        self.assertEqual(sent, ["a", "b"], "no replay across the helper change")


if __name__ == "__main__":
    unittest.main(verbosity=2)
