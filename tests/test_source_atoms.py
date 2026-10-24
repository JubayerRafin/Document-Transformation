import copy
import unittest
from types import SimpleNamespace as NS
from transformation.layout.source_atoms import is_source_atom,capture_source_atoms,preserved_source_characters


def box(x=10,y=10,x2=18,y2=19):return NS(x=x,y=y,x2=x2,y2=y2)
class SourceAtomsTests(unittest.TestCase):
    def fixture(self):
        char=NS(char_unicode='8',pdf_character_id=1,pdf_style=NS(font_size=8),box=box(),visual_bbox=NS(box=box()))
        item=NS(pdf_character=char,pdf_line=None,pdf_same_style_characters=None,pdf_formula=None)
        p=NS(debug_id='a',unicode='8',vertical=False,box=box(),pdf_paragraph_composition=[item])
        page=NS(page_number=0,pdf_paragraph=[p],cropbox=NS(box=box(0,0,100,100)))
        entries=capture_source_atoms(NS(page=[page]))
        return p,page,entries[(1,'a')]

    def test_notation_and_structured_identifiers_only(self):
        for text in ['64 × 64','PQ50','PQ{v1}','Encoder-ResNet-v3 [12]']:
            self.assertTrue(is_source_atom(text),text)
        for text in ['Accuracy of the model','Forward to section 4','A Summary','Résumé','']:
            self.assertFalse(is_source_atom(text),text)

    def test_exact_unchanged_source_can_be_copied(self):
        p,page,entry=self.fixture();result=preserved_source_characters(entry,p,page)
        self.assertEqual(result[0].pdf_style.font_size,8)
        self.assertIsNot(result[0],entry['paragraph'].pdf_paragraph_composition[0].pdf_character)

    def test_changed_text_and_missing_evidence_rejected(self):
        p,page,entry=self.fixture();p.unicode='9'
        self.assertIsNone(preserved_source_characters(entry,p,page))
        self.assertIsNone(preserved_source_characters(None,p,page))

    def test_formula_expansion_must_equal_source(self):
        p,page,entry=self.fixture();char=p.pdf_paragraph_composition[0].pdf_character
        p.unicode='{v1}';p.pdf_paragraph_composition=[NS(pdf_formula=NS(pdf_character=[char]))]
        self.assertIsNotNone(preserved_source_characters(entry,p,page))
        p.pdf_paragraph_composition[0].pdf_formula.pdf_character=[NS(char_unicode='9')]
        self.assertIsNone(preserved_source_characters(entry,p,page))

    def test_no_new_overlap(self):
        p,page,entry=self.fixture()
        other=NS(debug_id='b',box=box(40,40,50,50));page.pdf_paragraph.append(other)
        entry['neighbors']['b']=copy.deepcopy(other.box)
        self.assertIsNotNone(preserved_source_characters(entry,p,page))
        other.box=box(12,12,15,15)
        self.assertIsNone(preserved_source_characters(entry,p,page))

    def test_crop_and_unknown_glyph_geometry_rejected(self):
        p,page,entry=self.fixture();char=entry['paragraph'].pdf_paragraph_composition[0].pdf_character
        char.visual_bbox.box.x=-1
        self.assertIsNone(preserved_source_characters(entry,p,page))
        char.visual_bbox.box.x=10;char.pdf_character_id=None
        self.assertIsNone(preserved_source_characters(entry,p,page))

    def test_source_vector_graphics_not_accepted(self):
        p,page,entry=self.fixture()
        p.pdf_paragraph_composition=[NS(pdf_character=None,pdf_line=None,pdf_same_style_characters=None,
            pdf_formula=NS(pdf_curve=[object()],pdf_form=[],pdf_character=[]))]
        self.assertFalse(capture_source_atoms(NS(page=[page])))

    def test_inferred_space_does_not_require_a_printed_glyph_id(self):
        p,page,entry=self.fixture()
        space=copy.deepcopy(entry['paragraph'].pdf_paragraph_composition[0])
        space.pdf_character.char_unicode=' ';space.pdf_character.pdf_character_id=None
        entry['paragraph'].pdf_paragraph_composition.append(space)
        self.assertIsNotNone(preserved_source_characters(entry,p,page))
