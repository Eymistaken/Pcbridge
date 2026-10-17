"""Reading, writing and editing files for the fs_* tools.

`fs_read` used to return only the last `max_chars` characters, so the start
and middle of a large file could not be reached without `shell_run`. A page
here starts at a line (`start_line`, negative counts from the end) or at a
cursor from the previous page, ends on a line boundary when it can, and says
which lines it covers, how much is left and how to get the next page.

A cursor carries the byte offset, the line number and the first 16 hex
digits of the file's SHA-256. If the file changes between two pages the
cursor is refused instead of silently skipping or repeating content.

Writes (`fs_write`, `fs_edit`) are atomic and can be made conditional on the
SHA-256 a read reported; see the "writing" section below.
"""

from __future__ import annotations

import base64
import codecs
import difflib
import hashlib
import io
import json
import os
import tempfile
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


def binary_label(head: bytes) -> str | None:
    """The known binary format the start of a file names, or None."""
    label = next((name for magic, name in BINARY_SIGNATURES if head.startswith(magic)), None)
    if label is None and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        label = "WebP image"
    return label


def sniff_binary(head: bytes) -> str | None:
    """Why the start of a file says it is binary, or None."""
    label = binary_label(head)
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


# ---------------------------------------------------------------- writing
#
# Every write goes to a temporary file next to the target and replaces it
# with os.replace(), so a reader never sees half a file and a crash leaves
# the old one. `expected_sha256` turns a write into "only if the file is
# still what I read": the hash is checked again just before the replace, so
# a change made in between is a conflict, not a silent overwrite.

_HEX = frozenset("0123456789abcdef")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_expected(path: Path, current: bytes | None, expected: str | None) -> None:
    """Raise unless the file is what the caller last read."""
    if expected is None:
        return
    want = expected.strip().lower()
    if len(want) < 12 or not set(want) <= _HEX:
        raise FileToolError("expected_sha256 must be the file's SHA-256 in hex (at least the "
                            "first 12 digits), as fs_read or fs_edit reported it.")
    if current is None:
        raise FileToolError(f"Conflict: `{path}` does not exist, so it is not the file with "
                            f"sha256 {want}. Nothing was written.")
    have = sha256_of(current)
    if not have.startswith(want):
        raise FileToolError(
            f"Conflict: `{path}` changed since it was read (sha256 is now {have}, expected "
            f"{want}). Nothing was written; read it again and redo the change.")


def _current(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def atomic_write(path: Path, data: bytes, *, expected: str | None = None,
                 before: bytes | None = None) -> None:
    """Replace `path` with `data` in one step.

    `before` is what the caller based the change on; if the file no longer
    holds exactly that (or `expected` no longer matches), nothing is written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _file_mode(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".pcbridge-tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        now = _current(path)
        if before is not None and now != before:
            raise FileToolError(
                f"Conflict: `{path}` changed while this edit was being made (sha256 is now "
                f"{sha256_of(now) if now is not None else 'missing'}). Nothing was written; "
                "read it again and redo the change.")
        check_expected(path, now, expected)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------- editing


@dataclass(frozen=True)
class Edit:
    old_text: str
    new_text: str
    replace_all: bool = False


@dataclass(frozen=True)
class EditResult:
    before: bytes
    after: bytes
    replacements: int
    lines: list[tuple[int, int]]
    diff: str


def _newline_style(text: str) -> str | None:
    """"\\r\\n" when every line ends with CRLF, "\\n" when none does, None if mixed."""
    crlf = text.count("\r\n")
    lf = text.count("\n")
    if lf == 0:
        return None
    if crlf == lf:
        return "\r\n"
    if crlf == 0:
        return "\n"
    return None


def _match_lines(text: str, needle: str, limit: int = 10) -> list[int]:
    out, start = [], 0
    while len(out) < limit:
        i = text.find(needle, start)
        if i < 0:
            break
        out.append(text.count("\n", 0, i) + 1)
        start = i + max(1, len(needle))
    return out


def _not_found(text: str, old: str, n: int) -> FileToolError:
    hint = ""
    stripped = "\n".join(ln.strip() for ln in old.strip().splitlines())
    flat = "\n".join(ln.strip() for ln in text.splitlines())
    if stripped and stripped in flat:
        line = flat[: flat.index(stripped)].count("\n") + 1
        hint = (f" It does appear near line {line:,} with different indentation or trailing "
                "spaces; copy the text exactly as fs_read shows it.")
    return FileToolError(f"Edit {n}: old_text was not found, so nothing was changed.{hint}")


def apply_edits(path: Path, edits: list[Edit], *, context: int = 2) -> EditResult:
    """Apply `edits` in order to the file's text, in memory; nothing is written."""
    if not edits:
        raise FileToolError("Give old_text and new_text, or a list of edits.")
    raw = _current(path)
    if raw is None:
        raise FileToolError(f"File not found: {path}. Create it with fs_write.")
    text, refused = decode_text_file(raw)
    if text is None:
        raise FileToolError(f"`{path}` cannot be edited: {refused}.")
    bom = text.startswith("﻿")
    body = text[1:] if bom else text
    style = _newline_style(body)
    count = 0
    touched: list[tuple[int, int]] = []
    for n, e in enumerate(edits, 1):
        old, new = e.old_text, e.new_text
        if not old:
            raise FileToolError(f"Edit {n}: old_text is empty; use fs_write to create or append.")
        if style == "\r\n":
            # Match and write the file's own line endings, so a model that
            # sends "\n" neither misses the text nor mixes the endings.
            old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            new = new.replace("\r\n", "\n").replace("\n", "\r\n")
        found = body.count(old)
        if found == 0:
            raise _not_found(body, old, n)
        if found > 1 and not e.replace_all:
            where = ", ".join(f"{ln:,}" for ln in _match_lines(body, old))
            raise FileToolError(
                f"Edit {n}: old_text matches {found} places (lines {where}), so nothing was "
                "changed. Include more surrounding lines to make it unique, or set "
                "replace_all to change every one.")
        first = body.count("\n", 0, body.find(old)) + 1
        body = body.replace(old, new) if e.replace_all else body.replace(old, new, 1)
        count += found if e.replace_all else 1
        touched.append((first, first + new.count("\n")))
    after_text = ("﻿" if bom else "") + body
    after = after_text.encode("utf-8")
    diff = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), after_text.splitlines(keepends=True),
        fromfile=f"{path} (before)", tofile=f"{path} (after)", n=context))
    return EditResult(raw, after, count, touched, diff)


def trim_diff(diff: str, limit: int = 4000) -> str:
    if len(diff) <= limit:
        return diff
    cut = diff.rfind("\n", 0, limit)
    return diff[: cut + 1 if cut > 0 else limit] + f"…(diff truncated: {len(diff):,} characters)\n"


# ---------------------------------------------------------------- binary
#
# Any file, text or not, can be read as base64 chunks and written back the
# same way; the SHA-256 of the whole file is in every chunk's header, so the
# receiver can check the reassembled bytes. Chunked uploads collect in a
# staging file next to the target and replace it only when the hash the
# sender gives at the end matches.

IMAGE_TYPES = {"PNG image": "image/png", "JPEG image": "image/jpeg",
               "GIF image": "image/gif", "WebP image": "image/webp"}
# The Anthropic API scales a longer edge above 1568 px itself and refuses
# images over 5 MB of base64; stay under both so the picture arrives as sent.
IMAGE_LONG_EDGE = 1568
IMAGE_MAX_BYTES = 3_500_000


def chunk_bytes(max_chars: int) -> int:
    """Raw bytes per base64 chunk for a page budget of `max_chars` characters."""
    return max(3, (max_chars // 4) * 3)


@dataclass(frozen=True)
class Chunk:
    snap: Snapshot
    data: bytes
    start: int
    next_cursor: str | None

    @property
    def end(self) -> int:
        return self.start + len(self.data)


def read_chunk(path: Path, *, cursor: str | None = None, max_chars: int = 8000,
               snap: Snapshot | None = None) -> Chunk:
    snap = snap or snapshot(path)
    start = parse_cursor(cursor, snap)[0] if cursor else 0
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read(chunk_bytes(max_chars))
    end = start + len(data)
    return Chunk(snap, data, start, make_cursor(snap, end, 1) if end < snap.size else None)


def format_chunk(chunk: Chunk) -> str:
    s = chunk.snap
    kind = binary_label(s.head) or ("binary data" if sniff_binary(s.head) else "text")
    head = f"`{s.path}` · {plural(s.size, 'byte')} · {kind} · sha256 {s.sha256}"
    if not s.size:
        return f"{head}\nNothing to show here: the file is empty."
    text = base64.b64encode(chunk.data).decode("ascii")
    span = (f"Bytes {chunk.start:,}–{chunk.end - 1:,} of {s.size:,} as base64 "
            f"({len(text):,} characters)")
    if chunk.next_cursor:
        left = s.size - chunk.end
        calls = -(-left // max(1, len(chunk.data)))
        tail = (f"Not the end: {left:,} bytes left (about {calls:,} more call(s) of this size). "
                f"Next chunk: fs_read with encoding=\"base64\" and cursor=\"{chunk.next_cursor}\". "
                "The decoded chunks, joined in order, have the sha256 above.")
    else:
        tail = "End of file. The decoded chunks, joined in order, have the sha256 above."
    return f"{head}\n{span}\n```base64\n{text}\n```\n{tail}"


@dataclass(frozen=True)
class Preview:
    data: bytes
    mime: str
    note: str


def image_preview(path: Path, snap: Snapshot) -> Preview:
    """The picture for an image file: as it is when small enough, else scaled."""
    label = binary_label(snap.head) or ""
    mime = IMAGE_TYPES[label]
    raw = path.read_bytes()
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is None:
        if snap.size <= IMAGE_MAX_BYTES:
            return Preview(raw, mime, "shown as it is")
        raise FileToolError(
            f"it is a {label} of {snap.size:,} bytes, too large to show without Pillow "
            "(install pcbridge's desktop extra); read its exact bytes with encoding=\"base64\"")
    with Image.open(io.BytesIO(raw)) as im:
        w, h = im.size
        if max(w, h) <= IMAGE_LONG_EDGE and snap.size <= IMAGE_MAX_BYTES:
            return Preview(raw, mime, f"{w}x{h}, shown as it is")
        im.thumbnail((IMAGE_LONG_EDGE, IMAGE_LONG_EDGE))
        out = io.BytesIO()
        alpha = im.mode in ("RGBA", "LA", "P")
        if alpha:
            im.save(out, "PNG", optimize=True)
            mime2 = "image/png"
        else:
            im.convert("RGB").save(out, "JPEG", quality=85)
            mime2 = "image/jpeg"
        sw, sh = im.size
    return Preview(out.getvalue(), mime2,
                   f"{w}x{h}, shown scaled to {sw}x{sh}; the file itself is unchanged, and "
                   "encoding=\"base64\" gives its exact bytes")


def staging_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.pcbridge-upload")


@dataclass(frozen=True)
class Upload:
    path: Path
    staged: int
    done: bool
    sha256: str | None


def decode_base64(text: str) -> bytes:
    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (ValueError, TypeError):
        raise FileToolError("content_base64 is not valid base64.") from None


def _file_mode(path: Path) -> int:
    try:
        return path.stat().st_mode & 0o7777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


def upload_chunk(path: Path, data: bytes, offset: int, *, final_sha256: str | None = None,
                 expected: str | None = None, create_only: bool = False) -> Upload:
    """Add one chunk at `offset`; with `final_sha256`, check and put it in place."""
    stage = staging_path(path)
    have = stage.stat().st_size if stage.exists() else None
    if offset < 0:
        raise FileToolError("offset must be 0 or more.")
    if offset > 0 and have != offset:
        got = "nothing is staged" if have is None else f"{have:,} bytes are staged"
        raise FileToolError(
            f"Chunk out of order: {got} for `{path}`, so the next chunk must have offset="
            f"{have or 0}. Send offset=0 to start the upload over.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with stage.open("wb" if offset == 0 else "ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    staged = stage.stat().st_size
    if final_sha256 is None:
        return Upload(path, staged, False, None)
    want = final_sha256.strip().lower()
    if len(want) != 64 or not set(want) <= _HEX:
        raise FileToolError("final_sha256 must be the whole file's SHA-256: 64 hex digits.")
    h = hashlib.sha256()
    with stage.open("rb") as fh:
        while block := fh.read(_CHUNK):
            h.update(block)
    got = h.hexdigest()
    if got != want:
        stage.unlink()
        raise FileToolError(
            f"The {staged:,} uploaded bytes have sha256 {got}, not {want}; `{path}` was not "
            "changed and the upload was discarded. Start again with offset=0.")
    current = _current(path)
    if create_only and current is not None:
        stage.unlink()
        raise FileToolError(f"`{path}` already exists; the upload was discarded.")
    try:
        check_expected(path, current, expected)
    except FileToolError:
        stage.unlink()
        raise
    os.chmod(stage, _file_mode(path))
    os.replace(stage, path)
    return Upload(path, staged, True, got)
