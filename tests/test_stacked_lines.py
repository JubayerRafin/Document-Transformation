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



class SplitAtRulesTests(unittest.TestCase):
    def header(self, text_left, text_right, rule_x):
        def char(c, x):
            box = NS(x=x, y=544.2, x2=x + 9, y2=553.2)
            return NS(char_unicode=c, visual_bbox=NS(box=box), pdf_style=NS(font_size=9))
        chars = [char(c, 263 + 9 * i) for i, c in enumerate(text_left)] + [char(' ', 263 + 9 * len(text_left))]
        start = rule_x + 6
        chars += [char(c, start + 9 * i) for i, c in enumerate(text_right)]
        line = NS(pdf_character=chars, box=NS(x=263, y=544.2, x2=chars[-1].visual_bbox.box.x2, y2=553.2))
        paragraph = NS(pdf_paragraph_composition=[NS(pdf_line=line)], debug_id='h', vertical=False)
        return paragraph

    def run_split(self, paragraph, rules):
        from transformation.layout.stacked_lines import split_at_rules
        page = NS(pdf_paragraph=[paragraph], pdf_curve=[NS(box=r) for r in rules])
        count = split_at_rules(page, lambda p, line: None, lambda p: p.debug_id + '-c')
        texts = [''.join(c.char_unicode for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character) for p in page.pdf_paragraph]
        return count, texts

    def test_two_header_cells_joined_across_a_cell_border_are_split(self):
        border = NS(x=290.2, x2=291.0, y=536.7, y2=558.4)
        self.assertEqual(self.run_split(self.header('数量', '単価', 290.6), [border]), (1, ['数量', '単価']))

    def test_lines_without_a_crossing_rule_or_with_a_short_rule_stay_whole(self):
        far = NS(x=400, x2=400.8, y=536.7, y2=558.4)          # a border of another column
        short = NS(x=290.2, x2=291.0, y=550.0, y2=552.0)      # a tick mark, not a border
        wide = NS(x=280.0, x2=300.0, y=536.7, y2=558.4)       # a box, not a rule
        for rule in (far, short, wide):
            self.assertEqual(self.run_split(self.header('数量', '単価', 290.6), [rule])[0], 0)


class ListMarkerTests(unittest.TestCase):
    def list_paragraph(self, items):
        """items: list of lists of line texts; lines are 12 pt apart, starting at y=600 going down."""
        chars, y = [], 600.0
        for lines in items:
            for text in lines:
                for i, c in enumerate(text):
                    box = NS(x=60 + 5 * i, y=y, x2=65 + 5 * i, y2=y + 9)
                    chars.append(NS(char_unicode=c, visual_bbox=NS(box=box), pdf_style=NS(font_size=9)))
                y -= 12
        line = NS(pdf_character=chars, box=NS(x=60, y=y, x2=300, y2=609))
        return NS(pdf_paragraph_composition=[NS(pdf_line=line)], debug_id='list', vertical=False)

    def run_split(self, paragraph, marker_ys):
        from transformation.layout.stacked_lines import split_at_list_markers
        curves = [NS(box=NS(x=47.2, y=y + 2.5, x2=50.9, y2=y + 6.2)) for y in marker_ys]
        page = NS(pdf_paragraph=[paragraph], pdf_curve=curves)
        count = split_at_list_markers(page, lambda p, line: None, lambda p: p.debug_id + '-b')
        return count, [''.join(c.char_unicode for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character)
                       for p in page.pdf_paragraph]

    def test_each_bulleted_item_becomes_its_own_paragraph_and_wrapped_lines_stay_with_it(self):
        paragraph = self.list_paragraph([['First item'], ['Second item', 'wraps here'], ['Third'], ['Fourth']])
        # bullets beside lines 1, 2, 4 and 5 (y 600, 588, 564, 552); line 3 continues item 2
        self.assertEqual(self.run_split(paragraph, [600, 588, 564, 552]),
                         (3, ['First item', 'Second itemwraps here', 'Third', 'Fourth']))

    def test_paragraphs_without_bullets_beside_them_stay_whole(self):
        paragraph = self.list_paragraph([['Plain text'], ['continues']])
        self.assertEqual(self.run_split(paragraph, [])[0], 0)
        self.assertEqual(self.run_split(paragraph, [400])[0], 0)   # a bullet elsewhere on the page


if __name__ == '__main__':
    unittest.main()
