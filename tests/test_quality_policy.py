import json
import unittest
from unittest.mock import patch
import numpy as np
import pymupdf
from transformation.llm.translation_guard import validate_candidate
from transformation.llm.role_bindings import explicit_bindings,validate_bindings
from transformation.layout.ocr_fitting import source_fit_policy
from transformation.pdf.scan_reconstruction import fit_text,validate_placements
from transformation.validation.output_validation import inspect_output
from tools.ocr.recognize_paddle_boxes import bounded_recognition


class QualityPolicyTests(unittest.TestCase):
    def test_swapped_roles_rejected_without_rewriting_or_source_specific_names(self):
        source='Record for Maya Noor-Rahman (Employee ID STAFF-3982).'
        good='Maya Noor-Rahman (직원 번호 STAFF-3982)의 기록입니다.'
        bad='직원 ID STAFF-3982 (직원 번호 Maya Noor-Rahman)의 기록입니다.'
        validate_candidate(source,good)
        with self.assertRaisesRegex(ValueError,'relationship'):validate_candidate(source,bad)
        with self.assertRaisesRegex(ValueError,'relationship'):
            validate_candidate(source,'직원번호Maya Noor-Rahman, 직원 ID STAFF-3982')
        validate_candidate('Document ID: DOC-72; Employee ID: EMP-38',
                           '직원 번호: EMP-38; 문서 번호: DOC-72')
        with self.assertRaisesRegex(ValueError,'relationship'):
            validate_candidate('Document ID: DOC-72; Employee ID: EMP-38','문서 번호: EMP-38; 직원 번호: DOC-72')

    def test_unknown_labels_and_natural_postfix_order_are_not_certified(self):
        report=validate_bindings('Employee ID EMP-3982','EMP-3982에 대한 기록입니다.')
        self.assertEqual(report['status'],'not_established')
        self.assertEqual(explicit_bindings('An ordinary sentence without labels.'),[])
        validate_candidate('Employee ID EMP-3982','EMP-3982가 직원 번호입니다.')

    def test_adapter_retries_relationship_error_and_reports_failed_response(self):
        from transformation.llm.translategemma import translate
        source='Maya Noor-Rahman (Employee ID STAFF-3982)'
        bad='직원 번호 Maya Noor-Rahman, 직원 ID STAFF-3982'
        good='Maya Noor-Rahman (직원 번호 STAFF-3982)'
        # First attempt can reject missing marker preservation; retry the original
        # source also runs the role check, independently of marker count checks.
        with patch('transformation.llm.ollama_native.chat',side_effect=[(bad,{}),(bad,{})]):
            with self.assertRaisesRegex(ValueError,'relationship'):translate('unused','translategemma:12b',source,'Korean')
        with patch('transformation.llm.ollama_native.chat',side_effect=[(bad,{}),(good,{})]) as chat:
            self.assertEqual(translate('unused','translategemma:12b',source,'Korean',context='background '*200)[0],good)
            self.assertIn('Source field/value relationships',chat.call_args.args[2][0]['content'])

    def source(self,density):
        pdf=pymupdf.open();p=pdf.new_page(width=150,height=60)
        with pymupdf.open() as source:
            original=source.new_page(width=150,height=60);original.insert_text((10,15),'Device label',fontsize=6)
            raster=original.get_pixmap(matrix=pymupdf.Matrix(density,density),alpha=False)
        p.insert_image(p.rect,pixmap=raster)
        return pdf

    def test_high_detail_small_text_uses_source_relative_floor_only(self):
        region={'id':'a','text':'Device label','bbox':[10,10,90,15.5],'metadata':{'requires_review':False}}
        with self.source(3) as pdf:
            policy=source_fit_policy(region,pdf[0])
            self.assertGreaterEqual(policy['minimum'],4)
            self.assertLess(policy['minimum'],8)
            self.assertGreater(policy['source_font_estimate'],4.5)
            self.assertLess(policy['source_font_estimate'],7.5)
            fitted=fit_text(pymupdf.Rect(10,10,90,20),'장치 설명',pymupdf.Font('cjk'),
                            max_size=policy['maximum'],min_size=policy['minimum'])
            self.assertIsNotNone(fitted)
            self.assertTrue(validate_placements(pymupdf.Rect(10,10,90,20),fitted,pymupdf.Font('cjk')))
        with self.source(1) as pdf:self.assertEqual(source_fit_policy(region,pdf[0])['minimum'],8)
        region['metadata']['requires_review']=True
        with self.source(3) as pdf:self.assertEqual(source_fit_policy(region,pdf[0])['minimum'],8)

    def test_validation_exception_is_scoped_to_source_fitting_box(self):
        with pymupdf.open() as pdf:
            p=pdf.new_page();p.insert_text((20,30),'small',fontsize=6);p.insert_text((200,30),'other',fontsize=6)
            limits=[dict(page=1,bbox=[10,10,100,45],minimum=5)]
            result=inspect_output(pdf.tobytes(),'pdf',min_font_size=8,font_regions=limits)
            self.assertEqual([b['text'] for b in result['geometry_blockers']],['other'])
            with self.assertRaisesRegex(ValueError,'font validation'):
                inspect_output(pdf.tobytes(),'pdf',min_font_size=8,font_regions=[dict(page=1,bbox=[0,0,600,600],minimum=1)])

    class Model:
        def __init__(self,rows):self.rows=iter(rows);self.calls=0
        def predict(self,image):
            self.calls+=1;text,score=next(self.rows);return [dict(rec_text=text,rec_score=score)]

    def test_sensitive_identifier_requires_separate_recognizer(self):
        image=np.full((20,160,3),255,np.uint8)
        primary=self.Model([('Asset: MX-2981',.99)]*3)
        secondary=self.Model([('Asset: MX-2981',.99)]*2)
        result=bounded_recognition(image,primary,lambda:secondary,'en')
        self.assertFalse(result[1]);self.assertEqual(secondary.calls,2)
        primary=self.Model([('Asset: MX-2981',.99)]*3)
        secondary=self.Model([('Asset: MX-2987',.99)]*2)
        self.assertTrue(bounded_recognition(image,primary,lambda:secondary,'en')[1])

    def test_same_model_views_cannot_override_sensitive_case_conflict(self):
        image=np.full((20,160,3),255,np.uint8)
        p=self.Model([('Device: CX-713',.99)]*3)
        s=self.Model([('Device: Cx-713',.99)]*2)
        self.assertTrue(bounded_recognition(image,p,lambda:s,'en')[1])
        p=self.Model([('Device: CX–713',.99)]*3)
        s=self.Model([('Device: CX-713',.99)]*2)
        self.assertTrue(bounded_recognition(image,p,lambda:s,'en')[1])

    def test_saved_sensitive_evidence_cannot_bypass_independent_verification(self):
        from tests.temp_workspace import temporary_workspace
        from tests.test_partial_field_handoff import PartialFieldTests
        from transformation.ocr.paddle_handoff import prepare
        with temporary_workspace() as root:
            data,recognition,line=PartialFieldTests().fixture(root)
            line.update(text='Asset MX-718',bbox=[20,30,180,48],recognition_score=.99,requires_review=False)
            line.pop('confirmed_label')
            line['candidates']=[dict(text=line['text'],score=.99,variant=v,model='en_PP-OCRv5_mobile_rec') for v in ('source','enlarged')]
            (recognition/'lines.jsonl').write_text(json.dumps(line)+'\n')
            result=prepare(data,recognition,root/'out','unseen.pdf')
            ir=json.loads(__import__('pathlib').Path(result['document_ir']).read_text())
            self.assertTrue(ir['pages'][0]['regions'][0]['metadata']['requires_review'])
            with pymupdf.open(result['input_pdf']) as output:self.assertEqual(output[0].get_text().strip(),'')

    def test_actual_recognizer_identities_are_required_in_saved_proof(self):
        from tools.ocr.recognize_paddle_boxes import independent_literal_evidence
        reads=[dict(text='MX-718',score=.99,variant=v,model=m) for m in ('en_PP-OCRv5_mobile_rec','PP-OCRv5_server_rec') for v in ('source','enlarged')]
        self.assertTrue(independent_literal_evidence(reads,'MX-718'))
        reads[-1]['text']='MX-719'
        self.assertFalse(independent_literal_evidence(reads,'MX-718'))
