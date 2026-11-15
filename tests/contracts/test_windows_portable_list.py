"""tests/windows_portable.txt names test modules that exist, once each."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

LIST = Path(__file__).resolve().parents[1] / "windows_portable.txt"


class WindowsPortableListTest(unittest.TestCase):
    def test_every_entry_is_an_existing_test_module(self) -> None:
        entries = [line.strip() for line in LIST.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.startswith("#")]
        self.assertTrue(entries)
        self.assertEqual(len(entries), len(set(entries)), "duplicate entries")
        self.assertEqual(entries, sorted(entries), "keep the list sorted")
        for name in entries:
            with self.subTest(name=name):
                self.assertTrue(name.startswith("tests."), name)
                self.assertIsNotNone(importlib.util.find_spec(name), name)


if __name__ == "__main__":
    unittest.main()
