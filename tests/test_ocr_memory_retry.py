import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from tools.ocr import run_gpu_ocr


def scanned_page(path):
    with pymupdf.open() as image_doc:
        page = image_doc.new_page(width=400, height=300)
        page.insert_text((40, 80), 'Scanned text', fontsize=20)
        png = page.get_pixmap(dpi=150).tobytes('png')
    with pymupdf.open() as pdf:
        pdf.new_page(width=400, height=300).insert_image(pymupdf.Rect(0, 0, 400, 300), stream=png)
        pdf.save(path)


class OcrMemoryRetryTests(unittest.TestCase):
    def run_with(self, failures):
        calls = []

        def stage(name, arguments, out, limit, pool):
            calls.append((name, limit))
            if failures.get(name, 0):
                failures[name] -= 1
                raise MemoryError()
            if name == 'detection':
                (out / 'page-1_res.json').write_text('{}')
            else:
                (out / 'recognition').mkdir(exist_ok=True)
                (out / 'recognition/manifest.json').write_text('{}')
                (out / 'recognition/lines.jsonl').write_text('')

        with tempfile.TemporaryDirectory() as folder, patch.object(run_gpu_ocr, '_run_stage', side_effect=stage), \
                patch.object(run_gpu_ocr, '_free_gpu_memory') as free, patch('time.sleep'), \
                patch.object(run_gpu_ocr, 'render_page', side_effect=lambda page: (page.get_pixmap(dpi=500), {'policy': 'test'})):
            source = Path(folder, 'page.pdf')
            scanned_page(source)
            report = run_gpu_ocr.run(source, Path(folder, 'out'), 'en', model_pool=None)
            return report, calls, free.call_count

    def test_running_out_of_gpu_memory_is_retried_at_a_smaller_detector_size(self):
        report, calls, freed = self.run_with({'detection': 2})
        self.assertEqual(report['status'], 'completed')
        self.assertEqual([c for c in calls if c[0] == 'detection'], [('detection', 3072), ('detection', 3072), ('detection', 1536)])
        self.assertEqual(freed, 2)
        self.assertEqual(len(report['memory_retries']), 2)

    def test_other_errors_and_persistent_shortage_still_fail(self):
        with self.assertRaises(MemoryError):
            self.run_with({'detection': 5})
        with tempfile.TemporaryDirectory() as folder, patch.object(run_gpu_ocr, '_run_stage', side_effect=ValueError('bad page')), \
                patch.object(run_gpu_ocr, 'render_page', side_effect=lambda page: (page.get_pixmap(dpi=150), {})):
            source = Path(folder, 'page.pdf')
            scanned_page(source)
            with self.assertRaises(ValueError):
                run_gpu_ocr.run(source, Path(folder, 'out'), 'en')


if __name__ == '__main__':
    unittest.main()
