import unittest
from types import SimpleNamespace as NS
from transformation.layout.panels import panel_horizontal_bounds


class PanelPaddingTests(unittest.TestCase):
    def setUp(self):
        self.block = (40, 100, 140, 180, 'body', 0, 0)
        self.panel = NS(x0=20, y0=30, x1=170, y1=200, width=150, height=170)

    def test_containing_panel_padding_is_available(self):
        self.assertEqual(panel_horizontal_bounds(self.block, [self.block], [self.panel], 600), (26, 164))

    def test_neighbor_content_blocks_expansion(self):
        other = (145, 120, 160, 150, 'other', 1, 0)
        self.assertEqual(panel_horizontal_bounds(self.block, [self.block, other], [self.panel], 600), (40, 140))

    def test_page_background_does_not_justify_expansion(self):
        background = NS(x0=0, y0=0, x1=600, y1=400, width=600, height=400)
        self.assertEqual(panel_horizontal_bounds(self.block, [self.block], [background], 600), (40, 140))

    def test_no_container_leaves_original_bounds(self):
        self.assertEqual(panel_horizontal_bounds(self.block, [self.block], [], 600), (40, 140))
