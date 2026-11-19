import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.service import engine_server

PDF = b'%PDF-1.7\n%test document\n'


class FakeProcess:
    """Stands in for the engine: writes its result like backend.engine_job, after a short delay."""

    def __init__(self, folder, fail=False, delay=0.05):
        self.folder, self.fail, self.delay, self.killed = folder, fail, delay, False

    def wait(self):
        time.sleep(self.delay)
        if self.killed:
            return
        request = json.loads((self.folder / 'request.json').read_text(encoding='utf-8'))
        output = self.folder / 'output.pdf'
        output.write_bytes(b'%PDF translated ' + request['options']['target_language'].encode())
        (self.folder / 'events.jsonl').write_text(json.dumps({'stage': 'translation_and_reconstruction'}) + '\n', encoding='utf-8')
        terminal = {'status': 'failed', 'failure_code': 'ENGINE_FAILED'} if self.fail else \
            {'status': 'generated', 'output_file': str(output), 'warnings': ['review'], 'seconds': 1.5}
        (self.folder / 'terminal.json').write_text(json.dumps(terminal), encoding='utf-8')

    def terminate(self):
        self.killed = True


class EngineServerTests(unittest.TestCase):
    def start(self, api_key='', fail=False, delay=0.05):
        self.temp = tempfile.TemporaryDirectory()
        self.seen = []

        def runner(folder, request, profile, env):
            self.seen.append(json.loads(Path(request).read_text(encoding='utf-8')))
            self.process = FakeProcess(folder, fail, delay)
            return self.process

        self.service = engine_server.Service(self.temp.name, {'engine_python': 'python'}, api_key, runner=runner)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), engine_server.handler(self.service))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read()
                return response.status, (json.loads(data) if response.headers['Content-Type'] == 'application/json' else data)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def wait_for(self, job_id, status, headers=None):
        for _ in range(100):
            code, body = self.call('GET', f'/v1/jobs/{job_id}', headers=headers)
            if body.get('status') == status:
                return body
            time.sleep(0.05)
        self.fail(f'job did not reach {status}: {body}')

    def test_a_job_runs_and_returns_its_pdf(self):
        self.start()
        code, body = self.call('POST', '/v1/jobs?operation=translation&source_language=en&target_language=de', PDF,
                               {'Content-Type': 'application/pdf',
                                'X-TransformX-Options': json.dumps({'terminology': {'redaction': 'Schwärzung'}})})
        self.assertEqual(code, 202)
        done = self.wait_for(body['job_id'], 'generated')
        self.assertEqual((done['stage'], done['warnings'], done['seconds']), ('translation_and_reconstruction', ['review'], 1.5))
        code, pdf = self.call('GET', f"/v1/jobs/{body['job_id']}/result")
        self.assertEqual((code, pdf), (200, b'%PDF translated de'))
        options = self.seen[0]['options']
        self.assertEqual((options['operation'], options['target_language'], options['terminology']),
                         ('translation', 'de', {'redaction': 'Schwärzung'}))
        self.assertEqual(self.call('GET', '/v1/jobs')[1][0]['status'], 'generated')

    def test_a_failed_job_reports_its_code_and_has_no_result(self):
        self.start(fail=True)
        job_id = self.call('POST', '/v1/jobs?operation=pii', PDF)[1]['job_id']
        self.assertEqual(self.wait_for(job_id, 'failed')['failure_code'], 'ENGINE_FAILED')
        self.assertEqual(self.call('GET', f'/v1/jobs/{job_id}/result'), (409, {'error': 'NOT_READY'}))

    def test_the_api_key_is_required_except_for_health(self):
        self.start(api_key='secret-key')
        self.assertEqual(self.call('GET', '/health')[0], 200)
        self.assertEqual(self.call('GET', '/v1/jobs'), (401, {'error': 'UNAUTHORISED'}))
        self.assertEqual(self.call('GET', '/v1/jobs', headers={'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.call('GET', '/v1/jobs', headers={'Authorization': 'Bearer secret-key'}), (200, []))

    def test_bad_requests_are_refused(self):
        self.start()
        cases = [('/v1/jobs?operation=delete', PDF, {}, 'INVALID_OPERATION'),
                 ('/v1/jobs?source_language=en&target_language=en', PDF, {}, 'INVALID_LANGUAGE'),
                 ('/v1/jobs?target_language=../x', PDF, {}, 'INVALID_LANGUAGE'),
                 ('/v1/jobs', b'not a pdf', {}, 'NOT_A_PDF'),
                 ('/v1/jobs', PDF, {'X-TransformX-Options': '{"command": "rm"}'}, 'INVALID_OPTIONS'),
                 ('/v1/jobs', PDF, {'X-TransformX-Options': 'not json'}, 'INVALID_OPTIONS')]
        for path, body, headers, code in cases:
            self.assertEqual(self.call('POST', path, body, headers)[1], {'error': code}, path)
        self.assertEqual(self.call('GET', '/v1/jobs/../../etc'), (404, {'error': 'NOT_FOUND'}))
        self.assertEqual(self.call('GET', '/v1/jobs/' + '0' * 32), (404, {'error': 'NOT_FOUND'}))
        self.assertEqual(self.seen, [])

    def test_a_job_can_choose_a_translation_model_from_the_settings_file(self):
        import os
        from unittest.mock import patch
        settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(settings_dir.cleanup)
        key = Path(settings_dir.name) / 'openai_key'
        key.write_text('sk-test-not-real', encoding='utf-8')
        path = Path(settings_dir.name) / 'transformx.toml'
        path.write_text('[translation]\nlabel = "Standard"\n[translation_models.openai]\nlabel = "GPT"\n'
                        'endpoint = "https://api.example/v1"\nmodel = "gpt-5-mini"\n'
                        f'key_file = {json.dumps(str(key))}\nrequests_per_second = 5\n', encoding='utf-8')
        envs = []
        with patch.dict(os.environ, {'TRANSFORMX_SETTINGS': str(path), 'TRANSFORMX_TRANSLATION_API_KEY': 'local-key'}):
            self.start()
            original = self.service.runner
            self.service.runner = lambda folder, request, profile, env: (envs.append(env), original(folder, request, profile, env))[1]
            self.assertEqual(self.call('GET', '/v1/translation-models')[1],
                             [{'id': 'default', 'label': 'Standard'}, {'id': 'openai', 'label': 'GPT'}])
            self.assertEqual(self.call('POST', '/v1/jobs?translation_model=other', PDF)[1], {'error': 'UNKNOWN_TRANSLATION_MODEL'})
            job_id = self.call('POST', '/v1/jobs?translation_model=openai&target_language=de', PDF)[1]['job_id']
            self.wait_for(job_id, 'generated')
            default_id = self.call('POST', '/v1/jobs?target_language=de', PDF)[1]['job_id']
            self.wait_for(default_id, 'generated')
        chosen, default = self.seen
        self.assertEqual((chosen['profile']['translation_endpoint'], chosen['profile']['translation_model'],
                          chosen['profile']['engine_options']['translation_qps']), ('https://api.example/v1', 'gpt-5-mini', 5))
        self.assertNotIn('sk-test-not-real', json.dumps(chosen))       # the key never goes into request.json
        self.assertNotIn('translation_endpoint', default['profile'])
        self.assertEqual(envs[0]['TRANSFORMX_TRANSLATION_API_KEY_FILE'], str(key))
        self.assertNotIn('TRANSFORMX_TRANSLATION_API_KEY', envs[0])    # the default key is not sent elsewhere
        self.assertIn('https://api.example/v1', envs[0]['TRANSFORMX_OPENAI_COMPATIBLE'])
        self.assertEqual(envs[1]['TRANSFORMX_TRANSLATION_API_KEY'], 'local-key')

    def test_a_running_job_can_be_cancelled_and_its_files_removed(self):
        self.start(delay=1.0)
        job_id = self.call('POST', '/v1/jobs', PDF)[1]['job_id']
        self.wait_for(job_id, 'running')
        self.assertEqual(self.call('DELETE', f'/v1/jobs/{job_id}')[1]['status'], 'cancelled')
        self.assertTrue(self.process.killed)
        self.assertFalse((Path(self.temp.name) / 'jobs' / job_id).exists())
        self.assertEqual(self.call('GET', f'/v1/jobs/{job_id}')[0], 404)


if __name__ == '__main__':
    unittest.main()
