import sys
import types
import unittest
from unittest.mock import patch


class Limiter:
    def __init__(self):
        self.waits, self.qps = 0, None

    def wait(self, *args):
        self.waits += 1

    def set_max_qps(self, qps):
        self.qps = qps


class RequestRateLimitTests(unittest.TestCase):
    def test_only_requests_sent_to_the_model_wait_for_the_rate_limit(self):
        from transformation.llm import ollama_native
        vendor = types.ModuleType('babeldoc.translator.translator')
        limiter = Limiter()
        vendor._translate_rate_limiter = limiter
        package, translator = types.ModuleType('babeldoc'), types.ModuleType('babeldoc.translator')
        package.translator, translator.translator = translator, vendor
        modules = {'babeldoc': package, 'babeldoc.translator': translator, 'babeldoc.translator.translator': vendor}
        with patch.dict(sys.modules, modules), \
             patch.object(ollama_native, '_REQUEST_LIMITER', None):
            self.assertTrue(ollama_native.limit_requests_only())
            self.assertTrue(ollama_native.limit_requests_only())          # installing twice is harmless
            for _ in range(5):                                             # paragraphs BabelDOC handles
                vendor._translate_rate_limiter.wait()
            vendor._translate_rate_limiter.set_max_qps(64)                 # BabelDOC's --qps still applies
            self.assertEqual((limiter.waits, limiter.qps), (0, 64))
            with patch('transformation.llm.endpoints.check', side_effect=RuntimeError('stop after the wait')):
                with self.assertRaises(RuntimeError):
                    ollama_native.chat('http://127.0.0.1:8010/v1', 'm', [])
            # chat() checks the endpoint first, then waits; a refused endpoint sends nothing.
            self.assertEqual(limiter.waits, 0)
            with patch('transformation.llm.endpoints.check'), \
                 patch('transformation.llm.gpu_share.ensure', side_effect=RuntimeError('stop before sending')):
                with self.assertRaises(RuntimeError):
                    ollama_native.chat('http://127.0.0.1:8010/v1', 'm', [])
            self.assertEqual(limiter.waits, 1)


if __name__ == '__main__':
    unittest.main()
