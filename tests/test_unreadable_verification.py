"""Unreadable output evidence must remain uncertain, never privacy approval."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from tests.temp_workspace import temporary_workspace
from tests import test_partial_field_handoff as handoff_fixture
from tools.ocr.recognize_paddle_boxes import refine_recognition
from transformation.validation.privacy_verification import verify


class UnreadableVerificationTests(unittest.TestCase):
    def test_empty_models_do_not_fabricate_text(self):
        class Model:
            def predict(self, view): return [{'rec_text':'','rec_score':0.0}]
        best,review,decision,_=refine_recognition(np.full((20,100,3),255,np.uint8),Model(),Model,'en',[])
        self.assertEqual(best['text'],'')
        self.assertTrue(review)
        self.assertEqual(decision,'unreadable_source_region')

    def test_model_failure_still_raises(self):
        class Model:
            def predict(self, view): raise RuntimeError('predictor unavailable')
        with self.assertRaisesRegex(RuntimeError,'predictor unavailable'):
            refine_recognition(np.full((20,100,3),255,np.uint8),Model(),Model,'en',[])

    def test_unreadable_verification_never_passes_and_other_leaks_are_checked(self):
        import pymupdf
        with temporary_workspace() as root:
            with pymupdf.open() as pdf:
                pdf.new_page(); data=pdf.tobytes()
            regions=[{'id':'unknown','text':'','metadata':{'requires_review':True}},
                     {'id':'readable','text':'demo@example.invalid','metadata':{}}]
            def prepare(data,recognition,output,name,*,allow_unreadable=False):
                self.assertTrue(allow_unreadable)
                (Path(output)/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':regions}]}))
            with patch('transformation.ocr.paddle_pipeline.run_ocr',return_value=iter([{'recognition_dir':'fixture'}])), \
                 patch('transformation.ocr.paddle_handoff.prepare',side_effect=prepare):
                result=list(verify(data,'pdf',root,['demo@example.invalid'],['Email addresses'],'','',False,{'ocr_python':'fixture'}))[-1]
            self.assertFalse(result['passed'])
            self.assertEqual(result['reports'][0]['uncertain_regions'],['unknown'])
            # A selected value read again in one line is a placed finding: masked and checked again.
            report=result['reports'][0]
            self.assertEqual(report['known_residual_values'],[])
            self.assertEqual(report['located_residual_values'],['demo@example.invalid'])
            self.assertIn(('readable','demo@example.invalid','selected_value_reread'),
                          [(d['region_id'],d['value'],d['method']) for d in report['detected_pii']])

    def _translated(self, source_text, source_review=False):
        """An English output keeping one line of source pixels the English reader leaves empty."""
        import pymupdf
        box=[300,100,400,120]
        readings={'en':[{'id':'kept','text':'','bbox':box,'metadata':{'requires_review':True}},
                        {'id':'other','text':'Total due','bbox':[50,50,150,70],'metadata':{}}],
                  'ar':[{'id':'a1','text':source_text,'bbox':[305,101,395,119],'metadata':{'requires_review':source_review}}]}
        with temporary_workspace() as root:
            with pymupdf.open() as pdf:
                pdf.new_page(); data=pdf.tobytes()
            def run_ocr(pdf,output,python,timeout=None,source_language=None):
                yield {'recognition_dir':source_language}
            def prepare(data,recognition,output,name,*,allow_unreadable=False):
                (Path(output)/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':readings[recognition]}]}))
            with patch('transformation.ocr.paddle_pipeline.run_ocr',side_effect=run_ocr), \
                 patch('transformation.ocr.paddle_handoff.prepare',side_effect=prepare):
                return list(verify(data,'pdf',root,['يوسف الخطيب'],['Names'],'','',False,{'ocr_python':'fixture'},
                                   language='en',source_language='ar'))[-1]

    def test_kept_source_line_is_read_in_the_source_language(self):
        result=self._translated('اسم الموظف')
        self.assertTrue(result['passed'])
        self.assertEqual(result['reports'][0]['uncertain_regions'],[])

    def test_kept_source_line_with_a_selected_value_stays_uncertain(self):
        result=self._translated('اسم الموظف يوسف الخطيب')
        self.assertFalse(result['passed'])
        self.assertEqual(result['reports'][0]['uncertain_regions'],['kept'])

    def test_kept_source_line_unreadable_in_both_languages_stays_uncertain(self):
        result=self._translated('اسم',source_review=True)
        self.assertFalse(result['passed'])

    def test_verified_zero_detection_is_not_clean_privacy_evidence(self):
        from transformation.ocr.paddle_handoff import prepare
        with temporary_workspace() as root:
            data,recognition,_=handoff_fixture.PartialFieldTests().fixture(root)
            (recognition/'lines.jsonl').write_text('')
            manifest=json.loads((recognition/'manifest.json').read_text())
            manifest.update(lines=0,no_text_detected=True)
            (recognition/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'No OCR text'):
                prepare(data,recognition,root/'strict','fixture.pdf')
            result=prepare(data,recognition,root/'verification','fixture.pdf',allow_unreadable=True)
            ir=json.loads(Path(result['document_ir']).read_text())
            self.assertTrue(ir['pages'][0]['regions'][0]['metadata']['requires_review'])
