#!/usr/bin/env python3
"""Native pointer selection and IPC adapter contracts (Task 5.3).

The native client, grant, and display topology are deterministic fakes. No
test opens `/dev/uinput` or sends a real pointer event.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402
from pcbridge.desktop.backends.rust import RustInputProvider  # noqa: E402
from pcbridge.desktop.errors import (  # noqa: E402
    DesktopError,
    ErrorCategory,
    ErrorCode,
)
from pcbridge.desktop.lease import LeaseToken  # noqa: E402
from pcbridge.native import NativeResponse  # noqa: E402


class FakeGate:
    def current_token(self):
        return LeaseToken(grant_id="pointer-fixture", revoke_epoch=9)

    def last_token(self):
        return self.current_token()


class FakeNativeClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.is_running = True
        self.closed = False
        self.errors: dict[str, dict] = {}

    def request(self, method, params=None, *, binary=b"", timeout=None):
        self.requests.append((method, dict(params or {})))
        if method in self.errors:
            return NativeResponse(
                request_id="fixture",
                result=None,
                binary=b"",
                error=self.errors[method],
            )
        results = {
            "input.pointer.ensure": {
                "waited_seconds": 0.0,
                "position": [2500, 500],
                "held": [],
            },
            "input.pointer.move": {"position": [2500, 500]},
            "input.pointer.click": {"held": []},
            "input.pointer.drag": {"position": [2600, 600]},
            "input.pointer.scroll": {"position": [2600, 600]},
            "input.pointer.mouse_down": {"held": ["left"]},
            "input.pointer.mouse_up": {"held": []},
            "input.pointer.held": {"held": ["left"]},
            "input.pointer.release_all": {"released": []},
            "input.pointer.take_auto_released": {"released": []},
            "input.pointer.position": {"position": [2600, 600]},
        }
        return NativeResponse(
            request_id="fixture",
            result=results[method],
            binary=b"",
            error=None,
        )

    def close(self) -> None:
        self.closed = True
        self.is_running = False


class RustPointerAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.client = FakeNativeClient()
        self.provider = RustInputProvider(
            self.cfg,
            gate=FakeGate(),
            client=self.client,
        )

    def tearDown(self) -> None:
        self.provider.close()

    def test_move_sends_one_resolved_global_point_without_shot_or_monitor(self) -> None:
        with (
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.list_monitors",
                return_value=[object(), object()],
            ),
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.topology_id",
                return_value="layout-2",
            ),
        ):
            self.assertEqual(self.provider.move(2500, 500, smooth=False), (2500, 500))

        self.assertEqual(
            self.client.requests,
            [
                (
                    "input.pointer.move",
                    {
                        "grant_id": "pointer-fixture",
                        "revoke_epoch": 9,
                        "hold_max_seconds": 120,
                        "topology_id": "layout-2",
                        "pointer_speed": 5000,
                        "pointer_max_ms": 500,
                        "x": 2500,
                        "y": 500,
                        "smooth": False,
                    },
                )
            ],
        )
        params = self.client.requests[0][1]
        self.assertNotIn("shot", params)
        self.assertNotIn("monitor", params)

    def test_every_pointer_action_stays_on_the_native_boundary(self) -> None:
        with (
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.list_monitors",
                return_value=[object()],
            ),
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.topology_id",
                return_value="layout",
            ),
        ):
            self.assertEqual(self.provider.ensure(pointer=True), 0.0)
            self.provider.click("right", 2)
            self.provider.drag(10, 20, 30, 40, "left")
            self.provider.scroll(-3, horizontal=True)
            self.provider.mouse_down("left")
            self.provider.mouse_up("left")
            self.assertEqual(self.provider.position, (2600, 600))

        self.assertEqual(
            [method for method, _params in self.client.requests],
            [
                "input.pointer.ensure",
                "input.pointer.click",
                "input.pointer.drag",
                "input.pointer.scroll",
                "input.pointer.mouse_down",
                "input.pointer.mouse_up",
                "input.pointer.position",
            ],
        )
        for _method, params in self.client.requests[:-1]:
            self.assertEqual(params["topology_id"], "layout")
        click = dict(self.client.requests)["input.pointer.click"]
        # The press duration always goes explicitly, resolved from config.
        self.assertEqual(click["hold_ms"], self.provider.cfg.desktop.click_hold_ms)
        self.assertEqual((click["button"], click["count"]), ("right", 2))

    def test_click_hold_is_sent_and_checked_before_the_helper(self) -> None:
        with mock.patch(
            "pcbridge.desktop.backends.rust.monitorslib.list_monitors",
            return_value=[object()],
        ), mock.patch(
            "pcbridge.desktop.backends.rust.monitorslib.topology_id",
            return_value="layout",
        ):
            self.provider.click("left", 1, hold_ms=150)
            with self.assertRaises(inputlib.InputError):
                self.provider.click("left", 1, hold_ms=5000)
        clicks = [p for m, p in self.client.requests if m == "input.pointer.click"]
        self.assertEqual([c["hold_ms"] for c in clicks], [150])

    def test_display_change_is_typed_and_the_write_is_not_replayed(self) -> None:
        self.client.errors["input.pointer.move"] = {
            "code": "DISPLAY_CHANGED",
            "message": "pointer topology changed",
            "retryable": True,
            "category": "coordinate",
        }
        with (
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.list_monitors",
                return_value=[object()],
            ),
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.topology_id",
                return_value="stale-layout",
            ),
            self.assertRaises(DesktopError) as caught,
        ):
            self.provider.move(10, 20)

        self.assertEqual(caught.exception.code, ErrorCode.DISPLAY_CHANGED)
        self.assertEqual(caught.exception.category, ErrorCategory.COORDINATE)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(
            [method for method, _params in self.client.requests],
            ["input.pointer.move"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
