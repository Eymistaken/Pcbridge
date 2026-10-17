"""Reading files in pages for the fs_* tools.

`fs_read` used to return only the last `max_chars` characters, so the start
and middle of a large file could not be reached without `shell_run`. A page
here starts at a line (`start_line`, negative counts from the end) or at a
cursor from the previous page, ends on a line boundary when it can, and says
which lines it covers, how much is left and how to get the next page.

A cursor carries the byte offset, the line number and the first 16 hex
digits of the file's SHA-256. If the file changes between two pages the
cursor is refused instead of silently skipping or repeating content.
"""

from __future__ import annotations

import base64
import codecs
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

PAGE_MIN = 200
PAGE_MAX = 60000
_CHUNK = 1 << 20
_HEAD = 8192
_CURSOR_PREFIX = "c1."

# Signatures of known binary formats, so a refusal can say what the file is
# instead of only "binary". Detection does not depend on them: NUL bytes and
# invalid UTF-8 are refused too.
BINARY_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "PDF document"),
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"),
    (b"GIF89a", "GIF image"),
    (b"PK\x03\x04", "ZIP archive (also docx/xlsx/odt/jar)"),
    (b"\x1f\x8b", "gzip archive"),
    (b"\x7fELF", "ELF executable"),
    (b"SQLite format 3\x00", "SQLite database"),
)


class FileToolError(Exception):
    """A request that cannot be served; the message is for the caller."""


def sniff_binary(head: bytes) -> str | None:
    """Why the start of a file says it is binary, or None."""
    label = next((name for magic, name in BINARY_SIGNATURES if head.startswith(magic)), None)
    if label is None and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        label = "WebP image"
    if label is not None:
        return f"it is a binary file ({label})"
    if b"\x00" in head[:_HEAD]:
        return "it is a binary file (contains NUL bytes)"
    return None


def decode_text_file(raw: bytes) -> tuple[str | None, str | None]:
    """(text, None) or (None, reason) for a whole file's bytes."""
    refused = sniff_binary(raw)
    if refused:
        return None, refused
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, f"it is not valid UTF-8 text (invalid byte at offset {exc.start:,})"


@dataclass(frozen=True)
class Snapshot:
    path: Path
    size: int
    sha256: str
    lines: int
    head: bytes


def snapshot(path: Path) -> Snapshot:
    """Size, SHA-256 and line count of a file, in one streaming pass."""
    h = hashlib.sha256()
    newlines = 0
    size = 0
    head = b""
    last = b""
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            if len(head) < _HEAD:
                head += chunk[: _HEAD - len(head)]
            h.update(chunk)
            newlines += chunk.count(b"\n")
            size += len(chunk)
            last = chunk[-1:]
    lines = newlines + (1 if size and last != b"\n" else 0)
    return Snapshot(path, size, h.hexdigest(), lines, head)


def _path_tag(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:8]


def make_cursor(snap: Snapshot, offset: int, line: int) -> str:
    body = json.dumps({"o": offset, "l": line, "h": snap.sha256[:16], "p": _path_tag(snap.path)},
                      separators=(",", ":"))
    return _CURSOR_PREFIX + base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def parse_cursor(cursor: str, snap: Snapshot) -> tuple[int, int]:
    """(offset, line) of a cursor issued for this file, as it still is."""
    try:
        if not cursor.startswith(_CURSOR_PREFIX):
            raise ValueError
        raw = cursor[len(_CURSOR_PREFIX):]
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        offset, line, digest, tag = int(data["o"]), int(data["l"]), str(data["h"]), str(data["p"])
    except (ValueError, KeyError, TypeError):
        raise FileToolError("That cursor is not valid; pass the cursor exactly as the "
                            "previous page gave it, or start again with start_line.") from None
    if tag != _path_tag(snap.path):
        raise FileToolError("That cursor belongs to another file; pass the path it was issued for.")
    if digest != snap.sha256[:16]:
        raise FileToolError(
            f"`{snap.path}` changed since that cursor was issued, so the next page could skip "
            "or repeat content. Start again with start_line (for example start_line=1).")
    if not 0 <= offset <= snap.size or line < 1:
        raise FileToolError("That cursor is not valid for this file; start again with start_line.")
    return offset, line


def _offset_of_line(path: Path, line: int) -> int:
    """Byte offset where 1-based `line` starts."""
    if line <= 1:
        return 0
    seen = 0
    pos = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            start = 0
            while True:
                i = chunk.find(b"\n", start)
                if i < 0:
                    break
                seen += 1
                if seen == line - 1:
                    return pos + i + 1
                start = i + 1
            pos += len(chunk)
    return pos


@dataclass(frozen=True)
class Page:
    snap: Snapshot
    text: str
    first_line: int
    last_line: int
    start: int
    end: int
    starts_mid_line: bool
    ends_mid_line: bool
    next_cursor: str | None

    @property
    def eof(self) -> bool:
        return self.end >= self.snap.size


def _read_text(path: Path, offset: int, max_chars: int) -> str:
    """Up to `max_chars` characters of strict UTF-8 starting at `offset`."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    out: list[str] = []
    have = 0
    pos = offset
    with path.open("rb") as fh:
        fh.seek(offset)
        while have < max_chars:
            want = max(4096, (max_chars - have) * 4)
            raw = fh.read(want)
            final = len(raw) < want
            try:
                text = decoder.decode(raw, final=final)
            except UnicodeDecodeError as exc:
                bad = pos + exc.start
                raise FileToolError(
                    f"it is not valid UTF-8 text (invalid byte at offset {bad:,})") from None
            pos += len(raw)
            out.append(text)
            have += len(text)
            if final:
                break
    return "".join(out)[:max_chars]


def read_page(path: Path, *, start_line: int | None = None, cursor: str | None = None,
              max_chars: int = 8000, max_lines: int | None = None,
              snap: Snapshot | None = None) -> Page:
    """One page of a UTF-8 text file; see the module docstring."""
    snap = snap or snapshot(path)
    refused = sniff_binary(snap.head)
    if refused:
        raise FileToolError(refused)
    if cursor:
        offset, line = parse_cursor(cursor, snap)
        mid = offset > 0 and _byte_before(path, offset) != b"\n"
    else:
        want = 1 if start_line in (None, 0) else start_line
        if want < 0:
            want = max(1, snap.lines + want + 1)
        if want > max(snap.lines, 1):
            raise FileToolError(
                f"start_line {start_line} is past the end: `{path}` has {snap.lines:,} lines.")
        offset, line, mid = _offset_of_line(path, want), want, False
    text = _read_text(path, offset, max_chars)
    if max_lines is not None and max_lines > 0:
        cut = -1
        for _ in range(max_lines):
            cut = text.find("\n", cut + 1)
            if cut < 0:
                break
        if cut >= 0:
            text = text[: cut + 1]
    end = offset + len(text.encode("utf-8"))
    if end < snap.size and not text.endswith("\n") and "\n" in text:
        # End on a line boundary; only a single line longer than the page is cut.
        text = text[: text.rfind("\n") + 1]
        end = offset + len(text.encode("utf-8"))
    newlines = text.count("\n")
    ends_mid = bool(text) and not text.endswith("\n") and end < snap.size
    last = line + newlines - (1 if text.endswith("\n") else 0) if text else line - 1
    nxt = make_cursor(snap, end, line + newlines) if end < snap.size else None
    return Page(snap, text, line, last, offset, end, mid, ends_mid, nxt)


def _byte_before(path: Path, offset: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(offset - 1)
        return fh.read(1)


def fence_for(text: str) -> str:
    """A Markdown fence longer than any run of backticks in `text`."""
    run = longest = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def number_lines(text: str, first_line: int, continues: bool) -> str:
    """Prefix each line with its number; a continued line gets `+` instead."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out = []
    for i, ln in enumerate(lines):
        n = first_line + i
        mark = "+" if (i == 0 and continues) else str(n)
        out.append(f"{mark:>6}\t{ln}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def format_page(page: Page, *, line_numbers: bool = False) -> str:
    s = page.snap
    head = f"`{s.path}` · {plural(s.size, 'byte')} · {plural(s.lines, 'line')} · sha256 {s.sha256}"
    if not page.text:
        span = "Nothing to show here: the file is empty." if not s.size else \
            "Nothing left: this is the end of the file."
        return f"{head}\n{span}"
    span = f"Lines {page.first_line:,}–{page.last_line:,} of {s.lines:,}"
    notes = []
    if page.starts_mid_line:
        notes.append(f"continuing line {page.first_line:,} from the previous page")
    if page.ends_mid_line:
        notes.append(f"line {page.last_line:,} is longer than the page and continues on the next one")
    if notes:
        span += " (" + "; ".join(notes) + ")"
    body = number_lines(page.text, page.first_line, page.starts_mid_line) if line_numbers \
        else page.text
    fence = fence_for(body)
    if page.eof:
        tail = "End of file."
    else:
        left = s.size - page.end
        tail = (f"Not the end: {left:,} bytes left. Next page: fs_read with "
                f"cursor=\"{page.next_cursor}\".")
    # Only the page's own final newline goes; blank lines before it stay.
    body = body[:-1] if body.endswith("\n") else body
    return f"{head}\n{span}\n{fence}\n{body}\n{fence}\n{tail}"
