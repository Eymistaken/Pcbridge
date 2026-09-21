#!/usr/bin/env python3
"""Step 8.6: reading text off the screen with OCR.

tesseract is not installed on this machine (2026-09-22) and installing it
needs sudo, so the engine is replaced by a small fake `tesseract` script put
first on PATH. That still runs the real subprocess wrapper end to end: the
arguments, the PNG on stdin (never a file on disk), the upscale and the
inversion of dark frames, the TSV parsing, and the box divided back into the
screenshot's pixels. Matching, the MCP tools and the wait loop run on top of
it with the capture pipeline faked as in `test_mcp_contract`.

Nothing here reads the real screen or sends input.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop import ocr  # noqa: E402
from tests.contracts.test_mcp_contract import build_mcp  # noqa: E402

HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def tsv(*words: tuple) -> str:
    """Rows as tesseract 5 writes them: page/block/line records, then words."""
    rows = [HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t1920\t1080\t-1\t"]
    for block, par, line, number, left, top, width, height, conf, text in words:
        rows.append(f"4\t1\t{block}\t{par}\t{line}\t0\t{left}\t{top}\t{width}\t{height}\t-1\t")
        rows.append(
            f"5\t1\t{block}\t{par}\t{line}\t{number}\t{left}\t{top}\t{width}\t"
            f"{height}\t{conf}\t{text}"
        )
    return "\n".join(rows) + "\n"


SCREEN = tsv(
    (1, 1, 1, 1, 800, 400, 120, 24, 96.5, "Singleplayer"),
    (2, 1, 1, 1, 790, 460, 60, 24, 95.0, "Çok"),
    (2, 1, 1, 2, 856, 460, 110, 24, 93.1, "Oyunculu"),
    (3, 1, 1, 1, 100, 1000, 90, 20, 88.0, "Ayarlar"),
    (3, 1, 1, 2, 196, 1000, 10, 20, 40.0, "|"),
    (3, 1, 1, 3, 212, 1000, 70, 20, 91.0, "Çıkış"),
    (4, 1, 1, 1, 50, 50, 140, 20, 70.0, "Yükleniyor..."),
)


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.words = ocr.parse_tsv(SCREEN)

    def test_only_word_rows_are_kept_in_image_pixels(self) -> None:
        self.assertEqual(len(self.words), 7)
        first = self.words[0]
        self.assertEqual((first.text, first.left, first.top, first.width, first.height),
                         ("Singleplayer", 800, 400, 120, 24))
        halved = ocr.parse_tsv(SCREEN, factor=2.0)
        self.assertEqual((halved[0].left, halved[0].top, halved[0].width),
                         (400, 200, 60))

    def test_folding_ignores_case_turkish_letters_and_punctuation(self) -> None:
        self.assertEqual(ocr.fold("ÇIKIŞ"), "cikis")
        self.assertEqual(ocr.fold("İstanbul, ığdır!"), "istanbul igdir")
        self.assertEqual(ocr.fold("  Yükleniyor...  "), "yukleniyor")

    def test_an_exact_word_and_its_center(self) -> None:
        found = ocr.find(self.words, "singleplayer")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].exact)
        self.assertEqual(found[0].center, (860, 412))

    def test_several_words_match_consecutive_words_on_one_line(self) -> None:
        found = ocr.find(self.words, "cok oyunculu")
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].left, found[0].width), (790, 176))
        self.assertEqual(found[0].text, "Çok Oyunculu")

    def test_a_punctuation_word_does_not_break_a_phrase(self) -> None:
        found = ocr.find(self.words, "Ayarlar Çıkış")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].exact)

    def test_words_on_different_lines_are_not_joined(self) -> None:
        self.assertEqual(
            [m for m in ocr.find(self.words, "singleplayer cok") if m.exact], []
        )

    def test_a_misread_word_is_an_approximate_match(self) -> None:
        words = ocr.parse_tsv(tsv((1, 1, 1, 1, 10, 10, 100, 20, 60.0, "Singlep1ayer")))
        found = ocr.find(words, "Singleplayer")
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].exact)
        self.assertGreater(found[0].score, 0.85)
        self.assertTrue(ocr.seen(found))

    def test_a_weak_resemblance_is_not_seen(self) -> None:
        weak = ocr.Match("x", 0, 0, 1, 1, 50.0, exact=False, score=0.8)
        self.assertFalse(ocr.seen([weak]))
        self.assertEqual(ocr.find(self.words, "Tamamen başka bir şey"), [])

    def test_nearest_lines_give_a_hint(self) -> None:
        near = ocr.nearest_lines(self.words, "Yukleniyor")
        self.assertEqual(near[0].text, "Yükleniyor...")


class FakeEngineTests(unittest.TestCase):
    """The subprocess wrapper against a fake `tesseract` on PATH."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.seen = Path(self.tmp.name) / "seen"
        patcher = mock.patch.dict(os.environ, {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "FAKE_TESSERACT_SEEN": str(self.seen),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, langs: str = "eng\\ntur", body: str = "") -> None:
        script = self.bin / "tesseract"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, os, sys
            args = sys.argv[1:]
            if args == ["--list-langs"]:
                print('List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (2):')
                print("{langs}")
                sys.exit(0)
            data = sys.stdin.buffer.read()
            with open(os.environ["FAKE_TESSERACT_SEEN"], "wb") as handle:
                handle.write(data)
            with open(os.environ["FAKE_TESSERACT_SEEN"] + ".json", "w") as handle:
                json.dump(args, handle)
            {body}
            sys.stdout.write({SCREEN!r})
            """))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

    def png(self, size, color) -> Path:
        path = Path(self.tmp.name) / f"shot-{size[0]}.png"
        Image.new("RGB", size, color).save(path)
        return path

    def test_no_engine_is_reported_with_the_install_command(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            ok, why = ocr.available("tur+eng")
            self.assertFalse(ok)
            self.assertIn("sudo apt install tesseract-ocr tesseract-ocr-tur", why)
            with self.assertRaises(ocr.OcrError) as caught:
                ocr.read_words(self.png((1920, 1080), (250, 250, 250)), "tur+eng")
            self.assertTrue(caught.exception.missing)

    def test_missing_language_data_names_the_package(self) -> None:
        self.install(langs="eng")
        ok, why = ocr.available("tur+eng")
        self.assertFalse(ok)
        self.assertIn("tesseract-ocr-tur", why)
        self.assertTrue(ocr.available("eng")[0])

    def test_the_png_goes_on_stdin_and_the_boxes_come_back(self) -> None:
        self.install()
        words = ocr.read_words(self.png((1920, 1080), (250, 250, 250)), "tur+eng")
        self.assertEqual(json.loads(Path(f"{self.seen}.json").read_text()),
                         ["stdin", "stdout", "-l", "tur+eng", "--psm", "11", "tsv"])
        sent = self.seen.read_bytes()
        self.assertEqual(sent[:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(io.BytesIO(sent)) as image:
            # A whole monitor is not upscaled; it goes as grayscale.
            self.assertEqual((image.size, image.mode), ((1920, 1080), "L"))
        self.assertEqual(words[0].left, 800)

    def test_a_small_dark_image_is_inverted_upscaled_and_mapped_back(self) -> None:
        self.install()
        path = self.png((400, 300), (10, 10, 12))
        words = ocr.read_words(path, "tur+eng")
        with Image.open(io.BytesIO(self.seen.read_bytes())) as image:
            self.assertEqual(image.size, (800, 600))
            self.assertGreater(image.getpixel((5, 5)), 200, "a dark frame is inverted")
        # The fake answered in the upscaled space; boxes come back halved.
        self.assertEqual((words[0].left, words[0].top), (400, 200))

    def test_an_engine_failure_is_an_error_not_an_empty_answer(self) -> None:
        self.install(body='sys.stderr.write("Error opening data file\\\\n"); sys.exit(1)')
        with self.assertRaises(ocr.OcrError) as caught:
            ocr.read_words(self.png((1920, 1080), (250, 250, 250)), "tur+eng")
        self.assertIn("Error opening data file", str(caught.exception))
        self.assertFalse(caught.exception.missing)

    def test_a_bad_language_list_never_reaches_the_engine(self) -> None:
        self.install()
        with self.assertRaises(ocr.OcrError):
            ocr.read_words(self.png((1920, 1080), (250, 250, 250)), "tur; rm -rf")
        self.assertFalse(self.seen.exists())


MONITORS = monitorslib._ordered([
    monitorslib.Monitor(0, "DP-1", 1920, 0, 1920, 1080, 1.0, True),
    monitorslib.Monitor(0, "DP-2", 0, 0, 1920, 1080, 1.0, False),
])


class OcrToolTests(unittest.TestCase):
    """find_text and wait_for_text on the wire, with the capture faked."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mcp, self.store = build_mcp(Path(self.tmp.name))
        self.captures = 0
        self.patch(toolslib.capturelib, "available", return_value=(True, ""))
        self.patch(toolslib.capturelib, "capture", side_effect=self.fake_capture)
        self.patch(monitorslib, "list_monitors", return_value=MONITORS)
        self.patch(ocr, "available", return_value=(True, ""))

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_capture(self, spec, out_dir, scale_long_edge, **kwargs):
        self.captures += 1
        self.assertEqual(scale_long_edge, 0, "OCR must read the full resolution")
        self.assertFalse(kwargs.get("include_pointer"))
        monitor = MONITORS[1]
        path = Path(out_dir) / f"ocr-{self.captures}.png"
        Image.new("RGB", (1920, 1080), (240, 240, 240)).save(path)
        shot = capturelib.Shot(
            path=path, monitor=monitor, offset=(monitor.x, monitor.y),
            size=(1920, 1080), scaled=(1920, 1080), scale=1.0,
            id=f"m2-{self.captures:06x}", taken_at=time.time(),
            desktop_size=(1920, 1080),
        )
        (Path(out_dir) / f"{shot.id}.json").write_text(json.dumps(shot.meta()))
        return [shot]

    def tool(self, name):
        return asyncio.run(self.mcp.get_tool(name)).fn

    def test_a_missing_engine_refuses_before_any_capture(self) -> None:
        with mock.patch.object(ocr, "available", return_value=(False, "`tesseract` kurulu degil")):
            result = self.tool("find_text")(text="Oyna")
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"]["code"], "DEPENDENCY_MISSING")
        self.assertIn("tesseract", result.content[0].text)
        self.assertEqual(self.captures, 0)

    def test_find_text_answers_in_text_with_a_clickable_shot(self) -> None:
        with mock.patch.object(ocr, "read_words", return_value=ocr.parse_tsv(SCREEN)):
            result = self.tool("find_text")(text="Singleplayer", monitor="2")
        self.assertFalse(result.is_error)
        self.assertEqual([block.type for block in result.content], ["text"])
        text = result.content[0].text
        self.assertIn('x=860, y=412, shot="m2-000001"', text)
        match = result.structured_content["matches"][0]
        self.assertEqual((match["x"], match["y"], match["shot"]), (860, 412, "m2-000001"))
        # The id converts back to the right monitor.
        self.assertEqual(
            capturelib.to_global(860, 412, shot="m2-000001", dirs=[self.store.dir]),
            (1920 + 860, 412),
        )

    def test_not_found_says_so_and_offers_the_nearest_lines(self) -> None:
        with mock.patch.object(ocr, "read_words", return_value=ocr.parse_tsv(SCREEN)):
            result = self.tool("find_text")(text="Yukleniyor tamam")
        self.assertFalse(result.structured_content["found"])
        self.assertIn("bulunamadi", result.content[0].text)
        self.assertIn("Yükleniyor...", result.content[0].text)

    def test_the_window_capture_is_refused(self) -> None:
        result = self.tool("find_text")(text="Oyna", monitor="window")
        self.assertIn("window", result.content[0].text)
        self.assertEqual(self.captures, 0)

    def test_wait_returns_as_soon_as_the_text_shows_and_cleans_up(self) -> None:
        empty = ocr.parse_tsv(tsv((1, 1, 1, 1, 50, 50, 140, 20, 70.0, "Yükleniyor...")))
        answers = iter([empty, empty, ocr.parse_tsv(SCREEN)])
        with mock.patch.object(ocr, "read_words", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(toolslib.time, "sleep"):
            result = self.tool("wait_for_text")(text="Singleplayer", timeout_seconds=30)
        data = result.structured_content
        self.assertTrue(data["done"])
        self.assertEqual(data["attempts"], 3)
        self.assertIn("Goruldu", result.content[0].text)
        # Only the capture whose id was returned is kept.
        pngs = sorted(p.name for p in self.store.dir.glob("ocr-*.png"))
        records = sorted(p.name for p in self.store.dir.glob("m2-*.json"))
        self.assertEqual((pngs, records), (["ocr-3.png"], ["m2-000003.json"]))

    def test_wait_for_a_label_to_go_away(self) -> None:
        loading = ocr.parse_tsv(tsv((1, 1, 1, 1, 50, 50, 140, 20, 70.0, "Yükleniyor...")))
        answers = iter([loading, loading, ocr.parse_tsv(tsv(
            (1, 1, 1, 1, 800, 400, 120, 24, 96.5, "Singleplayer")))])
        with mock.patch.object(ocr, "read_words", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(toolslib.time, "sleep"):
            result = self.tool("wait_for_text")(text="Yükleniyor", gone=True)
        self.assertTrue(result.structured_content["done"])
        self.assertIn("Kayboldu", result.content[0].text)

    def test_a_timeout_reports_what_was_read(self) -> None:
        loading = ocr.parse_tsv(tsv((1, 1, 1, 1, 50, 50, 140, 20, 70.0, "Yükleniyor...")))
        with mock.patch.object(ocr, "read_words", return_value=loading):
            started = time.monotonic()
            result = self.tool("wait_for_text")(text="Singleplayer", timeout_seconds=2)
            elapsed = time.monotonic() - started
        self.assertFalse(result.structured_content["done"])
        self.assertIn("Zaman asimi", result.content[0].text)
        self.assertLess(elapsed, 4.0)
        self.assertEqual(len(list(self.store.dir.glob("ocr-*.png"))), 1)

    def test_a_grant_closed_mid_wait_stops_the_reading(self) -> None:
        loading = ocr.parse_tsv(tsv((1, 1, 1, 1, 50, 50, 140, 20, 70.0, "Yükleniyor...")))
        from pcbridge.desktop.safety import Decision
        from pcbridge.desktop.errors import ErrorCode

        closed = Decision(False, "Masaustu kontrolu su an kilitli.",
                          code=ErrorCode.GRANT_REQUIRED, permission_scope="pcbridge.desktop")
        calls = {"n": 0}

        def check(*args, **kwargs):
            calls["n"] += 1
            return Decision(True) if calls["n"] == 1 else closed

        with mock.patch.object(ocr, "read_words", return_value=loading), \
                mock.patch.object(toolslib.time, "sleep"), \
                mock.patch("tests.contracts.test_mcp_contract.OpenGate.check", side_effect=check):
            result = self.tool("wait_for_text")(text="Singleplayer", timeout_seconds=30)
        self.assertTrue(result.is_error)
        self.assertEqual(self.captures, 1, "a frame was taken after the grant closed")
        self.assertEqual(list(self.store.dir.glob("ocr-*.png")), [])

    def test_the_searched_text_is_not_written_to_the_audit_log(self) -> None:
        events = []
        with mock.patch.object(ocr, "read_words", return_value=ocr.parse_tsv(SCREEN)), \
                mock.patch("tests.contracts.test_mcp_contract.OpenGate.audit",
                           side_effect=lambda event, **fields: events.append((event, fields)),
                           create=True):
            self.tool("find_text")(text="Singleplayer")
        record = [fields for event, fields in events if event == "find_text"]
        self.assertEqual(record[0]["chars"], len("Singleplayer"))
        self.assertNotIn("Singleplayer", json.dumps(events, default=str))


if __name__ == "__main__":
    unittest.main()
