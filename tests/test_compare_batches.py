import tempfile
import unittest
from pathlib import Path

import pymupdf

from tools.evaluation.compare_batches import aggregates, measure


class CompareBatchTests(unittest.TestCase):
    def test_missing_output_page_is_not_scored_as_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.pdf'
            output = Path(directory) / 'output.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40, 60), 'First page')
                pdf.save(output)
                pdf.new_page().insert_text((40, 60), 'Missing page')
                pdf.save(source)
            metrics = measure(source, output, 72)
            self.assertFalse(metrics['page_sizes_match'])
            self.assertEqual(metrics['pages'][1]['status'], 'missing_output_page')
            self.assertAlmostEqual(metrics['pages'][0]['page_ssim']['score'], 1)

    def test_unmeasured_categories_do_not_get_perfect_f1(self):
        for metric in aggregates([{'measurements': {'pages': [{'status': 'missing_output_page'}]}}]):
            self.assertIsNone(metric['f1'])
