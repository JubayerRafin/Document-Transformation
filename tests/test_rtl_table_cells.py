"""Right-to-left table cells are read in the PDF's own order, not reversed (Arabic invoice, 2026-11-23)."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from extraction.coordinator import logical_table_text

LABEL = '\u0631\u0642\u0645 \u0627\u0644\u0645\u0648\u0638\u0641'        # "employee number", logical order
OTHER = '\u0627\u0644\u0642\u0633\u0645'


class FakePdf:
    """A one-page PDF whose text layer stores the label in logical order, as Chromium writes it."""
    def __init__(self, text):
        self.page = NS(rotation=0, get_text=lambda kind, clip=None: text)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getitem__(self, index): return self.page


class LogicalTableTextTests(unittest.TestCase):
    def run_fix(self, cells, pdf_text):
        region = NS(kind='table', cells=[[c['text'] for c in cells]], metadata={'cell_records': cells})
        with patch('extraction.coordinator.pymupdf.open', return_value=FakePdf(pdf_text)):
            logical_table_text('x.pdf', NS(pages=[NS(number=1, regions=[region])]))
        return region

    def test_a_reversed_cell_takes_the_pdf_order(self):
        cells = [{'row': 0, 'column': 0, 'text': LABEL[::-1], 'bbox': [50, 40, 200, 70]}]
        region = self.run_fix(cells, LABEL + '\n')
        self.assertEqual(cells[0]['text'], LABEL)
        self.assertEqual(region.cells[0][0], LABEL)

    def test_a_cell_is_kept_when_the_pdf_text_has_other_characters(self):
        cells = [{'row': 0, 'column': 0, 'text': OTHER, 'bbox': [50, 40, 200, 70]}]
        self.run_fix(cells, LABEL)
        self.assertEqual(cells[0]['text'], OTHER)

    def test_latin_cells_are_not_read_again(self):
        cells = [{'row': 0, 'column': 0, 'text': 'EMP-2020-36418', 'bbox': [50, 40, 200, 70]}]
        self.run_fix(cells, '81463-0202-PME')
        self.assertEqual(cells[0]['text'], 'EMP-2020-36418')


if __name__ == '__main__':
    unittest.main()
