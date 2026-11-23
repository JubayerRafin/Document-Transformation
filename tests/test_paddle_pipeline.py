import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from transformation.ocr.paddle_pipeline import run_ocr

class PaddlePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.pdf'

    def test_digital_pdf_rejected_before_model_start(self):
        with pymupdf.open() as pdf:
            page=pdf.new_page()
            page.insert_text((50,50),'native text')
            pdf.save(self.source)
        import sys
        with patch('transformation.ocr.paddle_pipeline.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'image-only'):
                list(run_ocr(self.source,self.root/'ocr',sys.executable))
            launch.assert_not_called()

    def test_native_failure_is_not_completed(self):
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.save(self.source)
        import sys
        with patch('transformation.ocr.paddle_pipeline.subprocess.Popen') as launch:
            launch.return_value.poll.return_value = -1073741819
            launch.return_value.returncode = -1073741819
            with self.assertRaisesRegex(RuntimeError, 'detecting failed'):
                list(run_ocr(self.source,self.root/'ocr',sys.executable))
        self.assertFalse((self.root/'ocr/diagnostic.json').exists())

    def test_generator_close_stops_worker(self):
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.save(self.source)
        import sys
        with patch('transformation.ocr.paddle_pipeline.subprocess.Popen') as launch:
            launch.return_value.poll.return_value = None
            generator=run_ocr(self.source,self.root/'ocr',sys.executable)
            self.assertEqual(next(generator)['stage'],'detecting')
            generator.close()
            launch.return_value.terminate.assert_called_once()


class GpuMemoryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _run(self, error):
        import sys
        def gpu(*args):
            yield {'stage': 'gpu_ocr', 'device': 'gpu:0'}
            raise error
        def cpu(source, folder, python, timeout, language):
            yield {'stage': 'completed', 'recognition_dir': str(Path(folder)/'recognition'), 'device': 'cpu'}
        with patch.dict('os.environ', {'TRANSFORMX_OCR_DEVICE': 'gpu:0'}), \
             patch('transformation.ocr.paddle_pipeline.run_server_gpu_ocr', gpu), \
             patch('transformation.ocr.paddle_pipeline.run_ocr_cpu', cpu):
            return list(run_ocr(self.root/'input.pdf', self.root/'ocr', sys.executable))

    def test_gpu_out_of_memory_reads_the_page_on_the_cpu(self):
        events = self._run(RuntimeError('Persistent GPU OCR failed; inspect private OCR stage logs. Error type: MemoryError'))
        self.assertEqual([e['stage'] for e in events], ['gpu_ocr', 'gpu_memory_fallback', 'completed'])
        self.assertEqual(events[-1]['recognition_dir'], str(self.root/'ocr'/'cpu-fallback'/'recognition'))

    def test_other_gpu_failures_are_not_hidden(self):
        with self.assertRaisesRegex(RuntimeError, 'ValueError'):
            self._run(RuntimeError('Persistent GPU OCR failed; inspect private OCR stage logs. Error type: ValueError'))
