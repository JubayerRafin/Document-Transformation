import unittest
import hashlib
import numpy as np
from transformation.pdf.scan_reconstruction import background, rule_pixels, validate_corrections

class ScanReconstructionTests(unittest.TestCase):
    def test_rule_restore_does_not_restore_adjacent_letter_fragment(self):
        image = np.full((150,150,3),255,dtype=np.uint8)
        image[:,70] = 100
        image[40,69] = 0
        mask = rule_pixels(image)
        self.assertTrue(mask[10,70])
        self.assertFalse(mask[40,69])

    def test_corrections_require_exact_source_and_ocr(self):
        regions = [{'id': 'r1', 'text': 'bad OCR'}]
        item = {'id': 'r1', 'expected_ocr': 'bad OCR', 'action': 'replace', 'output_text': 'Correct text'}
        data = {'source_sha256': hashlib.sha256(b'source').hexdigest(), 'corrections': [item]}
        self.assertEqual(validate_corrections(data, b'source', regions)['r1'], item)
        with self.assertRaises(ValueError):
            validate_corrections(data, b'other document', regions)
        with self.assertRaises(ValueError):
            validate_corrections(data, b'source', [{'id': 'r1', 'text': 'changed OCR'}])
        data['corrections'].append(item)
        with self.assertRaises(ValueError):
            validate_corrections(data, b'source', regions)

    def test_crossing_rules_are_preserved(self):
        image = np.full((150, 150, 3), 255, dtype=np.uint8)
        image[:, 70] = 100
        image[80, :] = 100
        mask = rule_pixels(image)
        # Intersection may interrupt detection but each long segment remains.
        self.assertTrue(mask[10, 70])
        self.assertTrue(mask[80, 10])
        self.assertFalse(mask[10, 10])

    def test_uniform_background(self):
        color, agreement = background(np.full((25, 50, 3), 240, dtype=np.uint8))
        self.assertEqual(agreement, 1)
        self.assertEqual(color.tolist(), [240, 240, 240])

    def test_jpeg_scans_are_recognised_and_backgrounds_encode_as_jpeg(self):
        import io
        import pymupdf
        from PIL import Image
        from transformation.pdf.scan_reconstruction import jpeg_scan
        def page_with(fmt):
            buffer = io.BytesIO(); Image.new('RGB', (200, 100), (240, 240, 240)).save(buffer, format=fmt)
            pdf = pymupdf.open(); page = pdf.new_page(width=200, height=100)
            page.insert_image(page.rect, stream=buffer.getvalue()); return pdf, page
        jpeg_pdf, jpeg_page = page_with('JPEG')
        png_pdf, png_page = page_with('PNG')
        self.assertTrue(jpeg_scan(jpeg_page))
        self.assertFalse(jpeg_scan(png_page))
        self.assertTrue(jpeg_page.get_pixmap().tobytes(output='jpeg', jpg_quality=85).startswith(b'\xff\xd8'))

    def test_letters_crossing_the_border_are_not_artwork(self):
        # Paper with glyph strokes of this and the neighbouring line crossing the box border.
        image = np.full((14, 120, 3), 238, dtype=np.uint8)
        for x in range(4, 116, 9):
            image[:, x:x + 2] = 30          # vertical strokes reach the top and bottom rows
        self.assertGreaterEqual(background(image)[1], .95)

    def test_a_dark_photo_behind_text_is_still_artwork(self):
        rng = np.random.default_rng(3)
        image = rng.integers(20, 200, size=(20, 60, 3), dtype=np.uint8)
        self.assertLess(background(image)[1], .75)

    def test_mixed_background_rejected(self):
        image = np.zeros((30, 30, 3), dtype=np.uint8)
        image[:, 15:] = 255
        self.assertLess(background(image)[1], .75)


class SizeHarmonyTests(unittest.TestCase):
    def test_lines_of_one_source_size_are_drawn_at_one_size(self):
        import pymupdf
        from transformation.pdf.scan_reconstruction import harmonize_sizes, fit_text
        font = pymupdf.Font('cjk')
        def item(text, estimate, size, width=200):
            box = pymupdf.Rect(0, 0, width, 30)
            return {'record': {}, 'box': box, 'text': text, 'size': size, 'policy': {'source_font_estimate': estimate, 'minimum': 5},
                    'fitted': fit_text(box, text, font, max_size=size, min_size=5)}
        cells = [item('SKU-PR-0417', 10.0, 14.0), item('SKU-LT-0982', 10.4, 10.0), item('SKU-FS-2201', 10.2, 10.0)]
        title = item('Quarterly Review', 20.0, 20.0)
        harmonize_sizes(cells + [title], font)
        self.assertEqual([c['size'] for c in cells], [10.0, 10.0, 10.0])
        self.assertTrue(cells[0]['record']['size_harmonized'])
        self.assertEqual(title['size'], 20.0)                  # a different source size is its own group


class NarrowedLineTests(unittest.TestCase):
    """Arabic scan, 2026-11-23: unshaped letters are wider than the joined script, so table cells
    kept their Arabic pixels, untranslated."""

    def test_unshaped_arabic_is_drawn_at_its_shaped_size_squeezed_to_the_box(self):
        from pathlib import Path
        import pymupdf
        from transformation.pdf.scan_reconstruction import fit_text, narrowed_line
        path = Path.home() / '.cache/babeldoc/fonts/GoNotoKurrent-Regular.ttf'
        if not path.exists():
            self.skipTest('needs a font covering Arabic')
        font = pymupdf.Font(fontfile=str(path))
        box = pymupdf.Rect(489.9, 492.7, 547.0, 504.7)           # the "قفازات السلامة" cell
        policy = {'minimum': 8.0, 'maximum': 11.4}
        self.assertIsNone(fit_text(box, 'قفازات السلامة', font, max_size=11.4, min_size=8.0))
        (baseline, text, size, scale), = narrowed_line(box, 'قفازات السلامة', font, policy)
        self.assertGreaterEqual(size, 8.0)
        self.assertLess(scale, 1.0)
        self.assertLessEqual(font.text_length(text, fontsize=size) * scale, box.width)
        # Drawn with that squeeze, the glyphs keep their height (BabelDOC's pdfminer takes a
        # character's size from it; MuPDF reports the square root of both scales) and stay in the box.
        boxes = []
        with pymupdf.open() as pdf:
            for squeeze in (scale, 1.0):
                page = pdf.new_page()
                page.insert_font(fontname='ar', fontbuffer=font.buffer)
                page.insert_text(baseline, text, fontname='ar', fontsize=size,
                                 morph=(pymupdf.Point(baseline), pymupdf.Matrix(squeeze, 1)))
                boxes.append(pymupdf.Rect(page.get_text('dict')['blocks'][0]['lines'][0]['spans'][0]['bbox']))
        self.assertAlmostEqual(boxes[0].height, boxes[1].height, places=1)
        self.assertLessEqual(boxes[0].x1, box.x1 + .5)

    def test_a_line_whose_shaped_text_does_not_fit_either_is_refused(self):
        import pymupdf
        from pathlib import Path
        from transformation.pdf.scan_reconstruction import narrowed_line
        path = Path.home() / '.cache/babeldoc/fonts/GoNotoKurrent-Regular.ttf'
        if not path.exists():
            self.skipTest('needs a font covering Arabic')
        font = pymupdf.Font(fontfile=str(path))
        self.assertIsNone(narrowed_line(pymupdf.Rect(0, 0, 20, 12), 'قفازات السلامة', font, {'minimum': 8.0, 'maximum': 11.4}))


class ScannedRuleTests(unittest.TestCase):
    def test_vertical_cell_borders_become_one_line_each_and_horizontal_rules_none(self):
        """Korean scan, 2026-11-23: "수량" and "단가" were joined across a scanned border."""
        from transformation.pdf.scan_reconstruction import vertical_rule_segments
        mask = np.zeros((100, 120), bool)
        mask[10:90, 30:32] = True                      # a 2 px border
        for y in range(10, 91, 10):
            mask[y, 20:110] = True                     # the table's horizontal rules
        mask[20:25, 60] = True                         # a short mark
        self.assertEqual(vertical_rule_segments(mask), [(31.0, 10.0, 91.0)])

    def test_a_light_grey_border_is_found_and_letters_are_not(self):
        from transformation.pdf.scan_reconstruction import faint_vertical_rules, vertical_rule_segments
        pixels = np.full((200, 100, 3), 236, np.uint8)
        pixels[20:180, 50] = 220                       # 16 levels darker, 160 pt long
        pixels[100:115, 20] = 0                        # a letter's stem
        self.assertEqual(vertical_rule_segments(faint_vertical_rules(pixels)), [(50.5, 20.0, 180.0)])
