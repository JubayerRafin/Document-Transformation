import json,os,socket,sys,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from tests.temp_workspace import temporary_workspace
from tools.ocr.model_pool import ModelPool
from tools.ocr.persistent_worker import checked_paths
from transformation.ocr.service import managed_session,receive,encode,request,Session


class PersistentOCRTests(unittest.TestCase):
    def test_model_settings_match_and_only_identical_predictors_reused(self):
        loader=Mock(side_effect=lambda kind,options:object());pool=ModelPool(loader)
        options={'model_name':'detector','device':'gpu:0','limit_side_len':1536}
        first=pool.detection(**options)
        self.assertIs(first,pool.detection(**options))
        self.assertIsNot(first,pool.detection(**dict(options,limit_side_len=3072)))
        self.assertIsNot(first,pool.recognition(**options))
        self.assertEqual(loader.call_count,3)
        self.assertEqual(pool.metrics()['model_reuse_hits'],1)
        self.assertEqual(loader.call_args_list[0].args,('detection',options))

    def test_failed_model_construction_does_not_poison_pool(self):
        pool=ModelPool(Mock(side_effect=[RuntimeError('failed'),object()]))
        with self.assertRaises(RuntimeError):pool.detection(device='gpu:0')
        self.assertEqual(pool.metrics()['resident_predictors'],0)
        pool.detection(device='gpu:0');self.assertEqual(pool.loads,1)

    def test_outside_paths_and_overwrite_rejected(self):
        with temporary_workspace() as root:
            job=root/'job';job.mkdir();source=job/'source.pdf';source.write_bytes(b'fixture')
            message={'source':str(source),'output':str(job/'new')}
            self.assertEqual(checked_paths(message,root),(source,job/'new'))
            for field,value in [('source',root.parent/'outside.pdf'),('output',root.parent/'outside')]:
                with self.assertRaises(ValueError):checked_paths(dict(message,**{field:str(value)}),root)
            (job/'new').mkdir()
            with self.assertRaises(ValueError):checked_paths(message,root)

    def test_resolved_symlink_escape_rejected(self):
        if os.name!='posix':self.skipTest('Unix service path policy')
        with temporary_workspace() as root:
            (root/'link').symlink_to(root.parent,target_is_directory=True)
            with self.assertRaises(ValueError):checked_paths({'source':str(root/'link/x'),'output':str(root/'link/y')},root)

    def test_disabled_session_launches_nothing(self):
        with patch('transformation.ocr.service.subprocess.Popen') as launch:
            with managed_session('unused','unused',False) as session:self.assertIsNone(session)
            launch.assert_not_called()

    def test_bounded_messages_disconnect_and_payload_limits(self):
        connection=Mock();connection.recv.side_effect=[b'{"ready":',b'true}\n']
        self.assertTrue(receive(connection)['ready'])
        connection.recv.side_effect=[b'']
        with self.assertRaisesRegex(RuntimeError,'no automatic replay'):receive(connection)
        with self.assertRaises(ValueError):encode({'value':'x'*65536})

    def test_session_stops_only_owned_process(self):
        with temporary_workspace() as root:
            session=Session(sys.executable,root);session.process=Mock();session.process.poll.return_value=None
            session.close();session.process.terminate.assert_called_once();session.process.wait.assert_called_once()

    def test_busy_session_is_waited_for_not_reported_unresponsive(self):
        with temporary_workspace() as root:
            session=Session(sys.executable,root);session.process=Mock();session.process.poll.return_value=None
            with patch('transformation.ocr.service.health',return_value=True) as probe,\
                 patch('transformation.ocr.service.subprocess.Popen') as launch:
                session.ensure()
            self.assertGreaterEqual(probe.call_args.kwargs['timeout'],60);launch.assert_not_called()

    def test_live_session_that_never_answers_is_not_restarted(self):
        with temporary_workspace() as root:
            session=Session(sys.executable,root);session.process=Mock();session.process.poll.return_value=None
            with patch('transformation.ocr.service.health',side_effect=socket.timeout()),\
                 patch('transformation.ocr.service.subprocess.Popen') as launch:
                with self.assertRaisesRegex(RuntimeError,'unresponsive'):session.ensure()
            launch.assert_not_called()

    def test_session_that_exits_while_busy_is_started_again(self):
        with temporary_workspace() as root:
            session=Session(sys.executable,root);old=Mock();old.poll.side_effect=[None,124];session.process=old
            fresh=Mock();fresh.poll.return_value=None
            with patch('transformation.ocr.service.health',side_effect=[RuntimeError('disconnected'),True]),\
                 patch('transformation.ocr.service.subprocess.Popen',return_value=fresh) as launch:
                session.ensure()
            launch.assert_called_once();self.assertIs(session.process,fresh)

    def test_client_failure_never_replays_or_returns_success(self):
        connection=Mock();connection.__enter__=Mock(return_value=connection);connection.__exit__=Mock(return_value=False)
        connection.recv.return_value=b'{"status":"failed","error_type":"ValueError"}\n'
        with patch.dict(os.environ,{'TRANSFORMX_OCR_SOCKET':'private.sock'}),patch('socket.socket',return_value=connection),patch.object(socket,'AF_UNIX',1,create=True):
            with self.assertRaisesRegex(RuntimeError,'ValueError'):list(request('a','b','en',20))
        connection.sendall.assert_called_once()

    def test_gpu_client_routes_through_existing_archive_validation(self):
        from transformation.ocr.paddle_pipeline import run_server_gpu_ocr
        with temporary_workspace() as root:
            evidence=root/'out'
            events=[{'stage':'gpu_ocr'},{'stage':'service_completed','service_metrics':{'model_loads':1},'client_seconds':1}]
            report={'total_seconds':1,'detection_seconds':.8,'recognition_seconds':.2}
            with patch.dict(os.environ,{'TRANSFORMX_OCR_SOCKET':'private.sock'}),\
                 patch('transformation.ocr.service.request',return_value=iter(events)) as send,\
                 patch('transformation.ocr.remote_ocr.unpack',return_value=report) as unpack,\
                 patch('transformation.ocr.paddle_pipeline.subprocess.Popen') as launch:
                result=list(run_server_gpu_ocr(root/'source.pdf',evidence,sys.executable,20,'en'))
            self.assertEqual(result[-1]['stage'],'completed');self.assertTrue(result[-1]['persistent'])
            launch.assert_not_called();unpack.assert_called_once()
            self.assertTrue(json.loads((evidence/'ocr-timing.json').read_text())['persistent'])

    def test_fresh_profile_disables_result_reuse_without_changing_other_options(self):
        from backend.engine_job import local_cache_options
        result=local_cache_options({'ignore_cache':True,'ocr_cache_mode':'fresh','http_keepalive':True},{},Path('jobs/new'))
        self.assertTrue(result['ignore_cache']);self.assertIsNone(result['ocr_cache_root']);self.assertTrue(result['http_keepalive'])

    def test_resource_probe_includes_separate_persistent_ocr_memory(self):
        from transformation.resource_probe import monitor
        with temporary_workspace() as root,patch.dict(os.environ,{'TRANSFORMX_OCR_SERVICE_PID':'987654'}),\
             patch('transformation.resource_probe.tree_rss',side_effect=lambda pid:999 if pid==987654 else 123),\
             patch('transformation.resource_probe.subprocess.run',side_effect=OSError):
            with monitor(root,True):pass
            report=json.loads((root/'resource-metrics.json').read_text())
        self.assertEqual(report['peak_tree_rss_bytes'],123);self.assertEqual(report['peak_ocr_service_rss_bytes'],999)


class GpuCacheReleaseTests(unittest.TestCase):
    def test_paddle_cache_is_released_when_paddle_is_loaded_and_ignored_otherwise(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from tools.ocr.persistent_worker import release_gpu_cache
        with patch.dict(sys.modules, {'paddle': None}):
            sys.modules.pop('paddle'); release_gpu_cache()            # not loaded: nothing to do
        empty = MagicMock()
        with patch.dict(sys.modules, {'paddle': SimpleNamespace(device=SimpleNamespace(cuda=SimpleNamespace(empty_cache=empty)))}):
            release_gpu_cache()
        empty.assert_called_once_with()
        broken = MagicMock(side_effect=RuntimeError('no CUDA device'))
        with patch.dict(sys.modules, {'paddle': SimpleNamespace(device=SimpleNamespace(cuda=SimpleNamespace(empty_cache=broken)))}):
            release_gpu_cache()                                       # CPU build: no error


if __name__=='__main__':unittest.main()
