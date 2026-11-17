"""Font style flags corrected from font names. No BabelDOC needed."""
import unittest
from types import SimpleNamespace as NS

from transformation.pdf.font_flags import correct, install, style_from_name


class FontNameTests(unittest.TestCase):
    def test_family_and_weight_come_from_the_name_when_it_states_them(self):
        cases = {'NHCQMT+AptosDisplay': (False, None, None), 'HYFGGF+Aptos-Bold': (False, True, None),
                 'Helvetica-Bold': (False, True, None), 'CFTWLU+TimesNewRomanPS-ItalicMT': (True, None, True),
                 'Palatino-Roman': (True, None, None), 'Microsoft Sans Serif': (False, None, None),
                 'Noto Sans CJK KR': (False, None, None), 'SourceHanSerifKR-Heavy': (True, True, None),
                 'Arial,Bold': (False, True, None), 'XYZ+F12': (None, None, None)}
        for name, expected in cases.items():
            self.assertEqual(style_from_name(name), expected, name)

    def test_wrong_flags_are_corrected_and_unknown_fonts_untouched(self):
        helvetica = NS(name='Helvetica-Bold', font_id='F1', serif=1, bold=0, italic=0)
        self.assertTrue(correct(helvetica))
        self.assertEqual((helvetica.serif, helvetica.bold), (False, True))
        unknown = NS(name='F12', font_id='F2', serif=1, bold=0, italic=0)
        self.assertFalse(correct(unknown)); self.assertEqual(unknown.serif, 1)

    def test_install_corrects_the_font_before_mapping_once(self):
        seen = []
        class Mapper:
            def map(self, original_font, char_unicode): seen.append(original_font.serif); return None
        self.assertTrue(install(Mapper)); self.assertTrue(install(Mapper))
        Mapper().map(NS(name='Aptos', font_id='F1', serif=1, bold=0, italic=0), 'a')
        self.assertEqual(seen, [False])


if __name__ == '__main__':
    unittest.main()
