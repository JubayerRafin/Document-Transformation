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
            self.assertEqual(result['reports'][0]['known_residual_values'],['demo@example.invalid'])

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
