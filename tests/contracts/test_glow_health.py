"""An alive process or a ready JSON field alone cannot authorize control."""

import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from pcbridge.desktop import glowstate
from pcbridge.desktop.lease import LeaseToken
from pcbridge.desktop import monitors
from tests.contracts.test_hyprland_monitors import output

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native/glow_health_cases.json"


class GlowHealthTests(unittest.TestCase):
    def test_output_identity_and_geometry_must_cover_the_current_table(self):
        table = monitors.resolve_state(monitors._hyprland_state([output("Virtual-1", 0, 0)]))
        record = {"topology_id": monitors.topology_id(table), "outputs": ["Virtual-1"], "strip_count": 4}
        self.assertTrue(glowstate.covers_outputs(record, table))
        renamed = monitors.resolve_state(monitors._hyprland_state([output("Virtual-2", 0, 0)]))
        self.assertEqual(monitors.topology_id(table), monitors.topology_id(renamed))
        self.assertFalse(glowstate.covers_outputs(record, renamed))
        changed = monitors.resolve_state(monitors._hyprland_state([output("Virtual-1", 0, 0, scale=1.25)]))
        self.assertFalse(glowstate.covers_outputs(record, changed))
        table = monitors.resolve_state(monitors._hyprland_state([
            output("Virtual-1", 0, 0), output("Virtual-2", 1280, 0)]))
        swapped = monitors.resolve_state(monitors._hyprland_state([
            output("Virtual-2", 0, 0), output("Virtual-1", 1280, 0)]))
        record = {"topology_id": monitors.topology_id(table),
                  "outputs": [m.connector for m in table], "strip_count": 8}
        self.assertEqual(monitors.topology_id(table), monitors.topology_id(swapped))
        self.assertFalse(glowstate.covers_outputs(record, swapped))

    def test_shared_native_refusal_fixture(self):
        data = json.loads(FIXTURE.read_text())
        token = LeaseToken(**data["token"])
        for case in data["cases"]:
            with self.subTest(case=case["name"]):
                self.assertIs(glowstate.matches({**data["base"], **case["patch"]}, token,
                    display=data["display"], signature=data["signature"], now_ms=case["now_ms"]), case["expected"])

    def test_writer_role_requires_actual_grant_and_session_arguments(self):
        token = LeaseToken("fixture-grant", 2)
        directory, binary = Path("/fixture/state"), Path("/fixture/native")
        valid = b"/fixture/native\0glow-watch\0/fixture/state\0fixture-grant\0" + b"2\0"
        info = SimpleNamespace(st_dev=1, st_ino=2, st_uid=os.getuid())
        environment = b"WAYLAND_DISPLAY=wayland-1\0HYPRLAND_INSTANCE_SIGNATURE=fixture-session\0"
        for raw, expected in [(valid, True), (valid.replace(b"fixture-grant", b"previous-grant"), False),
                              (valid.replace(b"2\0", b"3\0"), False), (valid.replace(b"glow-watch", b"idle-watch"), False)]:
            with self.subTest(raw=raw), mock.patch.object(Path, "read_bytes", return_value=raw), \
                    mock.patch.object(Path, "stat", return_value=info), \
                    mock.patch.object(Path, "open", return_value=io.BytesIO(environment)), \
                    mock.patch.dict(os.environ, WAYLAND_DISPLAY="wayland-1", HYPRLAND_INSTANCE_SIGNATURE="fixture-session"):
                self.assertIs(glowstate._writer_matches_binary(100, directory, binary, token), expected)
        with mock.patch.object(Path, "read_bytes", return_value=valid), \
                mock.patch.object(Path, "stat", return_value=info), \
                mock.patch.object(Path, "open", return_value=io.BytesIO(environment)), \
                mock.patch.dict(os.environ, WAYLAND_DISPLAY="wayland-2", HYPRLAND_INSTANCE_SIGNATURE="fixture-session"):
            self.assertFalse(glowstate._writer_matches_binary(100, directory, binary, token))

    def test_reader_rejects_untrusted_files_and_dead_or_reused_processes(self):
        data = json.loads(FIXTURE.read_text())
        token = LeaseToken(**data["token"])
        record = {**data["base"], "pid": os.getpid(), "owner_pid": os.getppid(),
                  "writer_start_ticks": glowstate._writer_start_ticks(os.getpid()),
                  "owner_start_ticks": glowstate._writer_start_ticks(os.getppid())}
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, WAYLAND_DISPLAY=data["display"], HYPRLAND_INSTANCE_SIGNATURE=data["signature"]), \
                mock.patch.object(glowstate, "_writer_matches_binary", return_value=True):
            directory = Path(temporary)
            path = directory / glowstate.STATE_FILE
            def read():
                return glowstate.read(directory, token, binary=Path("/fixture/native"), now_ms=10500)
            self.assertIsNone(read())
            path.write_text(json.dumps(record))
            path.chmod(0o600)
            self.assertEqual(read(), record)
            with mock.patch.object(glowstate, "_writer_start_ticks", return_value=None):
                self.assertIsNone(read())
            with mock.patch.object(glowstate, "_parent_pid", return_value=0):
                self.assertIsNone(read())
            path.chmod(0o644)
            self.assertIsNone(read())
            path.chmod(0o600)
            path.write_bytes(b"x" * 8193)
            self.assertIsNone(read())
            path.write_text("[]")
            self.assertIsNone(read())
            path.unlink()
            target = directory / "actual.json"
            target.write_text(json.dumps(record))
            target.chmod(0o600)
            path.symlink_to(target)
            self.assertIsNone(read())


if __name__ == "__main__":
    unittest.main()
