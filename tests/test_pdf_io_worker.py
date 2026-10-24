import multiprocessing
import unittest
import pymupdf
from tests.temp_workspace import temporary_workspace
from transformation.pdf.io_worker import subset_fonts,save_clean


class IOTests(unittest.TestCase):
    def test_spawn_targets_preserve_text_geometry_and_rendering(self):
        with temporary_workspace() as root:
            source=root/'source.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=300,height=150)
                page.insert_text((20,40),'Unseen 481',fontsize=12)
                page.draw_rect((20,70,160,110),fill=(.2,.6,.8))
                pdf.save(source)
            with pymupdf.open(source) as pdf:
                expected=pdf[0].get_pixmap().samples;expected_text=pdf[0].get_text()
            for target in (subset_fonts,save_clean):
                output=root/(target.__name__+'.pdf')
                process=multiprocessing.get_context('spawn').Process(target=target,args=(str(source),str(output)))
                process.start();process.join(timeout=20)
                if process.is_alive():process.kill();process.join();self.fail('I/O worker timed out')
                self.assertEqual(process.exitcode,0)
                with pymupdf.open(output) as pdf:
                    self.assertEqual(pdf[0].get_text(),expected_text)
                    self.assertEqual(pdf[0].get_pixmap().samples,expected)

    def test_invalid_input_returns_nonzero_without_output(self):
        with temporary_workspace() as root:
            output=root/'output.pdf'
            process=multiprocessing.get_context('spawn').Process(target=save_clean,args=(str(root/'absent.pdf'),str(output)))
            process.start();process.join(timeout=10)
            if process.is_alive():process.kill();process.join();self.fail('I/O worker timed out')
            self.assertNotEqual(process.exitcode,0)
            self.assertFalse(output.exists())
