import io
import json
import sys
import types
import unittest
import os
from pathlib import Path
from tests.temp_workspace import temporary_workspace
from unittest.mock import patch
from tools.service.cloud_worker import origin, preflight, engine_python, cuda_ready, rpc


class CloudWorkerReadinessTests(unittest.TestCase):
    def test_local_cache_policy_is_not_forwarded_to_renderer(self):
        from backend.engine_job import local_cache_options
        original={'ocr_cache_mode':'reuse','http_keepalive':True}
        result=local_cache_options(original,{},Path('jobs/job-id'))
        self.assertNotIn('ocr_cache_mode',result)
        self.assertEqual(result['ocr_cache_root'],Path('jobs/shared-ocr'))
        self.assertTrue(result['http_keepalive'])
        self.assertIn('ocr_cache_mode',original)

    def test_local_cache_fresh_disables_root_and_reuse_honors_server_root(self):
        from backend.engine_job import local_cache_options
        cfg={'ocr_cache_root':'trusted/cache'}
        self.assertIsNone(local_cache_options({'ocr_cache_mode':'fresh'},cfg,Path('jobs/a'))['ocr_cache_root'])
        self.assertEqual(local_cache_options({'ocr_cache_mode':'reuse'},cfg,Path('jobs/a'))['ocr_cache_root'],Path('trusted/cache'))
        with self.assertRaisesRegex(ValueError,'OCR cache mode'):
            local_cache_options({'ocr_cache_mode':'invalid'},cfg,Path('jobs/a'))

    def test_rpc_identifies_agent_and_keeps_worker_auth(self):
        with patch('tools.service.cloud_worker.urlopen',return_value=io.BytesIO(b'{"status":"recorded"}')) as request:
            rpc({'url':'https://app.example','token':'synthetic-token','worker_id':'worker'},'/worker/status')
        headers={k.lower():v for k,v in request.call_args.args[0].header_items()}
        self.assertEqual(headers['user-agent'],'TransformX-Worker/1.0')
        self.assertEqual(headers['authorization'],'Bearer synthetic-token')
        self.assertEqual(headers['x-worker-id'],'worker')

    def test_engine_interpreter_does_not_use_babeldoc_interpreter(self):
        self.assertEqual(engine_python({'engine_profile':{'python':'babeldoc','engine_python':'engine'}}),'engine')
        self.assertEqual(engine_python({'engine_profile':{'python':'babeldoc'}}),sys.executable)

    def test_separate_ocr_interpreter_is_checked_without_shell(self):
        with patch('tools.service.cloud_worker.subprocess.run',return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertTrue(cuda_ready({'ocr_python':'/trusted/ocr/python'}))
        self.assertEqual(run.call_args.args[0][0],'/trusted/ocr/python')
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_separate_ocr_without_cuda_is_not_ready(self):
        with patch('tools.service.cloud_worker.subprocess.run',return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(cuda_ready({'ocr_python':'/trusted/ocr/python'}))

    def test_server_ocr_device_is_set_before_engine_runs(self):
        from backend.engine_job import main
        with temporary_workspace() as root:
            source=root/'source.pdf';source.write_bytes(b'synthetic')
            request=root/'request.json'
            request.write_text(json.dumps({'source':str(source),'filename':'test.pdf',
                'profile':{'ocr_device':'gpu:0','engine_options':{'ocr_cache_mode':'reuse'}},'options':{'operation':'translation',
                'source_language':'en','target_language':'ko','protected_values':[],
                'document_context':'','pii_categories':[]}}),encoding='utf8')
            def workflow(*args,**kwargs):
                self.assertEqual(os.environ.get('TRANSFORMX_OCR_DEVICE'),'gpu:0')
                self.assertNotIn('ocr_cache_mode',kwargs)
                self.assertEqual(kwargs['ocr_cache_root'],root.parent/'shared-ocr')
                yield {'status':'failed','stage':'failed','seconds':0}
            with patch.dict(os.environ,{},clear=False),patch.object(sys,'argv',
                ['engine_job',str(request),str(root/'events.jsonl'),str(root/'terminal.json')]), \
                patch('transformation.workflows.run_workflow',side_effect=workflow):
                main()
            self.assertEqual(json.loads((root/'terminal.json').read_text())['status'],'failed')

    def check(self,models,cuda=True,gpus=1):
        paddle=types.SimpleNamespace(is_compiled_with_cuda=lambda:cuda,
            device=types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda:gpus)))
        response=io.BytesIO(json.dumps({'models':[{'name':x} for x in models]}).encode())
        with patch.dict(sys.modules,{'pymupdf':types.ModuleType('pymupdf'),'paddle':paddle}), \
             patch('tools.service.cloud_worker.subprocess.check_output',return_value='Allocated NVIDIA GPU'), \
             patch('tools.service.cloud_worker.urlopen',return_value=response):
            return preflight({'engine_profile':{'translation_endpoint':'http://127.0.0.1:11434/v1'}})

    def test_translation_model_does_not_advertise_pii_or_combined(self):
        state=self.check(['translategemma:12b'])
        self.assertTrue(state['ready'])
        self.assertEqual(state['operations'],['translation'])

    def test_both_models_advertise_all_operations(self):
        state=self.check(['translategemma:12b','qwen3.5:9b'])
        self.assertEqual(state['operations'],['translation','pii','combined'])

    def test_cpu_paddle_never_advertises_gpu_readiness(self):
        self.assertFalse(self.check(['translategemma:12b'],cuda=False)['ready'])

    def test_missing_gpu_or_models_never_advertises_readiness(self):
        self.assertFalse(self.check(['translategemma:12b'],gpus=0)['ready'])
        self.assertFalse(self.check([])['ready'])

    def test_origins_do_not_allow_credentials_paths_or_insecure_remote_hosts(self):
        for value in ['http://public.example','https://user:pass@example.com','https://example.com/admin']:
            with self.assertRaises(ValueError):origin(value)
        self.assertEqual(origin('https://app.example/'),'https://app.example')


if __name__=='__main__':unittest.main()
