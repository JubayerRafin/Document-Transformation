import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import pymupdf

from tests.temp_workspace import temporary_workspace
from transformation.pdf.nativeized_ir import refresh
from extraction.coordinator import text_regions


class RefreshTests(unittest.TestCase):
    def fixture(self,root):
        path=root/'new-layer.pdf'
        with pymupdf.open() as pdf:
            for text in ('Unseen OCR line 481','Digital page heading'):
                pdf.new_page(width=300,height=200).insert_text((20,35),text)
            pdf.save(path)
        original={'backend':'deep-doc-stage1-coordinated','units':[
            {'number':1,'id':'page-1','width':300,'height':200,'ocr_folder':'verified-evidence',
             'regions':[{'id':'uncertain','kind':'text','text':'uncertain source','bbox':[20,50,100,70],
                         'metadata':{'requires_review':True}}]},
            {'number':2,'id':'page-2','width':300,'height':200,'regions':[
                {'id':'table-real','kind':'table','bbox':[10,10,150,80],
                 'cells':[['Type','Count'],['Device','481']],'metadata':{'method':'docling'}}]}]}
        return path,original

    def test_refresh_uses_native_only_and_retains_digital_table_evidence(self):
        with temporary_workspace() as root:
            path,original=self.fixture(root); before=deepcopy(original)
            with patch('extraction.deep_doc_adapter.enrich',side_effect=AssertionError('semantic models must not rerun')):
                result=refresh(path,root,original)
            self.assertEqual(original,before)
            self.assertEqual(result['units'][1],original['units'][1])
            self.assertIsNot(result['units'][1],original['units'][1])
            self.assertIn('Unseen OCR line 481',[r['text'] for r in text_regions(result)])
            self.assertFalse(result['settings']['nativeization_refresh']['semantic_models_rerun'])

    def test_changed_page_dimensions_fail_instead_of_reusing_stale_boxes(self):
        with temporary_workspace() as root:
            path,original=self.fixture(root);original['units'][1]['width']=500
            with self.assertRaisesRegex(ValueError,'geometry'):refresh(path,root,original)

    def test_missing_page_or_duplicate_ids_fail(self):
        with temporary_workspace() as root:
            path,original=self.fixture(root)
            original['units'][1]['number']=1
            with self.assertRaisesRegex(ValueError,'inventory'):refresh(path,root,original)

    def test_runner_generates_only_requested_mono_output(self):
        import sys
        from transformation.pdf.runner import run_pdf
        with temporary_workspace() as root:
            path,_=self.fixture(root)
            def launch(command,**kwargs):
                output=Path(command[command.index('--output')+1]);
                (output/'fixture.ko.mono.pdf').write_bytes(path.read_bytes())
                process=unittest.mock.MagicMock();process.poll.return_value=0;process.returncode=0
                return process
            with patch('transformation.pdf.runner.subprocess.check_output',return_value='0.6.4'),\
                 patch('transformation.pdf.runner.subprocess.Popen',side_effect=launch) as process,\
                 patch('transformation.validation.quality.review_signals'):
                events=list(run_pdf(path.read_bytes(),'unseen.pdf',python=sys.executable,model='translategemma:12b',
                                    root=root/'runs',babeldoc_dual_output=False))
            self.assertEqual(events[-1]['status'],'generated',events[-1].get('error'))
            self.assertIn('--no-dual',process.call_args.args[0])
            self.assertFalse(events[-1]['bilingual_output_requested'])
