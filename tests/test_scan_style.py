import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pymupdf

from transformation.pdf import scan_style
from transformation.pdf.scan_lines import join_line_pieces

HAS_CV2 = importlib.util.find_spec('cv2') is not None


def scanned(lines, dpi=150, size=(560, 60)):
    """A one-page PDF whose only content is an image of the given (text, fontname, size, colour, y)."""
    with pymupdf.open() as drawn:
        page = drawn.new_page(width=size[0], height=size[1])
        for text, fontname, fontsize, colour, y in lines:
            page.insert_text((10, y), text, fontname=fontname, fontsize=fontsize, color=colour)
        png = page.get_pixmap(dpi=dpi).tobytes('png')
    scan = pymupdf.open()
    target = scan.new_page(width=size[0], height=size[1])
    target.insert_image(target.rect, stream=png)
    return scan


class LatinFontTests(unittest.TestCase):
    def test_latin_text_gets_helvetica_metrics_and_other_scripts_do_not(self):
        self.assertEqual(scan_style.latin_font('Japan · March–April')[0], 'helv')
        self.assertEqual(scan_style.latin_font('Pastéis de Nata', bold=True)[0], 'hebo')
        self.assertEqual(scan_style.latin_font('Safe travels — explore', italic=True)[0], 'heit')
        self.assertIsNone(scan_style.latin_font('여행 가이드'))
        self.assertIsNone(scan_style.latin_font('   '))

    def test_embedded_fonts_keep_characters_outside_latin_1(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname=scan_style.EMBEDDED['helv'], fontbuffer=scan_style.font('helv').buffer)
            page.insert_text((20, 40), 'App Center — Device “Console”', fontname=scan_style.EMBEDDED['helv'])
            self.assertIn('App Center — Device “Console”', page.get_text())


class InkColourTests(unittest.TestCase):
    def test_colours(self):
        white, yellow = (255, 255, 255), (255, 235, 60)
        self.assertEqual(scan_style.ink_color((20, 20, 24), white), (0, 0, 0))          # black text
        self.assertEqual(scan_style.ink_color((88, 74, 0), yellow), (0, 0, 0))          # black blurred on yellow
        self.assertEqual(scan_style.ink_color((250, 250, 248), (31, 78, 120)), (1, 1, 1))  # white on navy
        navy = scan_style.ink_color((31, 78, 120), white)
        self.assertAlmostEqual(navy[2], 120 / 255, places=2)                              # navy heading kept
        orange = scan_style.ink_color((225, 100, 66), white)
        self.assertAlmostEqual(orange[0], 225 / 255, places=2)                            # orange label kept
        grey = scan_style.ink_color((131, 131, 131), white)
        self.assertAlmostEqual(grey[0], 131 / 255, places=2)                              # grey note kept


@unittest.skipUnless(HAS_CV2, 'OpenCV measures letter heights')
class MeasuredStyleTests(unittest.TestCase):
    def styles(self, lines, dpi=150, height=None):
        height = height or 20 + 34 * len(lines)
        with scanned(lines, dpi, (560, height)) as scan:
            page = scan[0]
            items = []
            for text, fontname, fontsize, colour, y in lines:
                width = pymupdf.Font(fontname).text_length(text, fontsize=fontsize)
                items.append(((8, y - fontsize, 12 + width, y + fontsize * 0.3), (255, 255, 255), text))
            return scan_style.page_styles(page, items)

    def test_italic_line_is_found_and_upright_lines_are_not(self):
        lines = [('Five short slides built to test layout', 'heit', 16, (0, 0, 0), 30),
                 ('Learn a few phrases before arrival', 'helv', 16, (0, 0, 0), 64),
                 ('Greetings and Gestures', 'hebo', 16, (0, 0, 0), 98)]
        found = self.styles(lines)
        self.assertEqual([s['italic'] for s in found], [True, False, False])

    def test_one_bold_heading_among_regular_lines(self):
        lines = [('Quarterly Review Summary', 'hebo', 22, (0, 0, 0), 30)] + \
                [(text, 'helv', 22, (0, 0, 0), 64 + 34 * k) for k, text in enumerate(
                    ['Total amount due this month', 'Learn a few phrases first', 'Carry small cash for tips',
                     'Dress with awareness inside', 'Ask before photographing'])]
        found = self.styles(lines, dpi=300)
        self.assertEqual([s['bold'] for s in found], [True] + [False] * 5)

    def test_a_page_of_only_bold_lines_stays_regular(self):
        lines = [(text, 'hebo', 22, (0, 0, 0), 30 + 34 * k) for k, text in enumerate(
            ['Quarterly Review Summary', 'Total amount due', 'Greetings and Gestures', 'Day of the Dead', 'Midnight Sun'])]
        self.assertFalse(any(s['bold'] for s in self.styles(lines, dpi=300)))

    def test_coloured_text_keeps_its_colour(self):
        found = self.styles([('BEFORE YOU GO', 'helv', 14, (0.88, 0.39, 0.26), 30)])
        r, g, b = found[0]['ink']
        self.assertGreater(r, 0.7); self.assertLess(b, 0.45)


class LinePieceTests(unittest.TestCase):
    def page(self, width=600, height=80, dot_at=None, rule_at=None):
        pixels = np.full((height, width, 3), 255, np.uint8)
        if dot_at is not None:
            pixels[38:42, dot_at:dot_at + 4] = 40
        rules = np.zeros((height, width), bool)
        if rule_at is not None:
            rules[:, rule_at] = True
        return pixels, rules

    def region(self, key, x0, x1, text, y0=30, y1=48):
        return {'id': key, 'bbox': [x0, y0, x1, y1], 'text': text, 'metadata': {}}

    def join(self, regions, **page):
        mapping = {r['id']: {'source_text': r['text'], 'text': r['text']} for r in regions}
        pixels, rules = self.page(**page)
        return join_line_pieces(regions, mapping, pixels, rules, lambda r: not r.get('uncertain'))

    def test_spaced_capitals_become_one_line(self):
        regions = [self.region('a', 20, 90, 'BEFORE'), self.region('b', 100, 135, 'YOU'), self.region('c', 141, 170, 'GO')]
        joined, mapping = self.join(regions)
        self.assertEqual([r['text'] for r in joined], ['BEFORE YOU GO'])
        self.assertEqual(mapping['a']['text'], 'BEFORE YOU GO')
        self.assertEqual(joined[0]['bbox'], [20, 30, 170, 48])
        self.assertEqual(joined[0]['metadata']['joined_pieces'], ['a', 'b', 'c'])

    def test_table_cells_stay_apart(self):
        regions = [self.region('a', 20, 110, 'Kyoto, Japan'), self.region('b', 190, 300, 'Spring (Mar-May)')]
        self.assertEqual(len(self.join(regions)[0]), 2)

    def test_an_unread_symbol_between_pieces_keeps_them_apart(self):
        regions = [self.region('a', 20, 60, '2026'), self.region('b', 80, 180, 'Global Edition')]
        self.assertEqual(len(self.join(regions, dot_at=68)[0]), 2)

    def test_a_cell_border_keeps_pieces_apart(self):
        regions = [self.region('a', 20, 90, 'Item'), self.region('b', 100, 160, 'SKU')]
        self.assertEqual(len(self.join(regions, rule_at=95)[0]), 2)

    def test_pieces_on_different_lines_or_uncertain_stay_apart(self):
        lower = self.region('b', 100, 160, 'Kindly', y0=52, y1=70)
        self.assertEqual(len(self.join([self.region('a', 20, 90, 'Travel'), lower])[0]), 2)
        doubtful = dict(self.region('b', 100, 140, 'YOU'), uncertain=True)
        self.assertEqual(len(self.join([self.region('a', 20, 90, 'BEFORE'), doubtful])[0]), 2)


class WrappedLineTests(unittest.TestCase):
    def join(self, lines, inks=None, rule_rows=()):
        from transformation.pdf.scan_lines import join_wrapped_lines
        regions = [{'id': f'r{k}', 'bbox': box, 'text': text, 'metadata': {}} for k, (text, box) in enumerate(lines)]
        mapping = {r['id']: {'source_text': r['text'], 'text': r['text']} for r in regions}
        rules = np.zeros((200, 600), bool)
        for row in rule_rows:
            rules[row, :] = True
        styles = [{'ink': (inks or {}).get(k, (0, 0, 0))} for k in range(len(regions))]
        return join_wrapped_lines(regions, mapping, rules, lambda r: True, styles)

    def test_a_sentence_wrapped_onto_a_lower_case_line_is_one_paragraph(self):
        joined, mapping, styles = self.join([('Picnics beneath blooming sakura trees in parks and', [380, 237, 696, 254]),
                                             ('along rivers.', [383, 254, 457, 269])])
        self.assertEqual([r['text'] for r in joined], ['Picnics beneath blooming sakura trees in parks and along rivers.'])
        self.assertEqual(mapping['r0']['text'], 'Picnics beneath blooming sakura trees in parks and\nalong rivers.')
        self.assertEqual([l['id'] for l in joined[0]['metadata']['wrapped_lines']], ['r0', 'r1'])
        self.assertEqual(len(styles), 1)

    def test_a_line_ending_in_a_joining_sign_continues(self):
        joined, _, _ = self.join([("A Traveler's Guide to Cultures, Customs &", [66, 285, 492, 313]),
                                  ('Cuisine', [65, 314, 143, 338])])
        self.assertEqual(len(joined), 1)

    def test_chinese_and_japanese_lines_that_do_not_end_a_sentence_continue_without_a_space(self):
        joined, _, _ = self.join([('所有货物到达后必须立即检查，如有损坏须在两个工作日', [61, 141, 288, 150]),
                                  ('内报告。员工应在财政年度结束前完成年度安全培训。', [62, 152, 275, 162])])
        self.assertEqual([r['text'] for r in joined], ['所有货物到达后必须立即检查，如有损坏须在两个工作日内报告。员工应在财政年度结束前完成年度安全培训。'])
        apart = [[('従業員は会計年度末までに年次研修を修了する必要があります。', [61, 141, 288, 150]),
                  ('契約内容の変更は書面で行ってください。', [62, 152, 275, 162])],             # sentence ends
                 [('1.1 すべての貨物は到着時に検査してください、そして', [61, 141, 288, 150]),
                  ('2.1 今四半期の売上は増加しました。', [62, 152, 275, 162])],                 # new item
                 [('財務状況', [61, 141, 106, 150]), ('今後の予定について説明します。', [62, 152, 275, 162])]]  # heading
        for lines in apart:
            self.assertEqual(len(self.join(lines)[0]), 2, lines[0][0])

    def test_separate_lines_stay_apart(self):
        cases = [
            [('Learn a few phrases before arrival.', [91, 309, 488, 330]), ('carry small cash', [91, 331, 415, 350])],  # sentence ends
            [('Title: Software Engineer II', [125, 310, 283, 324]), ('Dept: Enterprise Solutions', [126, 326, 285, 340])],  # fields
            [('Travel Smart, Travel', [62, 205, 407, 245]), ('Kindly', [59, 247, 176, 289])],  # no wrap evidence
            [('Kyoto, Japan and', [40, 100, 140, 112]), ('lisbon', [40, 140, 100, 152])],  # too far apart
            [('Kyoto, Japan and', [40, 100, 140, 112]), ('lisbon', [300, 113, 360, 125])],  # not aligned
        ]
        for lines in cases:
            self.assertEqual(len(self.join(lines)[0]), 2, lines[0][0])
        # different colour, or a ruled line between them
        lines = [('Picnics beneath trees in parks and', [380, 37, 696, 54]), ('along rivers.', [383, 55, 457, 69])]
        self.assertEqual(len(self.join(lines, inks={1: (0.9, 0.4, 0.3)})[0]), 2)
        self.assertEqual(len(self.join(lines, rule_rows=(54,))[0]), 2)


class RegroupTests(unittest.TestCase):
    def test_lines_of_a_joined_translation_are_drawn_together_and_others_stay(self):
        from transformation.pdf.scan_lines import regroup_for_translations
        regions = [{'id': 'a', 'bbox': [47, 51, 116, 55], 'text': 'security@example-corp.com'},
                   {'id': 'b', 'bbox': [47, 42, 89, 47], 'text': 'authorized only.'},
                   {'id': 'c', 'bbox': [47, 33, 106, 39], 'text': 'Badge EMP-2024-8831'},
                   {'id': 'd', 'bbox': [47, 25, 107, 30], 'text': 'Restricted access area.'}]
        mapping = {'a': {'source_text': 'security@example-corp.com', 'text': 'security@example-corp.com'},
                   'c': {'source_text': 'Badge EMP-2024-8831 authorized only.', 'text': 'Abzeichen EMP-2024-8831, nur für autorisierte Personen.'},
                   'd': {'source_text': 'Restricted access area.', 'text': 'Bereich mit eingeschränktem Zugang.'}}
        out = regroup_for_translations(regions, mapping)
        self.assertEqual([r['id'] for r in out], ['a', 'c', 'd'])
        joined = out[1]
        self.assertEqual(joined['text'], 'Badge EMP-2024-8831 authorized only.')
        self.assertEqual(joined['bbox'], [47, 33, 106, 47])
        self.assertEqual(joined['metadata']['regrouped_lines'], ['c', 'b'])

    def test_nothing_changes_without_an_exact_text_match(self):
        from transformation.pdf.scan_lines import regroup_for_translations
        regions = [{'id': 'c', 'bbox': [47, 33, 106, 39], 'text': 'Badge EMP-2024-8831'},
                   {'id': 'b', 'bbox': [47, 42, 89, 47], 'text': 'authorised only.'}]
        mapping = {'c': {'source_text': 'Badge EMP-2024-8831 authorized only.', 'text': 'x'}}
        self.assertEqual(regroup_for_translations(regions, mapping), regions)


class OverlayLineTests(unittest.TestCase):
    def test_only_large_lines_count_as_overlay_text(self):
        from transformation.pdf.scan_reconstruction import _overlay_line
        self.assertTrue(_overlay_line({'bbox': [37, 144, 631, 187], 'metadata': {}}))            # slide heading
        self.assertFalse(_overlay_line({'bbox': [52, 14, 80, 22], 'metadata': {}}))              # price sticker in a product photo
        joined = {'bbox': [37, 120, 360, 136], 'metadata': {'joined_boxes': [[37, 121, 251, 135], [260, 120, 299, 136]]}}
        self.assertFalse(_overlay_line(joined))                                                  # small label


@unittest.skipUnless(HAS_CV2, 'OpenCV measures letter heights')
class NativeRebuildTests(unittest.TestCase):
    def test_spaced_orange_label_is_rebuilt_as_one_orange_helvetica_line(self):
        from transformation.pdf.scan_reconstruction import reconstruct
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with scanned([('B E F O R E    Y O U    G O', 'helv', 14, (0.88, 0.39, 0.26), 34)], dpi=200) as scan:
                scan.save(run / 'source.pdf')
            font = pymupdf.Font('helv')
            x, pieces = 10, []
            for word in ('B E F O R E', 'Y O U', 'G O'):
                width = font.text_length(word, fontsize=14)
                pieces.append((word, [x - 1, 22, x + width + 1, 38]))
                x += width + font.text_length('    ', fontsize=14)
            regions = [{'id': f'r{k}', 'text': w.replace(' ', ''), 'bbox': b, 'metadata': {}} for k, (w, b) in enumerate(pieces)]
            (run / 'ocr-document.json').write_text(json.dumps({'pages': [{'regions': regions}]}), encoding='utf-8')
            (run / 'region-translations.json').write_text(json.dumps(
                {r['id']: {'source_text': r['text'], 'text': r['text']} for r in regions}), encoding='utf-8')
            (run / 'output').mkdir()
            output, report = reconstruct(run, run / 'source.pdf', force_native=True)
            with pymupdf.open(output) as rebuilt:
                spans = [s for b in rebuilt[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
            self.assertEqual([s['text'] for s in spans], ['BEFORE YOU GO'])
            self.assertIn('NimbusSans', spans[0]['font'])
            red, blue = spans[0]['color'] >> 16, spans[0]['color'] & 255
            self.assertGreater(red, 180); self.assertLess(blue, 120)
            self.assertEqual(report['replaced'], 1)


if __name__ == '__main__':
    unittest.main()
