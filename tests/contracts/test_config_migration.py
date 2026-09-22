"""Config discovery, versioned migration and unknown-key warnings (2.0 step 2).

The migration must never change what a config file means: the migrated file
loads into the same effective settings as the original. The comparison is on
the loaded dataclasses, not on the text.

Set PCBRIDGE_TEST_REAL_CONFIG=/path/to/config.toml to also migrate a real
file (a copy is made; the original is only read).
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import config as configlib  # noqa: E402
from pcbridge import paths as pathslib  # noqa: E402

V1_SAMPLE = """\
# A pre-2.0 config: no config_version key.
public_url = "http://localhost:8765"
host = "127.0.0.1"
port = 8765

[auth]
password = "correct horse battery staple"
static_token = "sample-static-token-for-tests"

[paths]
default_workdir = "~"
state_dir = "{state}"

[limits]
max_output_chars = 12000
default_agent = "claude"   # misplaced on purpose: a real file had this

[desktop]
enabled = false
keyboard_layout = "tr+intl"

[agents.claude]
command = ["claude", "-p", "{{prompt}}"]
parser = "plain"
"""

IGNORED = {"source_path", "source_kind", "config_version", "warnings"}


def _effective(cfg: configlib.Config) -> dict:
    data = dataclasses.asdict(cfg)
    for key in IGNORED:
        data.pop(key, None)
    return data


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb-cfg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "state"
        env = {
            "XDG_CONFIG_HOME": str(self.tmp / "xdg-config"),
            "XDG_STATE_HOME": str(self.tmp / "xdg-state"),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("PCBRIDGE_CONFIG", None)
        self.legacy = self.tmp / "repo" / "config.toml"
        self.legacy.parent.mkdir()
        self.legacy.write_text(V1_SAMPLE.format(state=self.state))
        os.chmod(self.legacy, 0o600)
        p = mock.patch.object(pathslib, "LEGACY_REPO_CONFIG", self.legacy)
        p.start()
        self.addCleanup(p.stop)

    def test_legacy_file_is_found_and_flagged(self) -> None:
        cfg = configlib.load_config()
        self.assertEqual(cfg.source_kind, "legacy")
        self.assertEqual(cfg.config_version, 1)
        self.assertTrue(any("legacy config file" in w for w in cfg.warnings))

    def test_xdg_wins_over_legacy_and_env_wins_over_both(self) -> None:
        result = configlib.migrate_config(self.legacy)
        self.assertEqual(result.dest, pathslib.config_file())
        cfg = configlib.load_config()
        self.assertEqual((cfg.source_kind, cfg.source_path), ("xdg", pathslib.config_file()))
        other = self.tmp / "other.toml"
        shutil.copy(self.legacy, other)
        with mock.patch.dict(os.environ, {"PCBRIDGE_CONFIG": str(other)}):
            self.assertEqual(configlib.load_config().source_kind, "env")

    def test_migration_keeps_effective_settings(self) -> None:
        before = configlib.load_config(str(self.legacy))
        result = configlib.migrate_config(self.legacy)
        self.assertTrue(result.changed)
        self.assertEqual((result.from_version, result.to_version), (1, configlib.CONFIG_VERSION))
        after = configlib.load_config(str(result.dest))
        self.assertEqual(after.config_version, configlib.CONFIG_VERSION)
        self.assertEqual(_effective(before), _effective(after))
        # The old OCR default is pinned, not silently changed.
        self.assertEqual(after.desktop.ocr_languages, "tur+eng")

    def test_migration_writes_0600_backs_up_and_is_idempotent(self) -> None:
        dest = pathslib.config_file()
        dest.parent.mkdir(parents=True)
        dest.write_text("# an older hand-written file\n" + V1_SAMPLE.format(state=self.state))
        first = configlib.migrate_config(self.legacy)
        self.assertIsNotNone(first.backup)
        self.assertTrue(first.backup.exists())
        self.assertIn("an older hand-written file", first.backup.read_text())
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(first.backup.stat().st_mode), 0o600)
        second = configlib.migrate_config(self.legacy)
        self.assertFalse(second.changed)
        self.assertIsNone(second.backup)
        # The source is never touched when it is not the destination.
        self.assertNotIn("config_version", tomllib.loads(self.legacy.read_text()))

    def test_new_files_get_new_defaults_old_files_keep_old_ones(self) -> None:
        self.assertEqual(configlib.load_config(str(self.legacy)).desktop.ocr_languages, "tur+eng")
        v2 = self.tmp / "v2.toml"
        v2.write_text("config_version = 2\n" + self.legacy.read_text())
        os.chmod(v2, 0o600)
        self.assertEqual(configlib.load_config(str(v2)).desktop.ocr_languages, "eng")

    def test_comments_survive_migration(self) -> None:
        text, start = configlib.migrate_text(V1_SAMPLE.format(state=self.state))
        self.assertEqual(start, 1)
        self.assertIn("# misplaced on purpose", text)
        self.assertEqual(tomllib.loads(text)["config_version"], configlib.CONFIG_VERSION)
        again, start2 = configlib.migrate_text(text)
        self.assertEqual((again, start2), (text, configlib.CONFIG_VERSION))

    def test_unknown_keys_warn_in_english_and_do_not_crash(self) -> None:
        cfg = configlib.load_config(str(self.legacy))
        joined = "\n".join(cfg.warnings)
        self.assertIn("[limits] `default_agent`", joined)
        self.assertIn("belongs at the top of the file", joined)
        text = self.legacy.read_text()
        self.legacy.write_text(text.replace("enabled = false", "enabled = false\nenabeld = true"))
        cfg = configlib.load_config(str(self.legacy))
        self.assertTrue(any("[desktop] `enabeld`" in w for w in cfg.warnings))
        self.assertFalse(cfg.desktop.enabled)

    def test_loose_mode_warns(self) -> None:
        os.chmod(self.legacy, 0o644)
        cfg = configlib.load_config(str(self.legacy))
        self.assertTrue(any("readable by other users" in w for w in cfg.warnings))

    def test_invalid_toml_is_an_english_message_not_a_traceback(self) -> None:
        self.legacy.write_text("public_url = \n")
        with self.assertRaises(SystemExit) as ctx:
            configlib.load_config(str(self.legacy))
        self.assertIn("is not valid TOML", str(ctx.exception))

    def test_missing_config_names_the_setup_command(self) -> None:
        self.legacy.unlink()
        with self.assertRaises(SystemExit) as ctx:
            configlib.load_config()
        self.assertIn("pcbridge setup", str(ctx.exception))

    @unittest.skipUnless(os.environ.get("PCBRIDGE_TEST_REAL_CONFIG"), "set PCBRIDGE_TEST_REAL_CONFIG")
    def test_real_config_migrates_to_identical_settings(self) -> None:
        real = Path(os.environ["PCBRIDGE_TEST_REAL_CONFIG"])
        copy = self.tmp / "real-copy.toml"
        shutil.copy2(real, copy)
        before = configlib.load_config(str(copy))
        result = configlib.migrate_config(copy, self.tmp / "migrated.toml")
        after = configlib.load_config(str(result.dest))
        self.assertEqual(_effective(before), _effective(after))
        self.assertEqual(stat.S_IMODE(result.dest.stat().st_mode), 0o600)


class ExampleConfigTests(unittest.TestCase):
    def test_example_is_current_and_has_no_unknown_keys(self) -> None:
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        raw = tomllib.loads(text)
        self.assertEqual(raw.get("config_version"), configlib.CONFIG_VERSION)
        self.assertEqual(configlib._unknown_key_warnings(raw), [])


class PathsTests(unittest.TestCase):
    def test_xdg_values_are_honored_and_relative_ones_ignored(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/x/state", "XDG_CONFIG_HOME": "rel"}):
            self.assertEqual(pathslib.state_home(), Path("/x/state/pcbridge"))
            self.assertEqual(pathslib.config_home(), Path.home() / ".config" / "pcbridge")

    def test_runtime_dir_is_created_private(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="pcb-run-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.chmod(tmp, 0o700)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(tmp)}):
            (tmp / "pcbridge").mkdir(mode=0o755)
            os.chmod(tmp / "pcbridge", 0o755)
            path = pathslib.runtime_dir(create=True)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertEqual(pathslib.socket_path(), path / "mcp.sock")


if __name__ == "__main__":
    unittest.main()
