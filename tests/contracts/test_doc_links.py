#!/usr/bin/env python3
"""Every relative link in the tracked Markdown files points at a file that
exists (Step 9 of 2.0: the doc link check CI runs). Web links are not
fetched; anchors are not checked."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(```|~~~)")


def tracked_markdown() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.md", "**/*.md"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    return [ROOT / p for p in out]


class DocLinkTests(unittest.TestCase):
    def test_relative_links_resolve(self) -> None:
        broken = []
        files = tracked_markdown()
        self.assertTrue(files)
        for md in files:
            in_code = False
            for n, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
                if FENCE.match(line.strip()):
                    in_code = not in_code
                    continue
                if in_code:
                    continue
                for target in LINK.findall(line):
                    if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                        continue  # http:, mailto:, an in-page anchor
                    path = target.split("#", 1)[0].split("?", 1)[0]
                    path = re.sub(r":\d+(-\d+)?$", "", path)  # file.py:42
                    if not path:
                        continue
                    if not (md.parent / path).exists():
                        broken.append(f"{md.relative_to(ROOT)}:{n}: {target}")
        self.assertEqual(broken, [], "broken relative links:\n" + "\n".join(broken))


if __name__ == "__main__":
    unittest.main()
