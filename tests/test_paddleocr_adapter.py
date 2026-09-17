import subprocess
import sys
import unittest
from pathlib import Path

from adapt_paddleocr import html_table_to_cells


class PaddleAdapterTests(unittest.TestCase):
    def test_table_preserves_entities_and_cells(self):
        self.assertEqual(html_table_to_cells("<table><tr><th>Name</th><td>A &amp; B</td></tr></table>"), [["Name", "A & B"]])

    def test_merged_cells_fail_instead_of_silently_shifting_columns(self):
        with self.assertRaisesRegex(ValueError, "Merged cells"):
            html_table_to_cells('<table><tr><td colspan="2">X</td></tr></table>')

    def test_dewarped_coordinates_rejected_by_default(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "adapt_paddleocr.py", "paddleocr-raw"], cwd=root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("require inverse mapping", result.stderr)
