#!/usr/bin/env python3
"""Target-policy gates: password fields, repeated clicks, destructive closes.

These are docs/dev/desktop-rules.md §4 items 7, 6 and 5. They are content gates: they
inspect what an action would do, not whether the caller holds a grant. Grant,
lock, revoke and deadline checks stay in SafetyGate and are not retested here.

No real input is sent and no real accessibility tree is read. Every provider is
a fake, so this file is safe to run with every live-test flag unset.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import batch as batchlib  # noqa: E402
from pcbridge.desktop import policy  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends.python import PythonAccessibilityProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402


def _node(role: str, name: str = "Parola") -> uitreelib.Node:
    return uitreelib.Node(
        node_id="1b72",
        path=[0, 1],
        role=role,
        name=name,
        states=["editable", "focused"],
        actions=[],
        editable=True,
        depth=3,
    )


class _StubProvider(PythonAccessibilityProvider):
    """Resolves to a fixed node; everything below it stays untouched."""

    def __init__(self, node: uitreelib.Node) -> None:
        self._node = node

    def resolve(self, node_id: str) -> uitreelib.Node:  # type: ignore[override]
        return self._node


class PasswordFieldGateTests(unittest.TestCase):
    """Item 7 -- refuse to type into a password field."""

    def test_measured_role_name_is_the_one_the_gate_blocks(self) -> None:
        # Measured on this machine, not assumed: Atspi.role_get_name(
        # Atspi.Role.PASSWORD_TEXT) == "password text", and it is the only
        # AT-SPI role name containing "password". There is no password state.
        self.assertIn("password text", policy.PASSWORD_ROLES)

    def test_password_role_is_refused_with_a_stable_code(self) -> None:
        with self.assertRaises(DesktopError) as caught:
            policy.check_text_target(role="password text", name="Parola")
        err = caught.exception
        self.assertEqual(err.code, ErrorCode.PASSWORD_FIELD)
        self.assertEqual(err.category, ErrorCategory.SAFETY)
        self.assertFalse(err.retryable)

    def test_refusal_never_repeats_the_text_or_the_field_content(self) -> None:
        with self.assertRaises(DesktopError) as caught:
            policy.check_text_target(role="password text", name="Parola")
        body = caught.exception.message + caught.exception.suggested_action
        self.assertNotIn("hunter2", body)

    def test_role_match_ignores_case_and_padding(self) -> None:
        for role in ("Password Text", "  password text  ", "PASSWORD TEXT"):
            with self.subTest(role=role):
                with self.assertRaises(DesktopError):
                    policy.check_text_target(role=role, name="")

    def test_ordinary_text_targets_pass(self) -> None:
        for role in ("entry", "text", "terminal", "document text", ""):
            with self.subTest(role=role):
                policy.check_text_target(role=role, name="Arama")


class PasswordGateWiringTests(unittest.TestCase):
    """The gate sits at the provider boundary, so every caller inherits it.

    `ui_set_text` (MCP), `DeviceOps.ui_set_text` (computer_batch) and
    `bin/pcb-do` all reach the accessibility provider's `set_text`; gating
    there covers the three of them at once.
    """

    def test_provider_refuses_before_touching_the_helper(self) -> None:
        provider = _StubProvider(_node("password text"))
        with mock.patch.object(uitreelib.UiTree, "set_text") as writer:
            with self.assertRaises(DesktopError) as caught:
                provider.set_text("1b72", "hunter2")
        self.assertEqual(caught.exception.code, ErrorCode.PASSWORD_FIELD)
        writer.assert_not_called()

    def test_provider_still_writes_to_ordinary_fields(self) -> None:
        provider = _StubProvider(_node("entry", name="Arama"))
        with mock.patch.object(
            uitreelib.UiTree, "set_text", return_value={"ok": True, "role": "entry"}
        ) as writer:
            provider.set_text("1b72", "merhaba")
        writer.assert_called_once()

    def test_force_does_not_open_the_gate(self) -> None:
        # `force` belongs to SafetyGate (activity check). It must not reach
        # this gate at all -- the provider offers no parameter for it.
        import inspect

        params = inspect.signature(PythonAccessibilityProvider.set_text).parameters
        self.assertNotIn("force", params)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class _CountingOps:
    """Records what a batch actually executed. Knows no real device."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _log(self, what: str) -> str:
        self.calls.append(what)
        return what

    def click(self, button="left", count=1, x=None, y=None, monitor=None,
              shot=None) -> str:
        return self._log(f"click {x},{y}")

    def ui_click(self, node_id: str) -> str:
        return self._log(f"ui_click {node_id}")

    def move(self, x, y, monitor=None, shot=None) -> str:
        return self._log(f"move {x},{y}")

    def key(self, keys: str) -> str:
        return self._log(f"key {keys}")

    def focused(self) -> str:
        return "app | window"

    def held(self) -> list[str]:
        return []

    def release_all(self) -> list[str]:
        return []


def _run(actions, **kw):
    return batchlib.run(
        actions, _CountingOps(), check_focus=False, budget=1e6, **kw
    )


def _click(x=None, y=None):
    args = {"button": "left"}
    if x is not None:
        args.update(x=x, y=y)
    return batchlib.Action("click", args)


def _ui(node_id="1b72"):
    return batchlib.Action("ui_click", {"id": node_id})


class RepeatClickGateTests(unittest.TestCase):
    """Item 6 -- a loop hitting the same target stops instead of grinding on."""

    def test_third_identical_ui_click_never_executes(self) -> None:
        ops = _CountingOps()
        res = batchlib.run(
            [_ui(), _ui(), _ui(), _ui()], ops, check_focus=False, budget=1e6
        )
        self.assertEqual(res.stopped, "repeat")
        self.assertEqual(len(ops.calls), 2, "ucuncu tiklama fiilen gonderildi")
        self.assertEqual(len(res.remaining), 2, "kalan eylemler raporlanmadi")

    def test_identical_coordinate_clicks_count_as_the_same_target(self) -> None:
        ops = _CountingOps()
        res = batchlib.run(
            [_click(10, 20), _click(10, 20), _click(10, 20)],
            ops, check_focus=False, budget=1e6,
        )
        self.assertEqual(res.stopped, "repeat")
        self.assertEqual(len(ops.calls), 2)

    def test_different_targets_do_not_accumulate(self) -> None:
        ops = _CountingOps()
        res = batchlib.run(
            [_click(10, 20), _click(30, 40), _click(10, 20), _click(30, 40)],
            ops, check_focus=False, budget=1e6,
        )
        self.assertEqual(res.stopped, "")
        self.assertEqual(len(ops.calls), 4)

    def test_a_move_between_clicks_resets_the_streak(self) -> None:
        ops = _CountingOps()
        res = batchlib.run(
            [_click(10, 20), _click(10, 20),
             batchlib.Action("move", {"x": 99, "y": 99}), _click(10, 20)],
            ops, check_focus=False, budget=1e6,
        )
        self.assertEqual(res.stopped, "")
        self.assertEqual(len(ops.calls), 4)

    def test_wait_does_not_reset_the_streak(self) -> None:
        # "tikla, bekle, tikla, bekle, tikla" tam olarak donguye giren ajanin
        # deseni. `wait` sayaci sifirlarsa kapi hic atesLenmez.
        ops = _CountingOps()
        res = batchlib.run(
            [_ui(), batchlib.Action("wait", {"ms": 1}),
             _ui(), batchlib.Action("wait", {"ms": 1}), _ui()],
            ops, check_focus=False, budget=1e6, sleep=lambda _s: None,
        )
        self.assertEqual(res.stopped, "repeat")

    def test_limit_zero_disables_the_gate(self) -> None:
        ops = _CountingOps()
        res = batchlib.run(
            [_ui(), _ui(), _ui(), _ui()], ops,
            check_focus=False, budget=1e6, repeat_limit=0,
        )
        self.assertEqual(res.stopped, "")
        self.assertEqual(len(ops.calls), 4)

    def test_detail_names_the_target_so_the_caller_can_act(self) -> None:
        res = _run([_ui("abc1"), _ui("abc1"), _ui("abc1")])
        self.assertIn("abc1", res.detail)


class CloseConfirmationGateTests(unittest.TestCase):
    """Item 5 -- a close/quit shortcut needs the caller to say it meant it.

    Same design as `expect_focus`: the accident came from an intent that was
    never declared, so the fix is to make the caller declare it, not to add a
    flag that merely overpowers the check.
    """

    def test_close_combos_are_refused_without_confirmation(self) -> None:
        for keys in ("alt+F4", "ctrl+q", "ctrl+w", "ctrl+shift+q"):
            with self.subTest(keys=keys):
                with self.assertRaises(DesktopError) as caught:
                    policy.check_key_combo(keys)
                self.assertEqual(
                    caught.exception.code, ErrorCode.CONFIRMATION_REQUIRED
                )
                self.assertEqual(caught.exception.category, ErrorCategory.SAFETY)

    def test_confirmation_lets_the_combo_through(self) -> None:
        policy.check_key_combo("alt+F4", confirm_close=True)

    def test_chord_order_and_case_do_not_matter(self) -> None:
        for keys in ("F4+alt", "ALT+F4", " alt + f4 "):
            with self.subTest(keys=keys):
                with self.assertRaises(DesktopError):
                    policy.check_key_combo(keys)

    def test_ordinary_combos_are_untouched(self) -> None:
        for keys in ("ctrl+v", "Return", "alt+tab", "super", "ctrl+shift+t",
                     "f4", "alt", "ctrl+s"):
            with self.subTest(keys=keys):
                policy.check_key_combo(keys)

    def test_refusal_names_the_way_out(self) -> None:
        with self.assertRaises(DesktopError) as caught:
            policy.check_key_combo("ctrl+q")
        self.assertIn("confirm_close", caught.exception.message)

    def test_force_is_not_the_way_out(self) -> None:
        # `force` SafetyGate'in etkinlik kontrolunu atlar. Bu kapiyi acmaz,
        # yoksa ajan reddi gorunce once onu denerdi.
        import inspect

        params = inspect.signature(policy.check_key_combo).parameters
        self.assertNotIn("force", params)


class CloseConfirmationBatchTests(unittest.TestCase):
    """The whole plan is rejected at parse time, so nothing runs by halves."""

    def test_parse_rejects_an_unconfirmed_close_anywhere_in_the_list(self) -> None:
        with self.assertRaises(DesktopError) as caught:
            batchlib.parse([
                {"a": "ui_click", "id": "1b72"},
                {"a": "key", "keys": "alt+F4"},
            ])
        self.assertEqual(caught.exception.code, ErrorCode.CONFIRMATION_REQUIRED)

    def test_parse_accepts_a_confirmed_close(self) -> None:
        plan = batchlib.parse([{"a": "key", "keys": "alt+F4", "confirm_close": True}])
        self.assertEqual(len(plan), 1)

    def test_hold_is_gated_too(self) -> None:
        with self.assertRaises(DesktopError):
            batchlib.parse([{"a": "hold", "keys": "ctrl+q"}])

    def test_release_is_not_gated(self) -> None:
        # `release` basili tusu BIRAKIR; kapatma eylemi degil, temizliktir.
        plan = batchlib.parse([{"a": "release", "keys": "ctrl+q"}])
        self.assertEqual(len(plan), 1)
