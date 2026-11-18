"""Shaped drawing for Arabic/Hebrew/Indic paragraphs. Uses stand-ins for BabelDOC's layout objects."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import pymupdf

from transformation.pdf import complex_script as cs


def style(size=11, colour='0 g', font='F1'):
    return NS(font_size=size, graphic_state=NS(passthrough_per_char_instruction=colour), font_id=font)


def paragraph(text, box=(72, 600, 300, 640), **kwargs):
    run = NS(pdf_style=style(**kwargs), unicode=text)
    composition = NS(pdf_same_style_unicode_characters=run, pdf_formula=None, pdf_line=None,
                     pdf_same_style_characters=None, pdf_character=None)
    return NS(box=NS(x=box[0], y=box[1], x2=box[2], y2=box[3]), pdf_style=style(**kwargs), vertical=False,
              pdf_paragraph_composition=[composition], unicode=text, debug_id='p' + str(len(text)))


class ComplexScriptTests(unittest.TestCase):
    def test_only_scripts_that_need_shaping_are_taken(self):
        for text in ('আমার সোনার বাংলা', 'क्रेमलिन व्यक्ति', 'مرحبا بالعالم', 'שלום'):
            self.assertTrue(cs.needs_shaping(text), text)
        for text in ('Hello world', '한국어 문장', '日本語の文', 'Привет', 'Ünïcödé 123'):
            self.assertFalse(cs.needs_shaping(text), text)

    def test_take_records_logical_text_and_removes_it_from_typesetting(self):
        bengali, english = paragraph('ড্রোনগুলোর দ্বারা', colour='0.2 0.4 0.6 rg', size=12, font='F2'), paragraph('Keep me')
        page = NS(page_number=0, pdf_paragraph=[bengali, english],
                  pdf_font=[NS(font_id='F2', name='NotoSans-Bold'), NS(font_id='F1', name='Regular')])
        records = cs.take(NS(page=[page]))
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['text'], 'ড্রোনগুলোর দ্বারা'); self.assertEqual(record['font_size'], 12.0)
        self.assertEqual(record['color'], [0.2, 0.4, 0.6]); self.assertTrue(record['bold']); self.assertFalse(record['rtl'])
        self.assertEqual(bengali.pdf_paragraph_composition, [])      # BabelDOC will not place it
        self.assertEqual(len(english.pdf_paragraph_composition), 1)  # untouched
        self.assertTrue(cs.take(NS(page=[NS(page_number=0, pdf_paragraph=[paragraph('مرحبا')], pdf_font=[])]))[0]['rtl'])

    def test_protected_literals_inside_a_paragraph_stay_in_reading_order(self):
        run = lambda t: NS(pdf_same_style_unicode_characters=NS(pdf_style=style(), unicode=t), pdf_formula=None,
                           pdf_line=None, pdf_same_style_characters=None, pdf_character=None)
        formula = NS(pdf_same_style_unicode_characters=None, pdf_line=None, pdf_same_style_characters=None, pdf_character=None,
                     pdf_formula=NS(pdf_character=[NS(char_unicode=c) for c in 'HR-2024-00417']))
        p = paragraph('x'); p.pdf_paragraph_composition = [run('নথি আইডি: '), formula, run(' অভ্যন্তরীণ')]
        self.assertEqual(cs.logical_text(p), 'নথি আইডি: HR-2024-00417 অভ্যন্তরীণ')

    def test_fill_colour_instructions(self):
        self.assertEqual(cs.colour('0 g'), [0.0, 0.0, 0.0])
        self.assertEqual(cs.colour('/CS0 cs 0.5 0.25 1 sc'), [0.5, 0.25, 1.0])
        self.assertEqual(cs.colour('0 0 0 0.5 k'), [0.5, 0.5, 0.5])
        self.assertEqual(cs.colour(''), [0.0, 0.0, 0.0])
        # The last fill operator wins; stroke colours never colour text.
        self.assertEqual(cs.colour('0.851 0.886 0.953 rg 0 g'), [0.0, 0.0, 0.0])
        self.assertEqual(cs.colour('0.122 0.306 0.475 rg 1 g'), [1.0, 1.0, 1.0])
        self.assertEqual(cs.colour('0 g 0.18 0.455 0.71 rg /GS0 gs 0 0 m 1 0 l h W n'), [0.18, 0.455, 0.71])
        self.assertEqual(cs.colour('0.2 0.2 0.2 rg 1 0 0 RG 0.5 G'), [0.2, 0.2, 0.2])

    def test_drawn_paragraphs_are_outlines_in_their_box_with_a_correct_invisible_text_layer(self):
        import os
        font = os.environ.get('TRANSFORMX_TEST_SHAPED_FONT', str(Path.home() / '.cache/babeldoc/fonts/GoNotoKurrent-Regular.ttf'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'out.mono.pdf')
            with pymupdf.open() as pdf:
                pdf.new_page(width=400, height=300); pdf.save(path)
            # PDF coordinates (origin bottom-left): the first box is near the top of the page.
            records = [{'page': 0, 'box': [20, 200, 380, 280], 'text': 'ড্রোনগুলোর দ্বারা আক্রান্ত', 'font_size': 12.0,
                        'color': [0, 0, 0], 'bold': False, 'rtl': False, 'paragraph_id': 'a'},
                       {'page': 0, 'box': [20, 20, 380, 100], 'text': 'مرحبا بالعالم', 'font_size': 12.0,
                        'color': [0, 0, 0], 'bold': False, 'rtl': True, 'paragraph_id': 'b'}]
            results = cs.draw(path, records, font if Path(font).exists() else None)
            self.assertEqual([r['status'] for r in results], ['drawn', 'drawn'])
            with pymupdf.open(path) as pdf:
                page = pdf[0]
                ink = [d['rect'] for d in page.get_drawings()]
                self.assertTrue(any(r.y1 < 110 for r in ink))        # y flipped: glyph outlines in the top box
                self.assertTrue(any(r.y0 > 190 for r in ink))
                if Path(font).exists():
                    text = page.get_text()
                    self.assertIn('ড্রোনগুলোর দ্বারা', text)          # correct characters, reading order
                    import importlib.util
                    if importlib.util.find_spec('bidi'):              # requirements-transformation.txt
                        self.assertIn('مرحبا', text)
                    self.assertEqual(text.count('ড্রোনগুলোর'), 1)     # only the invisible layer is text


    def test_the_invisible_text_of_a_tight_box_stays_inside_the_page(self):
        import os
        font = os.environ.get('TRANSFORMX_TEST_SHAPED_FONT', str(Path.home() / '.cache/babeldoc/fonts/GoNotoKurrent-Regular.ttf'))
        if not Path(font).exists():
            self.skipTest('needs a font covering Devanagari')
        text = ('तुलना के लिए, यहां समान प्रकार की जानकारी दी गई है: कर्मचारी Kim Min-jun, आईडी EMP-2024-8831, '
                'ईमेल minjun.kim@example-corp.com, API कुंजी sk_live_4f9a-REDACT_ME.')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'out.pdf')
            with pymupdf.open() as pdf:
                pdf.new_page(width=600, height=400); pdf.save(path)
            # A box two lines high: the unshaped text does not fit it at full size.
            cs.draw(path, [{'page': 0, 'box': [40, 300, 560, 326], 'text': text, 'font_size': 11.0, 'color': [0, 0, 0],
                            'bold': False, 'rtl': False, 'paragraph_id': 'tight'}], font)
            with pymupdf.open(path) as pdf:
                page = pdf[0]
                extracted = ''.join(page.get_text().split())
                for value in ('EMP-2024-8831', 'minjun.kim@example-corp.com', 'sk_live_4f9a-REDACT_ME'):
                    self.assertIn(value, extracted)
                    self.assertTrue(page.search_for(value), value)
                for block in page.get_text('dict', clip=pymupdf.Rect(-1e4, -1e4, 1e4, 1e4))['blocks']:
                    for line in block.get('lines', []):
                        self.assertTrue(page.rect.contains(pymupdf.Rect(line['bbox'])), line['bbox'])


if __name__ == '__main__':
    unittest.main()


class ShapedFitTests(unittest.TestCase):
    def test_image_text_is_fitted_shaped_at_the_largest_size_that_fits_or_refused(self):
        rect = pymupdf.Rect(0, 0, 160, 30)
        fitted = cs.fit_shaped(rect, 'जारी किया गया: 2024-03-18', max_size=12, min_size=5)
        self.assertIsNotNone(fitted)
        outlines, size = fitted
        with outlines:
            self.assertEqual(len(outlines), 1)
            self.assertTrue(outlines[0].get_drawings())               # shaped glyphs drawn as outlines
        self.assertTrue(5 <= size <= 12)
        self.assertIsNone(cs.fit_shaped(pymupdf.Rect(0, 0, 20, 4), 'تحذير: منطقة ذات وصول مقيد', 12, 8))

    def test_numbers_in_right_to_left_text_are_kept_left_to_right(self):
        marked = cs._keep_left_to_right('الهاتف 010-2345-6789 والتاريخ 2024-03-18 والرمز HR-2024-00417')
        for value in ('010-2345-6789', '2024-03-18', 'HR-2024-00417'):
            self.assertIn('\u202a' + value + '\u202c', marked)
        self.assertEqual(cs._keep_left_to_right('بريد minjun.kim@example.com'), 'بريد minjun.kim@example.com')
