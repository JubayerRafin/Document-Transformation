import json,tempfile,unittest
from contextlib import contextmanager
import shutil
import uuid
import os
from unittest.mock import patch
from pathlib import Path
import pymupdf
from tools.evaluation.dataset import prepare,registry,pii_scores,signature,report,run,preflight
from transformation.ocr.cache import location,lookup,publish


def pdf_bytes(text='Example'):
    with pymupdf.open() as pdf:
        page=pdf.new_page(); page.insert_text((40,60),text); return pdf.tobytes()


@contextmanager
def test_directory():
    # Windows sandbox renames need ordinary inherited directory permissions.
    parent=Path(os.environ.get('LAB_TEST_TMP',tempfile.gettempdir())).resolve()
    root=parent/('dataset-test-'+uuid.uuid4().hex)
    root.mkdir(mode=0o755)
    try: yield str(root)
    finally:
        if root.resolve().parent!=parent or not root.name.startswith('dataset-test-'):
            raise RuntimeError('Refusing cleanup outside the test directory.')
        shutil.rmtree(root)


class DatasetTests(unittest.TestCase):
    def test_invalid_extraction_strategy_rejected_before_model_checks(self):
        with self.assertRaisesRegex(ValueError,'table_strategy'):
            preflight({'table_strategy':'unknown'},'translation')

    def test_batch_forwards_explicit_docling_strategy(self):
        with test_directory() as directory:
            root=Path(directory); source=root/'source.pdf'; source.write_bytes(pdf_bytes())
            config={'output_root':str(root/'batch'),'table_strategy':'docling','pdf_page_mode':'document','babeldoc_python':str(root/'python'),
                    'translation_model':'translategemma:12b','pii_model':'qwen3.5:9b','endpoint':'http://unused',
                    'source_language':'en','targets':['ko'],'timeout_seconds':3600,'pii_categories':[]}
            document={'id':'unrelated','path':str(source),'sha256':'fixture'}
            event={'stage':'complete','status':'generated','seconds':1,'run_dir':str(root/'generated')}
            with patch('tools.evaluation.dataset.preflight'),patch('tools.evaluation.dataset.registry',return_value=[document]),\
                 patch('tools.evaluation.dataset.signature',return_value='fixture'),\
                 patch('tools.evaluation.dataset.run_workflow',return_value=iter([event])) as workflow,\
                 patch('tools.evaluation.dataset.gpu_state',return_value=[{'name':'translategemma:12b','size':100,'size_vram':100}]),\
                 patch('tools.evaluation.dataset.report'):
                run(config,'translation')
            self.assertEqual(workflow.call_args.kwargs['table_strategy'],'docling')
            self.assertEqual(workflow.call_args.kwargs['pdf_page_mode'],'document')

    def test_corrupt_cache_pointer_is_not_reused(self):
        with test_directory() as directory:
            root=Path(directory);(root/'complete.json').write_text('{broken')
            self.assertIsNone(lookup(root))

    def test_same_pixels_on_different_page_geometry_have_separate_cache_keys(self):
        with test_directory() as directory:
            files=[]
            for width in (20.1,20.2):
                with pymupdf.open() as pdf:
                    pdf.new_page(width=width,height=20);files.append(pdf.tobytes())
            self.assertNotEqual(location(files[0],'en','gpu',directory),location(files[1],'en','gpu',directory))

    def test_exact_span_scores_count_false_positives_and_misses(self):
        expected=[{'region_id':'a','start':0,'end':4,'category':'Names'},
                  {'region_id':'a','start':10,'end':20,'category':'Identifiers'}]
        predicted=[{'region_id':'a','spans':[[0,4]],'category':'Names'},
                   {'region_id':'a','spans':[[25,30]],'category':'Names'}]
        scores=pii_scores(expected,predicted)
        self.assertEqual((scores['tp'],scores['fp'],scores['fn']),(1,1,1))
        self.assertEqual(scores['f1'],.5)

    def test_registry_rejects_modified_source(self):
        with test_directory() as directory:
            root=Path(directory); source=root/'a.pdf'; source.write_bytes(pdf_bytes())
            config={'documents':[{'id':'a','path':str(source)}],'targets':['ko'],'output_root':str(root/'batch')}
            prepare(config); self.assertEqual(len(registry(config)),1)
            source.write_bytes(pdf_bytes('Changed'))
            with self.assertRaisesRegex(ValueError,'changed after registration'): registry(config)

    def test_new_pod_connection_does_not_invalidate_policy_comparison(self):
        first={'translation_model':'translategemma:12b','ocr_remote':{'host':'192.0.2.1'},'endpoint':'http://127.0.0.1:11435/v1'}
        second={**first,'ocr_remote':{'host':'192.0.2.2'}}
        self.assertEqual(signature(first),signature(second))
        self.assertNotEqual(signature(first),signature({**first,'translation_model':'different'}))

    def test_ocr_key_uses_pixels_language_device_and_worker_policy(self):
        with test_directory() as directory:
            a=pdf_bytes(); b=pdf_bytes()
            self.assertEqual(location(a,'en','gpu',directory),location(b,'en','gpu',directory))
            self.assertNotEqual(location(a,'en','gpu',directory),location(a,'en','cpu',directory))
            self.assertNotEqual(location(a,'en','gpu',directory),location(a,'ko','gpu',directory))

    def test_incomplete_evidence_cannot_be_reused(self):
        with test_directory() as directory:
            root=Path(directory); recognition=root/'recognition'; recognition.mkdir()
            (recognition/'manifest.json').write_text('{"status":"processing"}')
            publish(root/'cache',recognition); self.assertIsNone(lookup(root/'cache'))
            (recognition/'manifest.json').write_text('{"status":"completed"}')
            self.assertEqual(lookup(root/'cache'),str(recognition.absolute()))

    def test_unreviewed_pii_is_not_given_a_fabricated_accuracy_score(self):
        with test_directory() as directory:
            root=Path(directory); source=root/'a.pdf'; source.write_bytes(pdf_bytes())
            config={'documents':[{'id':'a','path':str(source)}],'targets':['ko'],'output_root':str(root/'batch')}
            prepare(config); phase=root/'batch/pii'; phase.mkdir()
            digest=registry(config)[0]['sha256']; manifest=phase/'manifest.json'
            manifest.write_text(json.dumps({'status':'generated','seconds':1,'validation':{}}))
            (phase/'index.json').write_text(json.dumps({'records':[{'document_id':'a','target':None,
                'manifest':str(manifest),'source_sha256':digest,'gpu_verified':True}]}))
            report(config,'pii')
            row=json.loads((phase/'evaluation.json').read_text())['rows'][0]
            self.assertIsNone(row['pii_scores']); self.assertEqual(row['quality_status'],'not_reviewed')


if __name__=='__main__': unittest.main()
