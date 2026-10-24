import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from transformation.pdf.document_runner import run_document
from test_document_profile import pdf_bytes


class DocumentRunnerTests(unittest.TestCase):
    def test_document_mode_uses_one_worker_and_preserves_ir_page_ids(self):
        data=pdf_bytes(count=3)
        ir={'units':[{'number':n} for n in (1,2,3)]}
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'translated.pdf'; output.write_bytes(data)
            with patch('transformation.pdf.document_runner.run_pdf',return_value=iter([
                    dict(status='generated',run_dir=temp,output_pdf=str(output))])) as worker:
                events=list(run_document(data,'unseen.pdf',pdf_page_mode='document',external_ir=ir,
                                         ocr_remote={'host':'fixture'},root=Path(temp)))
            worker.assert_called_once()
            self.assertEqual(worker.call_args.args,(data,'unseen.pdf'))
            self.assertEqual(worker.call_args.kwargs['external_ir'],ir)
            self.assertNotIn('ocr_remote',worker.call_args.kwargs)
            self.assertNotIn('pdf_page_mode',worker.call_args.kwargs)
            self.assertEqual(events[-1]['status'],'generated')

    def test_document_mode_rejects_truncated_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'truncated.pdf'; output.write_bytes(pdf_bytes())
            with patch('transformation.pdf.document_runner.run_pdf',return_value=iter([
                    dict(status='generated',run_dir=temp,output_pdf=str(output))])):
                events=list(run_document(pdf_bytes(count=2),'unseen.pdf',pdf_page_mode='document'))
            self.assertEqual(events[-1]['status'],'failed')
            self.assertNotIn('output_pdf',events[-1])
            self.assertIn('page count',events[-1]['error'])

    def test_document_mode_retains_scan_page_routing(self):
        calls=[]
        def worker(data,name,**options):
            calls.append(options)
            path=Path(options['root'])/name; path.mkdir(parents=True)
            output=path/'output.pdf'; output.write_bytes(data)
            yield dict(status='generated',run_dir=str(path),output_pdf=str(output))
        with tempfile.TemporaryDirectory() as temp,patch('transformation.pdf.document_runner.run_pdf',side_effect=worker):
            events=list(run_document(pdf_bytes('image_only',2),'scan.pdf',root=Path(temp),
                                     pdf_page_mode='document',ocr_remote={'host':'fixture'}))
            self.assertEqual(len(calls),2)
            self.assertTrue(all('ocr_remote' in c and 'pdf_page_mode' not in c for c in calls))
            self.assertEqual(events[-1]['status'],'generated')

    def test_single_page_strips_orchestration_option(self):
        with patch('transformation.pdf.document_runner.run_pdf',return_value=iter([{'status':'processing'}])) as worker:
            list(run_document(pdf_bytes(),'single.pdf',pdf_page_mode='document'))
            self.assertNotIn('pdf_page_mode',worker.call_args.kwargs)

    def test_all_uncertain_scan_is_preserved_without_retrying_native_parser(self):
        data=pdf_bytes('image_only')
        with tempfile.TemporaryDirectory() as temp,patch('transformation.pdf.document_runner.run_pdf') as worker:
            events=list(run_document(data,'uncertain.pdf',root=Path(temp),external_ir={'units':[{'number':1,'preserve_source_only':True}]}))
            worker.assert_not_called()
            self.assertEqual(Path(events[-1]['output_pdf']).read_bytes(),data)
            self.assertEqual(events[-1]['source_pages_preserved_for_review'],[1])
            self.assertEqual(events[-1]['review_status'],'requires_human_review')

    def test_preservation_flag_cannot_skip_a_digital_page(self):
        data=pdf_bytes('digital')
        with patch('transformation.pdf.document_runner.run_pdf',return_value=iter([{'status':'test'}])) as worker:
            list(run_document(data,'native.pdf',external_ir={'units':[{'number':1,'preserve_source_only':True}]}))
            worker.assert_called_once()

    def test_mixed_document_routes_pages_and_merges(self):
        with pymupdf.open() as pdf:
            for kind in ('digital','image_only'):
                with pymupdf.open(stream=pdf_bytes(kind),filetype='pdf') as p:
                    pdf.insert_pdf(p)
            data=pdf.tobytes()
        calls=[]
        def worker(data,name,**options):
            calls.append(options)
            path=Path(options['root'])/name
            path.mkdir(parents=True)
            output=path/'output.pdf'
            output.write_bytes(data)
            yield dict(status='generated',run_dir=str(path),output_pdf=str(output))
        with tempfile.TemporaryDirectory() as temp, patch('transformation.pdf.document_runner.run_pdf',side_effect=worker):
            events=list(run_document(data,'unseen.pdf',root=Path(temp),ocr_remote={'host':'example'}))
            self.assertEqual(events[-1]['status'],'generated')
            self.assertNotIn('ocr_remote',calls[0])
            self.assertIn('ocr_remote',calls[1])
            with pymupdf.open(events[-1]['output_pdf']) as output:
                self.assertEqual(len(output),2)
                self.assertIn('Unseen',output[0].get_text())
                self.assertEqual(output[1].get_text(),'')

    def test_failed_page_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as temp, patch('transformation.pdf.document_runner.run_pdf',return_value=iter([dict(status='failed',run_dir=temp,error='mock failure')])):
            events=list(run_document(pdf_bytes(count=2),'test.pdf',root=Path(temp)))
            self.assertEqual(events[-1]['status'],'failed')
            self.assertNotIn('output_pdf',events[-1])

    def test_missing_ocr_rejected_before_work(self):
        with patch('transformation.pdf.document_runner.run_pdf') as worker:
            with self.assertRaisesRegex(ValueError,'Select a fresh'):
                list(run_document(pdf_bytes('image_only',2),'test.pdf'))
            worker.assert_not_called()
