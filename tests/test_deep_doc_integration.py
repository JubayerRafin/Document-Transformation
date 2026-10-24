"""Integration boundaries: dependency isolation, explicit failures and provenance."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import pymupdf

from extraction.coordinator import content_ir, extract_pdf
from extraction.models import Document, Page, Region


HAS_CORE=bool(importlib.util.find_spec('pdfplumber'))


class IntegrationTests(unittest.TestCase):
    def test_source_ir_keeps_execution_evidence(self):
        document=Document('form.pdf','digest','test','1',settings={'extraction_evidence':{'tables':{'accepted':2}}})
        self.assertEqual(content_ir(document)['settings'],document.settings)

    @unittest.skipUnless(HAS_CORE,'Deep Doc dependencies required')
    def test_image_branch_import_does_not_require_camelot(self):
        code="""
import sys
sys.modules['camelot']=None
from extraction.deep_doc_adapter import ImageAdapter, TextAdapter
assert ImageAdapter and TextAdapter
"""
        run=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(run.returncode,0,run.stderr)

    @unittest.skipUnless(HAS_CORE,'Deep Doc dependencies required')
    def test_explicit_docling_conversion_failure_is_not_an_empty_table_result(self):
        from extraction.deep_doc_adapter import DoclingAdapter
        detector=object.__new__(DoclingAdapter)
        detector.available=True; detector.errors=[]; detector.page_results=[]
        detector._converter=NS(convert=lambda _:NS(status='failure',errors=['broken conversion']))
        with tempfile.TemporaryDirectory() as folder:
            temporary=Path(folder)/'page.pdf'; temporary.write_bytes(b'test')
            with patch.object(detector,'_single_page_pdf',return_value=str(temporary)):
                self.assertEqual(detector.extract_page('unused',2),[])
            self.assertEqual(detector.page_results[0]['status'],'failed')
            self.assertIn('broken conversion',detector.errors[0]['error'])
            self.assertFalse(temporary.exists())

    @unittest.skipUnless(HAS_CORE,'Deep Doc dependencies required')
    def test_explicit_docling_stops_but_auto_keeps_independent_text_branch(self):
        from extraction.deep_doc_adapter import TableAdapter
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'arbitrary.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page(); page.insert_text((60,70),'Unrelated source document 4819')
                pdf.save(source)
            with patch.object(TableAdapter,'__init__',side_effect=ImportError('table dependency missing')):
                document=extract_pdf(source,root/'auto',strategy='auto')
                evidence=document.settings['extraction_evidence']
                self.assertEqual(evidence['tables']['status'],'unavailable')
                self.assertEqual(evidence['text']['accepted_pages'],[1])
                self.assertTrue(any(r.metadata.get('method')=='deep-doc-stage1-text-adapter' for r in document.pages[0].regions))
                with self.assertRaisesRegex(RuntimeError,'Explicit Docling'):
                    extract_pdf(source,root/'explicit',strategy='docling')
                self.assertTrue((root/'explicit/deep-doc/extraction-evidence.json').is_file())

    @unittest.skipUnless(HAS_CORE,'Deep Doc dependencies required')
    def test_docling_cell_geometry_and_spans_survive_without_dataframe_headers(self):
        from extraction.deep_doc_adapter import DoclingAdapter
        box=NS(l=10,t=20,r=100,b=60)
        box.to_top_left_origin=lambda _:box
        cell=NS(start_row_offset_idx=0,start_col_offset_idx=0,end_row_offset_idx=1,end_col_offset_idx=2,text='Merged heading',bbox=box)
        table=NS(prov=[NS(page_no=1,bbox=box)],data=NS(num_cols=2,num_rows=1,table_cells=[cell]))
        detector=object.__new__(DoclingAdapter); detector.geometry={}
        with patch('extraction.deep_doc_adapter.DoclingTableExtractor._table_to_json',return_value={'source':'docling'}):
            detector._table_to_json(table,4,0,NS(pages={1:NS(size=NS(height=800))}))
        self.assertEqual(detector.geometry[0]['cells'][0]['column_span'],2)
        self.assertEqual(detector.geometry[0]['matrix'],[['Merged heading','']])

    @unittest.skipUnless(HAS_CORE,'Deep Doc dependencies required')
    def test_table_only_pages_are_not_skipped(self):
        from extraction.deep_doc_adapter import enrich, TableAdapter, TextAdapter, ImageAdapter
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'table-only.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page(); page.insert_text((40,60),'Label Value')
                pdf.save(source)
            document=Document('table-only.pdf','digest','native','1',pages=[Page(1,595,842,0,regions=[
                Region('r','table',[35,40,180,90],cells=[['Label','Value']])])])
            table=NS(structural_unavailable=False,docling_candidate=NS(errors=[],page_results=[]),engine_calls={},extract=lambda *args:[])
            with patch('extraction.deep_doc_adapter.TableAdapter',return_value=table),patch.object(TextAdapter,'extract',return_value=[]),patch.object(ImageAdapter,'extract',return_value=[]):
                result=enrich(source,root,document,strategy='docling')
            self.assertEqual(result.settings['extraction_evidence']['eligible_pages'],[1])

    @unittest.skipUnless(HAS_CORE,'Deep Doc dependencies required')
    def test_matching_table_outline_cannot_hide_a_missing_duplicate_value(self):
        from extraction.deep_doc_adapter import enrich, TextAdapter, ImageAdapter
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'repeated.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page(); page.insert_text((45,65),'Part Count')
                page.insert_text((45,85),'Gizmo Gizmo'); pdf.save(source)
            region=Region('r','table',[35,40,200,100],cells=[['Part','Count'],['Gizmo','Gizmo']])
            document=Document('repeated.pdf','digest','native','1',pages=[Page(1,595,842,0,regions=[region])])
            element=NS(page_num=1,bbox=region.bbox,matrix=[['Part','Count'],['Gizmo','']],source_engine='docling',cell_records=[])
            table=NS(structural_unavailable=False,docling_candidate=NS(errors=[],page_results=[]),engine_calls={},extract=lambda *args:[element])
            with patch('extraction.deep_doc_adapter.TableAdapter',return_value=table),patch.object(TextAdapter,'extract',return_value=[]),patch.object(ImageAdapter,'extract',return_value=[]):
                result=enrich(source,root,document,strategy='docling')
            self.assertEqual(result.pages[0].regions[0].cells,[['Part','Count'],['Gizmo','Gizmo']])
            self.assertEqual(result.settings['extraction_evidence']['tables']['accepted'],0)
            self.assertEqual(result.settings['extraction_evidence']['tables']['rejected'][0]['missing_tokens'],['Gizmo'])

    @unittest.skipUnless(HAS_CORE,'Deep Doc dependencies required')
    def test_style_changes_do_not_insert_spaces_inside_identifiers_or_punctuation(self):
        from extraction.deep_doc_adapter import TextAdapter
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'mixed-style.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page()
                x=40
                for text,font in [('Code: ','helv'),('ZX-3819','hebo'),(', next item.','helv')]:
                    page.insert_text((x,60),text,fontname=font,fontsize=11)
                    x+=pymupdf.get_text_length(text,fontname=font,fontsize=11)
                pdf.save(source)
            result=TextAdapter({}).extract(str(source),[1])
            self.assertEqual(' '.join(e.content for e in result),'Code: ZX-3819, next item.')


if __name__=='__main__': unittest.main()
