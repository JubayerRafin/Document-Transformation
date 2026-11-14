"""Overlapping figures are processed together; the final check's findings are masked before release."""
import hashlib
import tempfile
import unittest
from pathlib import Path

import pymupdf

from transformation.ocr.pdf_assets import merge_overlapping
from transformation.validation.manual_redaction import redact_regions
from transformation.validation.review_items import final_check_regions


def fig(i, box): return {'id': i, 'kind': 'figure', 'bbox': box}


class MergeTests(unittest.TestCase):
    page = pymupdf.Rect(0, 0, 612, 792)

    def test_overlapping_figures_become_one_and_separate_ones_stay(self):
        out = merge_overlapping([fig('a', [60, 105, 400, 305]), fig('b', [60, 295, 400, 495]), fig('c', [60, 600, 400, 700])], self.page)
        self.assertEqual(len(out), 2)
        merged = next(r for r in out if r.get('merged_region_ids'))
        self.assertEqual(merged['merged_region_ids'], ['a', 'b'])
        self.assertEqual([round(v) for v in merged['bbox']], [60, 105, 400, 495])
        self.assertEqual(next(r for r in out if r['id'] == 'c')['bbox'], [60, 600, 400, 700])

    def test_a_chain_of_overlaps_merges_completely_in_any_order(self):
        out = merge_overlapping([fig('a', [0, 0, 100, 100]), fig('c', [180, 0, 300, 100]), fig('b', [90, 0, 190, 100])], self.page)
        self.assertEqual(len(out), 1)
        self.assertEqual(sorted(out[0]['merged_region_ids']), ['a', 'b', 'c'])

    def test_touching_edges_without_shared_area_stay_separate(self):
        self.assertEqual(len(merge_overlapping([fig('a', [0, 0, 100, 100]), fig('b', [100, 0, 200, 100])], self.page)), 2)


class FinalCheckTests(unittest.TestCase):
    report = {'page': 1, 'page_size': [600, 800], 'known_residual_values': [], 'uncertain_regions': [],
              'detected_pii': [{'value': 'Bronwyn Fairbanks', 'category': 'Names', 'bbox': [120, 400, 300, 420]},
                               {'value': 'Bronwyn Fairbanks', 'category': 'Names', 'bbox': [120, 400, 300, 420]},
                               {'value': 'Kestrel Freight', 'category': 'Names', 'bbox': [60, 100, 200, 118]},
                               {'value': 'no box', 'category': 'Names', 'bbox': None}]}

    def test_findings_become_padded_page_fractions_and_kept_values_stay(self):
        found = final_check_regions([self.report], keep=['Kestrel Freight'])
        self.assertEqual(len(found), 1)
        area = found[0]['region']
        self.assertEqual(area, {'page': 1, 'x': round(119 / 600, 4), 'y': round(399 / 800, 4), 'width': round(182 / 600, 4), 'height': round(22 / 800, 4)})
        self.assertEqual((found[0]['item']['state'], found[0]['item']['reason']), ('masked', 'final_check'))
        self.assertEqual(found[0]['box']['method'], 'final_check')
        self.assertNotIn('Bronwyn', str(found[0]['item']) + str(found[0]['box']))

    def test_masking_removes_the_pixels_on_an_image_only_page(self):
        with pymupdf.open() as text_pdf:
            p = text_pdf.new_page(width=600, height=800); p.insert_text((125, 415), 'Bronwyn Fairbanks', fontsize=14)
            png = p.get_pixmap(dpi=150).tobytes('png')
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800); page.insert_image(page.rect, stream=png); data = doc.tobytes()
        found = final_check_regions([self.report])
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / 'in.pdf', Path(tmp) / 'out.pdf'; src.write_bytes(data)
            redact_regions(src, dst, [f['region'] for f in found if 'Kestrel' not in str(f)], hashlib.sha256(data).hexdigest())
            with pymupdf.open(dst) as out:
                pix = out[0].get_pixmap(clip=pymupdf.Rect(125, 402, 290, 418))
                self.assertLessEqual(max(pix.samples), 12)


if __name__ == '__main__':
    unittest.main()


class TextOverlapTests(unittest.TestCase):
    def test_a_caption_touching_the_edge_is_not_overlapping_text(self):
        from transformation.ocr.pdf_assets import overlaps_text
        figure = pymupdf.Rect(60, 105, 400, 495)
        self.assertFalse(overlaps_text(pymupdf.Rect(50, 494, 73, 508), figure))   # 1 pt of the word's box
        self.assertTrue(overlaps_text(pymupdf.Rect(100, 300, 160, 314), figure))  # text printed over the image
        self.assertTrue(overlaps_text(pymupdf.Rect(390, 300, 420, 314), figure))  # a third of the word inside
