import unittest
from pathlib import Path

import pymupdf

from transformation.pdf import font_flags
from transformation.pdf.font_shapes import classify_pdf


class FontShapeTests(unittest.TestCase):
    def test_serif_and_sans_fonts_are_told_apart_by_their_letters(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'fonts.pdf')
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((72, 100), 'Illustrated Handbook III lll HHH', fontname='tiro', fontsize=14)   # Times
                page.insert_text((72, 140), 'Illustrated Handbook III lll HHH', fontname='helv', fontsize=14)   # Helvetica
                pdf.save(path)
            styles = classify_pdf(path)
        self.assertTrue(styles.get('Times-Roman'))
        self.assertIs(styles.get('Helvetica'), False)

    def test_measured_style_overrides_wrong_flags(self):
        from types import SimpleNamespace as NS
        font_flags.LEARNED.clear()
        font_flags.LEARNED['FormaDJRMicro-Regular'] = False
        try:
            font = NS(name='KIVVTM+FormaDJRMicro-Regular', serif=True, bold=False, italic=False, font_id='F1')
            self.assertTrue(font_flags.correct(font))
            self.assertFalse(font.serif)
        finally:
            font_flags.LEARNED.clear()


if __name__ == '__main__':
    unittest.main()
