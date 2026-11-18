import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from transformation.llm import endpoints

KEY = 'sk-test-0123456789abcdef'
CLEAN = {k: v for k, v in os.environ.items() if not k.startswith('TRANSFORMX_')}


class FakeServer:
    """A local OpenAI-compatible server that records what it receives."""

    def __init__(self, models=('translategemma:12b',), prefix=''):
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, body):
                data = json.dumps(body).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

            def do_GET(self):
                seen.append(('GET', self.path, dict(self.headers), None))
                self._reply({'data': [{'id': m} for m in models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append(('POST', self.path, dict(self.headers), body))
                self._reply({'choices': [{'message': {'content': 'Hallo'}, 'finish_reason': 'stop'}],
                             'usage': {'prompt_tokens': 3, 'completion_tokens': 1}})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base = f'http://127.0.0.1:{self.server.server_port}{prefix}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()


class RuleTests(unittest.TestCase):
    def test_local_servers_are_always_allowed(self):
        with patch.dict(os.environ, CLEAN, clear=True):
            endpoints.check('http://127.0.0.1:11434/v1')
            endpoints.check('http://localhost:8010/v1')

    def test_remote_servers_must_be_listed_and_use_https(self):
        with patch.dict(os.environ, CLEAN, clear=True):
            with self.assertRaisesRegex(ValueError, 'list it in TRANSFORMX_OPENAI_COMPATIBLE'):
                endpoints.check('https://llm.internal.example/v1')
        with patch.dict(os.environ, dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE='https://llm.internal.example,http://10.0.0.5:8000'), clear=True):
            endpoints.check('https://llm.internal.example/v1')
            with self.assertRaisesRegex(ValueError, 'must use https'):
                endpoints.check('http://10.0.0.5:8000/v1')
            with patch.dict(os.environ, {'TRANSFORMX_ALLOW_HTTP_REMOTE': '1'}):
                endpoints.check('http://10.0.0.5:8000/v1')

    def test_credentials_in_the_address_are_refused(self):
        with patch.dict(os.environ, CLEAN, clear=True):
            for bad in ('http://user:pw@127.0.0.1:8000/v1', 'http://127.0.0.1:8000/v1?key=x', 'ftp://127.0.0.1/v1', 'not a url'):
                with self.assertRaises(ValueError, msg=bad):
                    endpoints.check(bad)

    def test_gateway_paths_are_kept(self):
        self.assertEqual(endpoints.url('https://gw.example/team/v1', '/v1/chat/completions'),
                         'https://gw.example/team/v1/chat/completions')
        self.assertEqual(endpoints.url('http://127.0.0.1:8010/v1/', '/v1/models'), 'http://127.0.0.1:8010/v1/models')
        with patch.dict(os.environ, dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE='https://gw.example/team/v1'), clear=True):
            self.assertEqual(endpoints.kind('https://gw.example/team'), 'openai')
            self.assertEqual(endpoints.kind('https://gw.example/other/v1'), 'ollama')

    def test_keys_by_role_file_and_default(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = Path(folder, 'pii.key'); key_file.write_text(KEY + '-pii\n', encoding='utf-8')
            env = dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE='https://a.example,https://b.example,https://c.example',
                       TRANSFORMX_TRANSLATION_ENDPOINT='https://a.example/v1', TRANSFORMX_TRANSLATION_API_KEY=KEY,
                       TRANSFORMX_PII_ENDPOINT='https://b.example/v1', TRANSFORMX_PII_API_KEY_FILE=str(key_file),
                       TRANSFORMX_MODEL_API_KEY='default-key')
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(endpoints.api_key('https://a.example/v1'), KEY)
                self.assertEqual(endpoints.api_key('https://b.example'), KEY + '-pii')
                self.assertEqual(endpoints.api_key('https://c.example/v1'), 'default-key')
                self.assertIsNone(endpoints.api_key('http://127.0.0.1:8010/v1'))  # default key never goes to local servers
                self.assertEqual(endpoints.headers('https://a.example/v1')['Authorization'], 'Bearer ' + KEY)

    def test_model_family_by_name(self):
        for name in ('translategemma:12b', 'google/translategemma-12b-it', 'TranslateGemma-27B'):
            self.assertTrue(endpoints.is_translategemma(name))
        self.assertFalse(endpoints.is_translategemma('qwen3.5-9b-fp8'))


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()

    def tearDown(self):
        self.server.close()

    def test_local_openai_server_gets_the_key_and_thinking_switch_and_the_key_is_not_saved(self):
        from transformation.llm.ollama_native import chat
        with tempfile.TemporaryDirectory() as run:
            env = dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE=self.server.base, TRANSFORMX_TRANSLATION_ENDPOINT=self.server.base + '/v1',
                       TRANSFORMX_TRANSLATION_API_KEY=KEY, TRANSFORMX_RUN_DIR=run)
            with patch.dict(os.environ, env, clear=True):
                text, stats = chat(self.server.base + '/v1', 'translategemma:12b', [{'role': 'user', 'content': 'Hello'}])
            self.assertEqual(text, 'Hallo')
            method, path, headers, body = self.server.seen[-1]
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(headers['Authorization'], 'Bearer ' + KEY)
            self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})
            saved = ''.join(p.read_text(encoding='utf-8') for p in Path(run).rglob('*') if p.is_file())
            self.assertIn('transport_completed', saved)
            self.assertNotIn(KEY, saved)

    def test_remote_server_request_has_the_gateway_path_key_and_no_vllm_field(self):
        from transformation.llm import ollama_native
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return json.dumps({'choices': [{'message': {'content': 'Hallo'}, 'finish_reason': 'stop'}]}).encode()

        def fake_urlopen(request, timeout):
            captured.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data), timeout=timeout)
            return Response()

        env = dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE='https://gw.example/team/v1', TRANSFORMX_MODEL_API_KEY=KEY,
                   TRANSFORMX_MODEL_TIMEOUT='45')
        with patch.dict(os.environ, env, clear=True), patch('urllib.request.urlopen', fake_urlopen):
            text, _ = ollama_native.chat('https://gw.example/team/v1', 'google/translategemma-12b-it', [{'role': 'user', 'content': 'Hi'}])
        self.assertEqual(text, 'Hallo')
        self.assertEqual(captured['url'], 'https://gw.example/team/v1/chat/completions')
        self.assertEqual(captured['headers']['Authorization'], 'Bearer ' + KEY)
        self.assertNotIn('chat_template_kwargs', captured['body'])
        self.assertEqual(captured['timeout'], 45.0)

    def test_remote_servers_get_retries_on_busy_answers_and_local_ones_do_not(self):
        import urllib.error
        from transformation.llm import ollama_native
        calls = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return json.dumps({'choices': [{'message': {'content': 'Hallo'}, 'finish_reason': 'stop'}]}).encode()

        def flaky(request, timeout):
            calls.append(request.full_url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(request.full_url, 503, 'busy', {'Retry-After': '0'}, None)
            return Response()

        env = dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE='https://gw.example/v1')
        with patch.dict(os.environ, env, clear=True), patch('urllib.request.urlopen', flaky), patch('time.sleep'):
            self.assertEqual(ollama_native.chat('https://gw.example/v1', 'm', [{'role': 'user', 'content': 'x'}])[0], 'Hallo')
        self.assertEqual(len(calls), 3)
        calls.clear()
        env = dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE='http://127.0.0.1:8010')
        with patch.dict(os.environ, env, clear=True), patch('urllib.request.urlopen', flaky), patch('time.sleep'):
            with self.assertRaisesRegex(ValueError, 'HTTP 503'):
                ollama_native.chat('http://127.0.0.1:8010/v1', 'm', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(len(calls), 1)

    def test_unlisted_remote_server_is_refused_before_any_request(self):
        from transformation.llm.ollama_native import chat
        with patch.dict(os.environ, CLEAN, clear=True), patch('urllib.request.urlopen', side_effect=AssertionError('no request')):
            with self.assertRaisesRegex(ValueError, 'not on this machine'):
                chat('https://somewhere.example/v1', 'm', [{'role': 'user', 'content': 'x'}])

    def test_model_check_sends_the_key_and_finds_the_model(self):
        from transformation.workflows import check_models
        env = dict(CLEAN, TRANSFORMX_OPENAI_COMPATIBLE=self.server.base, TRANSFORMX_TRANSLATION_ENDPOINT=self.server.base + '/v1',
                   TRANSFORMX_TRANSLATION_API_KEY=KEY)
        with patch.dict(os.environ, env, clear=True):
            check_models('Translation only', translation_model='translategemma:12b', translation_endpoint=self.server.base + '/v1')
            with self.assertRaisesRegex(ValueError, 'not served'):
                check_models('Translation only', translation_model='other-model', translation_endpoint=self.server.base + '/v1')
        method, path, headers, _ = self.server.seen[0]
        self.assertEqual((method, path, headers['Authorization']), ('GET', '/v1/models', 'Bearer ' + KEY))


class WorkerCleanupTests(unittest.TestCase):
    def test_only_old_job_folders_are_deleted(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('cloud_worker', Path(__file__).resolve().parents[1] / 'tools/service/cloud_worker.py')
        worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
        with tempfile.TemporaryDirectory() as root:
            old, new = Path(root, '0b0e8f2c-1111-4222-8333-444455556666'), Path(root, '9a9e8f2c-1111-4222-8333-444455556666')
            other = Path(root, 'layout-service')
            for folder in (old, new, other):
                folder.mkdir(); (folder / 'file.txt').write_text('x')
            past = time.time() - 100 * 3600
            os.utime(old, (past, past)); os.utime(other, (past, past))
            self.assertEqual(worker.clean_old_jobs(root, hours=72), 1)
            self.assertFalse(old.exists()); self.assertTrue(new.exists()); self.assertTrue(other.exists())
            self.assertEqual(worker.clean_old_jobs(root, hours=0), 0)


class WorkerSettingTests(unittest.TestCase):
    def test_translation_model_name_can_be_set_for_the_worker(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('cloud_worker', Path(__file__).resolve().parents[1] / 'tools/service/cloud_worker.py')
        worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
        with patch.dict(os.environ, dict(CLEAN, TRANSFORMX_TRANSLATION_MODEL='google/translategemma-12b-it'), clear=True):
            self.assertEqual(worker.apply_profile_overrides({})['translation_model'], 'google/translategemma-12b-it')


if __name__ == '__main__':
    unittest.main()
