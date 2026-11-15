"""tmux key names become the bytes a terminal sends (the Windows sessions)."""

from __future__ import annotations

import unittest

from pcbridge.conpty import key_bytes


class KeyBytesTest(unittest.TestCase):
    def test_named_keys(self) -> None:
        self.assertEqual(key_bytes("Enter"), "\r")
        self.assertEqual(key_bytes("Escape"), "\x1b")
        self.assertEqual(key_bytes("Up"), "\x1b[A")
        self.assertEqual(key_bytes("PageDown"), "\x1b[6~")
        self.assertEqual(key_bytes("NPage"), "\x1b[6~")
        self.assertEqual(key_bytes("F5"), "\x1b[15~")
        self.assertEqual(key_bytes("BSpace"), "\x7f")

    def test_control_and_meta(self) -> None:
        self.assertEqual(key_bytes("C-c"), "\x03")
        self.assertEqual(key_bytes("C-D"), "\x04")
        self.assertEqual(key_bytes("M-x"), "\x1bx")

    def test_an_unknown_name_is_text_as_in_tmux(self) -> None:
        self.assertEqual(key_bytes("q"), "q")
        self.assertEqual(key_bytes("yes"), "yes")


if __name__ == "__main__":
    unittest.main()
