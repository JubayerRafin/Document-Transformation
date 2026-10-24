import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from transformation.analysis.document_profile import profile_pdf, plan_route
from transformation.pdf.runner import run_pdf


def pdf_bytes(kind='digital', count=1, rotation=0):
    with pymupdf.open() as doc:
        for _ in range(count):
            page = doc.new_page(width=200, height=200)
            if kind in {'digital', 'mixed'}:
                page.insert_text((20, 30), 'Unseen document text')
            if kind in {'image_only', 'mixed'}:
                pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 20, 20), False)
                pix.clear_with(255)
                page.insert_image(pymupdf.Rect(40, 40, 100, 100), pixmap=pix)
            page.set_rotation(rotation)
        return doc.tobytes()


class RoutingTests(unittest.TestCase):
    def test_routes_and_partial_coverage(self):
        for kind in ('digital', 'mixed', 'image_only', 'vector_or_empty'):
            profile = profile_pdf(pdf_bytes(kind))
            self.assertEqual(profile['pages'][0]['kind'], kind)
            native = plan_route(profile)
            self.assertEqual(native['supported'], kind in {'digital', 'mixed'})
            self.assertEqual(bool(native['warnings']), kind == 'mixed')
            self.assertEqual(plan_route(profile, True)['supported'], kind == 'image_only')

    def test_scan_limits(self):
        for count, rotation in ((2, 0), (1, 90)):
            self.assertFalse(plan_route(profile_pdf(pdf_bytes('image_only', count, rotation)), True)['supported'])
        profile = profile_pdf(pdf_bytes('image_only'))
        profile['pages'][0]['raster_pixels_72dpi'] = 20_000_001
        self.assertFalse(plan_route(profile, True)['supported'])

    def test_rejected_before_run_folder_or_subprocess(self):
        with tempfile.TemporaryDirectory() as folder, patch('transformation.pdf.runner.subprocess.Popen') as process:
            root = Path(folder) / 'runs'
            with self.assertRaisesRegex(ValueError, 'Preflight'):
                next(run_pdf(pdf_bytes('image_only'), 'unseen.pdf', python=sys.executable,
                             model='qwen3.5:9b', root=root))
            self.assertFalse(root.exists())
            process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
