#!/usr/bin/env python3
"""Accessibility on the real desktop (Tasks 6.1-6.3, Gate 6).

Reads are skipped unless `PCBRIDGE_TEST_ATSPI=1`. The action tests also need
`PCBRIDGE_TEST_INPUT=1`: they press buttons and fill fields through AT-SPI.
Both only ever touch `a11y_window.py`, a small GTK4 window this test opens
itself; every action is addressed by that window's bus name and object path,
which is exactly what the providers check. No keyboard or pointer device is
opened.

    PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_INPUT=1 \\
      ./.venv/bin/python -m unittest tests/live/test_accessibility_parity.py -v

`PCBRIDGE_A11Y_REPORT=<path>` writes the measured timings there as JSON.

The native helper (Tasks 6.2, 6.3) is compared with the Python one on the
same window when a helper with the accessibility methods is found
(`PCBRIDGE_NATIVE_BIN` or the packaged one): the same reads, and every
action test run once more through it. It runs under a grant written to a
scratch state directory, so the user's own grant is never touched and no
screen is shared.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends.python import PythonAccessibilityProvider  # noqa: E402
from pcbridge.desktop.backends.rust import RustAccessibilityProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402

SYSTEM_PYTHON = "/usr/bin/python3"
APP = "pcbridge-a11y-test"
READ = os.environ.get("PCBRIDGE_TEST_ATSPI") == "1"
ACT = READ and os.environ.get("PCBRIDGE_TEST_INPUT") == "1"
TEXT = "Çağrı ğüşıöç İĞÜŞÖÇ — pcbridge 6.1"
# Longer than the 5 characters the window's "Kod" field keeps.
LONG_CODE = "Çağrı-123"
REPORT: dict[str, list[float]] = {}


def _measure(name: str, started: float) -> None:
    REPORT.setdefault(name, []).append(round((time.monotonic() - started) * 1000, 1))


class A11yWindow:
    """`a11y_window.py` and what it reports."""

    def __init__(self, errors: Path) -> None:
        self._stderr = errors.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [SYSTEM_PYTHON, str(HERE / "a11y_window.py"), "--timeout", "90"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )
        self.events: list[dict] = []
        self.changed = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()
        self.ready = self.wait(lambda e: e.get("event") == "ready", 15)
        if self.ready is None:
            self.close()
            raise RuntimeError(f"test window did not start; see {errors}")

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            with self.changed:
                self.events.append(event)
                self.changed.notify_all()

    def mark(self) -> int:
        with self.changed:
            return len(self.events)

    def since(self, mark: int) -> list[dict]:
        with self.changed:
            return list(self.events[mark:])

    def wait(self, predicate, timeout: float, since: int = 0) -> dict | None:
        deadline = time.monotonic() + timeout
        with self.changed:
            while True:
                for event in self.events[since:]:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.changed.wait(remaining)

    def command(self, name: str) -> None:
        mark = self.mark()
        assert self.process.stdin is not None
        self.process.stdin.write(name + "\n")
        self.process.stdin.flush()
        done = self.wait(lambda e: e.get("command") == name, 5, since=mark)
        if done is None or done.get("event") != "done":
            raise AssertionError(f"window did not run {name!r}: {done}")
        time.sleep(0.3)  # let AT-SPI publish the new tree

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                assert self.process.stdin is not None
                self.process.stdin.write("quit\n")
                self.process.stdin.flush()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self._stderr.close()


def native_provider(case: unittest.TestCase, feature: str) -> RustAccessibilityProvider:
    """The native provider under a scratch grant, or skip without a helper
    that has `feature`."""
    scratch = tempfile.TemporaryDirectory()
    case.addCleanup(scratch.cleanup)
    cfg = dataclasses.replace(
        load_config(str(ROOT / "config.example.toml")), state_dir=Path(scratch.name)
    )
    gate = SafetyGate(cfg)
    gate.unlock(5, reason="accessibility parity")
    provider = RustAccessibilityProvider(cfg, gate=gate)
    case.addCleanup(provider.close)
    try:
        provider.windows()
    except DesktopError as exc:
        case.skipTest(f"no native helper with {feature}: {exc.message}")
    client = provider._helper.client
    if client is None or feature not in client.features:
        case.skipTest(f"the native helper has no {feature}; rebuild it")
    return provider


class _LiveCase(unittest.TestCase):
    #: Timings are kept per provider.
    label = "python"

    def setUp(self) -> None:
        ok, why = uitreelib.available()
        if not ok:
            self.skipTest(why)
        errors = Path(os.environ.get("TMPDIR", "/tmp")) / f"pcbridge-a11y-window-{os.getpid()}.log"
        self.window = A11yWindow(errors)
        self.addCleanup(self.window.close)
        self.tree = self.provider()

    def provider(self):
        return PythonAccessibilityProvider()

    def dump(self) -> uitreelib.Dump:
        """Dump the test window by name; it may take a moment to register."""
        deadline = time.monotonic() + 8
        while True:
            started = time.monotonic()
            try:
                dump = self.tree.dump(target=APP)
            except DesktopError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
                continue
            if dump.app_pid == self.window.ready["pid"] and len(self.closes(dump)) == 3:
                _measure(f"{self.label}_dump_ms", started)
                return dump
            if time.monotonic() > deadline:
                self.fail(f"test window not visible to AT-SPI: {dump.app} pid {dump.app_pid}")
            time.sleep(0.2)

    @staticmethod
    def closes(dump: uitreelib.Dump) -> list[uitreelib.Node]:
        return [n for n in dump.nodes if n.role == "push button" and n.name == "Kapat"]

    def group(self, dump: uitreelib.Dump, which: str) -> uitreelib.Node:
        """The "Kapat" of group A or B, the two in the window's own body.

        The third "Kapat" is the header bar's close button, which closes the
        window; the groups come first in the tree, so the first two are the
        groups' in order.
        """
        closes = self.closes(dump)
        return closes[0] if which == "a" else closes[1]

    def clicked(self, mark: int, within: float = 1.0) -> list[str]:
        time.sleep(within)
        return [e["button"] for e in self.window.since(mark) if e.get("event") == "clicked"]


@unittest.skipUnless(READ, "set PCBRIDGE_TEST_ATSPI=1: reads a test window's accessibility tree")
class LiveReadTests(_LiveCase):
    def test_dump_carries_the_window_identity(self) -> None:
        dump = self.dump()
        self.assertTrue(dump.app_bus.startswith(":"), dump.app_bus)
        self.assertEqual(dump.scope, "app")
        refs = [n.ref for n in dump.nodes]
        self.assertTrue(all(refs))
        self.assertEqual(len(refs), len(set(refs)))
        ids = [n.node_id for n in self.closes(dump)]
        self.assertEqual(len(set(ids)), 3, ids)

    def test_object_paths_survive_a_shifted_tree(self) -> None:
        before = self.dump()
        self.window.command("prepend")
        after = self.dump()
        old = {(n.role, n.name, n.node_id): n for n in before.nodes}
        kept = [(n, old[(n.role, n.name, n.node_id)]) for n in after.nodes
                if (n.role, n.name, n.node_id) in old]
        self.assertGreaterEqual(len(kept), len(before.nodes))
        moved = [n for n, was in kept if n.path != was.path]
        self.assertTrue(moved, "prepending should shift index paths")
        for now, was in kept:
            self.assertEqual(now.ref, was.ref, (now.role, now.name))


@unittest.skipUnless(ACT, "set PCBRIDGE_TEST_ATSPI=1 and PCBRIDGE_TEST_INPUT=1: presses the test window's buttons")
class LiveActionTests(_LiveCase):
    def test_a_moved_button_is_pressed_by_identity(self) -> None:
        dump = self.dump()
        target = self.group(dump, "b")
        self.window.command("prepend")
        mark = self.window.mark()
        started = time.monotonic()
        result = self.tree.click(target.node_id)
        _measure(f"{self.label}_click_ms", started)
        self.assertEqual(result["resolved_by"], "moved")
        self.assertEqual(self.clicked(mark), ["close-b"])

    def test_a_recreated_button_is_refused(self) -> None:
        dump = self.dump()
        target = self.group(dump, "a")
        self.window.command("rebuild")
        mark = self.window.mark()
        with self.assertRaises(DesktopError) as raised:
            self.tree.click(target.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ELEMENT_STALE)
        self.assertEqual(self.clicked(mark), [])
        fresh = self.dump()
        self.tree.click(self.group(fresh, "a").node_id)
        self.assertEqual(self.clicked(mark), ["close-a-1"])

    def test_a_removed_button_is_not_replaced_by_its_twin(self) -> None:
        dump = self.dump()
        target = self.group(dump, "a")
        self.window.command("remove-a")
        mark = self.window.mark()
        with self.assertRaises(DesktopError) as raised:
            self.tree.click(target.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ELEMENT_STALE)
        self.assertEqual(self.clicked(mark), [])
        self.assertIsNone(self.window.process.poll(), "the window must still be open")

    def test_a_closed_application_is_refused(self) -> None:
        dump = self.dump()
        target = self.group(dump, "b")
        self.window.close()
        with self.assertRaises(DesktopError) as raised:
            self.tree.click(target.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ELEMENT_STALE)

    def test_text_is_written_whole_and_the_password_field_is_refused(self) -> None:
        dump = self.dump()
        field = next(n for n in dump.nodes if n.role == "text" and n.name == "Ad")
        password = next(n for n in dump.nodes if n.role == "password text")
        mark = self.window.mark()
        started = time.monotonic()
        result = self.tree.set_text(field.node_id, TEXT)
        _measure(f"{self.label}_set_text_ms", started)
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["now_chars"], len(TEXT))
        got = self.window.wait(
            lambda e: e.get("field") == "name" and e.get("value") == TEXT, 3, since=mark
        )
        self.assertIsNotNone(got, [e for e in self.window.since(mark) if e.get("event") == "text"])
        with self.assertRaises(DesktopError) as raised:
            self.tree.set_text(password.node_id, "hunter2")
        self.assertEqual(raised.exception.code, ErrorCode.PASSWORD_FIELD)
        time.sleep(0.5)
        self.assertFalse(
            [e for e in self.window.since(mark) if e.get("field") == "password"]
        )

    def test_a_disabled_button_is_refused_not_reported_clicked(self) -> None:
        dump = self.dump()
        ok = next(n for n in dump.nodes if n.role == "push button" and n.name == "Tamam")
        self.window.command("disable-ok")
        mark = self.window.mark()
        with self.assertRaises(DesktopError) as raised:
            self.tree.click(ok.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ACTION_UNSUPPORTED)
        self.assertEqual(self.clicked(mark), [])

    def test_a_field_that_keeps_five_characters_is_reported(self) -> None:
        dump = self.dump()
        code = next(n for n in dump.nodes if n.role == "text" and n.name == "Kod")
        mark = self.window.mark()
        with self.assertRaises(DesktopError) as raised:
            self.tree.set_text(code.node_id, LONG_CODE)
        error = raised.exception
        self.assertEqual(error.code, ErrorCode.TEXT_MISMATCH)
        self.assertIn(f"{len(LONG_CODE)} karakter gonderildi", error.message)
        self.assertIn("simdi 5 karakter", error.message)
        self.assertNotIn(LONG_CODE, error.message)
        got = self.window.wait(
            lambda e: e.get("field") == "code" and e.get("value") == LONG_CODE[:5], 3, since=mark
        )
        self.assertIsNotNone(got, "the field holds what it kept")


@unittest.skipUnless(ACT, "set PCBRIDGE_TEST_ATSPI=1 and PCBRIDGE_TEST_INPUT=1: presses the test window's buttons")
class LiveNativeActionTests(LiveActionTests):
    """Every action test once more, through the native helper (Task 6.3)."""

    label = "native"

    def provider(self):
        return native_provider(self, "accessibility.action")

    def test_the_helper_opens_no_input_device(self) -> None:
        dump = self.dump()
        self.tree.click(self.group(dump, "b").node_id)
        client = self.tree._helper.client
        self.assertIsNotNone(client)
        pid = client._process.pid
        opened = []
        for fd in Path(f"/proc/{pid}/fd").iterdir():
            try:
                opened.append(os.readlink(fd))
            except OSError:
                continue
        self.assertFalse([path for path in opened if "uinput" in path], opened)


NODE_FIELDS = ("node_id", "ref", "path", "role", "name", "states", "actions", "editable", "depth")


def summary(dump: uitreelib.Dump) -> dict:
    return {
        "app": dump.app,
        "window": dump.window,
        "app_bus": dump.app_bus,
        "app_pid": dump.app_pid,
        "scope": dump.scope,
        "window_ref": dump.window_ref,
        "truncated": dump.truncated,
        "nodes": [{field: getattr(node, field) for field in NODE_FIELDS} for node in dump.nodes],
    }


@unittest.skipUnless(READ, "set PCBRIDGE_TEST_ATSPI=1: reads a test window's accessibility tree")
class LiveNativeReadTests(_LiveCase):
    """The native reader against the Python one, on the real accessibility bus."""

    def setUp(self) -> None:
        super().setUp()
        self.native = native_provider(self, "accessibility.read")

    def timed(self, name: str, read):
        started = time.monotonic()
        result = read()
        _measure(name, started)
        return result

    def test_both_readers_list_the_same_window_node_for_node(self) -> None:
        self.dump()  # wait until the window is visible to AT-SPI
        for _round in range(3):
            python = self.timed("python_dump_ms", lambda: self.tree.dump(target=APP))
            native = self.timed("native_dump_ms", lambda: self.native.dump(target=APP))
            self.assertEqual(summary(native), summary(python))
            self.assertEqual(native.backend, "linux.atspi.native")
        self.window.command("prepend")
        python = self.tree.dump(target=APP, interactive_only=False)
        native = self.native.dump(target=APP, interactive_only=False)
        self.assertEqual(summary(native), summary(python))

    def test_both_readers_see_the_same_test_window(self) -> None:
        self.dump()
        python = self.timed("python_windows_ms", self.tree.windows)
        native = self.timed("native_windows_ms", self.native.windows)
        mine = lambda windows: [w for w in windows if w.app_pid == self.window.ready["pid"]]  # noqa: E731
        self.assertEqual(mine(native), mine(python))
        self.assertEqual(len(mine(native)), 1)

    def test_a_large_tree_for_timing(self) -> None:
        # gnome-shell's tree changes by itself (the clock), so only time it.
        python = self.timed("python_shell_dump_ms", lambda: self.tree.dump(target="gnome-shell"))
        native = self.timed("native_shell_dump_ms", lambda: self.native.dump(target="gnome-shell"))
        REPORT.setdefault("shell_nodes", []).extend([len(python.nodes), len(native.nodes)])
        self.assertEqual(native.app, "gnome-shell")


def tearDownModule() -> None:
    path = os.environ.get("PCBRIDGE_A11Y_REPORT")
    if path and REPORT:
        Path(path).write_text(json.dumps(REPORT, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
