"""Straightening skewed scanned pages before OCR."""
import io
import unittest

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

from transformation.pdf import deskew


def scanned_page_pdf(angle, lines=14):
    """A one-page PDF holding only a picture of text-like lines, tilted by angle degrees."""
    image = Image.new('L', (1240, 1754), 255)
    draw = ImageDraw.Draw(image)
    for row in range(lines):
        y = 200 + row * 90
        for word in range(9):
            x = 150 + word * 105
            draw.rectangle((x, y, x + 80, y + 22), fill=0)
    image = image.rotate(-angle, resample=Image.BICUBIC, fillcolor=255)   # clockwise: lines fall right
    buffer = io.BytesIO(); image.save(buffer, format='PNG')
    pdf = pymupdf.open(); page = pdf.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buffer.getvalue())
    return pdf.tobytes()


class DeskewTests(unittest.TestCase):
    def test_the_tilt_of_text_lines_is_measured(self):
        for angle in (1.5, -1.0, 0.0):
            with pymupdf.open(stream=scanned_page_pdf(angle), filetype='pdf') as pdf:
                measured = deskew.skew_angle(deskew._gray(pdf[0], deskew.MEASURE_DPI))
            self.assertAlmostEqual(measured, angle, delta=0.15, msg=angle)

    def test_a_skewed_scan_is_turned_level_and_a_level_one_is_left_alone(self):
        data = deskew.deskewed(scanned_page_pdf(1.6))
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            self.assertEqual(len(pdf), 1)
            self.assertEqual(len(pdf[0].get_images()), 1)
            self.assertAlmostEqual(deskew.skew_angle(deskew._gray(pdf[0], deskew.MEASURE_DPI)), 0.0, delta=0.15)
        level = scanned_page_pdf(0.0)
        self.assertIs(deskew.deskewed(level), level)

    def test_pages_with_a_text_layer_are_never_changed(self):
        pdf = pymupdf.open(stream=scanned_page_pdf(2.0), filetype='pdf')
        pdf[0].insert_text((72, 72), 'Selectable text', fontsize=11)
        data = pdf.tobytes()
        self.assertIs(deskew.deskewed(data), data)

    def test_a_blank_or_photo_like_page_has_no_tilt(self):
        noise = (np.random.default_rng(1).random((800, 600)) * 255).astype(np.uint8)
        self.assertEqual(deskew.skew_angle(np.full((800, 600), 255, np.uint8)), 0.0)
        self.assertEqual(deskew.skew_angle(noise), 0.0)


if __name__ == '__main__':
    unittest.main()
