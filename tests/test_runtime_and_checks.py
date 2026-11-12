"""Runtime changes (shared OCR session, fewer figure passes) and quieter, sharper checks.

Synthetic only: no GPU, OCR models or language models run.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from tests.temp_workspace import temporary_workspace


class PrivacyCheckFilterTests(unittest.TestCase):
    def page(self):
        doc = pymupdf.open(); page = doc.new_page(width=300, height=200)
        page.draw_rect(pymupdf.Rect(20, 20, 120, 40), color=None, fill=(0, 0, 0))   # a redaction box
        page.insert_text((20, 90), 'KRW 850,000', fontsize=11)
        return doc, page

    def test_only_regions_that_could_hide_a_selected_value_count(self):
        from transformation.validation.privacy_verification import _could_hide_selected_value as relevant
        doc, page = self.page()
        values = ['ZK-55102-Q', 'ana.berg@example.test']
        self.assertFalse(relevant({'text': '', 'bbox': [20, 20, 120, 40]}, values, page))       # black box
        self.assertTrue(relevant({'text': '', 'bbox': [150, 100, 250, 140]}, values, page))     # unreadable blank area
        self.assertFalse(relevant({'text': 'KRW 850,000', 'bbox': [20, 80, 100, 95]}, values, page))
        self.assertFalse(relevant({'text': 'Issued: 2024-03-18'}, values, page))
        self.assertTrue(relevant({'text': 'Ref ZK-5510Z-Q'}, values, page))                     # OCR variant of a value
        self.assertTrue(relevant({'text': 'contact ana.berg@exampl'}, values, page))
        self.assertTrue(relevant({'text': 'call +46 8 555 0193'}, values, page))
        doc.close()


class PartialNameTests(unittest.TestCase):
    def pdf(self, text):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((50, 80), text, fontsize=11); return doc.tobytes()

    def test_half_masked_name_is_reported(self):
        from transformation.validation.output_validation import partial_name_residuals
        source = self.pdf('Adviser Jonas Wikstrom. Wikstrom agreed.')
        half = self.pdf('Adviser Wikstrom. Wikstrom agreed.')
        full = self.pdf('Adviser . agreed.')
        self.assertEqual(partial_name_residuals(source, half, ['Jonas Wikstrom']), ['Wikstrom'])
        self.assertEqual(partial_name_residuals(source, full, ['Jonas Wikstrom']), [])


class FigureProcessingTests(unittest.TestCase):
    def test_figures_without_selected_values_keep_their_original_image_and_skip_a_second_check(self):
        from transformation.ocr import pdf_assets
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=300)
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60), False); pix.clear_with(200)
            page.insert_image(pymupdf.Rect(50, 50, 210, 170), pixmap=pix)
            data = doc.tobytes()
        ir = {'units': [{'number': 1, 'regions': [{'id': 'f', 'kind': 'figure', 'bbox': [50, 50, 210, 170]}]}]}
        seen = {}
        def fake_pipeline(payload, name, **options):
            seen.update(options)
            yield {'status': 'generated', 'run_dir': 'r', 'output_pdf': 'unused', 'validation': {'pii_findings': 0}}
        with temporary_workspace() as root, patch('transformation.pipeline.run_pipeline', side_effect=fake_pipeline):
            events = list(pdf_assets.process(data, ir, root, False, True, ['Names'], True, {'timeout': 600}))
        self.assertFalse(seen['privacy_verification'])
        result = events[-1]
        self.assertEqual(result['reports'][0]['status'], 'no_selected_values')
        with pymupdf.open(stream=data, filetype='pdf') as before, pymupdf.open(stream=result['result'], filetype='pdf') as after:
            self.assertEqual(len(after[0].get_images()), len(before[0].get_images()))
            self.assertEqual(after[0].get_pixmap(dpi=36).samples, before[0].get_pixmap(dpi=36).samples)


class SharedOcrSessionTests(unittest.TestCase):
    def test_unreachable_session_falls_back_to_a_one_off_process(self):
        from transformation.ocr import paddle_pipeline
        def gone(*args, **kwargs):
            raise ConnectionRefusedError('session exited')
            yield
        class Finished:
            returncode = 0
            def poll(self): return 0
        with temporary_workspace() as root, patch.dict(os.environ, {'TRANSFORMX_OCR_SOCKET': 'gone.sock'}), \
             patch('transformation.ocr.service.request', side_effect=gone), \
             patch('transformation.ocr.paddle_pipeline.subprocess.Popen', return_value=Finished()) as launch, \
             patch('transformation.ocr.remote_ocr.unpack', return_value={'total_seconds': 1}):
            events = list(paddle_pipeline.run_server_gpu_ocr(root / 'source.pdf', root / 'out', sys.executable, 20, 'en'))
        launch.assert_called_once()
        self.assertEqual(events[-1]['stage'], 'completed')
        self.assertNotIn('persistent', events[-1])

    def test_remote_job_session_is_skipped_off_linux(self):
        from transformation.remote_worker import ocr_session
        if os.name == 'posix': self.skipTest('Windows-only behaviour')
        with temporary_workspace() as root, ocr_session(sys.executable, root) as persistent:
            self.assertFalse(persistent)


class DetectionConcurrencyTests(unittest.TestCase):
    def test_parallel_region_requests_keep_region_order(self):
        import threading, time
        from transformation.validation.privacy import detect
        active = {'now': 0, 'max': 0}; lock = threading.Lock()
        def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
            text = json.loads(messages[1]['content'])['text']
            with lock: active['now'] += 1; active['max'] = max(active['max'], active['now'])
            time.sleep(.05)
            with lock: active['now'] -= 1
            return json.dumps({'findings': [{'value': text.split()[-1], 'category': 'Names'}]}), {}
        # Long units are sent one per request, so these run concurrently.
        regions = [{'id': f'r{i}', 'text': 'x ' * 600 + f'Signed by Person{i}'} for i in range(8)]
        with patch('transformation.llm.ollama_native.chat', side_effect=chat):
            findings = detect(regions, ['Names'], 'http://127.0.0.1:1/v1', 'm', True, workers=4)
        self.assertEqual([f['value'] for f in findings], [f'Person{i}' for i in range(8)])
        self.assertGreater(active['max'], 1)


if __name__ == '__main__':
    unittest.main()



class OutputCheckReuseTests(unittest.TestCase):
    def test_only_new_or_changed_output_lines_go_back_to_the_model(self):
        from transformation.validation import privacy_verification
        with pymupdf.open() as pdf:
            pdf.new_page(width=300, height=200).insert_text((20, 40), 'x', fontsize=8); data = pdf.tobytes()
        regions = [{'id': 'a', 'text': 'Quarterly review notes', 'bbox': [0, 0, 10, 10], 'metadata': {}},
                   {'id': 'b', 'text': 'Contact: , mobile', 'bbox': [0, 20, 10, 30], 'metadata': {}},
                   {'id': 'c', 'text': 'Call +46 8 555 0193', 'bbox': [0, 40, 10, 50], 'metadata': {}}]
        def run_ocr(source, folder, python, timeout, source_language):
            yield {'recognition_dir': str(folder)}
        def prepare(data, recognition, folder, name, allow_unreadable=False):
            (Path(folder) / 'ocr-document.json').write_text(json.dumps({'pages': [{'regions': regions}]}), encoding='utf-8')
        asked = []
        def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
            asked.append(json.loads(messages[1]['content'])['text']); return json.dumps({'findings': []}), {}
        with temporary_workspace() as root, \
             patch('transformation.ocr.paddle_pipeline.run_ocr', side_effect=run_ocr), \
             patch('transformation.ocr.paddle_handoff.prepare', side_effect=prepare), \
             patch('transformation.llm.ollama_native.chat', side_effect=chat):
            result = list(privacy_verification.verify(
                data, 'pdf', root, [], ['Names', 'Phone numbers'], 'http://127.0.0.1:1/v1', 'm', True,
                {'ocr_python': 'python'}, language='en',
                analyzed_texts=['Section 2\nQuarterly review notes', 'Call +46 8 555 0193 anytime']))[-1]
        self.assertEqual(asked, ['Contact: , mobile'])
        # Format patterns still run on every line, including reused ones.
        self.assertEqual([f['value'] for f in result['reports'][0]['detected_pii']], ['+46 8 555 0193'])


class FigureConcurrencyTests(unittest.TestCase):
    def test_pii_figures_run_concurrently_and_report_in_document_order(self):
        import time as clock
        from transformation.ocr import pdf_assets
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=400)
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False); pix.clear_with(180)
            boxes = [[20, 20, 120, 120], [150, 20, 250, 120], [280, 20, 380, 120]]
            for b in boxes: page.insert_image(pymupdf.Rect(b), pixmap=pix)
            data = doc.tobytes()
        ir = {'units': [{'number': 1, 'regions': [{'id': f'f{i}', 'kind': 'figure', 'bbox': b} for i, b in enumerate(boxes)]}]}
        def slow(payload, name, **options):
            clock.sleep(.4)
            yield {'status': 'generated', 'run_dir': 'r', 'output_pdf': 'unused', 'validation': {'pii_findings': 0}}
        with temporary_workspace() as root, patch('transformation.pipeline.run_pipeline', side_effect=slow):
            started = clock.monotonic()
            events = list(pdf_assets.process(data, ir, root, False, True, ['Names'], True, {'timeout': 600}))
            elapsed = clock.monotonic() - started
        self.assertLess(elapsed, 1.0)  # three 0.4 s figures, not 1.2 s in sequence
        self.assertEqual([r['region_id'] for r in events[-1]['reports']], ['f0', 'f1', 'f2'])


class RepeatedTextTests(unittest.TestCase):
    def test_repeated_unit_text_is_asked_once(self):
        from transformation.validation.privacy import detect
        asked = []
        def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
            text = json.loads(messages[1]['content'])['text']; asked.append(text)
            return json.dumps({'findings': [{'value': 'Ivo Lenz', 'category': 'Names'}] if 'Ivo' in text else []}), {}
        regions = [{'id': f'p{i}', 'text': 'Prepared by Ivo Lenz'} for i in range(3)] + [{'id': 'x', 'text': 'Other'}]
        with patch('transformation.llm.ollama_native.chat', side_effect=chat):
            findings = detect(regions, ['Names'], 'http://127.0.0.1:1/v1', 'm', True)
        self.assertEqual(sorted(asked), ['Other', 'Prepared by Ivo Lenz'])
        self.assertEqual([f['region_id'] for f in findings], ['p0', 'p1', 'p2'])


class CheckPageSelectionTests(unittest.TestCase):
    def test_only_selected_pages_get_fresh_ocr(self):
        from transformation.validation import privacy_verification
        with pymupdf.open() as pdf:
            for _ in range(3): pdf.new_page(width=200, height=200)
            data = pdf.tobytes()
        seen = []
        def run_ocr(source, folder, python, timeout, source_language):
            seen.append(Path(folder).parent.name); yield {'recognition_dir': str(folder)}
        def prepare(data, recognition, folder, name, allow_unreadable=False):
            (Path(folder) / 'ocr-document.json').write_text(json.dumps({'pages': [{'regions': []}]}), encoding='utf-8')
        with temporary_workspace() as root, patch('transformation.ocr.paddle_pipeline.run_ocr', side_effect=run_ocr), \
             patch('transformation.ocr.paddle_handoff.prepare', side_effect=prepare):
            result = list(privacy_verification.verify(data, 'pdf', root, [], ['Names'], '', '', False,
                                                      {'ocr_python': 'python'}, language='en', pages={2}))[-1]
        self.assertEqual(seen, ['2']); self.assertEqual([r['page'] for r in result['reports']], [2])


class PrewarmTests(unittest.TestCase):
    def test_prewarm_sends_one_synthetic_page_in_the_background(self):
        from transformation.ocr import service
        calls = []
        def request(source, output, language, timeout):
            calls.append((Path(source).is_file(), Path(source).parent.parent.name, language)); yield {'stage': 'service_completed'}
        with temporary_workspace() as root, patch.object(service, 'request', side_effect=request):
            service.prewarm(root).join(10)
        self.assertEqual(calls, [(True, '.ocr-prewarm', 'en')])

    def test_prewarm_failure_is_silent(self):
        from transformation.ocr import service
        with temporary_workspace() as root, patch.object(service, 'request', side_effect=ConnectionRefusedError):
            service.prewarm(root).join(10)


class OcrSessionConnectTests(unittest.TestCase):
    def test_full_accept_queue_is_retried_not_dropped(self):
        import socket as sockets
        from transformation.ocr import service
        attempts = []
        class Fake:
            def __init__(self, *a): pass
            def settimeout(self, value): pass
            def connect(self, path):
                attempts.append(path)
                if len(attempts) < 3: raise BlockingIOError(11, 'Resource temporarily unavailable')
            def close(self): pass
        with patch.object(service.socket, 'socket', Fake), patch.object(service.socket, 'AF_UNIX', 1, create=True):
            self.assertIsInstance(service.connect('/x/worker.sock', wait=5), Fake)
        self.assertEqual(len(attempts), 3)

    def test_other_connect_errors_are_not_retried(self):
        from transformation.ocr import service
        class Fake:
            def __init__(self, *a): pass
            def settimeout(self, value): pass
            def connect(self, path): raise ConnectionRefusedError()
            def close(self): pass
        with patch.object(service.socket, 'socket', Fake), patch.object(service.socket, 'AF_UNIX', 1, create=True):
            with self.assertRaises(ConnectionRefusedError): service.connect('/x/worker.sock', wait=5)


class FigureRetryTests(unittest.TestCase):
    def test_redaction_figure_retries_one_transient_io_error(self):
        from transformation.ocr.pdf_assets import _run_figure
        calls = []
        def figure(payload, name, **options):
            calls.append(1)
            if len(calls) == 1: raise BlockingIOError(11, 'Resource temporarily unavailable')
            yield {'status': 'generated'}
        with patch('transformation.pipeline.run_pipeline', side_effect=figure), patch('time.sleep'):
            self.assertEqual(_run_figure(b'', 'r', False, True, ['Names'], True, {})['status'], 'generated')
        self.assertEqual(len(calls), 2)

    def test_logic_errors_and_second_failures_are_not_hidden(self):
        from transformation.ocr.pdf_assets import _run_figure
        def broken(payload, name, **options):
            raise BlockingIOError(11, 'busy'); yield
        with patch('transformation.pipeline.run_pipeline', side_effect=broken), patch('time.sleep'):
            with self.assertRaises(BlockingIOError): _run_figure(b'', 'r', False, True, ['Names'], True, {})
        def bad(payload, name, **options):
            raise ValueError('x'); yield
        with patch('transformation.pipeline.run_pipeline', side_effect=bad):
            with self.assertRaises(ValueError): _run_figure(b'', 'r', False, True, ['Names'], True, {})
