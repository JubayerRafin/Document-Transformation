"""A figure masked by its own run must replace the page's unmasked image even when its text could not
be translated (combined translation + PII jobs). Synthetic images only."""
import tempfile
import unittest
from pathlib import Path

import pymupdf

from transformation.ocr.pdf_assets import apply_results


def image_page(colour):
    """A one-page PDF whose page is a 100x50 image of a single colour."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 50), False)
    pix.set_rect(pix.irect, colour)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=100, height=50)
        page.insert_image(page.rect, stream=pix.tobytes('png'))
        return pdf.tobytes()


def centre_colour(data):
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        pix = pdf[0].get_pixmap()
        return pix.pixel(pix.width // 2, pix.height // 2)


class FigureMaskingTests(unittest.TestCase):
    def apply(self, event_extra, translate=True, redact=True):
        source = image_page((200, 30, 30))                       # the page's own image: red, "unmasked"
        with tempfile.TemporaryDirectory() as folder:
            masked = Path(folder, 'figure.pdf'); masked.write_bytes(image_page((0, 0, 0)))   # the run's output: black
            event = {'status': 'generated', 'run_dir': folder, 'output_pdf': str(masked), **event_extra}
            job = {'region': {'id': 'r1'}, 'box': pymupdf.Rect(0, 0, 100, 50), 'unit': {'number': 1}, 'raster': {}}
            result, reports = apply_results(source, [job], {0: event}, translate, redact, [])
        return centre_colour(result), reports[0]['status']

    def test_masked_figure_replaces_source_even_when_its_text_was_not_translated(self):
        colour, status = self.apply({'engine_run': {'source_pages_preserved_for_review': [1]},
                                     'validation': {'pii_findings': [{'value': 'x'}]}})
        self.assertEqual(colour, (0, 0, 0)); self.assertEqual(status, 'masked_untranslated_requires_review')

    def test_translation_only_keeps_the_source_image_when_nothing_was_translated(self):
        colour, status = self.apply({'engine_run': {'source_pages_preserved_for_review': [1]}}, redact=False)
        self.assertEqual(colour, (200, 30, 30)); self.assertEqual(status, 'source_preserved_requires_review')

    def test_nothing_selected_in_the_figure_keeps_the_source_image(self):
        colour, status = self.apply({'engine_run': {'source_pages_preserved_for_review': [1]},
                                     'validation': {'pii_findings': []}})
        self.assertEqual(colour, (200, 30, 30)); self.assertEqual(status, 'source_preserved_requires_review')


if __name__ == '__main__':
    unittest.main()
