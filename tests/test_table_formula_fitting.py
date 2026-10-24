import unittest
from types import SimpleNamespace as NS

from babeldoc.format.pdf.document_il.il_version_1 import Box, PdfCharacter, PdfFormula, PdfStyle
from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
from transformation.layout.table_fitting import install_table_fitting


class FormulaFittingTests(unittest.TestCase):
    def test_unchanged_source_atom_is_retained_only_after_bounded_layout_fails(self):
        from transformation.layout.source_atoms import capture_source_atoms
        from babeldoc.format.pdf.document_il.il_version_1 import PdfParagraphComposition
        char=PdfCharacter(char_unicode='8',box=Box(x=10,y=10,x2=15,y2=18),pdf_style=PdfStyle(font_size=8),pdf_character_id=1)
        char.visual_bbox=NS(box=char.box)
        p=NS(debug_id='atom',unicode='8',vertical=False,box=char.box,pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=char)])
        page=NS(page_number=0,pdf_paragraph=[p],cropbox=NS(box=Box(x=0,y=0,x2=100,y2=100)))
        class Harness:
            def render_paragraph(self,*args):raise AssertionError('No expanding fallback')
            def create_typesetting_units(self,*args):return [TypesettingUnit(char=char)]
            def _layout_typesetting_units(self,*args,**kwargs):return [],False
            def _update_paragraph_render_order(self,*args):pass
        install_table_fitting(Harness)
        fitter=Harness();fitter._transformx_source_atoms=capture_source_atoms(NS(page=[page]))
        fitter._transformx_table_slots={(1,'atom'):{'bbox':[10,10,11,11]}}
        fitter._transformx_table_fit_results=[]
        fitter.render_paragraph(p,page,{})
        self.assertEqual(fitter._transformx_table_fit_results[0]['status'],'preserved_source_atomic_geometry')
        self.assertEqual(p.pdf_paragraph_composition[0].pdf_character.box.x2,15)
        self.assertEqual(p.pdf_paragraph_composition[0].pdf_character.pdf_style.font_size,8)

    def test_formula_only_slot_seeds_layout_font_without_changing_source_glyphs(self):
        char=PdfCharacter(char_unicode='8',box=Box(x=0,y=0,x2=5,y2=9),pdf_style=PdfStyle(font_size=9))
        char.visual_bbox=NS(box=char.box)
        formula=PdfFormula(box=char.box,pdf_character=[char],x_offset=0,y_offset=0,x_advance=0)
        unit=TypesettingUnit(formular=formula)
        class Harness:
            def render_paragraph(self,*args):raise AssertionError('No expanding fallback')
            def create_typesetting_units(self,*args):return [unit]
            def _layout_typesetting_units(self,units,box,scale,*args,**kwargs):
                self.asserted_font=units[0].font_size
                return [units[0].relocate(11,11,scale)],True
            def _update_paragraph_render_order(self,*args):pass
        install_table_fitting(Harness)
        fitter=Harness();fitter._transformx_table_slots={(1,'number'):{'bbox':[10,10,30,30]}}
        fitter._transformx_table_fit_results=[]
        paragraph=NS(debug_id='number',unicode='8',vertical=False)
        fitter.render_paragraph(paragraph,NS(page_number=0),{})
        self.assertEqual(fitter.asserted_font,9)
        self.assertEqual(paragraph.pdf_paragraph_composition[0].pdf_character.pdf_style.font_size,9)
        self.assertTrue(fitter._transformx_table_fit_results[0]['formula_layout_font_seeded_from_source'])

    def corner_fit(self, *, corner=True, cid=41, shrink=False, body_size=10):
        body=PdfCharacter(char_unicode='q',box=Box(x=0,y=0,x2=5,y2=10),pdf_style=PdfStyle(font_size=body_size),pdf_character_id=40)
        script=PdfCharacter(char_unicode='z',box=Box(x=0,y=0,x2=3,y2=6),pdf_style=PdfStyle(font_size=6),pdf_character_id=cid)
        for char in [body,script]:char.visual_bbox=NS(box=char.box)
        formula=PdfFormula(box=script.box,pdf_character=[script],x_offset=0,y_offset=0,x_advance=0,is_corner_mark=corner)
        units=[TypesettingUnit(char=body),TypesettingUnit(formular=formula)]
        class Harness:
            def render_paragraph(self,*args):raise AssertionError('No expanding fallback')
            def create_typesetting_units(self,*args):return units
            def _layout_typesetting_units(self,units,box,scale,*args,**kwargs):
                scale=.9 if shrink else scale
                return [units[0].relocate(11,14,scale),units[1].relocate(17,11,scale)],True
            def _update_paragraph_render_order(self,*args):pass
        install_table_fitting(Harness)
        fitter=Harness();fitter._transformx_table_slots={(1,'corner'):{'bbox':[10,10,30,30]}}
        fitter._transformx_table_fit_results=[]
        paragraph=NS(debug_id='corner',unicode='q{v1}',vertical=False,pdf_paragraph_composition=['original'])
        fitter.render_paragraph(paragraph,NS(page_number=0),{})
        return paragraph,fitter._transformx_table_fit_results[0]

    def test_original_small_source_subscript_keeps_its_source_size(self):
        paragraph,record=self.corner_fit()
        self.assertEqual(record['preserved_source_corner_font_sizes'],{41:6})
        self.assertEqual(record['minimum_font_size'],7)
        self.assertEqual(paragraph.pdf_paragraph_composition[1].pdf_character.pdf_style.font_size,6)

    def test_source_subscript_cannot_be_shrunk_further(self):
        with self.assertRaisesRegex(RuntimeError,'cannot fit'):self.corner_fit(shrink=True)

    def test_tiny_body_text_or_unverified_formula_is_not_exempt(self):
        for options in [{'corner':False},{'cid':None},{'body_size':6}]:
            with self.subTest(options=options),self.assertRaisesRegex(RuntimeError,'cannot fit'):
                self.corner_fit(**options)

    def test_single_block_uses_only_proven_cell_padding_without_lowering_font_limit(self):
        char=PdfCharacter(char_unicode='보',box=Box(x=0,y=0,x2=7,y2=7),pdf_style=PdfStyle(font_size=7.5))
        char.visual_bbox=NS(box=char.box)
        formula=PdfFormula(box=char.box,pdf_character=[char],x_offset=0,y_offset=0,x_advance=0)
        class Harness:
            def render_paragraph(self,*args):raise AssertionError('No expanding fallback')
            def create_typesetting_units(self,*args):return [TypesettingUnit(formular=formula)]
            def _layout_typesetting_units(self,units,box,scale,spacing,*args,**kwargs):
                return [units[0].relocate(11,11,scale)],box.y2-box.y>9
            def _update_paragraph_render_order(self,*args):pass
        install_table_fitting(Harness)
        fitter=Harness();fitter._transformx_table_slots={(1,'h'):{'bbox':[10,10,72,18.8],'single_block_cell_bbox':[9,9,73,19.8]}}
        fitter._transformx_table_fit_results=[]
        paragraph=NS(debug_id='h',unicode='보상',vertical=False,pdf_style=PdfStyle(font_size=8.4))
        fitter.render_paragraph(paragraph,NS(page_number=0),{})
        self.assertEqual(paragraph.scale,1.0)
        self.assertEqual(paragraph.box.y2,19.55)
        self.assertTrue(fitter._transformx_table_fit_results[0]['reused_cell_padding'])

    def fit(self, *, graphics=False, outside=False, size=9, heading=False):
        char = PdfCharacter(char_unicode='+', box=Box(x=0, y=0, x2=5, y2=9),
                            pdf_style=PdfStyle(font_size=size))
        char.visual_bbox = NS(box=char.box)
        formula = PdfFormula(box=char.box, pdf_character=[char],
                             x_offset=0, y_offset=0, x_advance=0)
        if graphics:
            formula.pdf_curve.append(NS())

        class Harness:
            def render_paragraph(self, *args):
                raise AssertionError('Assigned slot must use bounded rendering')

            def create_typesetting_units(self, *args):
                return [TypesettingUnit(formular=formula)]

            def _layout_typesetting_units(self, units, box, scale, *args, **kwargs):
                return [units[0].relocate(50 if outside else 11, 11, scale)], True

            def _update_paragraph_render_order(self, paragraph):
                pass

        install_table_fitting(Harness)
        fitter = Harness()
        fitter._transformx_table_slots = {(1, 'grade'): {'bbox': [10, 10, 30, 30]}}
        fitter._transformx_table_fit_results = []
        if heading:
            fitter._transformx_heading_slots = fitter._transformx_table_slots
            fitter._transformx_table_slots = {}
            fitter._transformx_heading_fit_results = []
        paragraph = NS(debug_id='grade', unicode='B{v1}', vertical=False,
                       pdf_paragraph_composition=['original'])
        self.paragraph = paragraph
        fitter.render_paragraph(paragraph, NS(page_number=0), {})
        return paragraph

    def test_formula_symbol_is_preserved(self):
        paragraph = self.fit()
        self.assertEqual(paragraph.pdf_paragraph_composition[0].pdf_character.char_unicode, '+')

    def test_graphics_are_rejected_without_mutation(self):
        with self.assertRaisesRegex(RuntimeError, 'Non-text formulas'):
            self.fit(graphics=True)
        self.assertEqual(self.paragraph.pdf_paragraph_composition, ['original'])

    def test_overflow_rejected_without_mutation(self):
        with self.assertRaisesRegex(RuntimeError, 'cannot fit'):
            self.fit(outside=True)
        self.assertEqual(self.paragraph.pdf_paragraph_composition, ['original'])

    def test_tiny_formula_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'cannot fit'):
            self.fit(size=6)

    def test_heading_uses_bounded_rendering(self):
        self.assertEqual(self.fit(heading=True,size=12).scale,1)

    def test_heading_overflow_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Heading fitting requires review'):
            self.fit(heading=True,outside=True,size=12)

    def test_heading_does_not_shrink_to_table_font_limit(self):
        with self.assertRaisesRegex(RuntimeError, 'Heading fitting requires review'):
            self.fit(heading=True,size=9)
