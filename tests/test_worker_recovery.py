"""Worker reliability: time limits, process cleanup, result retries and reason codes.

Synthetic only: no network, GPU, Ollama or real documents.
"""
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from tests.temp_workspace import temporary_workspace
from tools.service import cloud_worker as cw

CFG={'url':'https://app.example','token':'t'*64,'worker_id':'w','engine_profile':{}}
JOB={'id':'11111111-1111-4111-8111-111111111111','lease':'lease'}


def http_error(code):
    return HTTPError('https://app.example',code,'error',{},io.BytesIO(b'{}'))


class SupervisionTests(unittest.TestCase):
    def test_timeout_stops_the_engine_and_its_children(self):
        with temporary_workspace() as root:
            beat=root/'child-beat'
            # The engine starts a child that keeps writing; both must stop.
            child=f"import time,pathlib\nwhile True:\n pathlib.Path({str(beat)!r}).write_text(str(time.time()));time.sleep(.1)"
            parent=f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child!r}])\ntime.sleep(60)"
            process=cw.spawn([sys.executable,'-c',parent])
            try:
                deadline=time.monotonic()+20
                while not beat.exists() and time.monotonic()<deadline:time.sleep(.1)
                self.assertTrue(beat.exists(),'child process did not start')
                with self.assertRaises(cw.JobFailure) as failure:
                    cw.supervise(process,1,lambda elapsed:None,interval=60)
                self.assertEqual(failure.exception.code,'ENGINE_TIMEOUT')
                self.assertIsNotNone(process.poll())
                time.sleep(1);first=beat.read_text();time.sleep(1)
                self.assertEqual(beat.read_text(),first,'child process kept running after the time limit')
            finally:
                cw.stop_process(process)

    def test_progress_ticks_after_the_first_interval_not_immediately(self):
        ticks=[]
        process=cw.spawn([sys.executable,'-c','import time; time.sleep(1.6)'])
        cw.supervise(process,30,ticks.append,interval=.5)
        self.assertGreaterEqual(len(ticks),2)
        self.assertGreaterEqual(ticks[0],.45)

    def test_lost_lease_during_progress_stops_processing(self):
        process=cw.spawn([sys.executable,'-c','import time; time.sleep(60)'])
        def tick(elapsed):raise cw.JobFailure('LEASE_LOST')
        with self.assertRaises(cw.JobFailure) as failure:
            cw.supervise(process,60,tick,interval=.2)
        self.assertEqual(failure.exception.code,'LEASE_LOST')
        self.assertIsNotNone(process.poll())

    def test_time_limit_configuration_is_validated(self):
        self.assertEqual(cw.run_limit({}),3600.0)
        for value in (10,'3600',True,10**6):
            with self.assertRaises(ValueError):cw.run_limit({'timeout_seconds':value})


class DeliveryTests(unittest.TestCase):
    def test_transient_failures_are_retried_and_progress_is_reported(self):
        outcomes=[URLError('reset'),http_error(503),{'status':'recorded'}]
        calls=[]
        def rpc(cfg,path,payload=None,**kwargs):
            calls.append(path)
            if path.endswith('/heartbeat'):return {}
            outcome=outcomes.pop(0)
            if isinstance(outcome,Exception):raise outcome
            return outcome
        sleeps=[]
        with patch.object(cw,'rpc',side_effect=rpc):
            self.assertEqual(cw.deliver(CFG,JOB,b'%PDF-x',{},sleep=sleeps.append),{'status':'recorded'})
        self.assertEqual(sleeps,[5,15])
        self.assertEqual(calls.count('/worker/jobs/'+JOB['id']+'/result'),3)
        self.assertEqual(calls.count('/worker/jobs/'+JOB['id']+'/heartbeat'),2)

    def test_each_retry_sends_identical_bytes(self):
        bodies=[]
        def rpc(cfg,path,payload=None,raw=None,**kwargs):
            if path.endswith('/result'):
                bodies.append(raw)
                if len(bodies)==1:raise URLError('timeout')
            return {}
        with patch.object(cw,'rpc',side_effect=rpc):cw.deliver(CFG,JOB,b'%PDF-x',{'pages':1},sleep=lambda s:None)
        self.assertEqual(bodies[0],bodies[1])

    def test_rejected_results_are_not_retried(self):
        for code,expected in ((400,'RESULT_UPLOAD_FAILED'),(409,'LEASE_LOST')):
            with patch.object(cw,'rpc',side_effect=http_error(code)) as rpc:
                with self.assertRaises(cw.JobFailure) as failure:
                    cw.deliver(CFG,JOB,b'%PDF-x',{},sleep=lambda s:None)
            self.assertEqual(failure.exception.code,expected)
            self.assertEqual(rpc.call_count,1)

    def test_exhausted_retries_report_upload_failure(self):
        with patch.object(cw,'rpc',side_effect=URLError('offline')) as rpc:
            with self.assertRaises(cw.JobFailure) as failure:
                cw.deliver(CFG,JOB,b'%PDF-x',{},sleep=lambda s:None)
        self.assertEqual(failure.exception.code,'RESULT_UPLOAD_FAILED')
        results=[c for c in rpc.call_args_list if c.args[1].endswith('/result')]
        self.assertEqual(len(results),len(cw.UPLOAD_RETRY_DELAYS)+1)

    def test_heartbeat_conflict_means_the_job_was_taken_away(self):
        with patch.object(cw,'rpc',side_effect=http_error(409)):
            with self.assertRaises(cw.JobFailure) as failure:cw.heartbeat(CFG,JOB,'stage',1)
        self.assertEqual(failure.exception.code,'LEASE_LOST')
        for error in (http_error(502),URLError('offline')):
            with patch.object(cw,'rpc',side_effect=error):cw.heartbeat(CFG,JOB,'stage',1)


class ReportingTests(unittest.TestCase):
    def test_failure_codes_come_from_structured_fields_only(self):
        self.assertEqual(cw.failure_code({'redaction_review_required':True}),'REDACTION_REVIEW_REQUIRED')
        self.assertEqual(cw.failure_code({'failure_code':'PRIVACY_CHECK_BLOCKED'}),'PRIVACY_CHECK_BLOCKED')
        self.assertEqual(cw.failure_code({'failure_code':'jane@example.com leaked'}),'ENGINE_FAILED')
        self.assertEqual(cw.failure_code({}),'ENGINE_FAILED')
        self.assertEqual(cw.JobFailure('not a code').code,'ENGINE_FAILED')

    def test_check_summary_contains_counts_and_no_document_text(self):
        result={'translation_errors':2,'scan_acceptance_blockers':[{'text':'secret'}],
                'validation':{'independent_checks':{'acceptable':False,
                    'known_sensitive_residuals':[],'geometry_blockers':[{'text':'Jane Doe'}],
                    'layout_review_candidates':[{},{}],'uninspected_content':[{'kind':'raster'}],
                    'rendered_privacy':{'passed':False,'reports':[{'detected_pii':['Jane Doe']}]}}}}
        summary=cw.summarize_checks(result)
        self.assertEqual(summary,{'acceptable':False,'known_sensitive_residuals':0,'partial_name_residuals':0,'geometry_blockers':1,
            'layout_review_candidates':2,'uninspected_content':1,'scan_acceptance_blockers':1,
            'translation_errors':2,'privacy_verification':'failed'})
        self.assertNotIn('Jane',json.dumps(summary))
        self.assertEqual(cw.summarize_checks({})['privacy_verification'],'not_run')

    def test_agent_reports_only_the_reason_code_and_keeps_polling(self):
        job={'id':JOB['id'],'lease':'lease','options':{'operation':'pii'}}
        claims=[{'job':job}]
        sent=[]
        def rpc(cfg,path,payload=None,**kwargs):
            sent.append((path,payload))
            if path=='/worker/claim':
                if claims:return claims.pop()
                raise KeyboardInterrupt
            return {}
        with temporary_workspace() as root, \
             patch.object(cw,'preflight',return_value={'ready':True,'operations':['pii']}), \
             patch.object(cw,'rpc',side_effect=rpc), \
             patch.object(cw,'execute',side_effect=cw.JobFailure('ENGINE_TIMEOUT','detail with jane@example.com')):
            with self.assertRaises(KeyboardInterrupt):cw._run(None,CFG,root)
        fail=[p for p in sent if p[0].endswith('/fail')]
        self.assertEqual(fail,[('/worker/jobs/'+JOB['id']+'/fail',{'code':'ENGINE_TIMEOUT'})])

    def test_lost_lease_is_not_reported_as_a_failure(self):
        job={'id':JOB['id'],'lease':'lease','options':{'operation':'pii'}}
        claims=[{'job':job}];sent=[]
        def rpc(cfg,path,payload=None,**kwargs):
            sent.append(path)
            if path=='/worker/claim':
                if claims:return claims.pop()
                raise KeyboardInterrupt
            return {}
        with temporary_workspace() as root, \
             patch.object(cw,'preflight',return_value={'ready':True,'operations':['pii']}), \
             patch.object(cw,'rpc',side_effect=rpc), patch.object(cw,'execute',side_effect=cw.JobFailure('LEASE_LOST')):
            with self.assertRaises(KeyboardInterrupt):cw._run(None,CFG,root)
        self.assertFalse(any(p.endswith('/fail') for p in sent))

    def test_pipeline_maps_exceptions_to_reason_codes(self):
        from transformation.pipeline import failure_code
        from transformation.validation.value_redaction import RedactionLocalizationError,RedactionVerificationError
        self.assertEqual(failure_code(RedactionLocalizationError([])),'REDACTION_REVIEW_REQUIRED')
        self.assertEqual(failure_code(RedactionVerificationError('x','PRIVACY_CHECK_BLOCKED')),'PRIVACY_CHECK_BLOCKED')
        self.assertEqual(failure_code(TimeoutError('slow')),'ENGINE_TIMEOUT')
        self.assertEqual(failure_code(RuntimeError('other')),'ENGINE_FAILED')
        self.assertTrue(issubclass(RedactionVerificationError,ValueError))

    def test_engine_job_records_timeout_code(self):
        from backend.engine_job import main
        with temporary_workspace() as root:
            source=root/'source.pdf';source.write_bytes(b'synthetic')
            request=root/'request.json'
            request.write_text(json.dumps({'source':str(source),'filename':'test.pdf','profile':{},
                'options':{'operation':'translation','source_language':'en','target_language':'ko',
                'protected_values':[],'document_context':'','pii_categories':[]}}),encoding='utf8')
            def workflow(*args,**kwargs):
                raise TimeoutError('slow')
                yield
            with patch.object(sys,'argv',['engine_job',str(request),str(root/'events.jsonl'),str(root/'terminal.json')]), \
                 patch('transformation.workflows.run_workflow',side_effect=workflow):
                main()
            terminal=json.loads((root/'terminal.json').read_text())
            self.assertEqual(terminal['failure_code'],'ENGINE_TIMEOUT')


if __name__=='__main__':unittest.main()


class KeepOcrWarmTests(unittest.TestCase):
    def session(self, pids):
        from unittest.mock import Mock
        s = Mock(); s.root = __import__('pathlib').Path('root'); s.process = Mock(pid=pids[0])
        def ensure(busy_wait=180): s.process = Mock(pid=pids[1])
        s.ensure.side_effect = ensure
        return s

    def test_live_service_is_only_checked(self):
        from unittest.mock import patch
        from tools.service.cloud_worker import keep_ocr_warm
        s = self.session([7, 7])
        with patch('transformation.ocr.service.prewarm') as warm: keep_ocr_warm(s)
        s.ensure.assert_called_once(); warm.assert_not_called()

    def test_restarted_service_is_warmed_before_the_next_job(self):
        from unittest.mock import patch
        from tools.service.cloud_worker import keep_ocr_warm
        s = self.session([7, 8])
        with patch('transformation.ocr.service.prewarm') as warm: keep_ocr_warm(s)
        warm.assert_called_once()

    def test_a_failed_check_does_not_stop_the_worker(self):
        from unittest.mock import Mock, patch
        from tools.service.cloud_worker import keep_ocr_warm
        s = Mock(); s.ensure.side_effect = RuntimeError('unresponsive')
        with patch('transformation.ocr.service.prewarm') as warm: keep_ocr_warm(s)
        warm.assert_not_called(); keep_ocr_warm(None)


class PiiServerTests(unittest.TestCase):
    def test_environment_points_pii_at_another_server_without_editing_the_config(self):
        from unittest.mock import patch
        from tools.service.cloud_worker import apply_profile_overrides
        profile = {'pii_endpoint': 'http://127.0.0.1:11434/v1'}
        with patch.dict(os.environ, {'TRANSFORMX_PII_ENDPOINT': 'http://127.0.0.1:8000/v1', 'TRANSFORMX_PII_MODEL': 'm-fp8'}, clear=True):
            self.assertEqual(apply_profile_overrides(profile), {'pii_endpoint': 'http://127.0.0.1:8000/v1', 'pii_model': 'm-fp8'})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(apply_profile_overrides({'pii_endpoint': 'x'}), {'pii_endpoint': 'x'})

    def test_environment_points_translation_at_its_installed_environments(self):
        from unittest.mock import patch
        from tools.service.cloud_worker import apply_profile_overrides
        env = {'TRANSFORMX_BABELDOC_PYTHON': '/srv/babeldoc/bin/python', 'TRANSFORMX_ENGINE_PYTHON': '/srv/engine/bin/python'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(apply_profile_overrides({'ocr_python': '/srv/paddle/bin/python'}),
                             {'ocr_python': '/srv/paddle/bin/python', 'python': '/srv/babeldoc/bin/python',
                              'engine_python': '/srv/engine/bin/python'})

    def test_environment_sets_translation_server_and_concurrency(self):
        from unittest.mock import patch
        from tools.service.cloud_worker import apply_profile_overrides
        env = {'TRANSFORMX_TRANSLATION_ENDPOINT': 'http://127.0.0.1:8010/v1', 'TRANSFORMX_TRANSLATION_WORKERS': '16',
               'TRANSFORMX_TRANSLATION_QPS': '64'}
        with patch.dict(os.environ, env, clear=True):
            profile = apply_profile_overrides({'engine_options': {'table_strategy': 'auto', 'translation_qps': 8}})
        self.assertEqual(profile['translation_endpoint'], 'http://127.0.0.1:8010/v1')
        self.assertEqual(profile['engine_options'], {'table_strategy': 'auto', 'translation_qps': 64,
                                                     'translation_workers': 16, 'ollama_parallelism': 16})

    def test_translation_served_by_vllm_is_ready_without_ollama(self):
        from unittest.mock import patch
        from tools.service.cloud_worker import translation_ready
        listing = io.BytesIO(json.dumps({'data': [{'id': 'translategemma:12b'}]}).encode())
        profile = {'translation_endpoint': 'http://127.0.0.1:8010/v1'}
        with patch.dict(os.environ, {'TRANSFORMX_OPENAI_COMPATIBLE': 'http://127.0.0.1:8000,http://127.0.0.1:8010'}), \
             patch('urllib.request.urlopen', return_value=listing) as call:
            self.assertTrue(translation_ready(profile, 'http://127.0.0.1:11434', []))
        self.assertEqual(call.call_args.args[0], 'http://127.0.0.1:8010/v1/models')
        self.assertTrue(translation_ready({}, 'http://127.0.0.1:11434', ['translategemma:12b']))   # Ollama default
        self.assertFalse(translation_ready({}, 'http://127.0.0.1:11434', ['qwen3.5:9b']))

    def test_pii_is_ready_only_when_its_server_serves_the_model(self):
        from unittest.mock import patch
        from tools.service.cloud_worker import pii_ready
        listing = io.BytesIO(json.dumps({'data': [{'id': 'm-fp8'}]}).encode())
        with patch.dict(os.environ, {'TRANSFORMX_OPENAI_COMPATIBLE': 'http://127.0.0.1:8000'}), \
             patch('urllib.request.urlopen', return_value=listing) as call:
            self.assertTrue(pii_ready({'pii_endpoint': 'http://127.0.0.1:8000/v1', 'pii_model': 'm-fp8'}))
        self.assertTrue(call.call_args.args[0].endswith('/v1/models'))
        with patch('urllib.request.urlopen', side_effect=OSError('down')):
            self.assertFalse(pii_ready({'pii_endpoint': 'http://127.0.0.1:8000/v1', 'pii_model': 'm-fp8'}))
