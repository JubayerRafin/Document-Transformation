import json,unittest
from unittest.mock import patch
from tests.temp_workspace import temporary_workspace
from transformation.runtime_metrics import summarize
from transformation.resource_probe import monitor


class RuntimeMetricTests(unittest.TestCase):
    def test_benchmark_snapshot_hashes_private_context_instead_of_recording_it(self):
        from io import BytesIO
        from types import SimpleNamespace as NS
        from transformation.resource_probe import snapshot
        with temporary_workspace() as root,patch('urllib.request.urlopen',return_value=BytesIO(b'{"models":[]}')),\
                patch('transformation.resource_probe.subprocess.run',return_value=NS(stdout='GPU, 32000 MiB, driver')):
            source=root/'source.pdf';source.write_bytes(b'fixture')
            snapshot(root,source,{'document_context':'PRIVATE_CONTEXT_SENTINEL','protected_values':['PRIVATE_VALUE_SENTINEL']})
            data=(root/'benchmark-metadata.json').read_text()
        self.assertNotIn('PRIVATE_CONTEXT_SENTINEL',data)
        self.assertNotIn('PRIVATE_VALUE_SENTINEL',data)
        self.assertIn('context_and_protection_sha256',data)

    def test_parallel_request_durations_are_not_wall_time(self):
        rows=[{'event':'transport_completed','time':12,'seconds':2,'eval_count':30,'eval_duration':1_000_000_000},
              {'event':'transport_completed','time':13,'seconds':2,'eval_count':30,'eval_duration':1_000_000_000}]
        r=summarize(rows)
        self.assertEqual(r['max_observed_in_flight'],2)
        self.assertEqual(r['decode_tokens_per_second'],30)
        self.assertEqual(r['http_seconds_sum'],4)
        self.assertEqual(r['mean_http_seconds'],2)

    def test_missing_measurements_are_unknown_and_summary_omits_contents(self):
        self.assertFalse(summarize([])['timing_available'])
        r=summarize([{'event':'transport_completed','seconds':1,'source':'PRIVATE_SENTINEL'}])
        self.assertIsNone(r['decode_tokens_per_second'])
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(r))

    def test_resource_probe_can_be_disabled_without_gpu_or_output(self):
        with temporary_workspace() as root,patch('transformation.resource_probe.subprocess.run') as run:
            with monitor(root,False):pass
            run.assert_not_called();self.assertFalse((root/'resource-metrics.json').exists())

    def test_resource_probe_records_unavailable_gpu_without_failing_job(self):
        with temporary_workspace() as root,patch('transformation.resource_probe.subprocess.run',side_effect=OSError),\
                patch('transformation.resource_probe.tree_rss',return_value=1234):
            with monitor(root,True):pass
            r=json.loads((root/'resource-metrics.json').read_text())
        self.assertEqual(r['peak_tree_rss_bytes'],1234)
        self.assertIsNone(r['peak_visible_gpu_mib'])
