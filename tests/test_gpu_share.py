"""GPU sharing between model servers (vLLM with sleep mode, and Ollama). No servers needed."""
import os
import unittest
import urllib.error
from unittest.mock import patch

from transformation.llm import gpu_share

PII, TRANSLATION, OLLAMA = 'http://127.0.0.1:8000', 'http://127.0.0.1:8001', 'http://127.0.0.1:11434'


class FakeServers:
    """vLLM servers sleep/wake; Ollama unloads a model with keep_alive 0. Waking while another
    model still holds the GPU fails, as on a 24 GB card."""
    def __init__(self, awake=(PII,), loaded=()):
        self.awake, self.loaded, self.calls = set(awake), list(loaded), []

    def __call__(self, url, data=None, method='GET', timeout=120):
        base, path = url[:21], url[21:]
        self.calls.append((method, url[16:]))
        if path == '/is_sleeping': return {'is_sleeping': base not in self.awake}
        if path in ('/sleep?level=1', '/sleep?level=2'): self.awake.discard(base); return {}
        if path == '/wake_up?tags=weights' or path == '/collective_rpc': return {}
        if path in ('/wake_up', '/wake_up?tags=kv_cache'):
            if self.awake or self.loaded: raise urllib.error.HTTPError(url, 500, 'out of memory', {}, None)
            self.awake.add(base); return {}
        if url == OLLAMA + '/api/ps': return {'models': [{'name': n} for n in self.loaded]}
        if url == OLLAMA + '/api/generate' and data.get('keep_alive') == 0:
            self.loaded.remove(data['model']); return {}
        raise AssertionError('unexpected call ' + url)


class GpuShareTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        # Never the machine's shared record: tests may run on a server with a live worker.
        levels = patch.object(gpu_share, 'LEVELS_PATH', os.path.join(folder.name, 'levels.json'))
        levels.start(); self.addCleanup(levels.stop)

    def run_with(self, servers, endpoint, shared=(PII, TRANSLATION)):
        env = {gpu_share.SWAP_ENV: ','.join(shared)} if shared else {}
        with patch.dict(os.environ, env, clear=True), patch.object(gpu_share, '_request', side_effect=servers):
            gpu_share.ensure(endpoint)

    def test_ollama_call_puts_every_shared_server_to_sleep(self):
        servers = FakeServers(awake=(PII,))
        self.run_with(servers, OLLAMA + '/v1', shared=(PII,))
        self.assertEqual(servers.awake, set())

    def test_switch_between_two_vllm_servers_sleeps_one_before_waking_the_other(self):
        servers = FakeServers(awake=(PII,))
        self.run_with(servers, TRANSLATION + '/v1')
        self.assertEqual(servers.awake, {TRANSLATION})
        posts = [u for m, u in servers.calls if m == 'POST']
        self.assertEqual(posts, [':8000/sleep?level=1', ':8001/wake_up'])

    def test_a_level_two_server_reloads_its_weights_on_wake(self):
        servers = FakeServers(awake=(PII,))
        self.run_with(servers, TRANSLATION + '/v1', shared=(PII, TRANSLATION + '#2'))
        posts = [u for m, u in servers.calls if m == 'POST']
        self.assertEqual(posts, [':8000/sleep?level=1', ':8001/wake_up?tags=weights', ':8001/collective_rpc',
                                 ':8001/wake_up?tags=kv_cache'])
        servers.calls.clear()
        self.run_with(servers, PII + '/v1', shared=(PII, TRANSLATION + '#2'))
        self.assertEqual([u for m, u in servers.calls if m == 'POST'], [':8001/sleep?level=2', ':8000/wake_up'])

    def test_a_server_left_half_awake_by_an_interrupted_switch_is_put_back_to_sleep(self):
        servers = FakeServers(awake=())                       # both report sleeping
        self.run_with(servers, PII + '/v1', shared=(PII, TRANSLATION + '#2'))
        posts = [u for m, u in servers.calls if m == 'POST']
        self.assertEqual(posts, [':8001/sleep?level=2', ':8000/wake_up'])

    def test_a_server_slept_at_level_two_is_reloaded_even_when_another_process_lists_it_without_the_marker(self):
        servers = FakeServers(awake=(TRANSLATION,))
        self.run_with(servers, PII + '/v1', shared=(PII, TRANSLATION + '#2'))      # the worker: weights discarded
        servers.calls.clear()
        self.run_with(servers, TRANSLATION + '/v1', shared=(PII, TRANSLATION))     # a process without '#2'
        posts = [u for m, u in servers.calls if m == 'POST']
        self.assertEqual(posts, [':8000/sleep?level=1', ':8001/wake_up?tags=weights', ':8001/collective_rpc',
                                 ':8001/wake_up?tags=kv_cache'])
        servers.calls.clear()
        # That process sleeps it at its own level, and the record says so: a later wake by the
        # worker ('#2') reloads the weights anyway, which is slower but always correct.
        self.run_with(servers, PII + '/v1', shared=(PII, TRANSLATION))
        self.assertEqual([u for m, u in servers.calls if m == 'POST'][0], ':8001/sleep?level=1')
        self.assertEqual(gpu_share._recorded().get(TRANSLATION), 1)
        self.assertNotIn(PII, gpu_share._recorded())

    def test_vllm_call_unloads_ollama_first(self):
        servers = FakeServers(awake=(), loaded=['translategemma:12b'])
        self.run_with(servers, PII + '/v1', shared=(PII,))
        self.assertEqual(servers.awake, {PII}); self.assertEqual(servers.loaded, [])
        posts = [u for m, u in servers.calls if m == 'POST']
        self.assertEqual(posts, [':11434/api/generate', ':8000/wake_up'])

    def test_no_switch_when_the_server_already_has_the_gpu(self):
        for endpoint, servers in ((PII, FakeServers(awake=(PII,))), (OLLAMA, FakeServers(awake=()))):
            self.run_with(servers, endpoint)
            self.assertFalse([c for c in servers.calls if c[0] == 'POST'], endpoint)

    def test_nothing_happens_without_sharing_enabled(self):
        servers = FakeServers()
        self.run_with(servers, OLLAMA, shared=())
        self.assertEqual(servers.calls, [])

    def test_a_failed_wake_is_reported_not_ignored(self):
        servers = FakeServers(awake=())
        with patch.object(gpu_share, '_ollama_loaded', return_value=[]), \
                patch.object(FakeServers, '__call__', side_effect=lambda url, data=None, method='GET', timeout=120:
                             {'is_sleeping': True} if url.endswith('/is_sleeping') else
                             {} if '/sleep?' in url else (_ for _ in ()).throw(urllib.error.HTTPError(url, 500, 'oom', {}, None))):
            with self.assertRaisesRegex(RuntimeError, 'could not wake'):
                self.run_with(servers, PII)

    def test_transport_gives_the_gpu_to_the_server_it_calls(self):
        from transformation.llm import ollama_native
        called = []
        with patch.object(gpu_share, 'ensure', side_effect=called.append), \
                patch.dict(os.environ, {ollama_native.OPENAI_SERVERS_ENV: PII}, clear=True), \
                patch.object(ollama_native, '_openai_chat', return_value=('{}', {})), \
                patch('urllib.request.urlopen', side_effect=OSError('stop')):
            ollama_native.chat(PII + '/v1', 'pii-model', [{'role': 'user', 'content': 'x'}])
            with self.assertRaises(Exception):
                ollama_native.chat(OLLAMA + '/v1', 'translategemma:12b', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(called, [PII, OLLAMA])


if __name__ == '__main__':
    unittest.main()
