"""Word passphrases for the remote password.

As strong as a random string of the same entropy, but easy to type on a
phone: six words from the EFF Large Wordlist (`data/README.md`), chosen with
`secrets`, joined by hyphens, for example `table-cloud-pencil-sea-seven-oak`.
"""

from __future__ import annotations

import functools
import math
import secrets
from pathlib import Path

WORDLIST = Path(__file__).resolve().parent / "data" / "eff_large_wordlist.txt"
DEFAULT_WORDS = 6


@functools.lru_cache(maxsize=1)
def words() -> tuple[str, ...]:
    """The wordlist without its dice numbers and without hyphenated words."""
    out = []
    for line in WORDLIST.read_text(encoding="utf-8").splitlines():
        word = line.split("\t")[-1].strip()
        if word and "-" not in word:
            out.append(word)
    return tuple(out)


def generate(count: int = DEFAULT_WORDS) -> str:
    pool = words()
    return "-".join(secrets.choice(pool) for _ in range(count))


def bits(count: int = DEFAULT_WORDS) -> float:
    """The entropy of a passphrase of `count` words."""
    return count * math.log2(len(words()))
