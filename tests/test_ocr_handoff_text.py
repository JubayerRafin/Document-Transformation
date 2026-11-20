import os
import unittest

from transformation.ocr.paddle_handoff import drawing_order, handoff_fonts
from transformation.pdf.unmapped_text import read_area, visual_to_logical


class DrawingOrderTests(unittest.TestCase):
    SAMPLES = ['يجب تخزين الجهاز بين 5 و30 درجة.', 'المبلغ USD 1,250.50 فقط', 'عوّض', 'Total 12']

    def test_right_to_left_text_drawn_in_visual_order_reads_back_in_reading_order(self):
        for text in self.SAMPLES:
            drawn = drawing_order(text)
            self.assertEqual(len(drawn), len(text))
            # The reader sorts a right-to-left line's clusters by position; drawn left to right.
            clusters = []
            for c in drawn:
                import unicodedata
                if clusters and unicodedata.combining(c):
                    clusters[-1] = (clusters[-1][0], clusters[-1][1] + c)
                else:
                    clusters.append((len(clusters), c))
            back = visual_to_logical(clusters) if any('\u0600' <= c <= '\u06ff' for c in text) else drawn
            self.assertEqual(back, text)

    def test_left_to_right_text_is_unchanged(self):
        self.assertEqual(drawing_order('Invoice 2026-07-12'), 'Invoice 2026-07-12')
        self.assertEqual(drawing_order('शिपमेंट 12'), 'शिपमेंट 12')

    def test_an_invisible_arabic_line_reads_back_from_the_pdf_in_reading_order(self):
        import pymupdf
        fonts = [(n, f) for n, f in handoff_fonts() if f.has_glyph(ord('ب'))]
        if not fonts:
            self.skipTest('no Arabic font on this machine')
        name, font = fonts[0]
        text = 'يجب تخزين الجهاز بين 5 و30 درجة.'
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_font(fontname=name, fontbuffer=font.buffer)
        page.insert_text((50, 100), drawing_order(text), fontsize=10, fontname=name, render_mode=3)
        self.assertEqual(read_area(page, pymupdf.Rect(40, 80, 400, 110)), text)


if __name__ == '__main__':
    unittest.main()
