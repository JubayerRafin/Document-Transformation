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
