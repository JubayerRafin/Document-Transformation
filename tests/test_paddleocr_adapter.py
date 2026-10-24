import subprocess
import sys
import unittest
import tempfile
import json
from pathlib import Path

from tools.ocr.adapt_paddleocr import html_table_to_cells


class PaddleAdapterTests(unittest.TestCase):
    def test_explicit_paths_reproduce_saved_prediction_without_overwrite(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root / 'runs') as temp:
            out = Path(temp) / 'converted'
            command = [sys.executable, 'tools/ocr/adapt_paddleocr.py', '--source', 'samples/Sample02.pdf',
                       '--raw-dir', 'experiments/paddleocr-raw-nowarp', '--output-dir', str(out)]
            result = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = json.loads((out / 'normalized.json').read_text(encoding='utf-8'))
            expected = json.loads((root / 'experiments/paddleocr-raw-nowarp/normalized.json').read_text(encoding='utf-8'))
            self.assertEqual(actual, expected)
            again = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn('already exists', again.stderr)

    def test_table_preserves_entities_and_cells(self):
        self.assertEqual(html_table_to_cells("<table><tr><th>Name</th><td>A &amp; B</td></tr></table>"), [["Name", "A & B"]])

    def test_merged_cells_fail_instead_of_silently_shifting_columns(self):
        with self.assertRaisesRegex(ValueError, "Merged cells"):
            html_table_to_cells('<table><tr><td colspan="2">X</td></tr></table>')

    def test_dewarped_coordinates_rejected_by_default(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "tools/ocr/adapt_paddleocr.py", "paddleocr-raw"], cwd=root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("require inverse mapping", result.stderr)
