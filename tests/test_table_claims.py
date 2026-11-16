"""A paragraph claimed by several table cells gets one slot or none, never a failed job."""
import unittest

from transformation.pdf.babeldoc_worker import resolve_cell_claims


class CellClaimTests(unittest.TestCase):
    def test_single_claim_stands(self):
        self.assertEqual(resolve_cell_claims([([0, 0, 10, 10], 'a')]), 'a')

    def test_nested_cells_resolve_to_the_innermost(self):
        # A merged cell around a smaller one, as detected around masking boxes (combined jobs, 2026-11-16).
        claims = [([221, 622, 426, 644], 'merged'), ([329, 633, 426, 644], 'inner')]
        self.assertEqual(resolve_cell_claims(claims), 'inner')
        self.assertEqual(resolve_cell_claims(list(reversed(claims))), 'inner')

    def test_overlapping_cells_that_are_not_nested_get_no_slot(self):
        self.assertIsNone(resolve_cell_claims([([0, 0, 100, 20], 'left'), ([50, 0, 150, 20], 'right')]))


if __name__ == '__main__':
    unittest.main()
