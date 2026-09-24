"""The settings registry and editor (`pcbridge/settings.py`).

Covers the roadmap's "done" line for the settings CLI: every setting the
loader reads can be changed, and a change never loses data -- comments and
untouched bytes stay, a file that would not load is never written, the old
file is backed up, secrets are never shown.
"""

from __future__ import annotations

import dataclasses
import difflib
import os
import re
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
from pcbridge import settings as S  # noqa: E402
from tests.contracts.test_english_only import _is_turkish  # noqa: E402

PASSWORD = "settings-test-password-1234"
TOKEN = "settings-test-static-token-5678"


def example_config(state: Path) -> str:
    """config.example.toml with the fields a real file must have."""
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    text = re.sub(r'(?m)^public_url = .*$', 'public_url = "http://localhost:8765"', text, count=1)
    text = re.sub(r'(?m)^password = .*$', f'password = "{PASSWORD}"', text, count=1)
    text = re.sub(r'(?m)^static_token = .*$', f'static_token = "{TOKEN}"', text, count=1)
    text = re.sub(r'(?m)^state_dir = .*$', f'state_dir = "{state}"', text, count=1)
    return text


class EditorCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.toml"
        self.original = example_config(self.dir / "state")
        self.path.write_text(self.original, encoding="utf-8")
        self.path.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PCBRIDGE_")}
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def editor(self) -> S.ConfigEditor:
        return S.ConfigEditor(self.path)

    def backups(self) -> list[Path]:
        return sorted(self.dir.glob("config.toml.backup-*"))

    def changed_lines(self, before: str, after: str) -> list[str]:
        return [ln for ln in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
                if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


class RegistryTests(EditorCase):
    def test_every_key_the_loader_reads_is_editable_or_excused(self) -> None:
        keys = {s.key for s in S.registry(["claude"])}
        sections = {"auth", "paths", "limits", "server", "native", "desktop", "agents", "tools"}
        expected = {k for k in configlib._TOP_KEYS if k not in sections}
        expected.discard("inline_images")  # the legacy top-level spelling of server.inline_images
        for section, names in configlib._SECTION_KEYS.items():
            expected |= {f"{section}.{n}" for n in names}
        expected |= {f"desktop.{f.name}" for f in dataclasses.fields(configlib.DesktopSpec)}
        expected |= {f"agents.claude.{f.name}" for f in dataclasses.fields(configlib.AgentSpec)
                     if f.name != "name"}
        missing = expected - keys - set(S.NOT_EDITABLE)
        self.assertEqual(missing, set(), "settings the loader reads but the editor cannot change")
        self.assertEqual(keys - expected, set(), "registry entries the loader does not read")

    def test_every_setting_has_an_english_description(self) -> None:
        for s in S.registry(["claude"]):
            self.assertTrue(s.help.strip(), f"{s.key} has no description")
            self.assertFalse(_is_turkish(s.help), f"{s.key}: {s.help[:80]}")
            self.assertIn(s.kind, S.KINDS, s.key)

    def test_registry_defaults_are_the_loaders(self) -> None:
        minimal = (
            'public_url = "http://localhost:8765"\n'
            f'[auth]\npassword = "{PASSWORD}"\n'
            f'[paths]\nstate_dir = "{self.dir / "state"}"\n'
            '[agents.claude]\ncommand = ["true"]\n'
        )
        self.path.write_text(minimal)
        cfg = configlib.load_config(str(self.path), check_state=False)
        ed = self.editor()
        attr = {"server.inline_images": cfg.inline_images, "tools.profile": cfg.tools_profile}
        for s in ed.settings:
            if s.secret or s.key.startswith("paths.") or s.key == "public_url":
                continue
            if s.key in attr:
                actual = attr[s.key]
            elif s.section in ("", "auth", "limits"):
                actual = getattr(cfg, s.name)
            elif s.section == "native":
                actual = getattr(cfg.native, s.name)
                if s.name == "binary_path":
                    actual = "" if actual is None else str(actual)
            elif s.section == "desktop":
                actual = getattr(cfg.desktop, s.name)
            else:
                actual = getattr(cfg.agents["claude"], s.name)
                if s.name == "command":
                    continue
            self.assertEqual(ed.value(s.key), actual, s.key)

    def test_descriptions_come_from_the_example_file(self) -> None:
        helps = S.example_help()
        self.assertIn("deliberate", helps["desktop.enabled"])
        self.assertIn("12 hours", helps["auth.access_token_ttl"])


class EditTests(EditorCase):
    def test_a_file_that_is_only_read_and_saved_is_unchanged(self) -> None:
        ed = self.editor()
        self.assertEqual(ed.render(), self.original)
        result = ed.save()
        self.assertEqual(result.changed, [])
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.backups(), [])

    def test_each_kind_round_trips_and_only_its_line_changes(self) -> None:
        cases = {
            "desktop.restore_clipboard": ("false", False),
            "desktop.unlock_idle_seconds": ("45", 45),
            "desktop.capture_backend": ("screencast", "screencast"),
            "desktop.gui_launch_blocklist": ('["Text Editor", "Firefox"]', ["Text Editor", "Firefox"]),
            "desktop.ocr_languages": ("eng+deu", "eng+deu"),
            "server.inline_images": ("auto", "auto"),
            "port": ("8800", 8800),
        }
        for key, (text, expected) in cases.items():
            with self.subTest(key=key):
                self.path.write_text(self.original)
                ed = self.editor()
                s = ed.setting(key)
                ed.set(key, S.parse_value(s, text))
                ed.save()
                after = self.path.read_text()
                self.assertEqual(S.ConfigEditor(self.path).value(key), expected)
                changed = self.changed_lines(self.original, after)
                self.assertEqual(len(changed), 2, changed)
                self.assertTrue(changed[1].startswith(f"+{s.name}"), changed)

    def test_inline_comment_survives_a_change(self) -> None:
        ed = self.editor()
        ed.set("auth.access_token_ttl", 3600)
        ed.save()
        self.assertIn("access_token_ttl = 3600      # 12 hours", self.path.read_text())

    def test_other_blocks_stay_byte_identical(self) -> None:
        block = re.search(r"(?ms)^\[agents\.antigravity\].*?(?=^# More agents)", self.original).group(0)
        ed = self.editor()
        ed.set("desktop.enabled", True)
        ed.set("agents.claude.default_model", "opus")
        ed.save()
        after = self.path.read_text()
        self.assertIn(block, after)

    def test_a_table_edit_keeps_the_comments_of_other_entries(self) -> None:
        ed = self.editor()
        aliases = dict(ed.value("agents.claude.aliases"))
        aliases["opus five"] = "opus"
        del aliases["orta"]
        ed.set("agents.claude.aliases", aliases)
        ed.save()
        after = self.path.read_text()
        self.assertIn("# Variants of a blocked model map to it too", after)
        self.assertNotIn('"orta"', after.split("[agents.antigravity]")[0])
        self.assertEqual(S.ConfigEditor(self.path).value("agents.claude.aliases")["opus five"], "opus")

    def test_a_multiline_list_stays_multiline(self) -> None:
        ed = self.editor()
        ed.set("agents.claude.command", ["claude", "-p", "{prompt}", "--verbose"])
        ed.save()
        self.assertRegex(self.path.read_text(), r'command = \[\n\s+"claude",')

    def test_a_missing_key_goes_into_its_own_section(self) -> None:
        text = self.original.replace("max_sync_timeout = 120\n", "")
        self.path.write_text(text)
        ed = self.editor()
        ed.set("limits.max_sync_timeout", 90)
        ed.save()
        raw = tomllib.loads(self.path.read_text())
        self.assertEqual(raw["limits"]["max_sync_timeout"], 90)
        # Not after the banner comment that belongs to the next section.
        after = self.path.read_text()
        self.assertLess(after.index("max_sync_timeout = 90"), after.index("# NATIVE DESKTOP HELPER"))

    def test_a_missing_section_is_created(self) -> None:
        text = re.sub(r"(?ms)^# -+\n# \[tools\].*", "", self.original)
        self.assertNotIn("[tools]", text)
        self.path.write_text(text)
        ed = self.editor()
        ed.set("tools.profile", "core")
        result = ed.save()
        self.assertEqual(tomllib.loads(self.path.read_text())["tools"]["profile"], "core")
        self.assertEqual(result.restart, S.RESTART_CLIENTS)

    def test_a_missing_top_level_key_goes_before_the_first_table(self) -> None:
        text = self.original.replace('mcp_path = "/mcp"\n', "")
        self.path.write_text(text)
        ed = self.editor()
        ed.set("mcp_path", "/other")
        ed.save()
        self.assertEqual(tomllib.loads(self.path.read_text())["mcp_path"], "/other")

    def test_reset_removes_the_key_so_the_default_applies(self) -> None:
        ed = self.editor()
        ed.reset("desktop.pointer_speed")
        ed.save()
        ed = self.editor()
        self.assertFalse(ed.is_set("desktop.pointer_speed"))
        self.assertEqual(ed.value("desktop.pointer_speed"), 5000)


class SafetyTests(EditorCase):
    def test_an_invalid_value_is_refused_and_nothing_is_written(self) -> None:
        ed = self.editor()
        ed.set("desktop.unlock_default_minutes", 500)  # above unlock_max_minutes
        with self.assertRaises(S.SettingsError) as ctx:
            ed.save()
        self.assertIn("unlock_max_minutes", str(ctx.exception))
        self.assertIn(str(self.path), str(ctx.exception))
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.backups(), [])
        self.assertEqual(list(self.dir.glob(".config-check-*")), [])

    def test_a_save_backs_up_the_old_file_and_keeps_mode_0600(self) -> None:
        ed = self.editor()
        ed.set("desktop.enabled", True)
        result = ed.save()
        self.assertIsNotNone(result.backup)
        self.assertEqual(result.backup.read_text(), self.original)
        for p in (self.path, result.backup):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p)
        self.assertFalse(self.path.with_name("config.toml.tmp").exists())

    def test_a_file_changed_on_disk_is_not_overwritten(self) -> None:
        ed = self.editor()
        ed.set("desktop.enabled", True)
        self.path.write_text(self.original + "\n# edited by hand\n")
        with self.assertRaises(S.SettingsError):
            ed.save()
        self.assertTrue(self.path.read_text().endswith("# edited by hand\n"))

    def test_validation_does_not_create_the_state_directory(self) -> None:
        ed = self.editor()
        ed.set("paths.state_dir", str(self.dir / "typo" / "state"))
        ed.save()
        self.assertFalse((self.dir / "typo").exists())

    def test_secrets_are_never_formatted(self) -> None:
        ed = self.editor()
        for key in ("auth.password", "auth.static_token"):
            s = ed.setting(key)
            self.assertTrue(s.secret)
            shown = S.format_value(s, ed.value(key))
            self.assertNotIn(PASSWORD, shown)
            self.assertNotIn(TOKEN, shown)
            self.assertEqual(shown, "set (hidden)")

    def test_an_environment_override_is_reported(self) -> None:
        with mock.patch.dict(os.environ, {"PCBRIDGE_PASSWORD": "x" * 20}):
            self.assertEqual(self.editor().env_override("auth.password"), "PCBRIDGE_PASSWORD")
        self.assertIsNone(self.editor().env_override("auth.password"))

    def test_unknown_and_excluded_keys_say_why(self) -> None:
        ed = self.editor()
        with self.assertRaisesRegex(S.SettingsError, "not editable"):
            ed.setting("config_version")
        with self.assertRaisesRegex(S.SettingsError, "Did you mean: desktop.enabled"):
            ed.setting("enabled")
        with self.assertRaisesRegex(S.SettingsError, r"no \[agents.codex\] block"):
            ed.setting("agents.codex.enabled")


class ParseTests(unittest.TestCase):
    def setting(self, kind: str, choices: tuple[str, ...] = ()) -> S.Setting:
        return S.Setting("x.y", "x", "y", kind, None, choices)

    def test_values(self) -> None:
        self.assertIs(S.parse_value(self.setting("bool"), "Yes"), True)
        self.assertIs(S.parse_value(self.setting("bool"), "off"), False)
        self.assertEqual(S.parse_value(self.setting("int"), "5_000"), 5000)
        self.assertEqual(S.parse_value(self.setting("choice", ("a", "b")), '"B"'), "b")
        self.assertEqual(S.parse_value(self.setting("str"), '"a b"'), "a b")
        self.assertEqual(S.parse_value(self.setting("list"), "a, b ,c"), ["a", "b", "c"])
        self.assertEqual(S.parse_value(self.setting("list"), '["a", "b"]'), ["a", "b"])
        self.assertEqual(S.parse_value(self.setting("table"), 'opus = "high"'), {"opus": "high"})

    def test_bad_values(self) -> None:
        for kind, text in (("bool", "maybe"), ("int", "ten"), ("list", "[1,"), ("table", "{x")):
            with self.subTest(kind=kind), self.assertRaises(S.SettingsError):
                S.parse_value(self.setting(kind), text)
        with self.assertRaises(S.SettingsError):
            S.parse_value(self.setting("choice", ("a",)), "z")


if __name__ == "__main__":
    unittest.main()
