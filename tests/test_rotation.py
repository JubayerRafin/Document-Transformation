import unittest

import numpy as np
import pymupdf

from transformation.pdf.rotation import unrotated


def render(page):
    pix = page.get_pixmap(dpi=40)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)


class RotationTests(unittest.TestCase):
    def test_rotated_pages_become_unrotated_and_look_the_same(self):
        with pymupdf.open() as doc:
            for rotation in (0, 90, 180, 270):
                page = doc.new_page(width=300, height=200)
                page.insert_text((20, 40), f'Page turned {rotation} degrees, ref AB-2024-0{rotation}', fontsize=12)
                page.draw_rect(pymupdf.Rect(10, 10, 150, 60))
                page.set_rotation(rotation)
            data = doc.tobytes()
        with pymupdf.open(stream=data) as before, pymupdf.open(stream=unrotated(data)) as after:
            for index in range(4):
                self.assertEqual(after[index].rotation, 0)
                self.assertEqual(after[index].rect, before[index].rect)
                self.assertEqual(float(np.abs(render(after[index]) - render(before[index])).mean()), 0.0)
                self.assertEqual(after[index].get_text().split(), before[index].get_text().split())

    def test_unrotated_or_unreadable_files_are_returned_unchanged(self):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), 'Plain page')
            data = doc.tobytes()
        self.assertIs(unrotated(data), data)
        self.assertEqual(unrotated(b'not a pdf'), b'not a pdf')
        self.assertEqual(unrotated(data[:40]), data[:40])


if __name__ == '__main__':
    unittest.main()
