"""Cells of a found table whose border runs through a word without being drawn there are dropped."""
import unittest

import numpy as np

from transformation.pdf.babeldoc_worker import cells_with_undrawn_borders


class UndrawnBorderTests(unittest.TestCase):
    def setUp(self):
        self.ink = np.zeros((200, 200), bool)      # scale 1: one pixel per point
        self.words = [(40, 50, 70, 60, 'Tray'), (110, 50, 130, 60, 'jam')]
        self.cells = [(20, 20, 60, 100), (60, 20, 150, 100), (20, 100, 150, 180)]

    def test_a_border_through_a_word_that_is_not_drawn_drops_its_cells(self):
        # the edge x=60 runs through "Tray" (40-70) and nothing is drawn there: a clipped picture line
        self.assertEqual(cells_with_undrawn_borders(self.cells, self.words, self.ink, 1.0), {0, 1})

    def test_a_drawn_border_through_spilled_text_keeps_its_cells(self):
        self.ink[20:100, 60] = True
        self.assertEqual(cells_with_undrawn_borders(self.cells, self.words, self.ink, 1.0), set())

    def test_cells_clear_of_words_are_kept(self):
        self.assertEqual(cells_with_undrawn_borders([(20, 100, 150, 180), None], self.words, self.ink, 1.0), set())


if __name__ == '__main__':
    unittest.main()
