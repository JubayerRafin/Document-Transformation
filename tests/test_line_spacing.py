"""Translated paragraphs keep a tighter source line spacing instead of BabelDOC's 1.5 / 1.3."""
import unittest
from types import SimpleNamespace as NS

from transformation.layout import line_spacing as ls


def char(x, y, text='a', size=10):
    return NS(box=NS(x=x, y=y, x2=x + 5, y2=y + size * 0.7), char_unicode=text)


def paragraph(lines, size=10, pitch=12, pid='p1'):
    chars = [char(10 + 6 * k, 700 - row * pitch - (2 if k % 3 == 0 else 0)) for row in range(lines) for k in range(8)]
    composition = NS(pdf_character=None, pdf_line=NS(pdf_character=chars), pdf_same_style_characters=None)
    return NS(pdf_style=NS(font_size=size), vertical=False, pdf_paragraph_composition=[composition], debug_id=pid)


class LineSpacingTests(unittest.TestCase):
    def test_the_source_pitch_is_measured_in_font_sizes(self):
        self.assertAlmostEqual(ls.line_pitch(paragraph(3, pitch=12)), 1.2, places=2)
        self.assertIsNone(ls.line_pitch(paragraph(1)))

    def test_spacing_follows_a_tighter_source_within_the_floor(self):
        document = NS(page=[NS(pdf_paragraph=[paragraph(3, pitch=12, pid='a'), paragraph(3, pitch=8, pid='b'),
                                              paragraph(3, pitch=20, pid='c'), paragraph(1, pid='d')])])
        ls.measure(document)
        self.assertAlmostEqual(ls.spacing(1.5, NS(debug_id='a'), cjk=True), 1.25)
        self.assertAlmostEqual(ls.spacing(1.3, NS(debug_id='a'), cjk=False), 1.2)
        self.assertAlmostEqual(ls.spacing(1.3, NS(debug_id='b'), cjk=False), 1.15)   # floor
        self.assertAlmostEqual(ls.spacing(1.5, NS(debug_id='c'), cjk=True), 1.5)     # never looser
        self.assertAlmostEqual(ls.spacing(1.5, NS(debug_id='d'), cjk=True), 1.5)     # one line: unchanged

    def test_the_layout_receives_the_adjusted_spacing(self):
        seen = []

        class Typesetting:
            is_cjk = True
            def _layout_typesetting_units(self, units, box, scale, line_skip, paragraph, use_english_line_break=True):
                seen.append(line_skip); return [], True

        ls.install(Typesetting); ls.install(Typesetting)
        ls.measure(NS(page=[NS(pdf_paragraph=[paragraph(2, pitch=12, pid='x')])]))
        Typesetting()._layout_typesetting_units([], None, 1.0, 1.5, NS(debug_id='x'))
        self.assertAlmostEqual(seen[0], 1.25)


if __name__ == '__main__':
    unittest.main()
