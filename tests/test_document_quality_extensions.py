import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import pymupdf
from transformation.llm.ollama_native import translate_paragraph
from transformation.llm.translation_guard import validate_candidate
from transformation.llm.terminology import schema_terms,prepare
from transformation.pdf.source_grouping import join_block_continuations
from transformation.layout.line_breaks import join_split_dates,mark_date_units,atomic_tokens
from transformation.validation.output_validation import inspect_output


def paragraph(text,identifier,x=10,y=50,x2=180,y2=60):
    chars=[NS(char_unicode=c) for c in text]
    return NS(unicode=text,debug_id=identifier,layout_label='text',vertical=False,
        box=NS(x=x,y=y,x2=x2,y2=y2),pdf_paragraph_composition=[NS(pdf_same_style_characters=NS(pdf_character=chars))])


class DocumentQualityExtensions(unittest.TestCase):
    def test_entity_value_swaps_are_rejected_between_repeated_roles(self):
        source='Maya Noor (Employee ID STAFF-73); Ravi Shah (Employee ID STAFF-91)'
        with self.assertRaisesRegex(ValueError,'association'):
            validate_candidate(source,'Maya Noor (직원 번호 STAFF-91); Ravi Shah (직원 번호 STAFF-73)')
        validate_candidate(source,'Ravi Shah (직원 번호 STAFF-91); Maya Noor (직원 번호 STAFF-73)')

    def test_schema_glossary_comes_from_product_columns_only(self):
        ir={'units':[{'number':1,'regions':[dict(id='t',cells=[['Product','Amount'],['Orion Z8','USD 55']]),
            dict(id='x',cells=[['Description'],['Capacity 500 GB']])]}]}
        terms,evidence=schema_terms(ir)
        self.assertEqual(terms,{'Orion Z8':'Orion Z8'})
        terms,_=schema_terms(ir,{'Orion Z8':'오리온 Z8'})
        self.assertEqual(terms['Orion Z8'],'오리온 Z8')
        self.assertEqual(evidence[0]['region_id'],'t')

    def test_terminology_is_deterministic_and_does_not_rewrite_identifiers(self):
        with patch('transformation.llm.ollama_native._translate_paragraph',return_value=('오리온 Z8',{})) as worker:
            value,stats=translate_paragraph('unused','model','Orion Z8','Korean',terminology={'Orion Z8':'오리온 Z8'})
            self.assertEqual(value,'오리온 Z8')
            self.assertEqual(worker.call_args.args[2],'오리온 Z8')
            self.assertIn('오리온 Z8',worker.call_args.args[4])
        with self.assertRaises(ValueError):prepare('Orion Z8',{'Orion Z8':'Orion Z9'})
        self.assertEqual(prepare('AB AB12',{'AB':'BA','AB12':'AB12'})[0],'BA AB12')

    def test_glossary_term_crossing_office_runs_preserves_indexed_runs(self):
        import json
        from transformation.office.styled_translation import translate_runs
        def echo(endpoint,model,messages):
            payload=json.loads(messages[-1]['content'])
            return json.dumps({'runs':payload['runs']}),{}
        with patch('transformation.llm.ollama_native.chat',side_effect=echo):
            result=translate_runs('unused','model',['Orion ','Z8'],'Korean',terminology={'Orion Z8':'오리온 Z8'})
        self.assertEqual(result,['오리온 Z8',''])

    def test_same_block_continuation_preserves_characters_and_identity(self):
        a=paragraph('The service depends','a');b=paragraph('on network access','b',y=38,y2=48)
        page=NS(page_number=0,pdf_paragraph=[a,b])
        overrides,records=join_block_continuations(page,[((9,37,181,61),'The service depends\non network access')])
        self.assertEqual(len(page.pdf_paragraph),1)
        self.assertEqual(overrides[id(a)],'The service depends on network access')
        self.assertEqual(records[0]['source_region_ids'],['a','b'])
        self.assertEqual(len(a.pdf_paragraph_composition),2)

    def test_distinct_fields_tables_and_unproven_text_are_not_joined(self):
        for source,table in [('Field: A\nValue: B',False),('The service depends\non invented access',False),('The service depends\non network access',True)]:
            a=paragraph('The service depends','a');b=paragraph('on network access','b',y=38,y2=48)
            if table:b.layout_label='table'
            page=NS(page_number=0,pdf_paragraph=[a,b])
            self.assertFalse(join_block_continuations(page,[((9,37,181,61),source)])[0])
            self.assertEqual(len(page.pdf_paragraph),2)

    def test_rich_source_date_is_joined_before_translation(self):
        a=paragraph('Approval date: 2031-08-','a',y=100,y2=111)
        b=paragraph('19','b',y=88,y2=99)
        page=NS(page_number=0,pdf_paragraph=[a,b])
        self.assertEqual(len(join_split_dates(NS(page=[page]))),1)
        self.assertEqual(a.unicode,'Approval date: 2031-08-19')
        self.assertEqual(len(a.pdf_paragraph_composition),2)

    def test_formula_characters_and_cjk_particles_keep_dates_atomic(self):
        chars=[NS(char_unicode=c) for c in '2031-08-19']
        unit=NS(try_get_unicode=lambda:None,formular=NS(pdf_curve=[],pdf_form=[],pdf_character=chars),can_break_line_cache=None)
        self.assertTrue(mark_date_units([unit]));self.assertFalse(unit.can_break_line_cache)
        self.assertIn('2031-08-19 10:45 UTC',atomic_tokens('날짜: 2031-08-19 10:45 UTC 기록'))

    def test_intended_overlap_requires_exact_placement_evidence_and_stays_reviewable(self):
        with pymupdf.open() as pdf:
            p=pdf.new_page(width=300,height=200)
            pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,100,80),False);pix.clear_with(255)
            p.insert_image((20,20,120,100),pixmap=pix);p.insert_text((30,50),'Translated',fontsize=10)
            span=next(s for b in p.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'])
            proof=dict(page=1,text=span['text'],bbox=span['bbox'],image_bbox=[20,20,120,100],region_id='figure-a')
            result=inspect_output(pdf.tobytes(),'pdf',intended_overlays=[proof])
            self.assertEqual(result['layout_review_candidates'][0]['classification'],'intended_raster_text_placement_requires_review')
            self.assertFalse(result['acceptable'])
            proof['text']='Other text'
            self.assertEqual(inspect_output(pdf.tobytes(),'pdf',intended_overlays=[proof])['layout_review_candidates'][0]['classification'],'unverified_overlap')
