import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import pymupdf
from transformation.ocr.remote_ocr import run_remote,background_command


class TransportTests(unittest.TestCase):
    def inputs(self,root):
        source=root/'source.pdf'; key=root/'key';key.write_text('test')
        with pymupdf.open() as pdf:
            pdf.new_page();pdf.save(source)
        return source,{'host':'192.0.2.1','port':22,'key':str(key)}

    def test_detached_job_closes_all_ssh_descriptors_and_records_exit(self):
        command=background_command('/workspace/test','false')
        self.assertIn('< /dev/null &',command)
        self.assertIn('2>&1',command)
        self.assertIn('result=$?',command)
        self.assertIn('exit.status',command)
        self.assertNotIn('exec timeout',command)

    def test_polling_downloads_only_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source,config=self.inputs(root)
            process=MagicMock();process.poll.return_value=0;process.returncode=0
            with patch('transformation.ocr.remote_ocr.subprocess.Popen',return_value=process),patch('transformation.ocr.remote_ocr.subprocess.check_output',side_effect=['RUNNING','0']) as status,patch('transformation.ocr.remote_ocr.time.sleep'),patch('transformation.ocr.remote_ocr.unpack',return_value={'total_seconds':9.4}) as unpack:
                events=list(run_remote(source,root/'out',config))
            self.assertEqual(status.call_count,2)
            self.assertTrue(all(c.kwargs['timeout']==20 for c in status.call_args_list))
            unpack.assert_called_once();self.assertEqual(events[-1]['stage'],'completed')
            self.assertEqual(events[-1]['gpu_seconds'],9.4)

    def test_failure_cannot_download_or_claim_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source,config=self.inputs(root)
            process=MagicMock();process.poll.return_value=0;process.returncode=0
            with patch('transformation.ocr.remote_ocr.subprocess.Popen',return_value=process),patch('transformation.ocr.remote_ocr.subprocess.check_output',side_effect=['1','model failed']),patch('transformation.ocr.remote_ocr.subprocess.run'),patch('transformation.ocr.remote_ocr.unpack') as unpack:
                with self.assertRaisesRegex(RuntimeError,'status 1'):list(run_remote(source,root/'out',config))
                unpack.assert_not_called()
            self.assertEqual((root/'out/gpu_ocr.log').read_text(),'model failed')


if __name__=='__main__':unittest.main()
