from pathlib import Path
import unittest
from unittest.mock import patch
from backend_examples.worker_adapter import EngineSettings, execute
from tests.temp_workspace import temporary_workspace


class BackendAdapterTests(unittest.TestCase):
    def test_client_cannot_override_server_endpoint(self):
        with self.assertRaisesRegex(ValueError, 'Only document'):
            execute(b'x', 'a.pdf', {'operation':'translation','target_language':'ko','translation_endpoint':'http://other'},
                    EngineSettings(Path('runs'), 'python'), lambda _:None, lambda _:'id')

    def test_pii_stays_source_language_and_does_not_expose_internal_paths(self):
        with temporary_workspace() as directory:
            output = directory/'result.pdf'; output.write_bytes(b'fixture')
            events = [{'stage':'complete','status':'generated','seconds':1,'output_file':str(output),'error':'private'}]
            public=[]
            with patch('backend_examples.worker_adapter.run_workflow',return_value=iter(events)) as engine:
                result=execute(b'x','a.pdf',{'operation':'pii','source_language':'en','pii_categories':['Names']},
                    EngineSettings(directory,'private-python'),public.append,lambda path:'opaque-artifact')
            self.assertEqual(engine.call_args.kwargs['target'],'en')
            self.assertEqual(engine.call_args.args[2],'PII only')
            self.assertEqual(result['review_status'],'requires_review')
            self.assertNotIn('private',str(public));self.assertNotIn(str(output),str(result))

    def test_failed_engine_never_publishes_artifact(self):
        with patch('backend_examples.worker_adapter.run_workflow',return_value=iter([
            {'stage':'failed','status':'failed','seconds':1,'error':'private trace'}])):
            with patch('builtins.print') as publish:
                result=execute(b'x','a.pdf',{'operation':'translation','target_language':'ko'},
                    EngineSettings(Path('runs'),'python'),lambda _:None,publish)
                publish.assert_not_called();self.assertEqual(result['status'],'failed')

    def test_output_outside_storage_is_not_published(self):
        with temporary_workspace() as directory:
            output=directory/'outside.pdf';output.write_bytes(b'fixture')
            with patch('backend_examples.worker_adapter.run_workflow',return_value=iter([
                    {'stage':'complete','status':'generated','seconds':1,'output_file':str(output)}])):
                with self.assertRaisesRegex(ValueError,'outside'):
                    execute(b'x','a.pdf',{'operation':'translation','target_language':'ko'},
                        EngineSettings(directory/'job','python'),lambda _:None,lambda _:'id')

    def test_path_filename_rejected(self):
        with self.assertRaisesRegex(ValueError,'filename'):
            execute(b'x','../a.pdf',{'operation':'translation','target_language':'ko'},
                    EngineSettings(Path('runs'),'python'),lambda _:None,lambda _:'id')
