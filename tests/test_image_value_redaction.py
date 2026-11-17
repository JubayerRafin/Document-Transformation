"""Value-only redaction of text that exists only as image pixels (scans, screenshots).

Synthetic only: text is drawn, rasterized and embedded as an image; no OCR models run.
"""
import unittest

import numpy as np
import pymupdf

from transformation.validation.value_redaction import (
    RedactionLocalizationError, pixel_word_boxes, redact_pdf,
)


def image_page(lines, width=420, height=160, dpi=200):
    """Return (pdf bytes, {line text: display bbox}) for text that exists only as pixels."""
    boxes = {}
    with pymupdf.open() as drawing:
        page = drawing.new_page(width=width, height=height)
        for x, y, text in lines:
            page.insert_text((x, y), text, fontsize=11)
            hits = page.search_for(text)
            box = hits[0]
            for hit in hits[1:]: box |= hit
            boxes[text] = list(box + (-2, -2, 2, 2))
        png = page.get_pixmap(dpi=dpi).tobytes('png')
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=png)
        return pdf.tobytes(), boxes


def word_box(text, word, line_y=60, x=30):
    with pymupdf.open() as drawing:
        page = drawing.new_page(width=420, height=160)
        page.insert_text((x, line_y), text, fontsize=11)
        return page.search_for(word)[0]


def ink(data, rect):
    """Fraction of dark pixels inside rect on the rendered output page."""
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        pix = pdf[0].get_pixmap(dpi=200, clip=pymupdf.Rect(rect), colorspace=pymupdf.csGRAY)
        image = np.frombuffer(pix.samples, dtype=np.uint8)
        return float((image < 100).mean())


def image_pixels(data, rect):
    """Pixels of the embedded image beneath rect (ignores boxes painted on top)."""
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        page = pdf[0]
        info = page.get_image_info(xrefs=True)[0]
        pix = pymupdf.Pixmap(pdf, info['xref'])
        if pix.n - pix.alpha != 1: pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
        bounds = pymupdf.Rect(info['bbox'])
        sx, sy = pix.width / bounds.width, pix.height / bounds.height
        r = pymupdf.Rect(rect)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        return image[int(r.y0 * sy):int(r.y1 * sy), int(r.x0 * sx):int(r.x1 * sx)]


class PixelWordBoxTests(unittest.TestCase):
    def test_words_are_split_at_real_ink_gaps(self):
        data, boxes = image_page([(30, 60, 'Last login IP: 172.16.5.114')])
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            words = pixel_word_boxes(pdf[0], boxes['Last login IP: 172.16.5.114'], 'Last login IP: 172.16.5.114')
        self.assertEqual(len(words), 4)
        value = word_box('Last login IP: 172.16.5.114', '172.16.5.114')
        self.assertLess(abs(words[3].x0 - value.x0), 2.5)
        self.assertLess(abs(words[3].x1 - value.x1), 2.5)

    def test_typical_field_lines_split_into_their_words(self):
        for line in ('Badge EMP-2024-8831', 'API Key: sk_live_4f9a...REDACT_ME',
                     'Device: WPX-0042 Owner: minjun.kim@example-corp.com', 'Issued: 2024-03-18'):
            data, boxes = image_page([(30, 60, line)])
            with pymupdf.open(stream=data, filetype='pdf') as pdf:
                words = pixel_word_boxes(pdf[0], boxes[line], line)
            self.assertIsNotNone(words, line)
            self.assertEqual(len(words), len(line.split()), line)
            last = word_box(line, line.split()[-1])
            self.assertLess(abs(words[-1].x1 - last.x1), 2.5, line)

    def test_word_count_mismatch_is_rejected_not_guessed(self):
        data, boxes = image_page([(30, 60, 'Badge EMP-2024-8831')])
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            # OCR text with a different number of words than the pixels show.
            self.assertIsNone(pixel_word_boxes(pdf[0], boxes['Badge EMP-2024-8831'], 'Badge EMP 2024 8831 extra words'))


class ImageRedactionTests(unittest.TestCase):
    def redact(self, line, value, extra_lines=()):
        data, boxes = image_page([(30, 60, line), *extra_lines])
        region = {'id': 'r', 'kind': 'text', 'text': line, 'bbox': boxes[line]}
        ir = {'units': [{'id': 'u', 'number': 1, 'regions': [region]}]}
        output, audit = redact_pdf(data, ir, [{'region_id': 'r', 'value': value, 'category': 'Identifiers'}])
        return data, output, audit

    def test_value_without_a_colon_is_removed_and_label_kept(self):
        line = 'Badge EMP-2024-8831'
        data, output, audit = self.redact(line, 'EMP-2024-8831')
        self.assertEqual(audit[0]['localization'], 'pixel_word_gaps')
        value, label = word_box(line, 'EMP-2024-8831'), word_box(line, 'Badge')
        self.assertGreater(ink(output, value + (1, 1, -1, -1)), .95)    # covered
        self.assertLess(ink(output, label), .6)                       # label still readable, not a black box
        self.assertGreater(ink(output, label), .05)
        # The image pixels themselves are cleared, not just painted over.
        original, redacted = image_pixels(data, value), image_pixels(output, value)
        self.assertLess(original.min(), 100)
        self.assertGreater(redacted.min(), 100)

    def test_ip_address_after_label_is_removed(self):
        line = 'Last login IP: 172.16.5.114'
        _, output, audit = self.redact(line, '172.16.5.114')
        self.assertEqual(audit[0]['localization'], 'pixel_word_gaps')
        self.assertGreater(ink(output, word_box(line, '172.16.5.114') + (1, 1, -1, -1)), .95)
        self.assertLess(ink(output, word_box(line, 'login')), .6)

    def test_value_fused_with_its_label_covers_the_value_and_keeps_the_label(self):
        # OCR reads "Device:WPX-0042" as one word; only the value part is covered.
        line = 'Device:WPX-0042 Owner'
        _, output, audit = self.redact(line, 'WPX-0042')
        self.assertEqual(audit[0]['localization'], 'pixel_word_gaps')
        self.assertGreater(ink(output, word_box(line, 'WPX-0042') + (1, 1, -1, -1)), .95)   # value fully covered
        self.assertLess(ink(output, word_box(line, 'Devic')), .6)                          # label readable
        self.assertLess(ink(output, word_box(line, 'Owner')), .6)

    def test_unsplittable_line_falls_back_to_the_whole_line_and_says_so(self):
        line = 'Badge EMP-2024-8831'
        data, boxes = image_page([(30, 60, line)])
        # OCR merged the words, so the text has a different word count than the pixels.
        region = {'id': 'r', 'kind': 'text', 'text': 'Badge EMP-2024-8831 x y z', 'bbox': boxes[line]}
        ir = {'units': [{'id': 'u', 'number': 1, 'regions': [region]}]}
        output, audit = redact_pdf(data, ir, [{'region_id': 'r', 'value': 'EMP-2024-8831', 'category': 'Identifiers'}])
        self.assertEqual(audit[0]['localization'], 'whole_line_fallback')
        self.assertFalse(audit[0]['label_preserved'])
        self.assertGreater(ink(output, pymupdf.Rect(boxes[line]) + (2, 2, -2, -2)), .95)

    def test_rotated_scan_value_is_removed_in_display_coordinates(self):
        line = 'Reference: RX-55102-T'
        data, boxes = image_page([(30, 60, line)])
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            pdf[0].set_rotation(90); data = pdf.tobytes()
            # Display coordinates of the line after rotation.
            display = pymupdf.Rect(boxes[line]) * pdf[0].rotation_matrix
            value_display = word_box(line, 'RX-55102-T') * pdf[0].rotation_matrix
            label_display = word_box(line, 'Reference:') * pdf[0].rotation_matrix
        region = {'id': 'r', 'kind': 'text', 'text': line, 'bbox': list(display)}
        ir = {'units': [{'id': 'u', 'number': 1, 'regions': [region]}]}
        # On a rotated page the line runs vertically, so word gaps are not horizontal:
        # the splitter must not guess; the value is still removed (whole line) and audited.
        output, audit = redact_pdf(data, ir, [{'region_id': 'r', 'value': 'RX-55102-T', 'category': 'Identifiers'}])
        self.assertIn(audit[0]['localization'], {'pixel_word_gaps', 'whole_line_fallback'})
        with pymupdf.open(stream=output, filetype='pdf') as pdf:
            self.assertEqual(pdf[0].rotation, 90)
            pix = pdf[0].get_pixmap(dpi=200, clip=value_display + (1, 1, -1, -1), colorspace=pymupdf.csGRAY)
            self.assertGreater(float((np.frombuffer(pix.samples, dtype=np.uint8) < 100).mean()), .95)

    def test_upright_text_on_a_rotated_page_is_split_into_words(self):
        # A scan stored sideways and displayed upright via /Rotate: text is horizontal on screen.
        line = 'Tenant ref: TN-40917'
        with pymupdf.open() as drawing:
            page = drawing.new_page(width=420, height=160)
            page.insert_text((30, 60), line, fontsize=11)
            box = page.search_for(line)[0] + (-2, -2, 2, 2)
            value = page.search_for('TN-40917')[0]
            png = page.get_pixmap(dpi=200).tobytes('png')
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=160, height=420)
            # Store the scan sideways, then rotate the page so it displays upright.
            page.insert_image(page.rect, stream=png, rotate=90)
            page.set_rotation(90)
            data = pdf.tobytes()
            self.assertEqual(pdf[0].rect.width, 420)
        region = {'id': 'r', 'kind': 'text', 'text': line, 'bbox': list(box)}
        ir = {'units': [{'id': 'u', 'number': 1, 'regions': [region]}]}
        output, audit = redact_pdf(data, ir, [{'region_id': 'r', 'value': 'TN-40917', 'category': 'Identifiers'}])
        self.assertEqual(audit[0]['localization'], 'pixel_word_gaps')
        with pymupdf.open(stream=output, filetype='pdf') as pdf:
            covered = pdf[0].get_pixmap(dpi=200, clip=value + (1, 1, -1, -1), colorspace=pymupdf.csGRAY)
            label = pdf[0].get_pixmap(dpi=200, clip=pymupdf.Rect(box.x0 + 2, box.y0, value.x0 - 8, box.y1), colorspace=pymupdf.csGRAY)
            self.assertGreater(float((np.frombuffer(covered.samples, dtype=np.uint8) < 100).mean()), .95)
            self.assertLess(float((np.frombuffer(label.samples, dtype=np.uint8) < 100).mean()), .6)

    def test_native_page_ignores_extractor_boxes_in_another_frame(self):
        # Extractor boxes in media-box coordinates on a page with an offset crop box.
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=600, height=800)
            page.insert_text((140, 160), 'Account: AC-77120-9', fontsize=11)
            page.set_cropbox(pymupdf.Rect(100, 100, 500, 700))
            data = pdf.tobytes()
        region = {'id': 'r', 'kind': 'text', 'text': 'Account: AC-77120-9', 'bbox': [140, 150, 260, 164]}
        ir = {'units': [{'id': 'u', 'number': 1, 'regions': [region]}]}
        output, audit = redact_pdf(data, ir, [{'region_id': 'r', 'value': 'AC-77120-9', 'category': 'Identifiers'}])
        self.assertEqual(audit[0]['method'], 'native_glyph_spans')
        with pymupdf.open(stream=output, filetype='pdf') as pdf:
            text = pdf[0].get_text()
            self.assertIn('Account:', text); self.assertNotIn('AC-77120', text)

    def test_line_box_without_any_visible_text_still_fails_closed(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=200, height=100)
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False); pix.clear_with(255)
            page.insert_image(page.rect, pixmap=pix); data = pdf.tobytes()
        ir = {'units': [{'number': 1, 'regions': [{'id': 'r', 'text': 'Name: Ann Lee', 'bbox': [20, 30, 150, 50]}]}]}
        with self.assertRaises(RedactionLocalizationError):
            redact_pdf(data, ir, [{'region_id': 'r', 'value': 'Ann Lee'}])


if __name__ == '__main__':
    unittest.main()
