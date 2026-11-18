import unittest

from transformation.pdf.text_layer import _remap


class TextLayerTests(unittest.TestCase):
    def test_single_glyph_non_breaking_mappings_become_plain(self):
        cmap = ('2 beginbfchar\n<0001> <00a0>\n<000e> <2011>\n<0005> <0041>\nendbfchar\n'
                '2 beginbfrange\n<0010> <0010> <00A0>\n<0020> <0022> <2011>\nendbfrange\n')
        fixed, changed = _remap(cmap)
        self.assertEqual(changed, 3)
        self.assertIn('<0001> <0020>', fixed)
        self.assertIn('<000e> <002d>', fixed)
        self.assertIn('<0005> <0041>', fixed)            # other characters untouched
        self.assertIn('<0010> <0010> <0020>', fixed)
        self.assertIn('<0020> <0022> <2011>', fixed)     # a range is never shifted

    def test_nothing_to_change(self):
        cmap = '1 beginbfchar\n<0003> <0020>\nendbfchar\n'
        self.assertEqual(_remap(cmap), (cmap, 0))


if __name__ == '__main__':
    unittest.main()
