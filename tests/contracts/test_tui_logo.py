"""The Overview's live logo: bitmaps, colors, glow and the click reactions,
drawn headless without a terminal."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tui import logo as L  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402


class BitmapTests(unittest.TestCase):
    def test_the_wordmark_is_50_by_9_and_takes_56_by_7_cells(self) -> None:
        self.assertEqual({len(r) for r in L.WORD}, {50})
        self.assertEqual(len(L.WORD), 9)
        self.assertEqual(L.Logo("word").cells, (56, 7))

    def test_the_icon_takes_21_by_8_cells(self) -> None:
        self.assertEqual({len(r) for r in L.ICON}, {15})
        self.assertEqual(L.Logo("icon").cells, (21, 8))

    def test_every_row_has_the_logo_width(self) -> None:
        for glyph in ("word", "icon"):
            for state in L.STATES:
                rows = L.Logo(glyph, state).text_rows()
                self.assertEqual({len(r) for r in rows}, {L.Logo(glyph).cells[0]})


class ColorTests(unittest.TestCase):
    def test_idle_is_a_gray_gradient_with_no_glow(self) -> None:
        logo = L.Logo("word", "idle")
        pix = logo.pixels()
        self.assertEqual(set(pix), logo.on)       # no glow pixel at all
        top_left = min((p for p in logo.on if p[1] == logo.mt + 2), key=lambda p: p[0])
        top_right = max((p for p in logo.on if p[1] == logo.mt + 2), key=lambda p: p[0])
        self.assertEqual(pix[top_left], L.mix((214, 219, 225), L.BOTTOM, 0.16 * 2 / 8))
        self.assertGreater(sum(pix[top_left]), sum(pix[top_right]))

    def test_active_and_error_glow_within_three_pixels(self) -> None:
        for state, glow in (("active", (70, 138, 235)), ("error", (232, 72, 90))):
            logo = L.Logo("word", state, base=(0, 0, 0))
            pix = logo.pixels()
            ring = {p for p in pix if p not in logo.on}
            self.assertTrue(ring)
            self.assertTrue(all(logo.ring[p] <= 3 for p in ring))
            near = next(p for p, d in logo.ring.items() if d == 1)
            far = next(p for p, d in logo.ring.items() if d == 3)
            alphas = L.STATES[state]["rings"]
            self.assertEqual(pix[near], L.mix((0, 0, 0), glow, alphas[0]))
            self.assertEqual(pix[far], L.mix((0, 0, 0), glow, alphas[2]))

    def test_the_glow_blends_over_the_given_base(self) -> None:
        a = L.Logo("icon", "active", base=(14, 18, 22)).pixels()
        b = L.Logo("icon", "active", base=(30, 60, 40)).pixels()
        logo = L.Logo("icon", "active")
        ring = next(iter(logo.ring))
        self.assertNotEqual(a[ring], b[ring])

    def test_osc11_replies_are_parsed(self) -> None:
        self.assertEqual(L.parse_osc11("\x1b]11;rgb:1e1e/2b2b/2222\x07"), (30, 43, 34))
        self.assertEqual(L.parse_osc11("\x1b]11;rgb:ff/80/00\x1b\\"), (255, 128, 0))
        self.assertIsNone(L.parse_osc11(""))
        self.assertIsNone(L.parse_osc11("garbage"))

    def test_tri_ends_where_it_started(self) -> None:
        for t in (0.0, 0.25, 0.8, 1.0):
            self.assertAlmostEqual(L.tri(t), t)
            self.assertAlmostEqual(L.tri(t + 2), t)


class ReactionTests(unittest.TestCase):
    def test_each_reaction_changes_the_frame_midway_and_ends_where_it_began(self) -> None:
        for reaction in L.REACTIONS:
            logo = L.Logo("word", "idle", seed=1)
            still = logo.pixels()
            logo.reaction, logo.progress = reaction, 0.5
            if reaction == "sparkle":
                logo._pick_sparkles(1.0)
            self.assertNotEqual(logo.pixels(), still, reaction)
            logo.progress = 1.0
            logo._sparkles = set()
            self.assertEqual(logo.pixels(), still, reaction)

    def test_clicks_while_a_reaction_runs_are_ignored_and_never_repeat(self) -> None:
        class Host(App):
            def compose(self) -> ComposeResult:
                yield L.Logo("word", seed=3, id="logo")

        async def go():
            app = Host()
            async with app.run_test(size=(80, 10)) as pilot:
                logo = app.query_one(L.Logo)
                seen = []
                for _ in range(6):
                    self.assertTrue(logo.click())
                    seen.append(logo.reaction)
                    self.assertFalse(logo.click())          # ignored while it runs
                    self.assertIsNotNone(logo._timer)
                    logo.finish()
                    self.assertIsNone(logo._timer)          # an idle logo uses no timer
                for a, b in zip(seen, seen[1:]):
                    self.assertNotEqual(a, b)
                logo.click()
                await pilot.pause(L.REACTIONS[logo.reaction] + 0.3)
                self.assertIsNone(logo.reaction)            # it finished on its own
                self.assertIsNone(logo._timer)

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
