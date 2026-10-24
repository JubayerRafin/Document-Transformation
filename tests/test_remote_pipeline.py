import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch
import zipfile

from tests.temp_workspace import temporary_workspace
from transformation.remote_pipeline import unpack,bundle_code
from transformation.remote_worker import server_options


class RemotePipelineTests(unittest.TestCase):
    def test_connected_dispatch_never_calls_local_pipeline(self):
        from transformation.workflows import run_workflow
        with patch('transformation.remote_pipeline.run_remote_pipeline',return_value=iter([{'status':'generated'}])) as remote, patch('transformation.workflows.run_pipeline') as local:
            self.assertEqual(list(run_workflow(b'input','unseen.pdf',execution_remote={'host':'1.2.3.4'})),[{'status':'generated'}])
            local.assert_not_called()
            self.assertNotIn('execution_remote',remote.call_args.kwargs)

    def test_remote_failure_never_falls_back(self):
        from transformation.workflows import run_workflow
        with patch('transformation.remote_pipeline.run_remote_pipeline',side_effect=RuntimeError('offline')), patch('transformation.workflows.run_pipeline') as local:
            with self.assertRaisesRegex(RuntimeError,'offline'):
                list(run_workflow(b'input','unseen.pdf',execution_remote={'host':'1.2.3.4'}))
            local.assert_not_called()

    def test_worker_uses_server_paths_and_loopback_without_recursive_ssh(self):
        result=server_options({'source':'en','target':'ko','protected_values':['unseen-value']},'/workspace/job')
        self.assertEqual(result['translation_endpoint'],'http://127.0.0.1:11434/v1')
        self.assertEqual(result['python'],'/workspace/transformx-babeldoc/bin/python')
        self.assertNotIn('ocr_remote',result)
        with self.assertRaises(ValueError):server_options({'python':'C:/local/python.exe'},'/workspace/job')
        self.assertIsNone(server_options({'ocr_cache_mode':'fresh'},'/workspace/job')['ocr_cache_root'])

    def test_only_referenced_cached_ocr_is_exported_and_relocated(self):
        from transformation.remote_worker import export_ocr_evidence
        from transformation.ocr.remote_ocr import FILES
        with temporary_workspace() as root:
            job=root/'job';run=job/'runs/one';run.mkdir(parents=True)
            cache=root/'cache';attempt=cache/'key/attempt-123';attempt.mkdir(parents=True)
            for name in FILES:
                path=attempt/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('{}' if path.suffix=='.json' else 'evidence')
            (cache/'unrelated').mkdir()
            (run/'ocr-document.json').write_text(json.dumps({'settings':{'recognition_manifest':{'detection_run':str(attempt)}}}))
            terminal=export_ocr_evidence(job,{'ocr_progress':{'recognition_dir':str(attempt/'recognition')}},cache)
            self.assertTrue((job/'ocr-evidence/key/attempt-123/gpu-run.json').is_file())
            self.assertFalse((job/'ocr-evidence/unrelated').exists())
            self.assertTrue(Path(terminal['ocr_progress']['recognition_dir']).is_relative_to(job))

    def test_download_relocates_artifacts_and_keeps_provenance(self):
        with temporary_workspace() as root:
            archive=root/'result.zip';job='/workspace/job'
            with zipfile.ZipFile(archive,'w') as zip:
                zip.writestr('terminal.json',json.dumps({'status':'generated','run_dir':job+'/runs/one','output_pdf':job+'/runs/one/output/result.pdf'}))
                zip.writestr('runs/one/output/result.pdf',b'pdf bytes')
                zip.writestr('runs/one/source-ir.json',json.dumps({'asset':job+'/runs/one/figure.png'}))
            result=unpack(archive,root/'received',job)
            self.assertTrue(Path(result['output_pdf']).is_file())
            self.assertEqual(json.loads((root/'received/server-terminal.json').read_text())['run_dir'],job+'/runs/one')
            self.assertTrue(json.loads((root/'received/runs/one/source-ir.json').read_text())['asset'].startswith(str(root/'received')))

    def test_download_rejects_traversal_and_missing_artifact(self):
        with temporary_workspace() as root:
            for name in ('../escape','/absolute','C:/escape','..\\escape'):
                archive=root/'bad.zip'
                with zipfile.ZipFile(archive,'w') as zip:
                    zip.writestr('terminal.json','{"status":"failed"}')
                    zip.writestr(name,'bad')
                with self.assertRaises(ValueError):unpack(archive,root/'received','/workspace/job')
            with zipfile.ZipFile(archive,'w') as zip:
                zip.writestr('terminal.json',json.dumps({'status':'generated','output_pdf':'/workspace/job/absent.pdf'}))
            with self.assertRaises(ValueError):unpack(archive,root/'received','/workspace/job')

    def test_engine_upload_contains_only_code_and_requirements(self):
        with temporary_workspace() as root:
            bundle_code(root/'code.tar.gz')
            with tarfile.open(root/'code.tar.gz') as tar:
                names=tar.getnames()
            self.assertIn('transformation/remote_worker.py',names)
            self.assertFalse(any(n.startswith(('runs/','configs/','samples/','.ssh/')) for n in names))

    def test_result_must_match_uploaded_source_hash(self):
        with temporary_workspace() as root:
            with zipfile.ZipFile(root/'result.zip','w') as zip:
                zip.writestr('terminal.json',json.dumps({'status':'generated','source_sha256':'different'}))
            with self.assertRaisesRegex(ValueError,'uploaded document'):
                unpack(root/'result.zip',root/'received','/workspace/job','expected')

    def test_server_gpu_ocr_dispatches_without_ssh(self):
        import os
        from transformation.ocr.paddle_pipeline import run_ocr
        with patch.dict(os.environ,{'TRANSFORMX_OCR_DEVICE':'gpu:0'}), patch('transformation.ocr.paddle_pipeline.run_server_gpu_ocr',return_value=iter([{'device':'gpu:0'}])) as gpu:
            self.assertEqual(list(run_ocr('source','out','python')),[{'device':'gpu:0'}])
            gpu.assert_called_once()
