"""Real private-socket lifecycle tests with fake OCR; never load Paddle or Ollama."""
import json,multiprocessing,os,socket,tempfile,time,unittest
from pathlib import Path
from transformation.ocr.service import encode,receive,health,request
from tools.ocr.persistent_worker import serve
from unittest.mock import patch


def fake_ocr(source,output,language,model_pool):
    if language=='timeout':time.sleep(5)
    if language=='fail':raise ValueError('private source text must not escape')
    model_pool.loader=lambda kind,options:object()
    model_pool.detection(device='gpu:0',limit_side_len=1536)
    output.mkdir();(output/'fixture.json').write_text(json.dumps({'preserved':True}))


@unittest.skipUnless(os.name=='posix','Linux production socket lifecycle')
class OCRServiceLinuxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='txocr-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);os.chmod(self.root,0o700)
        self.path=self.root/'worker.sock';self.job=self.root/'job';self.job.mkdir()
        self.source=self.job/'source.pdf';self.source.write_bytes(b'fixture')
        self.process=multiprocessing.Process(target=serve,args=(self.path,self.root,os.getpid(),10,fake_ocr))
        self.process.start();self.addCleanup(self.stop)
        started=time.monotonic()
        while time.monotonic()-started<10:
            try:
                if health(self.path):break
            except OSError:time.sleep(.03)
        else:self.fail('Private socket did not start')

    def stop(self):
        if self.process.is_alive():self.process.terminate()
        self.process.join(5)

    def submit(self,name,language='en',timeout=3):
        with patch.dict(os.environ,{'TRANSFORMX_OCR_SOCKET':str(self.path)}):
            return list(request(self.source,self.job/name,language,timeout))

    def test_two_jobs_reuse_model_and_failures_never_leak_text(self):
        first=self.submit('one')[-1];second=self.submit('two')[-1]
        self.assertEqual(first['service_metrics']['model_loads'],1)
        self.assertEqual(second['service_metrics']['model_loads'],0)
        self.assertEqual(second['service_metrics']['model_reuse_hits'],1)
        with self.assertRaisesRegex(RuntimeError,'Error type: ValueError') as caught:self.submit('bad','fail')
        self.assertNotIn('private source text',str(caught.exception))
        self.assertTrue(health(self.path));self.assertEqual(self.submit('three')[-1]['stage'],'service_completed')

    def test_watchdog_stops_only_timed_out_ocr_child(self):
        with self.assertRaises((RuntimeError,TimeoutError)):self.submit('slow','timeout',.15)
        self.process.join(3);self.assertEqual(self.process.exitcode,124)

    def test_control_path_rejection_does_not_create_output(self):
        outside=self.root.parent/'not-a-job'
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.path));connection.sendall(encode({'action':'ocr','source':str(self.source),
                'output':str(outside),'language':'en','timeout':2}))
            reply=receive(connection)
        self.assertEqual(reply,{'status':'failed','error_type':'ValueError'});self.assertFalse(outside.exists())


if __name__=='__main__':unittest.main()
