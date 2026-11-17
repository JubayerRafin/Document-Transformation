"""Words that fit at the end of a line are not split. No BabelDOC needed."""
import unittest
from types import SimpleNamespace as NS

from transformation.layout.line_breaks import install_whole_word_breaks


def units(text, width=5.0):
    return [NS(can_break_line=(c == ' '), width=width) for c in text]


class WholeWordTests(unittest.TestCase):
    def test_the_rest_of_the_word_after_the_current_letter_is_measured_once(self):
        class Typesetting: pass
        install_whole_word_breaks(Typesetting); install_whole_word_breaks(Typesetting)
        measure = Typesetting()._get_width_before_next_break_point
        word = units('ment quelques')
        self.assertEqual(measure(word, 1.0), 15.0)          # 'ent' after 'm', not 'ment'
        self.assertEqual(measure(word, 0.5), 7.5)
        self.assertEqual(measure(units(' word'), 1.0), 0)   # at a space: break point
        self.assertEqual(measure([], 1.0), 0)

    def test_a_word_ending_just_inside_the_line_is_placed_whole(self):
        # BabelDOC's check at each letter: current_x + unit_width + width_after > right edge.
        class Typesetting: pass
        install_whole_word_breaks(Typesetting)
        measure = Typesetting()._get_width_before_next_break_point
        right, x, word = 100.0, 55.0, units('seulement')    # 9 letters x 5 = 45: ends at exactly 100
        for i, unit in enumerate(word):
            self.assertLessEqual(x + unit.width + measure(word[i:], 1.0), right, f'split before letter {i}')
            x += unit.width


class AttachedPunctuationTests(unittest.TestCase):
    def test_punctuation_next_to_cjk_text_gets_no_extra_gap(self):
        from transformation.layout.line_breaks import install_attached_punctuation
        class Unit:
            def __init__(self, text): self.text = text
            def try_get_unicode(self): return self.text
            def calc_mixed_character_blacklist(self): return self.text in ['。', '，']
        install_attached_punctuation(Unit); install_attached_punctuation(Unit)
        for mark in ('.', ':', ')', '(', '%', '。'):
            self.assertTrue(Unit(mark).calc_mixed_character_blacklist(), mark)
        for other in ('A', '7', '-', None):
            self.assertFalse(Unit(other).calc_mixed_character_blacklist(), other)


if __name__ == '__main__':
    unittest.main()
