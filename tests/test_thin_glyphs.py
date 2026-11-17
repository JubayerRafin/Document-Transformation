"""Thin punctuation stays inside its word when BabelDOC reads a page. No BabelDOC needed."""
import unittest
from dataclasses import dataclass
from types import SimpleNamespace as NS

from transformation.pdf.thin_glyphs import align, install, thin_punctuation


@dataclass
class Box:
    x: float
    y: float
    x2: float
    y2: float


def char(text, visual_y, font_y=(95.0, 107.0)):
    return NS(char_unicode=text, box=Box(10, *font_y[:1], 16, font_y[1]) if font_y else None,
              visual_bbox=NS(box=Box(10.5, visual_y[0], 15.5, visual_y[1])))


class ThinGlyphTests(unittest.TestCase):
    def test_flat_punctuation_takes_the_letters_height_and_everything_else_is_unchanged(self):
        underscore, period = char('_', (93.5, 94.5)), char('.', (95.0, 96.5))
        letter, digit, minus = char('k', (95.0, 105.0)), char('4', (95.0, 104.0)), char('−', (99.0, 100.0))
        page = NS(pdf_character=[letter, underscore, digit, period, minus])
        self.assertEqual(align(page), 2)
        # The letters' median extent, not the font box (95-107), which reaches above the visible text
        # into a heading region above the line.
        self.assertEqual((underscore.visual_bbox.box.y, underscore.visual_bbox.box.y2), (95.0, 104.5))
        self.assertEqual((underscore.visual_bbox.box.x, underscore.visual_bbox.box.x2), (10.5, 15.5))
        self.assertEqual((letter.visual_bbox.box.y, digit.visual_bbox.box.y2), (95.0, 104.0))
        self.assertEqual(minus.visual_bbox.box.y, 99.0)                     # a math sign stays a formula candidate
        self.assertFalse(thin_punctuation(char('_', (93.5, 94.5), font_y=None)))
        self.assertFalse(thin_punctuation(char(None, (93.5, 94.5))))

    def test_only_letters_of_the_same_line_count_and_a_lone_mark_gets_the_lower_part_of_its_font_box(self):
        hyphen = char('-', (99.0, 100.0))
        other_line = char('Q', (60.0, 75.0), font_y=(58.0, 76.0))
        page = NS(pdf_character=[other_line, hyphen])
        align(page)
        self.assertEqual(hyphen.visual_bbox.box.y, 95.0)
        self.assertAlmostEqual(hyphen.visual_bbox.box.y2, 95.0 + 0.7 * 12.0)

    def test_install_aligns_each_page_before_the_original_step_once(self):
        seen = []
        class Finder:
            def process_page(self, page): seen.append(page.pdf_character[0].visual_bbox.box.y)
        self.assertTrue(install(Finder)); self.assertTrue(install(Finder))
        Finder().process_page(NS(pdf_character=[char('_', (93.5, 94.5))]))
        self.assertEqual(seen, [95.0])


if __name__ == '__main__':
    unittest.main()
