from collections import Counter
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import pymupdf
from babeldoc.format.pdf.document_il.il_version_1 import PdfStyle
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting,TypesettingUnit
from transformation.layout.table_fitting import fitting_scales,install_table_fitting,characters_fit


class AdaptiveSlotTests(unittest.TestCase):
    def test_only_failed_verified_ocr_slot_can_remove_inferred_indent(self):
        font=pymupdf.Font('helv')
        class Harness(Typesetting):
            def __init__(self):self.font_mapper=NS(base_font=font)
            def create_typesetting_units(self,paragraph,fonts):
                return [TypesettingUnit(unicode=char,font=font,font_size=17,
                    style=PdfStyle(font_size=17,font_id='base'),xobj_id=0) for char in paragraph.unicode]
            def _update_paragraph_render_order(self,paragraph):pass
        install_table_fitting(Harness)
        def instance(locked,width):
            h=Harness();h._transformx_table_slots={(1,'narrow'):{'bbox':[10,10,10+width,36],'min_font_size':8,'source_locked_ocr':locked}}
            h._transformx_table_fit_results=[];return h
        def paragraph():return NS(debug_id='narrow',unicode='B',vertical=False,first_line_indent=True,pdf_paragraph_composition=['original'])
        ordinary=instance(False,17);p=paragraph()
        with self.assertRaisesRegex(RuntimeError,'cannot fit'):ordinary.render_paragraph(p,NS(page_number=0),{})
        self.assertTrue(p.first_line_indent);self.assertEqual(p.pdf_paragraph_composition,['original'])
        locked=instance(True,17);p=paragraph();locked.render_paragraph(p,NS(page_number=0),{})
        record=locked._transformx_table_fit_results[0]
        self.assertTrue(record['removed_inferred_indent']);self.assertFalse(p.first_line_indent)
        self.assertTrue(characters_fit([c.pdf_character for c in p.pdf_paragraph_composition],[10,10,27,36],8,True))
        wide=instance(True,150);p=paragraph();wide.render_paragraph(p,NS(page_number=0),{})
        self.assertFalse(wide._transformx_table_fit_results[0]['removed_inferred_indent'])
        self.assertTrue(p.first_line_indent);self.assertEqual(wide._transformx_table_fit_results[0]['attempts'],1)

    def test_scale_floor_uses_smallest_actual_font(self):
        scales=fitting_scales([NS(font_size=20),NS(font_size=12)],8)
        self.assertAlmostEqual(scales[-1],8/12)
        self.assertTrue(all(scale*12>=8-1e-8 for scale in scales))
        self.assertIn(.7,scales)

    def test_no_enlargement_or_shrinking_below_font_floor(self):
        self.assertEqual(fitting_scales([NS(font_size=6)],8),(1.0,))
        self.assertEqual(fitting_scales([NS(font_size=8)],8),(1.0,))

    def test_real_glyphs_fit_below_old_cutoff_without_expanding_slot(self):
        font=pymupdf.Font('korea')
        class Harness(Typesetting):
            def __init__(self):self.font_mapper=NS(base_font=font)
            def create_typesetting_units(self,paragraph,fonts):
                return [TypesettingUnit(unicode=char,font=font,font_size=14,
                    style=PdfStyle(font_size=14,font_id='base'),xobj_id=0) for char in paragraph.unicode]
            def _update_paragraph_render_order(self,paragraph):pass
        install_table_fitting(Harness)
        def fitter():
            instance=Harness()
            instance._transformx_table_slots={(1,'test'):{'bbox':[10,10,245,30],'min_font_size':8}}
            instance._transformx_table_fit_results=[]
            return instance
        def paragraph():return NS(debug_id='test',unicode='가나다라마바사 아자차카타파하 가나다라마 바사아자차 카타파하.',vertical=False,first_line_indent=False,pdf_paragraph_composition=['original'])
        old=fitter();failed=paragraph()
        with patch('transformation.layout.table_fitting.fitting_scales',return_value=(1,.95,.9,.85,.8,.75)):
            with self.assertRaisesRegex(RuntimeError,'cannot fit'):old.render_paragraph(failed,NS(page_number=0),{})
        self.assertEqual(failed.pdf_paragraph_composition,['original'])
        improved=fitter();result=paragraph();improved.render_paragraph(result,NS(page_number=0),{})
        characters=[composition.pdf_character for composition in result.pdf_paragraph_composition]
        self.assertLess(result.scale,.75)
        self.assertTrue(characters_fit(characters,[10,10,245,30],8,check_visual=True))
        self.assertEqual(Counter(c.char_unicode for c in characters if not c.char_unicode.isspace()),Counter(c for c in result.unicode if not c.isspace()))
        self.assertEqual(improved._transformx_table_fit_results[0]['slot'],[10,10,245,30])
