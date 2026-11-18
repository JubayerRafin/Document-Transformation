import unittest

import pymupdf

from transformation.ocr.table_rules import split_at_rules


def scanned_table(rows):
    """An image-only page of a ruled table (rows of cell texts), like a scan."""
    with pymupdf.open() as drawing:
        page = drawing.new_page(width=500, height=200)
        xs = [40, 200, 260, 380, 470]
        for y in range(40, 40 + 30 * (len(rows) + 1), 30):
            page.draw_line((40, y), (470, y), width=1)
        for x in xs:
            page.draw_line((x, 40), (x, 40 + 30 * len(rows)), width=1)
        for r, cells in enumerate(rows):
            for c, text in enumerate(cells):
                page.insert_text((xs[c] + 4, 40 + 30 * r + 20), text, fontsize=11)
        png = page.get_pixmap(dpi=200).tobytes('png')
    doc = pymupdf.open()
    doc.new_page(width=500, height=200).insert_image(pymupdf.Rect(0, 0, 500, 200), stream=png)
    return doc


class TableRuleTests(unittest.TestCase):
    def test_a_line_across_a_cell_border_is_split_into_cells(self):
        doc = scanned_table([['Barcode scanner', 'Qty', 'Unit price', 'Total'], ['QRF-2020-34513', '479', 'USD 1.00', 'USD 479']])
        page = doc[0]
        # The detector joined the code with the next cell's quantity, and two header cells.
        lines = [{'id': 'L1', 'text': 'QRF-2020-34513 479', 'bbox': [42, 72, 230, 92], 'candidates': [{'text': 'x'}],
                  'recognition_score': 0.99},
                 {'id': 'L2', 'text': 'Qty Unit price', 'bbox': [202, 42, 340, 62], 'recognition_score': 0.99},
                 {'id': 'L3', 'text': 'Barcode scanner', 'bbox': [42, 42, 160, 62], 'recognition_score': 0.99}]
        out = split_at_rules(page, lines)
        texts = [(l['id'], l['text']) for l in out]
        self.assertEqual(texts, [('L1_cell1', 'QRF-2020-34513'), ('L1_cell2', '479'),
                                 ('L2_cell1', 'Qty'), ('L2_cell2', 'Unit price'), ('L3', 'Barcode scanner')])
        first, second = (pymupdf.Rect(l['bbox']) for l in out[:2])
        self.assertLess(first.x1, 200.5)
        self.assertGreater(second.x0, 199.5)
        self.assertEqual(out[0]['candidates'], [])          # evidence was for the whole line
        self.assertEqual(out[0]['split_from'], 'L1')

    def test_letters_and_lines_without_rules_are_left_whole(self):
        doc = scanned_table([['Item list', 'l', 'I', '|']])
        page = doc[0]
        lines = [{'id': 'A', 'text': 'Item list', 'bbox': [42, 42, 160, 62]},
                 {'id': 'B', 'text': 'one', 'bbox': [42, 42, 160, 62]}]
        self.assertEqual(split_at_rules(page, lines), lines)
        with pymupdf.open() as plain:
            p = plain.new_page(width=300, height=100)
            p.insert_text((20, 50), 'Illustrated lilies | ill', fontsize=14)
            png = p.get_pixmap(dpi=200).tobytes('png')
        scan = pymupdf.open()
        scan.new_page(width=300, height=100).insert_image(pymupdf.Rect(0, 0, 300, 100), stream=png)
        line = [{'id': 'C', 'text': 'Illustrated lilies | ill', 'bbox': [18, 36, 200, 56]}]
        self.assertEqual(split_at_rules(scan[0], line), line)   # "|" and tall letters are not table rules


if __name__ == '__main__':
    unittest.main()
