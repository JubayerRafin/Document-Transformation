import sys
import unittest
from unittest.mock import patch,MagicMock
import pymupdf

from tests.temp_workspace import temporary_workspace
from transformation.pdf.runner import run_pdf
from transformation.pdf.stage_timing import install_rate_timing
from transformation.remote_worker import server_options


class PacingTests(unittest.TestCase):
    def test_invalid_rates_rejected_before_processing(self):
        for value in (True,0,17,1.5,'8'):
            with self.assertRaisesRegex(ValueError,'translation_qps'):
                list(run_pdf(b'not a PDF','unseen.pdf',python=sys.executable,model='translategemma:12b',translation_qps=value))

    def test_rate_changes_pacing_but_keeps_one_worker(self):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((30,30),'Unseen document heading')
            data=pdf.tobytes()
        with temporary_workspace() as root,patch('transformation.pdf.runner.subprocess.check_output',return_value='0.6.4'),patch('transformation.pdf.runner.subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value=1;spawn.return_value.returncode=1
            for rate in (1,8):
                events=list(run_pdf(data,'unseen.pdf',python=sys.executable,model='translategemma:12b',root=root,translation_qps=rate))
                command=spawn.call_args.args[0]
                self.assertEqual(command[command.index('--qps')+1],str(rate))
                self.assertEqual(command[command.index('--pool-max-workers')+1],'1')
                self.assertEqual(events[-1]['translation_qps'],rate)

    def test_limiter_measurement_preserves_return_and_exception(self):
        class Limiter:
            def wait(self,fail=False):
                if fail:raise ValueError('test failure')
                return 'original'
        install_rate_timing(Limiter);install_rate_timing(Limiter)
        with patch('transformation.pdf.stage_timing.time.monotonic',side_effect=[1,1.2,2,2.1]),patch('transformation.llm.ollama_native.record_progress') as record:
            self.assertEqual(Limiter().wait(),'original')
            with self.assertRaisesRegex(ValueError,'test failure'):Limiter().wait(True)
            self.assertEqual(record.call_count,2)
            self.assertEqual(record.call_args_list[0].kwargs['seconds'],.2)

    def test_old_rate_can_be_selected_for_controlled_comparison(self):
        self.assertEqual(server_options({},'/workspace/job')['translation_qps'],8)
        self.assertEqual(server_options({'translation_qps':1},'/workspace/job')['translation_qps'],1)

    def test_opt_in_parallel_workers_reach_vendor_command(self):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((30,30),'Independent heading')
            data=pdf.tobytes()
        with temporary_workspace() as root,patch('transformation.pdf.runner.subprocess.check_output',return_value='0.6.4'),patch('transformation.pdf.runner.subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value=1;spawn.return_value.returncode=1
            events=list(run_pdf(data,'new.pdf',python=sys.executable,model='translategemma:12b',root=root,
                                translation_workers=2,ollama_parallelism=2))
            command=spawn.call_args.args[0]
            self.assertEqual(command[command.index('--pool-max-workers')+1],'2')
            self.assertEqual(events[-1]['translation_workers'],2)
            self.assertEqual(events[-1]['ollama_parallelism_declared'],2)
            self.assertEqual(spawn.call_args.kwargs['env']['TRANSFORMX_TRANSLATION_WORKERS'],'2')

    def test_layout_no_spin_keeps_other_runtime_options_unchanged(self):
        from types import SimpleNamespace
        from transformation.pdf.layout_cpu import install
        runtime=MagicMock();runtime._transformx_no_spin=False
        module=SimpleNamespace(onnxruntime=runtime)
        with patch.dict('os.environ',{'TRANSFORMX_LAYOUT_IDLE_SPINNING':'0'}):
            self.assertTrue(install(module));self.assertTrue(install(module))
        module.onnxruntime.InferenceSession(b'model',providers=['CPUExecutionProvider'])
        options=runtime.SessionOptions.return_value
        self.assertEqual(options.add_session_config_entry.call_count,2)
        runtime.InferenceSession.assert_called_once_with(b'model',providers=['CPUExecutionProvider'],sess_options=options)
        explicit=object()
        module.onnxruntime.InferenceSession(b'model',sess_options=explicit)
        self.assertIs(runtime.InferenceSession.call_args.kwargs['sess_options'],explicit)
        self.assertEqual(runtime.SessionOptions.call_count,1)

    def test_review_archive_omits_only_work_files_and_preserves_server_data(self):
        import json,zipfile
        from transformation.remote_worker import pack_result
        with temporary_workspace() as root:
            paths={'terminal.json':'{}','runs/one/output/result.pdf':'final',
                'runs/one/source-ir.json':'{}','runs/one/engine/job/process.log':'log',
                'runs/one/engine/job/work/subset-input.pdf':'large temporary file',
                'runs/one/engine/job/work/translate_tracking.json':'{}',
                'ocr-evidence/key/attempt/gpu-run.json':'{}'}
            for name,body in paths.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
            pack_result(root,'review')
            with zipfile.ZipFile(root/'result.zip') as archive:
                self.assertNotIn('runs/one/engine/job/work/subset-input.pdf',archive.namelist())
                for name in paths:
                    if not name.endswith('subset-input.pdf'):self.assertEqual(archive.read(name).decode(),paths[name])
                self.assertEqual(json.loads(archive.read('package-info.json'))['omitted_temporary_files'],1)
            self.assertEqual((root/'runs/one/engine/job/work/subset-input.pdf').read_text(),'large temporary file')
            pack_result(root,'full')
            with zipfile.ZipFile(root/'result.zip') as archive:
                self.assertIn('runs/one/engine/job/work/subset-input.pdf',archive.namelist())
