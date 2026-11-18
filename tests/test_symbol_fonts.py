import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from transformation.pdf.symbol_fonts import add_symbol_fallback


class FakeFont:
    def __init__(self, glyphs):
        self.glyphs = set(glyphs)

    def has_glyph(self, code):
        return code in self.glyphs

    def char_lengths(self, *args):
        return []


def mapper(fonts):
    return SimpleNamespace(fonts=dict(fonts), fontid2fontpath={}, fontid2font=dict(fonts),
                           fallback_font_ids=['GoNotoKurrent-Regular.ttf'], fallback_fonts=[fonts['GoNotoKurrent-Regular.ttf']])


class SymbolFontTests(unittest.TestCase):
    def assets(self, available):
        module = ModuleType('babeldoc.assets.assets')

        def get_font_and_metadata(name):
            if name not in available:
                raise FileNotFoundError(name)
            return f'/fonts/{name}', {'ascent': 900, 'descent': -200, 'encoding_length': 2}
        module.get_font_and_metadata = get_font_and_metadata
        package = ModuleType('babeldoc.assets')
        package.assets = module
        return patch.dict(sys.modules, {'babeldoc': ModuleType('babeldoc'), 'babeldoc.assets': package,
                                        'babeldoc.assets.assets': module})

    def test_a_font_with_check_boxes_becomes_the_last_fallback(self):
        m = mapper({'GoNotoKurrent-Regular.ttf': FakeFont([0x41])})
        with self.assets({'LXGWWenKaiGB-Regular.1.520.ttf'}), \
                patch('pymupdf.Font', side_effect=lambda fontfile: FakeFont([0x2610, 0x2611])):
            self.assertEqual(add_symbol_fallback(m), 'LXGWWenKaiGB-Regular.1.520.ttf')
        self.assertEqual(m.fallback_font_ids[-1], 'LXGWWenKaiGB-Regular.1.520.ttf')
        self.assertTrue(m.fallback_fonts[-1].has_glyph(0x2610))
        self.assertEqual(m.fallback_fonts[-1].encoding_length, 2)

    def test_a_font_already_loaded_for_another_role_is_reused(self):
        maru = FakeFont([0x2610])
        m = mapper({'GoNotoKurrent-Regular.ttf': FakeFont([0x41]), 'MaruBuri-Regular.ttf': maru})
        with self.assets(set()):
            self.assertEqual(add_symbol_fallback(m), 'MaruBuri-Regular.ttf')
        self.assertIs(m.fallback_fonts[-1], maru)
        with self.assets(set()):
            self.assertIsNone(add_symbol_fallback(m))     # added once only

    def test_nothing_changes_when_no_symbol_font_is_available(self):
        m = mapper({'GoNotoKurrent-Regular.ttf': FakeFont([0x41])})
        with self.assets(set()):
            self.assertIsNone(add_symbol_fallback(m))
        self.assertEqual(m.fallback_font_ids, ['GoNotoKurrent-Regular.ttf'])


if __name__ == '__main__':
    unittest.main()
