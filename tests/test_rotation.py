import unittest

import numpy as np
import pymupdf

from transformation.pdf.rotation import unrotated


def render(page):
    pix = page.get_pixmap(dpi=40)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)


class RotationTests(unittest.TestCase):
    def test_rotated_pages_that_display_upright_text_look_the_same(self):
        # Content drawn sideways and a /Rotate that turns it upright: the common landscape page.
        with pymupdf.open() as doc:
            for rotation in (0, 90, 180, 270):
                page = doc.new_page(width=300, height=200)
                page.set_rotation(rotation)
                # Draw through the derotation matrix so the text reads upright as displayed.
                point = pymupdf.Point(20, 40) * page.derotation_matrix
                page.insert_text(point, f'Page turned {rotation} degrees, ref AB-2024-0{rotation}', fontsize=12,
                                 rotate=rotation)
            data = doc.tobytes()
        with pymupdf.open(stream=data) as before, pymupdf.open(stream=unrotated(data)) as after:
            for index in range(4):
                self.assertEqual(after[index].rotation, 0)
                self.assertEqual(after[index].rect, before[index].rect)
                self.assertEqual(float(np.abs(render(after[index]) - render(before[index])).mean()), 0.0)
                self.assertEqual(after[index].get_text().split(), before[index].get_text().split())

    def test_rotated_pages_showing_sideways_text_come_out_upright(self):
        with pymupdf.open() as doc:
            for rotation in (90, 180, 270):
                page = doc.new_page(width=300, height=200)
                page.insert_text((20, 40), f'Page turned {rotation} degrees, enough text to judge.', fontsize=12)
                page.set_rotation(rotation)
            data = doc.tobytes()
        with pymupdf.open(stream=unrotated(data)) as after:
            for page in after:
                self.assertEqual(page.rotation, 0)
                dirs = {tuple(round(v) for v in line['dir']) for b in page.get_text('dict')['blocks'] for line in b.get('lines', [])}
                self.assertEqual(dirs, {(1, 0)})

    def test_unrotated_or_unreadable_files_are_returned_unchanged(self):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), 'Plain page')
            data = doc.tobytes()
        self.assertIs(unrotated(data), data)
        self.assertEqual(unrotated(b'not a pdf'), b'not a pdf')
        self.assertEqual(unrotated(data[:40]), data[:40])


if __name__ == '__main__':
    unittest.main()


class SidewaysTextTests(unittest.TestCase):
    def page_with_text(self, morph_degrees, text='Sideways report line with enough characters to judge.'):
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=500)
        for row in range(3):
            page.insert_text((150, 100 + 30 * row), text, fontsize=11, rotate=morph_degrees)
        return doc

    def directions(self, data):
        from collections import Counter
        with pymupdf.open(stream=data) as doc:
            return Counter(tuple(round(v) for v in line['dir'])
                           for block in doc[0].get_text('dict')['blocks'] for line in block.get('lines', []))

    def test_sideways_and_upside_down_text_is_turned_upright(self):
        for degrees in (90, 270, 180):
            with self.page_with_text(degrees) as doc:
                data = doc.tobytes()
            self.assertNotEqual(set(self.directions(data)), {(1, 0)}, degrees)
            fixed = unrotated(data)
            self.assertEqual(set(self.directions(fixed)), {(1, 0)}, degrees)
            with pymupdf.open(stream=fixed) as doc:
                self.assertIn('Sideways report line', doc[0].get_text())

    def test_upright_pages_and_vertical_cjk_writing_are_left_alone(self):
        with self.page_with_text(0) as doc:
            data = doc.tobytes()
        self.assertIs(unrotated(data), data)
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=500)
        page.insert_font(fontname='jp', fontbuffer=pymupdf.Font('japan').buffer)
        for column in range(3):
            page.insert_text((300 - 30 * column, 60), '縦書きの日本語の文章です。これは縦に書かれています。', fontname='jp',
                             fontsize=12, rotate=270)
        data = doc.tobytes()
        self.assertEqual(text_turn_of(data), 0)


def text_turn_of(data):
    from transformation.pdf.rotation import text_turn
    with pymupdf.open(stream=data) as doc:
        return text_turn(doc[0])
