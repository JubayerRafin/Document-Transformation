import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from transformation import settings

EXAMPLE = Path(__file__).resolve().parents[1] / 'configs' / 'transformx.example.toml'


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def write(self, text):
        path = Path(self.temp.name) / 'transformx.toml'
        path.write_text(text, encoding='utf-8')
        return path

    def test_the_example_file_is_valid_and_sets_nothing(self):
        self.assertEqual(settings.read(EXAMPLE), {})

    def test_every_problem_is_reported_at_once(self):
        path = self.write('[translation]\nendpont = "x"\nrequests_per_second = "fast"\n'
                          '[output]\nfigures = "magic"\n[colour]\nx = 1\n')
        with self.assertRaises(settings.SettingsError) as caught:
            settings.read(path)
        message = str(caught.exception)
        for expected in ('translation.endpont: unknown setting', 'requests_per_second: must be a whole number',
                         'output.figures: must be one of', '[colour]: unknown section'):
            self.assertIn(expected, message)
        self.assertEqual(len(settings.check(path, probe=False)), 4)

    def test_values_reach_the_environment_profile_and_engine_options(self):
        path = self.write('[translation]\nendpoint = "https://llm.example/v1"\nmodel = "gpt-5-mini"\n'
                          'requests_per_second = 5\n[models]\nremote_servers = ["https://llm.example/v1"]\n'
                          'allow_http_remote = false\n[ocr]\ndevice = "cpu"\nreuse_results = false\n'
                          '[output]\nfigures = "babeldoc"\n[engine_options]\ntable_strategy = "native"\n')
        environ, profile = {}, {'engine_options': {'figure_engine': 'ocr-slots', 'translation_qps': 8}}
        settings.apply(profile, path, environ)
        self.assertEqual(environ['TRANSFORMX_TRANSLATION_ENDPOINT'], 'https://llm.example/v1')
        self.assertEqual(environ['TRANSFORMX_TRANSLATION_MODEL'], 'gpt-5-mini')
        self.assertEqual(environ['TRANSFORMX_TRANSLATION_QPS'], '5')
        self.assertEqual(environ['TRANSFORMX_OPENAI_COMPATIBLE'], 'https://llm.example/v1')
        self.assertEqual(environ['TRANSFORMX_ALLOW_HTTP_REMOTE'], '0')
        self.assertEqual(profile['ocr_device'], 'cpu')
        # The file overrides the site's connection file; options it does not name are kept.
        self.assertEqual(profile['engine_options'], {'figure_engine': 'babeldoc', 'translation_qps': 8,
                                                     'ocr_cache_mode': 'fresh', 'table_strategy': 'native'})

    def test_environment_variables_win_over_the_file(self):
        path = self.write('[translation]\nmodel = "from-file"\n[service]\nport = 9090\n')
        environ = {'TRANSFORMX_TRANSLATION_MODEL': 'from-environment'}
        settings.apply({}, path, environ)
        self.assertEqual(environ['TRANSFORMX_TRANSLATION_MODEL'], 'from-environment')
        self.assertEqual(environ['TRANSFORMX_SERVICE_PORT'], '9090')
        with patch.dict(os.environ, {'TRANSFORMX_TRANSLATION_MODEL': 'from-environment'}, clear=True):
            exports = settings.shell_exports(path)
            rows = {name: (value, source) for name, value, source in settings.report(path)[1]}
        self.assertNotIn('TRANSFORMX_TRANSLATION_MODEL', exports)
        self.assertIn('export TRANSFORMX_SERVICE_PORT=9090', exports)
        self.assertEqual(rows['translation.model'], ('from-environment', 'environment (TRANSFORMX_TRANSLATION_MODEL)'))
        self.assertEqual(rows['service.port'], (9090, 'file'))
        self.assertEqual(rows['service.host'][1], 'default')

    def test_keys_are_never_shown_and_key_files_are_checked(self):
        key = Path(self.temp.name) / 'key'
        key.write_text('sk-secret-value', encoding='utf-8')
        path = self.write(f'[translation]\nkey_file = {json.dumps(str(key))}\n[pii]\nkey_file = "/missing/key"\n')
        with patch.dict(os.environ, {'TRANSFORMX_SERVICE_API_KEY': 'service-secret'}, clear=True):
            rows = settings.report(path)[1]
            problems = settings.check(path, probe=False)
        text = repr(rows)
        self.assertNotIn('sk-secret-value', text)
        self.assertNotIn('service-secret', text)
        self.assertIn('(readable)', text)
        self.assertEqual(len(problems), 1)
        self.assertIn('pii.key_file: file not found', problems[0])

    def test_relative_paths_are_relative_to_the_application(self):
        path = self.write('[worker]\nconnection_file = "configs/gpu.worker.local.json"\n')
        value = settings.read(path)['worker']['connection_file']
        self.assertEqual(Path(value), settings.APP / 'configs' / 'gpu.worker.local.json')

    def test_no_file_changes_nothing(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(settings, 'DEFAULT_FILE', Path(self.temp.name) / 'none'):
            profile = {'engine_options': {'a': 1}}
            self.assertIsNone(settings.apply(profile))
            self.assertEqual(profile, {'engine_options': {'a': 1}})
            self.assertEqual(dict(os.environ), {})


class ModelServerCheckTests(unittest.TestCase):
    def serve(self, status=200, models=('translategemma:12b',)):
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                body = json.dumps({'data': [{'id': m} for m in models]}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/v1'

    def check(self, text):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'transformx.toml'
        path.write_text(text, encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True):
            return settings.check(path)

    def test_a_served_model_passes(self):
        endpoint = self.serve()
        self.assertEqual(self.check(f'[translation]\nendpoint = "{endpoint}"\nmodel = "translategemma:12b"\n'), [])
        self.assertEqual(self.seen[0][0], '/v1/models')

    def test_a_missing_model_and_a_refused_key_are_explained(self):
        endpoint = self.serve(models=('other-model',))
        problems = self.check(f'[translation]\nendpoint = "{endpoint}"\nmodel = "gpt-5-mini"\n')
        self.assertIn('model "gpt-5-mini" is not served there', problems[0])
        endpoint = self.serve(status=401)
        self.assertIn('refused the API key', self.check(f'[pii]\nendpoint = "{endpoint}"\n')[0])

    def test_unlisted_remote_and_silent_servers_are_reported(self):
        self.assertIn('translation.endpoint', self.check('[translation]\nendpoint = "https://llm.example/v1"\n')[0])
        self.assertIn('no server answers', self.check('[translation]\nendpoint = "http://127.0.0.1:9/v1"\n')[0])


if __name__ == '__main__':
    unittest.main()


class WorkerModelChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key = Path(self.temp.name) / 'key'
        self.key.write_text('sk-test-not-real', encoding='utf-8')
        self.path = Path(self.temp.name) / 'transformx.toml'
        self.path.write_text('[translation]\nlabel = "Standard"\n'
                             '[translation_models.openai]\nlabel = "GPT"\nendpoint = "https://api.example/v1"\n'
                             f'model = "gpt-5-mini"\nkey_file = {json.dumps(str(self.key))}\n'
                             '[translation_models.gateway]\nendpoint = "http://127.0.0.1:8020/v1"\nmodel = "team-model"\n',
                             encoding='utf-8')

    def env(self, **extra):
        return patch.dict(os.environ, {'TRANSFORMX_SETTINGS': str(self.path), **extra}, clear=True)

    def test_the_worker_offers_only_models_whose_server_answers(self):
        from tools.service import cloud_worker
        served = lambda workflow, model, endpoint, base, names: model == 'gpt-5-mini'
        with self.env(), patch.object(cloud_worker, '_served', side_effect=served):
            offered = cloud_worker.translation_models({'translation_endpoint': 'http://127.0.0.1:8010/v1'}, True)
            none_local = cloud_worker.translation_models({}, False)
        self.assertEqual(offered, [{'id': 'default', 'label': 'Standard', 'external': False},
                                   {'id': 'openai', 'label': 'GPT', 'external': True}])
        self.assertEqual([m['id'] for m in none_local], ['openai'])
        self.assertNotIn('api.example', json.dumps(offered))

    def test_a_job_runs_with_its_chosen_model_and_key_file_only(self):
        from tools.service import cloud_worker
        cfg = {'engine_profile': {'translation_endpoint': 'http://127.0.0.1:8010/v1', 'engine_options': {'translation_qps': 64}}}
        with self.env(TRANSFORMX_TRANSLATION_API_KEY='local-key'):
            profile, env = cloud_worker.job_translation(cfg, {'operation': 'translation', 'translation_model': 'openai'})
            same, none = cloud_worker.job_translation(cfg, {'operation': 'translation'})
            pii, _ = cloud_worker.job_translation(cfg, {'operation': 'pii', 'translation_model': 'openai'})
            with self.assertRaises(cloud_worker.JobFailure) as caught:
                cloud_worker.job_translation(cfg, {'operation': 'translation', 'translation_model': 'removed'})
        self.assertEqual((profile['translation_endpoint'], profile['translation_model']), ('https://api.example/v1', 'gpt-5-mini'))
        self.assertEqual(cfg['engine_profile']['translation_endpoint'], 'http://127.0.0.1:8010/v1')  # worker profile unchanged
        self.assertEqual(env['TRANSFORMX_TRANSLATION_API_KEY_FILE'], str(self.key))
        self.assertNotIn('TRANSFORMX_TRANSLATION_API_KEY', env)
        self.assertEqual((same, none), (cfg['engine_profile'], None))
        self.assertIs(pii, cfg['engine_profile'])
        self.assertEqual(caught.exception.code, 'TRANSLATION_MODEL_UNAVAILABLE')

    def test_named_models_are_checked_in_the_file(self):
        bad = Path(self.temp.name) / 'bad.toml'
        bad.write_text('[translation_models.default]\nendpoint = "https://x/v1"\nmodel = "m"\n'
                       '[translation_models.GPT]\nendpoint = "https://x/v1"\nmodel = "m"\n'
                       '[translation_models.ok]\nendpoint = "ftp://x"\n[translation_models.extra]\nendpoint = "https://x/v1"\n'
                       'model = "m"\ncolour = "blue"\n', encoding='utf-8')
        problems = settings.check(bad, probe=False)
        text = '\n'.join(problems)
        self.assertIn('not "default"', text)
        self.assertIn('translation_models.ok.endpoint: must start with http', text)
        self.assertIn('translation_models.extra.colour: unknown setting', text)
        self.assertEqual(len(problems), 4)
