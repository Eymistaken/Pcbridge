"""The audit log has one writer, so its rotation holds for every line."""

from __future__ import annotations

import json
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import auditing  # noqa: E402
from pcbridge import remoteclients as rc  # noqa: E402


class WriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "audit.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_line_is_json_with_the_time_and_the_event(self) -> None:
        auditing.write_line(self.path, auditing.record("shell_run", {"cmd": "ls", "exit": 0}))
        rec = json.loads(self.path.read_text())
        self.assertEqual(rec["event"], "shell_run")
        self.assertEqual(rec["cmd"], "ls")
        self.assertRegex(rec["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")

    def test_a_new_file_is_private(self) -> None:
        auditing.write_line(self.path, "{}")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_it_rotates_to_one_copy_at_the_limit(self) -> None:
        for i in range(30):
            auditing.write_line(self.path, json.dumps({"i": i, "pad": "x" * 40}), max_bytes=500)
        rotated = Path(f"{self.path}.1")
        self.assertTrue(rotated.exists())
        self.assertLess(self.path.stat().st_size, 500 + 80)
        self.assertLess(rotated.stat().st_size, 500 + 80)
        self.assertFalse(Path(f"{self.path}.2").exists())

    def test_zero_turns_rotation_off(self) -> None:
        for i in range(30):
            auditing.write_line(self.path, json.dumps({"i": i, "pad": "x" * 40}), max_bytes=0)
        self.assertFalse(Path(f"{self.path}.1").exists())

    def test_append_drops_empty_fields_and_rotates_with_the_config_limit(self) -> None:
        cfg = SimpleNamespace(audit_log=self.path, audit_max_bytes=200)
        for _ in range(10):
            auditing.append(cfg, "job_cancel", job="20261114-000000-abcdef", client=None)
        rec = json.loads(self.path.read_text().splitlines()[-1])
        self.assertNotIn("client", rec)
        self.assertTrue(Path(f"{self.path}.1").exists())

    def test_remote_revocation_rotates_too(self) -> None:
        self.path.write_text("x" * 300 + "\n")
        rc._audit(self.path, "remote_client_revoked", 200, names=["Google"])
        self.assertTrue(Path(f"{self.path}.1").exists())
        self.assertEqual(json.loads(self.path.read_text())["names"], ["Google"])


class OneWriterTests(unittest.TestCase):
    def test_no_module_appends_to_the_audit_log_by_itself(self) -> None:
        """auth.py once appended without the rotation check; every writer
        now goes through auditing.write_line."""
        for name in ("auth.py", "remoteclients.py", "desktop/safety.py"):
            text = (ROOT / "pcbridge" / name).read_text(encoding="utf-8")
            self.assertIn("auditing.", text, name)
            self.assertIsNone(re.search(r"audit_log\)?\.open\(", text), name)


if __name__ == "__main__":
    unittest.main()
