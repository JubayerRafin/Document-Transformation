import importlib.util
import json
from pathlib import Path
import tarfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch,MagicMock

import pymupdf

from tests.temp_workspace import temporary_workspace
from transformation.profiling import timed


class RuntimeTests(unittest.TestCase):
    def test_ollama_telemetry_keeps_timing_but_not_prompt_content(self):
        from io import BytesIO
        from transformation.llm.ollama_native import chat
        response={'done':True,'message':{'content':'result'},'load_duration':123,'eval_duration':456,'eval_count':7}
        with patch('urllib.request.urlopen',return_value=BytesIO(json.dumps(response).encode())),\
             patch('transformation.llm.ollama_native.record_progress') as progress:
            text,_=chat('http://127.0.0.1:11435/v1','example',[{'role':'user','content':'private source'}])
        self.assertEqual(text,'result');self.assertEqual(progress.call_args.kwargs['load_duration'],123)
        self.assertNotIn('private source',str(progress.call_args))

    def test_failed_span_is_recorded_without_swallowing_error(self):
        with temporary_workspace() as root:
            with self.assertRaisesRegex(ValueError,'fixture'):
                with timed(root,'test'):raise ValueError('fixture')
            record=json.loads((root/'runtime-timing.jsonl').read_text())
            self.assertEqual(record['status'],'failed');self.assertGreaterEqual(record['seconds'],0)
            self.assertNotIn('fixture',str(record))

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber'),'Deep Doc required')
    def test_docling_reuses_converter_but_keeps_page_evidence_separate(self):
        from extraction.deep_doc_adapter import DoclingAdapter,_cached_docling_converter
        _cached_docling_converter.cache_clear()
        try:
            with patch('extraction.deep_doc_adapter._build_docling_converter',return_value=object()) as build:
                first=object.__new__(DoclingAdapter);first._init_docling()
                second=object.__new__(DoclingAdapter);second._init_docling()
            build.assert_called_once();self.assertIs(first._converter,second._converter)
            self.assertIsNot(first.page_results,second.page_results)
            self.assertFalse(first.converter_timing['reused_in_process']);self.assertTrue(second.converter_timing['reused_in_process'])
        finally:_cached_docling_converter.cache_clear()

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber'),'Deep Doc required')
    def test_deferred_legacy_pass_does_not_call_image_extractor(self):
        from extraction.deep_doc_adapter import enrich
        from extraction.models import Document,Page
        with temporary_workspace() as root:
            source=root/'blank.pdf'
            with pymupdf.open() as pdf:pdf.new_page();pdf.save(source)
            document=Document('blank.pdf','digest','native','1',pages=[Page(1,595,842,0,regions=[])])
            with patch('extraction.deep_doc_adapter.ImageAdapter') as image:
                result=enrich(source,root,document,defer_figures=True)
            image.assert_not_called()
            self.assertEqual(result.settings['extraction_evidence']['images']['status'],'deferred_to_spatial_pass')

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber') and importlib.util.find_spec('cv2'),'Figure dependencies required')
    def test_single_spatial_pass_preserves_final_figure_pixels_and_text(self):
        from extraction.coordinator import extract_pdf,content_ir,text_regions
        from extraction.figures import enrich_figures
        with temporary_workspace() as root:
            source=root/'unseen.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=420,height=420)
                page.insert_text((35,45),'A new synthetic diagram')
                page.draw_rect((60,100,150,220),fill=(.2,.6,.3))
                page.draw_rect((240,120,340,210),fill=(.3,.4,.8))
                pdf.save(source)
            results=[]
            for defer in (False,True):
                folder=root/str(defer)
                ir=content_ir(extract_pdf(source,folder,'auto',defer_figures=defer))
                enrich_figures(source,ir,folder/'spatial')
                results.append((sorted(r['text'] for r in text_regions(ir)),
                    [(a['bbox'],Path(a['path']).read_bytes()) for a in ir['assets']]))
            self.assertTrue(results[0][1])
            self.assertEqual(results[0],results[1])

    def test_remote_upload_bundles_source_and_workers_in_one_connection(self):
        from transformation.ocr.remote_ocr import run_remote
        with temporary_workspace() as root:
            source=root/'source.pdf';key=root/'key';key.write_text('fixture')
            with pymupdf.open() as pdf:pdf.new_page();pdf.save(source)
            process=MagicMock();process.poll.return_value=0;process.returncode=0
            with patch('transformation.ocr.remote_ocr.subprocess.Popen',return_value=process) as launch,\
                 patch('transformation.ocr.remote_ocr.subprocess.check_output',return_value='0'),\
                 patch('transformation.ocr.remote_ocr.unpack',return_value={'total_seconds':1}):
                events=list(run_remote(source,root/'out',{'host':'192.0.2.1','port':22,'key':str(key)}))
            self.assertEqual(launch.call_count,2)  # Single upload/launch and result download.
            self.assertNotIn('-n',launch.call_args_list[0].args[0][:-1])
            self.assertIn('&& { nohup',launch.call_args_list[0].args[0][-1])
            with tarfile.open(root/'out/inputs.tar') as archive:
                self.assertEqual(set(archive.getnames()),{'source.pdf','run_gpu_ocr.py','diagnose_paddle_detector.py','recognize_paddle_boxes.py','raster_policy.py'})
            self.assertEqual(events[-1]['transport_timing']['status_polls'],1)
