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
        self.assertTrue(mergeable(para('품', 49.2, 260.0), para('목', 49.2, 247.1)))
        self.assertFalse(mergeable(para('완료.', 49.2, 260.0), para('품목', 49.2, 247.1)))
        self.assertFalse(mergeable(para('Total', 49.2, 260.0), para('due', 49.2, 247.1)))
        self.assertFalse(mergeable(para('完成。', 49.2, 260.0), para('手套', 49.2, 247.1)))
        self.assertTrue(mergeable(upper, para('手套', 49.2, 247.1)))

    def test_lines_of_other_scripts_join_only_inside_one_ruled_cell(self):
        upper, lower = para('परामर्श ', 49.2, 260.0), para('घंटे', 49.2, 247.1)
        cell = ([NS(x=45, y=240, x2=45.8, y2=275), NS(x=120, y=240, x2=120.8, y2=275)], [])
        self.assertTrue(mergeable(upper, lower, cell))
        self.assertFalse(mergeable(upper, lower))                       # no table rules
        self.assertFalse(mergeable(upper, lower, ([NS(x=45, y=240, x2=45.8, y2=275)], [])))  # one side only
        row_border = ([*cell[0]], [NS(x=45, y=257.0, x2=120.8, y2=257.6)])
        self.assertFalse(mergeable(upper, lower, row_border))           # two rows of the cell column
        self.assertFalse(mergeable(para('Done.', 49.2, 260.0), lower, cell))
        self.assertFalse(mergeable(para('安全', 49.2, 260.0), para('手套', 49.2, 247.1), row_border))

    def test_compact_cjk_means_han_and_kana_only(self):
        self.assertTrue(is_compact_cjk('热敏标签'))
        self.assertTrue(is_compact_cjk('バーコード'))
        self.assertTrue(is_compact_cjk('바코드'))
        self.assertFalse(is_compact_cjk('바코드 USB'))
        self.assertFalse(is_compact_cjk('USD 12'))



class SplitAtRulesTests(unittest.TestCase):
    def header(self, text_left, text_right, rule_x):
        def char(c, x):
            box = NS(x=x, y=544.2, x2=x + 9, y2=553.2)
            return NS(char_unicode=c, visual_bbox=NS(box=box), pdf_style=NS(font_size=9))
        chars = [char(c, 263 + 9 * i) for i, c in enumerate(text_left)]
        space = char(' ', 263 + 9 * len(text_left))
        space.visual_bbox.box.x2 = rule_x + 6   # BabelDOC's space spans the whole gap, border included
        chars.append(space)
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

    def test_bullets_after_right_to_left_items_split_them_too(self):
        paragraph = self.list_paragraph([['First item'], ['Second item']])
        from transformation.layout.stacked_lines import split_at_list_markers
        curves = [NS(box=NS(x=118, y=y + 2.5, x2=121.7, y2=y + 6.2)) for y in (600, 588)]
        page = NS(pdf_paragraph=[paragraph], pdf_curve=curves)
        self.assertEqual(split_at_list_markers(page, lambda p, line: None, lambda p: p.debug_id + '-b'), 1)

    def test_items_with_bullet_characters_become_paragraphs(self):
        paragraph = self.list_paragraph([['– Some servers', 'wrap here'], ['– In most cases'], ['– If you use']])
        self.assertEqual(self.run_split(paragraph, []),
                         (2, ['– Some serverswrap here', '– In most cases', '– If you use']))

    def test_numbered_items_become_paragraphs_and_wrapped_lines_stay_with_them(self):
        paragraph = self.list_paragraph([['1. Any change', 'wraps here'], ['2. Customer'], ['3. All goods']])
        self.assertEqual(self.run_split(paragraph, []),
                         (2, ['1. Any changewraps here', '2. Customer', '3. All goods']))

    def test_numbered_items_without_a_stored_space_split_too(self):
        paragraph = self.list_paragraph([['1.Die Prüfung'], ['2.Bitte'], ['3.Alle']])
        self.assertEqual(self.run_split(paragraph, [])[0], 2)
        self.assertEqual(self.run_split(self.list_paragraph([['1.5 percent'], ['2.5 more']]), [])[0], 0)
        self.assertEqual(self.run_split(self.list_paragraph([['1 . Die'], ['2. Bitte'], ['3 . Alle']]), [])[0], 2)

    def test_lettered_sub_steps_become_paragraphs(self):
        paragraph = self.list_paragraph([['a. Click Add', 'on the page'], ['b. Open another'], ['c. Paste']])
        self.assertEqual(self.run_split(paragraph, []),
                         (2, ['a. Click Addon the page', 'b. Open another', 'c. Paste']))

    def test_a_number_out_of_sequence_or_in_running_text_does_not_split(self):
        self.assertEqual(self.run_split(self.list_paragraph([['1. Steps', '5. later', '2. next']]), []),
                         (1, ['1. Steps5. later', '2. next']))
        self.assertEqual(self.run_split(self.list_paragraph([['Revenue rose by', '2. percent']]), [])[0], 0)

    def test_a_numbered_line_indented_from_the_first_does_not_split(self):
        paragraph = self.list_paragraph([['1. Any change'], ['2. Customer']])
        for c in paragraph.pdf_paragraph_composition[0].pdf_line.pdf_character[13:]:
            c.visual_bbox.box.x += 20
            c.visual_bbox.box.x2 += 20
        self.assertEqual(self.run_split(paragraph, [])[0], 0)

    def test_a_dash_inside_running_text_does_not_split_it(self):
        paragraph = self.list_paragraph([['Prices rose', '– and fell', 'again']])
        self.assertEqual(self.run_split(paragraph, [])[0], 0)

    def test_paragraphs_without_bullets_beside_them_stay_whole(self):
        paragraph = self.list_paragraph([['Plain text'], ['continues']])
        self.assertEqual(self.run_split(paragraph, [])[0], 0)
        self.assertEqual(self.run_split(paragraph, [400])[0], 0)   # a bullet elsewhere on the page


class LoneMarkTests(unittest.TestCase):
    def test_a_vowel_sign_split_off_below_its_line_joins_that_line(self):
        from transformation.layout.stacked_lines import attach_lone_marks
        word = NS(pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=[NS(char_unicode='स', pdf_style=NS(font_size=9))]))],
                  box=NS(x=55, y=528.4, x2=120, y2=537.4), xobj_id=0, debug_id='w')
        mark = NS(pdf_paragraph_composition=[NS(pdf_line=None, pdf_formula=NS(pdf_character=[
                      NS(char_unicode='ु', pdf_style=NS(font_size=9))]))],
                  box=NS(x=60.4, y=526.1, x2=65.2, y2=528.7), xobj_id=0, debug_id='m')
        far = NS(pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=[NS(char_unicode='x', pdf_style=NS(font_size=9))]))],
                 box=NS(x=300, y=528.4, x2=340, y2=537.4), xobj_id=0, debug_id='f')
        page = NS(pdf_paragraph=[word, mark, far])
        self.assertEqual(attach_lone_marks(page, lambda p: None), 1)
        self.assertEqual([p.debug_id for p in page.pdf_paragraph], ['w', 'f'])
        self.assertEqual(len(word.pdf_paragraph_composition), 2)

    def test_letters_are_not_lone_marks(self):
        from transformation.layout.stacked_lines import _is_lone_mark
        self.assertFalse(_is_lone_mark(NS(pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=[NS(char_unicode='स')]))])))
        self.assertTrue(_is_lone_mark(NS(pdf_paragraph_composition=[NS(pdf_formula=NS(pdf_character=[NS(char_unicode='\x00')]))])))
        self.assertFalse(_is_lone_mark(NS(pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=[NS(char_unicode='•')]))])))


class OverlappingFragmentTests(unittest.TestCase):
    def piece(self, pid, text, x, y, x2, y2):
        chars = [NS(char_unicode=c, pdf_style=NS(font_size=9)) for c in text]
        return NS(debug_id=pid, box=NS(x=x, y=y, x2=x2, y2=y2), vertical=False, xobj_id=0,
                  pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=chars))])

    def test_overlapping_pieces_of_one_hindi_line_become_one_paragraph(self):
        from transformation.layout.stacked_lines import join_overlapping_fragments
        # Boxes measured in the Hindi report: "परामर्श", "र्श घंट", "घंटे" on one line of a cell.
        a = self.piece('a', 'परामर्श', 49.0, 507.3, 74.6, 512.9)
        b = self.piece('b', 'र्श घंट', 72.3, 507.4, 82.8, 517.0)
        c = self.piece('c', 'घंटे', 81.0, 507.7, 88.1, 516.7)
        other_cell = self.piece('d', 'ZSC-2023', 120.0, 507.3, 170.0, 516.0)
        page = NS(pdf_paragraph=[a, b, c, other_cell])
        boxes = {id(p.pdf_paragraph_composition[0]): p.box for p in (a, b, c)}

        def update(p):  # as BabelDOC: the joined paragraph's box covers all its pieces
            parts = [boxes[id(comp)] for comp in p.pdf_paragraph_composition]
            p.box = NS(x=min(q.x for q in parts), y=min(q.y for q in parts),
                       x2=max(q.x2 for q in parts), y2=max(q.y2 for q in parts))
        self.assertEqual(join_overlapping_fragments(page, update), 2)
        self.assertEqual([p.debug_id for p in page.pdf_paragraph], ['a', 'd'])

    def test_touching_or_latin_pieces_stay_apart(self):
        from transformation.layout.stacked_lines import join_overlapping_fragments
        left = self.piece('l', 'परामर्श', 49.0, 507.3, 74.6, 512.9)
        right = self.piece('r', 'घंटे', 74.8, 507.7, 88.1, 516.7)           # touching, not overlapping
        latin_a = self.piece('x', 'Total', 49.0, 400.0, 75.0, 409.0)
        latin_b = self.piece('y', 'due', 72.0, 400.0, 90.0, 409.0)
        page = NS(pdf_paragraph=[left, right, latin_a, latin_b])
        self.assertEqual(join_overlapping_fragments(page, lambda p: None), 0)


class BrokenLineTests(unittest.TestCase):
    def piece(self, pid, text, x, x2, y=78.7, size=7.0):
        step = (x2 - x) / max(1, len(text))
        chars = []
        for i, c in enumerate(text):
            box = NS(x=x + i * step, y=y, x2=x + (i + 1) * step, y2=y + size)
            chars.append(NS(char_unicode=c, visual_bbox=NS(box=box), pdf_style=NS(font_size=size)))
        return NS(debug_id=pid, box=NS(x=x, y=y, x2=x2, y2=y + size), vertical=False, xobj_id=0,
                  pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=chars))])

    def run_join(self, pieces, curves=()):
        from transformation.layout.stacked_lines import join_broken_line
        page = NS(pdf_paragraph=list(pieces), pdf_curve=[NS(box=c) for c in curves])
        count = join_broken_line(page, lambda p: None)
        texts = [''.join(c.char_unicode for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character)
                 for p in page.pdf_paragraph]
        return count, texts

    def test_a_contents_line_cut_into_letters_becomes_one_paragraph_without_its_leader(self):
        # Measured in an HP manual's contents (x positions of the pieces of one line).
        pieces = [self.piece('a', 'A', 44.8, 50.9), self.piece('b', 'ppen', 51.6, 72.7), self.piece('c', 'di', 73.6, 80.4),
                  self.piece('d', 'x', 81.1, 85.6), self.piece('e', 'B E', 88.3, 103.1), self.piece('f', 'nv', 104.1, 113.7),
                  self.piece('g', 'program', 254.5, 290.9, y=76.8), self.piece('h', 'stewardshi', 199.0, 245.8),
                  self.piece('i', 'p', 246.9, 251.6, y=77.1), self.piece('j', '.' * 40, 291.6, 523.1),
                  self.piece('k', '206', 524.6, 540.6)]
        count, texts = self.run_join(pieces)
        self.assertIn('AppendixB Env', texts[0])
        self.assertIn('.' * 40, texts)
        self.assertIn('206', texts)

    def test_a_form_row_of_whole_fields_is_never_joined(self):
        row = [self.piece('a', '김민준', 41, 60), self.piece('b', 'EMP-2026-14033', 61, 140),
               self.piece('c', 'Employee', 141, 190), self.piece('d', 'ID', 191, 202),
               self.piece('e', 'Copperleaf Software', 300, 463)]
        count, texts = self.run_join(row)
        self.assertNotIn('EMP-2026-14033', ''.join(t for t in texts if '김민준' in t))
        self.assertIn('Copperleaf Software', texts)

    def test_a_sentence_cut_mid_word_into_long_pieces_is_joined(self):
        pieces = [self.piece('a', 'The control pa', 50, 120), self.piece('b', 'nel provides the printe', 120.5, 240),
                  self.piece('c', 'r.', 240.4, 250)]
        count, texts = self.run_join(pieces)
        self.assertEqual(texts, ['The control panel provides the printer.'.replace(' panel', ' pa' + 'nel')])

    def test_separate_cells_and_words_of_unbroken_lines_stay_apart(self):
        count, texts = self.run_join([self.piece('a', 'Total', 50, 70), self.piece('b', 'USD 12', 72.5, 100)])
        self.assertEqual(count, 0)                                  # word gap only, nothing cut mid-word
        count, _ = self.run_join([self.piece('a', 'Qua', 50, 62), self.piece('b', 'ntity', 62.5, 80)],
                                 curves=[NS(x=62.1, y=70, x2=62.4, y2=95)])
        self.assertEqual(count, 0)                                  # a table rule between them


if __name__ == '__main__':
    unittest.main()


class SplitAtBadgesTests(unittest.TestCase):
    """Callout numbers in their own circles stay separate paragraphs (drawn after their circle)."""
    def line(self, items):
        chars = []
        for c, x in items:
            box = NS(x=x, y=158.9, x2=x + 7.7, y2=172.9)
            chars.append(NS(char_unicode=c, visual_bbox=NS(box=box), pdf_style=NS(font_size=14)))
        line = NS(pdf_character=chars, box=NS(x=items[0][1], y=158.9, x2=items[-1][1] + 7.7, y2=172.9))
        return NS(pdf_paragraph_composition=[NS(pdf_line=line)], debug_id='c', vertical=False)

    def run_split(self, paragraph, circles):
        from transformation.layout.stacked_lines import split_at_badges
        page = NS(pdf_paragraph=[paragraph], pdf_curve=[NS(box=b) for b in circles])
        count = split_at_badges(page, lambda p, line: None, lambda p: p.debug_id + '-b')
        return count, [''.join(c.char_unicode for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character)
                       for p in page.pdf_paragraph]

    def test_two_numbers_in_two_circles_become_two_paragraphs(self):
        circles = [NS(x=267.8, y=159.3, x2=285.8, y2=177.3), NS(x=286.6, y=159.3, x2=304.6, y2=177.3)]
        self.assertEqual(self.run_split(self.line([('3', 272.8), (' ', 280.6), ('4', 291.7)]), circles), (1, ['3', '4']))

    def test_one_badge_or_none_keeps_the_line_whole(self):
        one = [NS(x=267.8, y=159.3, x2=285.8, y2=177.3)]
        self.assertEqual(self.run_split(self.line([('3', 272.8), (' ', 280.6), ('4', 291.7)]), one)[0], 0)
        self.assertEqual(self.run_split(self.line([('A', 100), ('B', 108)]), [])[0], 0)

    def test_a_large_box_such_as_a_table_cell_is_not_a_badge(self):
        cells = [NS(x=260, y=150, x2=289, y2=230), NS(x=289, y=150, x2=400, y2=180)]
        self.assertEqual(self.run_split(self.line([('3', 272.8), (' ', 280.6), ('4', 291.7)]), cells)[0], 0)


class LogicalRtlOrderTests(unittest.TestCase):
    """Characters of right-to-left lines are put into reading order before paragraphs are built."""
    def chars(self, items, y=500):
        return [NS(char_unicode=c, visual_bbox=NS(box=NS(x=x, y=y, x2=x + 5, y2=y + 10))) for c, x in items]

    def order(self, chars):
        from transformation.layout.stacked_lines import logical_rtl_order
        page = NS(pdf_character=list(chars))
        logical_rtl_order(page)
        return ''.join(c.char_unicode for c in page.pdf_character)

    def test_runs_stored_left_to_right_come_back_in_reading_order(self):
        # On the page, left to right: "891 " then "نحو" (reading: "نحو 891").
        line = self.chars([('8', 100), ('9', 105), ('1', 110), (' ', 115), ('ن', 130), ('ح', 125), ('و', 120)])
        self.assertEqual(self.order(line), 'نحو 891')

    def test_a_line_already_in_reading_order_is_unchanged(self):
        line = self.chars([('ن', 130), ('ح', 125), ('و', 120), (' ', 115), ('1', 100), ('2', 105)])
        self.assertEqual(self.order(line), 'نحو 12')

    def test_left_to_right_lines_and_far_apart_cells_are_untouched(self):
        latin = self.chars([('a', 100), ('b', 105), ('c', 110)])
        self.assertEqual(self.order(latin), 'abc')
        cells = self.chars([('4', 100), ('3', 105)]) + self.chars([('ب', 300), ('ا', 295)])
        self.assertEqual(self.order(cells), '43با')


class LogicalRtlRunsTests(LogicalRtlOrderTests):
    def test_runs_of_one_line_stored_far_apart_are_ordered_as_one_line(self):
        # Stream: the left run first (".12 "), then the right run ("ارتفعت"), as Chromium writes them.
        line = self.chars([('.', 100), ('1', 105), ('2', 110), (' ', 115)]) + \
               self.chars([('ت', 145), ('ف', 140), ('ع', 135), ('ر', 130), ('ا', 125), (' ', 120)])
        self.assertEqual(self.order(line), 'تفعرا  12.')


class LeadLabelTests(unittest.TestCase):
    def paragraph(self, text, x, y, size=9):
        chars = [NS(char_unicode=c, visual_bbox=NS(box=NS(x=x + i * 5, y=y, x2=x + i * 5 + 5, y2=y + size)),
                    pdf_style=NS(font_size=size)) for i, c in enumerate(text)]
        box = NS(x=x, y=y, x2=x + 5 * len(text), y2=y + size)
        line = NS(pdf_character=chars, box=box)
        return NS(pdf_paragraph_composition=[NS(pdf_line=line)], box=box, vertical=False, xobj_id=None, debug_id=text[:4])

    def run_join(self, paragraphs, rules=()):
        from transformation.layout.stacked_lines import join_lead_labels
        page = NS(pdf_paragraph=list(paragraphs), pdf_curve=[NS(box=r) for r in rules])
        joined = join_lead_labels(page, lambda p: None)
        return joined, [''.join(c.char_unicode for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character)
                        for p in page.pdf_paragraph]

    def test_a_note_label_joins_the_text_it_starts(self):
        label, body = self.paragraph('NOTE:', 100, 500), self.paragraph('Create an account', 135, 500)
        self.assertEqual(self.run_join([label, body]), (1, ['NOTE:Create an account']))

    def test_far_labels_other_lines_long_texts_and_ruled_cells_stay_apart(self):
        far = [self.paragraph('Name:', 100, 500), self.paragraph('Kim', 300, 500)]
        below = [self.paragraph('NOTE:', 100, 500), self.paragraph('Text', 135, 480)]
        sentence = [self.paragraph('This is a whole sentence:', 100, 500), self.paragraph('Text', 230, 500)]
        for case in (far, below, sentence):
            self.assertEqual(self.run_join(case)[0], 0)
        rule = NS(x=132, y=495, x2=132.6, y2=515)
        self.assertEqual(self.run_join([self.paragraph('Item:', 100, 500), self.paragraph('Text', 135, 500)], [rule])[0], 0)


class InstallOrderTests(unittest.TestCase):
    def test_a_piece_cut_off_a_list_item_joins_it_once_the_list_is_split(self):
        """HP manual, 2026-11-23: "3. ... click the Email Setup lin" | "k." stayed two paragraphs because
        the cut was looked for while the three items were still one paragraph."""
        from transformation.layout.stacked_lines import install

        def chars(text, x, y, size=9.0, step=5.0):
            return [NS(char_unicode=c, visual_bbox=NS(box=NS(x=x + i * step, y=y, x2=x + (i + 1) * step, y2=y + size)),
                       box=NS(x=x + i * step, y=y, x2=x + (i + 1) * step, y2=y + size), pdf_style=NS(font_size=size))
                    for i, c in enumerate(text)]

        items = chars('1. Open the tab', 60, 600) + chars('2. Click the lin', 60, 588)
        listed = NS(debug_id='list', vertical=False, xobj_id=0, box=NS(x=60, y=588, x2=140, y2=609),
                    pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=items, box=NS(x=60, y=588, x2=140, y2=609)))])
        tail = chars('k.', 140.5, 588)
        cut = NS(debug_id='cut', vertical=False, xobj_id=0, box=NS(x=140.5, y=588, x2=150.5, y2=597),
                 pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=tail, box=NS(x=140.5, y=588, x2=150.5, y2=597)))])
        page = NS(pdf_paragraph=[listed, cut], pdf_curve=[], pdf_character=[])

        def refresh(paragraph, update_unicode=True):
            cs = [c for comp in paragraph.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
            paragraph.box = NS(x=min(c.box.x for c in cs), y=min(c.box.y for c in cs),
                               x2=max(c.box.x2 for c in cs), y2=max(c.box.y2 for c in cs))

        class Finder:
            def process_page(self, page):
                return None
            def update_line_data(self, line):
                pass
            update_paragraph_data = staticmethod(refresh)
            def _set_paragraph_render_order(self, page):
                pass

        install(Finder)
        Finder().process_page(page)
        texts = [''.join(c.char_unicode for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character)
                 for p in page.pdf_paragraph]
        self.assertEqual(texts, ['1. Open the tab', '2. Click the link.'])
