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

    def test_a_code_wrapped_at_its_hyphens_is_one_paragraph(self):
        lines = [para('GQX-', 89.0, 260.0), para('2024-', 89.0, 248.6), para('99135', 89.0, 237.2)]
        self.assertEqual(join_stacked_lines(lines, lambda p: None), 2)
        self.assertEqual([text(p) for p in lines], ['GQX-2024-99135'])
        self.assertFalse(mergeable(para('Notes -', 89.0, 260.0), para('later', 89.0, 248.6)))   # a dash, not a hyphen
        self.assertFalse(mergeable(para('GQX-', 89.0, 260.0), para('2024', 140.0, 248.6)))     # another column

    def test_lines_inside_one_found_table_cell_join_unless_the_lower_one_is_a_list_item(self):
        cell = NS(x=40.0, y=230.0, x2=120.0, y2=275.0)
        upper, lower = para('सुरक्षा', 49.2, 260.0), para('दस्ताने', 49.2, 247.1)
        self.assertFalse(mergeable(upper, lower, ([], [])))                     # no rules: apart
        self.assertTrue(mergeable(upper, lower, ([], [], (cell,))))              # one found cell
        self.assertFalse(mergeable(upper, para('दस्ताने', 49.2, 200.0), ([], [], (cell,))))   # outside it
        self.assertFalse(mergeable(para('Open the tray', 49.2, 260.0), para('2. Close it', 49.2, 247.1), ([], [], (cell,))))

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

    def test_right_to_left_numbered_items_line_up_on_the_right(self):
        """Arabic manual, 2026-11-23: items of different lengths end at one right edge."""
        chars, y = [], 600.0
        for text in ['1. من المقرر إجراء', '2. يجب', '3. من المقرر']:
            right = 300.0
            for i, c in enumerate(text):           # logical order, drawn from the right
                box = NS(x=right - 5 * (i + 1), y=y, x2=right - 5 * i, y2=y + 9)
                chars.append(NS(char_unicode=c, visual_bbox=NS(box=box), pdf_style=NS(font_size=9)))
            y -= 12
        paragraph = NS(pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=chars, box=None))],
                       debug_id='list', vertical=False)
        self.assertEqual(self.run_split(paragraph, []), (2, ['1. من المقرر إجراء', '2. يجب', '3. من المقرر']))

    def test_a_short_number_glyph_stays_on_its_line_and_right_edges_may_differ_a_little(self):
        """Arabic manual on the pod, 2026-11-23: "1." drawn lower and smaller than the letters beside
        it became a line of its own, and the items' right edges differed by 1.6 pt."""
        from transformation.layout.stacked_lines import _visual_lines, _item_number
        chars, y = [], 600.0
        for n, text in enumerate(['1. من المقرر', '2. يجب', '3. من', '4. يجب']):
            right = 300.0 + 0.55 * n
            for i, c in enumerate(text):
                low = c in '1234.'
                box = NS(x=right - 5 * (i + 1), y=y - (1.5 if low else 0), x2=right - 5 * i, y2=y + (5 if low else 9))
                chars.append(NS(char_unicode=c, visual_bbox=NS(box=box), pdf_style=NS(font_size=10.5)))
            y -= 14
        self.assertEqual(len(_visual_lines(chars)), 4)
        self.assertEqual(_item_number('1.'), 1)
        paragraph = NS(pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=chars, box=None))],
                       debug_id='list', vertical=False)
        self.assertEqual(self.run_split(paragraph, [])[0], 3)

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


class OverhangingSpaceTests(unittest.TestCase):
    def test_a_space_running_past_the_letters_is_cut_back(self):
        """Arabic manual, 2026-11-23: a line-end space from 526.7 to 650.2 pt widened a list item's
        box past the page and its English translation ran off the page."""
        from transformation.layout.stacked_lines import trim_overhanging_spaces
        letters = [NS(char_unicode=c, box=NS(x=480 + 6 * i, y=0, x2=486 + 6 * i, y2=9), pdf_style=NS(font_size=9))
                   for i, c in enumerate('مرحبا')]
        space_box = NS(x=500.0, y=0, x2=650.2, y2=9)
        space = NS(char_unicode=' ', box=space_box, visual_bbox=NS(box=space_box), advance=150.2, pdf_style=NS(font_size=9))
        inner_box = NS(x=486.0, y=0, x2=492.0, y2=9)
        inner = NS(char_unicode=' ', box=inner_box, visual_bbox=NS(box=inner_box), advance=6, pdf_style=NS(font_size=9))
        paragraph = NS(pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=letters + [space, inner]))])
        updated = []
        self.assertEqual(trim_overhanging_spaces(NS(pdf_paragraph=[paragraph]), updated.append), 1)
        self.assertEqual((space_box.x, space_box.x2), (500.0, 503.0))
        self.assertEqual((inner_box.x, inner_box.x2), (486.0, 492.0))    # inside the letters: kept
        self.assertEqual(updated, [paragraph])
        self.assertEqual(trim_overhanging_spaces(NS(pdf_paragraph=[paragraph]), updated.append), 0)


class OverlappingFragmentTests(unittest.TestCase):
    def piece(self, pid, text, x, y, x2, y2):
        chars = [NS(char_unicode=c, pdf_style=NS(font_size=9)) for c in text]
        return NS(debug_id=pid, box=NS(x=x, y=y, x2=x2, y2=y2), vertical=False, xobj_id=0,
                  pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=chars))])

    def test_arabic_pieces_inside_their_own_cell_paragraph_are_removed(self):
        """Arabic newsletter, 2026-11-24: "كرس", "رسي", "كر" inside the box of "كرسي مكتب"."""
        from transformation.layout.stacked_lines import drop_contained_duplicates
        whole = self.piece('w', 'كرسي مكتب', 259.8, 760.0, 280.8, 782.0)
        pieces = [self.piece('p1', 'كرس', 266.5, 771.0, 280.8, 781.0), self.piece('p2', 'رسي', 260.5, 771.0, 276.3, 781.0),
                  self.piece('p3', 'كر', 272.5, 771.0, 280.8, 781.0)]
        other = self.piece('o', 'مكتب', 300.0, 771.0, 320.0, 781.0)          # another cell: kept
        latin = self.piece('l', 'Total', 262.0, 771.0, 279.0, 781.0)         # not a joined script: kept
        page = NS(pdf_paragraph=[whole, *pieces, other, latin])
        self.assertEqual(drop_contained_duplicates(page), 3)
        self.assertEqual([p.debug_id for p in page.pdf_paragraph], ['w', 'o', 'l'])

    def test_arabic_pieces_at_the_top_of_the_word_are_removed(self):
        """The same newsletter on a later run: only the letters' tops were read again, above the word's
        box (boxes as BabelDOC measured them); they were translated "Dedicated", "Resy", "Sure"."""
        from transformation.layout.stacked_lines import drop_contained_duplicates
        whole = self.piece('w', 'كرسي مكتب', 260.5, 52.2, 280.0, 69.5)
        pieces = [self.piece('p1', 'كرس', 272.5, 64.9, 276.3, 67.5), self.piece('p2', 'رسي', 266.3, 69.3, 272.5, 70.6),
                  self.piece('p3', 'كر', 276.0, 69.5, 280.0, 73.9)]
        line_above = self.piece('a', 'كرسي', 262.0, 74.0, 280.0, 83.0)     # a line of its own above: kept
        page = NS(pdf_paragraph=[whole, *pieces, line_above])
        self.assertEqual(drop_contained_duplicates(page), 3)
        self.assertEqual([p.debug_id for p in page.pdf_paragraph], ['w', 'a'])

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

    def test_pieces_held_as_style_runs_take_their_characters_size(self):
        """Hindi invoice, 2026-11-23: "प्रब" | "ंधक" (manager) had no paragraph size, counted as 1 pt
        tall lines and were translated "Management" and "Mortgage" over each other."""
        from transformation.layout.stacked_lines import join_overlapping_fragments
        def run(pid, text, x, y, x2, y2):
            chars = [NS(char_unicode=c, pdf_style=NS(font_size=9)) for c in text]
            return NS(debug_id=pid, box=NS(x=x, y=y, x2=x2, y2=y2), vertical=False, xobj_id=0,
                      pdf_paragraph_composition=[NS(pdf_line=None, pdf_same_style_characters=NS(pdf_character=chars))])
        a, b = run('a', 'प्रब', 271.7, 582.9, 283.7, 589.7), run('b', 'ंधक', 281.5, 583.2, 298.3, 591.8)
        c, d = run('c', 'कु', 381.6, 541.9, 390.0, 550.7), run('d', 'ल', 389.7, 544.0, 396.9, 550.7)
        page = NS(pdf_paragraph=[a, b, c, d])
        self.assertEqual(join_overlapping_fragments(page, lambda p: None), 2)
        self.assertEqual([p.debug_id for p in page.pdf_paragraph], ['a', 'c'])

    def test_short_arabic_pieces_beside_their_line_join_it(self):
        """Arabic report, 2026-11-23: "ساعات استشا" | "ر" | "ة" in one table cell; "رة" became "Rate"."""
        from transformation.layout.stacked_lines import join_overlapping_fragments
        main = self.piece('m', 'ساعات استشا', 496.28, 329.58, 545.28, 338.68)
        ra = self.piece('r', 'ر', 491.77, 327.43, 494.72, 332.34)
        ta = self.piece('t', 'ة', 488.77, 329.58, 491.77, 335.71)
        page = NS(pdf_paragraph=[main, ra, ta])
        boxes = {id(p.pdf_paragraph_composition[0]): p.box for p in (main, ra, ta)}

        def update(p):  # as BabelDOC: the joined paragraph's box covers all its pieces
            parts = [boxes[id(comp)] for comp in p.pdf_paragraph_composition]
            p.box = NS(x=min(q.x for q in parts), y=min(q.y for q in parts),
                       x2=max(q.x2 for q in parts), y2=max(q.y2 for q in parts))
        self.assertEqual(join_overlapping_fragments(page, update), 2)
        self.assertEqual(len(page.pdf_paragraph), 1)
        text = ''.join(c.char_unicode for comp in page.pdf_paragraph[0].pdf_paragraph_composition
                       for c in comp.pdf_line.pdf_character)
        self.assertEqual(text, 'ساعات استشارة')   # right piece first

    def test_arabic_pieces_stay_apart_across_a_rule_or_a_wide_gap(self):
        from transformation.layout.stacked_lines import join_overlapping_fragments
        main = self.piece('m', 'ساعات استشا', 496.28, 329.58, 545.28, 338.68)
        ra = self.piece('r', 'رة', 488.77, 329.58, 494.72, 338.0)
        rule = NS(box=NS(x=495.0, y=320.0, x2=495.5, y2=345.0))
        self.assertEqual(join_overlapping_fragments(NS(pdf_paragraph=[main, ra], pdf_curve=[rule]), lambda p: None), 0)
        far = self.piece('f', 'رة', 480.0, 329.58, 492.0, 338.0)          # 4.3 pt away
        long_word = self.piece('w', 'استشارات', 470.0, 329.58, 495.0, 338.0)  # both longer than 3 letters
        self.assertEqual(join_overlapping_fragments(NS(pdf_paragraph=[main, far]), lambda p: None), 0)
        self.assertEqual(join_overlapping_fragments(NS(pdf_paragraph=[main, long_word]), lambda p: None), 0)

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

    def test_pieces_whose_advance_boxes_meet_inside_a_word_are_joined(self):
        """Canon manual, 2026-11-23: "You ca" | "n register ...", 1.5 pt of ink between "a" and "n"."""
        a, b = self.piece('a', 'You ca', 89.3, 116.9, size=9.0), self.piece('b', 'n register', 116.9, 160.0, size=9.0)
        for p, inset in ((a, 0.4), (b, 0.7)):        # ink sits inside the advance box (side bearings)
            for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character:
                c.box = c.visual_bbox.box
                c.visual_bbox = NS(box=NS(x=c.box.x + inset, y=c.box.y, x2=c.box.x2 - inset, y2=c.box.y2))
        count, texts = self.run_join([a, b])
        self.assertEqual((count, texts), (1, ['You can register']))
        # The same pieces with a word space between their advance boxes stay apart.
        a, b = self.piece('a', 'You ca', 89.3, 116.9, size=9.0), self.piece('b', 'Nothing', 119.6, 160.0, size=9.0)
        for p in (a, b):
            for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character:
                c.box = c.visual_bbox.box
        self.assertEqual(self.run_join([a, b])[0], 0)

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


class DrawnBoxTests(unittest.TestCase):
    def test_a_line_of_a_picture_past_its_frame_is_not_a_table_border(self):
        """HP manual, 2026-11-23: a printer drawing's line ran below its picture through "Tray 1" and
        cut the word into "Tr" | "ay"."""
        from transformation.layout.stacked_lines import vertical_rules, drawn_boxes
        picture = NS(xobj_id=575, box=NS(x=111.6, y=476.8, x2=416.8, y2=709.2))
        stray = NS(xobj_id=575, box=NS(x=316.9, y=301.0, x2=318.3, y2=478.2))
        border = NS(xobj_id=None, box=NS(x=200.0, y=300.0, x2=200.5, y2=400.0))
        inside = NS(xobj_id=575, box=NS(x=150.0, y=500.0, x2=151.0, y2=600.0))
        page = NS(pdf_curve=[stray, border, inside], pdf_xobject=[picture])
        rules = vertical_rules(page)
        self.assertEqual([(r.x, r.y2) for r in rules], [(200.0, 400.0), (150.0, 600.0)])
        self.assertEqual(stray.box.y, 301.0)                       # the IR itself is left as it was
        self.assertEqual(len(drawn_boxes(NS(pdf_curve=[stray], pdf_xobject=[]))), 1)


class LonePunctuationTests(unittest.TestCase):
    def line(self, text, x, y, step=4.0, size=8.0):
        chars = [NS(char_unicode=c, visual_bbox=NS(box=NS(x=x + i * step, y=y, x2=x + (i + 1) * step, y2=y + size)),
                    pdf_style=NS(font_size=size)) for i, c in enumerate(text)]
        return NS(pdf_character=chars, box=NS(x=x, y=y, x2=x + step * len(text), y2=y + size))

    def test_a_hyphen_inside_a_wrapped_code_goes_back_to_its_line(self):
        from transformation.layout.stacked_lines import attach_lone_punctuation
        lines = [self.line('GQX', 89.4, 106.3), self.line('2024-', 89.4, 96.9), self.line('99135', 89.4, 87.5)]
        code = NS(debug_id='c', box=NS(x=89.4, y=87.5, x2=110.6, y2=114.3), xobj_id=0, vertical=False,
                  pdf_paragraph_composition=[NS(pdf_line=l) for l in lines])
        hyphen = NS(debug_id='h', box=NS(x=101.4, y=106.3, x2=104.0, y2=114.3), xobj_id=0, vertical=False,
                    pdf_paragraph_composition=[NS(pdf_line=self.line('-', 101.4, 106.3, step=2.6))])
        page = NS(pdf_paragraph=[code, hyphen])
        self.assertEqual(attach_lone_punctuation(page, lambda p, line: None), 1)
        self.assertEqual(page.pdf_paragraph, [code])
        self.assertEqual(''.join(c.char_unicode for c in lines[0].pdf_character), 'GQX-')

    def test_bullets_and_punctuation_outside_a_paragraph_stay(self):
        from transformation.layout.stacked_lines import attach_lone_punctuation
        text = NS(debug_id='t', box=NS(x=60, y=100, x2=200, y2=108), xobj_id=0, vertical=False,
                  pdf_paragraph_composition=[NS(pdf_line=self.line('Open the tray', 60, 100))])
        bullet = NS(debug_id='b', box=NS(x=61, y=100, x2=64, y2=108), xobj_id=0, vertical=False,
                    pdf_paragraph_composition=[NS(pdf_line=self.line('•', 61, 100))])
        outside = NS(debug_id='o', box=NS(x=40, y=100, x2=43, y2=108), xobj_id=0, vertical=False,
                     pdf_paragraph_composition=[NS(pdf_line=self.line('-', 40, 100))])
        page = NS(pdf_paragraph=[text, bullet, outside])
        self.assertEqual(attach_lone_punctuation(page, lambda p, line: None), 0)


class StepNumberTests(unittest.TestCase):
    """Canon manual, 2026-11-23: a large red "2" was redrawn in its step's small black type."""

    def paragraph(self, items):
        chars, x = [], 70.0
        for text, size in items:
            for ch in text:
                box = NS(x=x, y=100, x2=x + 0.6 * size, y2=100 + size)
                chars.append(NS(char_unicode=ch, visual_bbox=NS(box=box), box=box, pdf_style=NS(font_size=size)))
                x += 0.6 * size
        line = NS(pdf_character=chars, box=NS(x=70, y=100, x2=x, y2=116))
        return NS(pdf_paragraph_composition=[NS(pdf_line=line)], debug_id='s', vertical=False)

    def run_split(self, paragraph):
        from transformation.layout.stacked_lines import split_step_numbers
        page = NS(pdf_paragraph=[paragraph])
        count = split_step_numbers(page, lambda p, line: None, lambda p: p.debug_id + '-n')
        return count, [''.join(c.char_unicode for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character)
                       for p in page.pdf_paragraph]

    def test_a_large_step_number_becomes_its_own_paragraph(self):
        self.assertEqual(self.run_split(self.paragraph([('2', 16), (' Select the paper source.', 9)])),
                         (1, ['2', 'Select the paper source.']))

    def test_numbers_set_like_their_text_or_without_text_stay(self):
        self.assertEqual(self.run_split(self.paragraph([('2 Select the paper source.', 9)]))[0], 0)
        self.assertEqual(self.run_split(self.paragraph([('12', 16), (' 345', 9)]))[0], 0)       # no letters
        self.assertEqual(self.run_split(self.paragraph([('2024', 16), (' Annual report', 9)]))[0], 0)   # not a step number
