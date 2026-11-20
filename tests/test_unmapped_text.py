"""Re-reading paragraphs whose characters BabelDOC could not map (ActualText clusters)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import pymupdf

from transformation.pdf import unmapped_text as ut


class JoinTests(unittest.TestCase):
    def test_pieces_of_one_indic_word_on_a_line_are_joined_without_a_space(self):
        # MuPDF reports "পদক্ষে" and "প" as two pieces with a 16 pt gap at 18 pt type.
        pieces = [(168, 57.7, 144.2, 'পরবর্তী পদক্ষে', 18), (168, 160.8, 173.6, 'প', 18)]
        self.assertEqual(ut.join_pieces(pieces), 'পরবর্তী পদক্ষেপ')

    def test_latin_words_with_a_gap_and_new_lines_keep_their_spaces(self):
        pieces = [(100, 10, 50, 'Hello', 10), (100, 54, 90, 'world', 10), (114, 10, 40, 'again', 10)]
        self.assertEqual(ut.join_pieces(pieces), 'Hello world again')

    def test_touching_pieces_are_joined(self):
        self.assertEqual(ut.join_pieces([(100, 10, 40, 'Quarter', 10), (100, 40.5, 50, 'ly', 10)]), 'Quarterly')


class AcceptTests(unittest.TestCase):
    def test_mupdf_text_holding_the_read_characters_is_accepted(self):
        babeldoc = 'সব চা(cid:12)ন পৌঁ(cid:16)(cid:17)ছা(cid:12)ক্ষেন(cid:12)র CML-2019-64178'
        self.assertTrue(ut.acceptable(babeldoc, 'সব চালান পৌঁছানোর পর CML-2019-64178'))

    def test_garbled_shaped_text_is_replaced_when_its_reliable_parts_agree(self):
        # BabelDOC also misreads Bengali glyphs as other letters and digits; only Latin words and
        # longer numbers, the script and the length are compared.
        babeldoc = 'পর পর(cid:19)(cid:9)(cid:12) করক্ষের্তী হক্ষেব এব(cid:23) পৌঁক(cid:12)ক্ষেন(cid:12) দ( ZR-2027-04861'
        self.assertTrue(ut.acceptable(babeldoc, 'পর পরীক্ষা করতে হবে এবং কোনো ক্ষতি দুই ZR-2027-04861'))
        self.assertFalse(ut.acceptable(babeldoc, 'পর পরীক্ষা করতে হবে এবং কোনো ক্ষতি দুই ZR-2027-04862'))
        self.assertFalse(ut.acceptable(babeldoc, 'पर परीक्षा करनी होगी और कोई क्षति ZR-2027-04861'))
        self.assertFalse(ut.acceptable(babeldoc, 'পর পরীক্ষা করতে হবে ZR-2027-04861 ' + 'এবং কোনো ক্ষতি দুই কর্মদিবসের ' * 8))

    def test_unreadable_or_mismatching_or_too_long_text_is_refused(self):
        babeldoc = 'Invoice (cid:3)(cid:4) total 4,200'
        self.assertFalse(ut.acceptable(babeldoc, ''))
        self.assertFalse(ut.acceptable(babeldoc, 'Invoice \ufffd total 4,200'))
        self.assertFalse(ut.acceptable(babeldoc, 'Something else entirely'))
        self.assertFalse(ut.acceptable(babeldoc, 'Invoice ab total 4,200 ' + 'and a neighbouring paragraph ' * 4))
        self.assertTrue(ut.acceptable(babeldoc, 'Invoice ab total 4,200'))


class RepairTests(unittest.TestCase):
    def test_only_paragraphs_with_unread_characters_are_reread_from_their_area(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'source.pdf')
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=400, height=300)
                page.insert_text((20, 50), 'Quarterly total 4,200', fontsize=12)
                page.insert_text((20, 200), 'Second paragraph', fontsize=12)
                pdf.save(path)
            # BabelDOC boxes are in PDF coordinates (origin bottom-left).
            garbled = NS(box=NS(x=19, y=245, x2=200, y2=262), unicode='(cid:5)uarterly total 4,200', debug_id='a')
            clean = NS(box=NS(x=19, y=95, x2=200, y2=112), unicode='Second paragraph', debug_id='b')
            ir_page = NS(page_number=0, pdf_paragraph=[garbled, clean])
            with pymupdf.open(path) as pdf:
                overrides, records = ut.repair_page(ir_page, pdf[0])
            self.assertEqual(overrides, {id(garbled): 'Quarterly total 4,200'})
            self.assertEqual(garbled.unicode, 'Quarterly total 4,200')
            self.assertEqual(clean.unicode, 'Second paragraph')
            self.assertEqual([r['status'] for r in records], ['reread'])

    def test_right_to_left_paragraphs_are_reread_in_reading_order(self):
        font = Path.home() / '.cache/babeldoc/fonts/GoNotoKurrent-Regular.ttf'
        if not font.exists():
            self.skipTest('needs a font covering Arabic')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'source.pdf')
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=400, height=300)
                page.insert_htmlbox(pymupdf.Rect(20, 40, 380, 70), '<div dir="rtl">يجب إكمال التدريب</div>',
                                    css=f'@font-face {{font-family: a; src: url({font.as_posix()});}} * {{font-family: a; font-size: 12pt;}}')
                pdf.save(path)
            with pymupdf.open(path) as pdf:
                read = ut.read_area(pdf[0], pymupdf.Rect(19, 39, 381, 71))
                # BabelDOC's reader returns the glyphs in drawing order: the text reversed.
                paragraph = NS(box=NS(x=19, y=229, x2=381, y2=261), unicode=read[::-1], debug_id='ar')
                overrides, records = ut.repair_page(NS(page_number=0, pdf_paragraph=[paragraph]), pdf[0])
            self.assertEqual(records[0]['status'], 'reread')
            self.assertEqual(paragraph.unicode, read)
            self.assertFalse(ut._PRESENTATION_FORMS.search(read))     # shaped glyph variants folded

    def test_presentation_forms_fold_to_base_letters(self):
        self.assertEqual(ut.fold('ﻳﺠﺐ'), 'يجب')
        self.assertEqual(ut.fold('m² ﬁle'), 'm² ﬁle')                 # other compatibility forms are kept

if __name__ == '__main__':
    unittest.main()


class RightToLeftOrderTests(unittest.TestCase):
    def test_a_right_to_left_line_reads_right_to_left_with_numbers_left_to_right(self):
        from transformation.pdf.unmapped_text import visual_to_logical
        # "بين 5 و30 درجة." drawn left to right: ". ة ج ر د   3 0 و   5   ن ي ب"
        drawn = list('.ةجرد') + [' '] + list('30و') + [' '] + ['5'] + [' '] + list('نيب')
        clusters = [(float(x), c) for x, c in enumerate(drawn)]
        self.assertEqual(visual_to_logical(clusters), 'بين 5 و30 درجة.')

    def test_amounts_percentages_and_latin_runs_stay_in_their_own_order(self):
        from transformation.pdf.unmapped_text import visual_to_logical
        def logical(drawn):
            return visual_to_logical([(float(x), c) for x, c in enumerate(drawn)])
        self.assertEqual(logical(list('%12') + [' '] + list('ةبسنب')), 'بنسبة 12%')
        self.assertEqual(logical(list('USD 1,250.50') + [' '] + list('غلبم')), 'مبلغ USD 1,250.50')

    def test_a_mark_written_before_its_zero_width_letter_is_put_after_it(self):
        from transformation.pdf.unmapped_text import _clusters
        line = {'spans': [{'chars': [
            {'c': 'ع', 'bbox': (67.2, 0, 73.3, 9)}, {'c': 'ّ', 'bbox': (61.9, 0, 67.2, 9)},
            {'c': 'و', 'bbox': (62.9, 0, 62.9, 9)}, {'c': 'ض', 'bbox': (51.4, 0, 61.9, 9)}]}]}
        self.assertEqual([t for _, t in _clusters(line)], ['ع', 'وّ', 'ض'])
