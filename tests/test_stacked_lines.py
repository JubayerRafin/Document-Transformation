import unittest
from types import SimpleNamespace as NS

from transformation.layout.stacked_lines import join_stacked_lines, mergeable, is_compact_cjk


def para(text, x, y, size=8.6, label='fallback_line'):
    """One-line paragraph; y is the line's bottom in PDF coordinates (growing upwards)."""
    chars = [NS(char_unicode=c, pdf_style=NS(font_size=size)) for c in text]
    line = NS(pdf_character=chars, box=NS(x=x, y=y, x2=x + size * len(text), y2=y + size))
    return NS(pdf_paragraph_composition=[NS(pdf_line=line)], layout_label=label, xobj_id=None, vertical=False)


def text(p):
    return ''.join(ch.char_unicode for c in p.pdf_paragraph_composition for ch in c.pdf_line.pdf_character)


class StackedLineTests(unittest.TestCase):
    def test_a_word_wrapped_one_character_per_line_becomes_one_paragraph_per_table_cell(self):
        # Two cells of one column: 安全手套 (gaps 4-5 pt) and 热敏标签, 12 pt below.
        cell1 = [para(c, 49.2, y) for c, y in zip('安全手套', (260.0, 247.1, 234.3, 221.6))]
        cell2 = [para(c, 49.3, y) for c, y in zip('热敏标签', (200.9, 188.7, 175.2, 162.4))]
        paragraphs = cell1 + cell2
        joined = join_stacked_lines(paragraphs, lambda p: None)
        self.assertEqual(joined, 6)
        self.assertEqual([text(p) for p in paragraphs], ['安全手套', '热敏标签'])

    def test_lines_of_other_columns_sizes_or_scripts_or_after_a_full_stop_stay_apart(self):
        upper = para('安全', 49.2, 260.0)
        self.assertFalse(mergeable(upper, para('手套', 80.0, 247.1)))            # another column
        self.assertFalse(mergeable(upper, para('手套', 49.2, 247.1, size=12)))   # another size
        self.assertFalse(mergeable(upper, para('手套', 49.2, 230.0)))            # gap of a paragraph
        self.assertFalse(mergeable(para('완료。', 49.2, 260.0), para('手套', 49.2, 247.1)))
        self.assertFalse(mergeable(para('Total', 49.2, 260.0), para('due', 49.2, 247.1)))
        self.assertFalse(mergeable(para('完成。', 49.2, 260.0), para('手套', 49.2, 247.1)))
        self.assertTrue(mergeable(upper, para('手套', 49.2, 247.1)))

    def test_compact_cjk_means_han_and_kana_only(self):
        self.assertTrue(is_compact_cjk('热敏标签'))
        self.assertTrue(is_compact_cjk('バーコード'))
        self.assertFalse(is_compact_cjk('바코드'))
        self.assertFalse(is_compact_cjk('USD 12'))


if __name__ == '__main__':
    unittest.main()
