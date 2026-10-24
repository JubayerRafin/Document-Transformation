import json
import unittest
from unittest.mock import patch
import pymupdf
from tests.temp_workspace import temporary_workspace
from transformation.pdf.figure_worker import main


class FigureTests(unittest.TestCase):
    def fixture(self,root,slots):
        (root/'output').mkdir()
        with pymupdf.open() as pdf:
            page=pdf.new_page(width=300,height=100)
            page.insert_text((20,45),'Device 481',fontsize=12)
            image=page.get_pixmap()
            with pymupdf.open() as raster:
                raster.new_page(width=300,height=100).insert_image((0,0,300,100),pixmap=image)
                raster.save(root/'source.pdf')
        regions=[{'id':'a','text':'Device 481','bbox':[20,30,120,50],
                  'metadata':{'requires_review':False}},
                 {'id':'blocked','text':'uncertain','bbox':[170,30,250,50],
                  'metadata':{'requires_review':True}}]
        (root/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':regions}]}))
        (root/'verified-slots.json').write_text(json.dumps([regions[0]] if slots else []))
        (root/'extraction-context.json').write_text('{}')
        (root/'manifest.json').write_text(json.dumps(dict(target_language='ko',source_language='en',
            model='translategemma:12b',endpoint='http://localhost:11435/v1',protected_values=[],document_context='')))

    def test_only_verified_slots_are_translated_and_source_ids_survive(self):
        with temporary_workspace() as root:
            self.fixture(root,True)
            with patch('transformation.llm.ollama_native.translate_paragraph',return_value=('장치 481',{})) as translate:
                main(root)
            translate.assert_called_once()
            records=json.loads((root/'region-translations.json').read_text())
            self.assertEqual(set(records),{'a'})
            self.assertEqual(records['a']['source_text'],'Device 481')
            self.assertTrue((root/'output/scan-reconstructed.pdf').is_file())
            report=json.loads((root/'scan-reconstruction.json').read_text())
            self.assertTrue(any(r['id']=='blocked' and r['status']=='original_preserved' for r in report['regions']))

    def test_changed_identifier_does_not_produce_accepted_translation(self):
        with temporary_workspace() as root:
            self.fixture(root,True)
            with patch('transformation.llm.ollama_native.translate_paragraph',return_value=('장치 482',{})):
                with self.assertRaisesRegex(RuntimeError,'failed validation'):main(root)
            self.assertEqual(json.loads((root/'region-translations.json').read_text()),{})
            self.assertFalse((root/'output/scan-reconstructed.pdf').exists())

    def test_all_uncertain_figure_has_no_model_calls(self):
        with temporary_workspace() as root:
            self.fixture(root,False)
            with patch('transformation.llm.ollama_native.translate_paragraph') as translate:
                main(root)
            translate.assert_not_called()
            self.assertEqual(json.loads((root/'scan-reconstruction.json').read_text())['replaced'],0)

    def test_stage_timing_preserves_return_values_and_exceptions(self):
        from types import SimpleNamespace
        from transformation.pdf.stage_timing import install
        class Monitor:
            def stage_start(self,name,total):return SimpleNamespace(name=name)
            def stage_done(self,stage):raise ValueError('vendor failure')
        with patch('transformation.llm.ollama_native.record_progress') as progress:
            install(Monitor);instance=Monitor();stage=instance.stage_start('Parse PDF',1)
            with self.assertRaisesRegex(ValueError,'vendor failure'):instance.stage_done(stage)
            self.assertEqual(progress.call_args.args,('pdf_stage_end',))
            self.assertEqual(progress.call_args.kwargs['stage'],'Parse PDF')
