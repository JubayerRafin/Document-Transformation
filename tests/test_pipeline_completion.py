from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import pymupdf

from transformation.validation.privacy import mask_runs
from transformation.validation.output_validation import inspect_output
from transformation.pdf.ir_bridge import map_document


class CompletionTests(unittest.TestCase):
    def test_sensitive_value_spanning_styles(self):
        self.assertEqual(mask_runs(['Email: ma','ya@example','.org and public'],['maya@example.org']),['Email: [REDACTED]','',' and public'])

    def test_relationship_and_metadata_residuals_detected(self):
        data=BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('_rels/.rels','<Relationships><Relationship Target="mailto:maya@example.org"/></Relationships>')
            archive.writestr('docProps/core.xml','<root><name>maya@example.org</name></root>')
        result=inspect_output(data.getvalue(),'docx',['maya@example.org'])
        self.assertEqual(len(result['known_sensitive_residuals']),2)

    def test_nested_embedded_package_inspected(self):
        inner=BytesIO(); outer=BytesIO()
        with zipfile.ZipFile(inner,'w') as archive: archive.writestr('sheet.xml','<r>secret@example.org</r>')
        with zipfile.ZipFile(outer,'w') as archive: archive.writestr('word/embeddings/workbook.xlsx',inner.getvalue())
        self.assertTrue(inspect_output(outer.getvalue(),'docx',['secret@example.org'])['known_sensitive_residuals'])

    def test_page_and_geometry_mapping_rejects_ambiguity(self):
        paragraph=NS(box=NS(x=10,y=70,x2=80,y2=90),unicode='Title',debug_id='native-1')
        document=NS(page=[NS(page_number=0,pdf_paragraph=[paragraph])])
        region={'id':'external-1','bbox':[10,10,80,30],'text':'Title'}
        ir={'units':[{'number':1,'height':100,'regions':[region]}]}
        result=map_document(document,ir)
        self.assertEqual(result[0]['region_id'],'external-1')
        ir['units'][0]['regions'].append(dict(region,id='external-2'))
        self.assertEqual(map_document(document,ir)[0]['status'],'ambiguous')

    def test_mapping_does_not_modify_slotted_vendor_objects(self):
        from dataclasses import dataclass
        @dataclass(slots=True)
        class Paragraph:
            box:object
            unicode:str
            debug_id:str
        paragraph=Paragraph(NS(x=10,y=70,x2=80,y2=90),'Title','native-1')
        document=NS(page=[NS(page_number=0,pdf_paragraph=[paragraph])])
        ir={'units':[{'number':1,'height':100,'regions':[{'id':'r','bbox':[10,10,80,30],'text':'Title'}]}]}
        self.assertEqual(map_document(document,ir)[0]['region_id'],'r')

    def test_styled_translation_order_validation(self):
        from transformation.office.styled_translation import translate_runs
        with patch('transformation.llm.ollama_native.chat',return_value=('{"runs":[{"id":1,"text":"two"},{"id":0,"text":"one"}]}',{})):
            with self.assertRaises(ValueError): translate_runs('unused','test',['one ','two'],'German')

    def test_styled_translation_preserves_protected_value(self):
        from transformation.office.styled_translation import translate_runs
        def model(endpoint,model,messages,**kwargs):
            payload=json.loads(messages[-1]['content'])
            return json.dumps({'runs':payload['runs']}),{}
        with patch('transformation.llm.ollama_native.chat',side_effect=model):
            self.assertEqual(translate_runs('unused','test',['Contact: ','Maya Chen'],'German',['Maya Chen']),['Contact: ','Maya Chen'])
            result=translate_runs('unused','test',['Contact: Ma','ya Chen'],'German',['Maya Chen'])
            self.assertEqual(''.join(result),'Contact: Maya Chen')

    def test_blank_pages_preserved_without_model_or_ocr(self):
        from transformation.pdf.document_runner import run_document
        with tempfile.TemporaryDirectory() as folder,pymupdf.open() as pdf:
            pdf.new_page(); pdf.new_page()
            with patch('transformation.pdf.document_runner.run_pdf') as engine:
                result=list(run_document(pdf.tobytes(),'blank.pdf',root=folder))[-1]
                self.assertEqual(result['status'],'generated'); engine.assert_not_called()
                with pymupdf.open(result['output_pdf']) as output: self.assertEqual(len(output),2)

    def test_nativeization_cleans_pixels_and_creates_searchable_text(self):
        from transformation.pdf.scan_native import prepare_native
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); ocr=root/'ocr'; ocr.mkdir()
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=300,height=150); page.insert_text((25,50),'Accepted source text',fontsize=12)
                pix=page.get_pixmap(alpha=False)
            with pymupdf.open() as raster:
                page=raster.new_page(width=300,height=150); page.insert_image(page.rect,pixmap=pix); data=raster.tobytes()
            ir={'units':[{'number':1,'ocr_folder':str(ocr),'regions':[{'id':'line-1','text':'Accepted source text','bbox':[24,36,150,53],'metadata':{'requires_review':False}}]}]}
            output,blockers=prepare_native(data,ir,root)
            with pymupdf.open(stream=output,filetype='pdf') as pdf:
                self.assertIn('Accepted source text',pdf[0].get_text())
                self.assertEqual(len(pdf),1)
            self.assertFalse(blockers)

    def test_uncertain_scan_text_stays_blocked(self):
        from transformation.pdf.scan_native import prepare_native
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); ocr=root/'ocr'; ocr.mkdir()
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=200,height=100); data=pdf.tobytes()
            ir={'units':[{'number':1,'ocr_folder':str(ocr),'regions':[{'id':'bad','text':'Uncertain','bbox':[20,20,100,40],'metadata':{'requires_review':True}}]}]}
            output,blockers=prepare_native(data,ir,root)
            self.assertEqual(len(blockers),1)
            with pymupdf.open(stream=output,filetype='pdf') as pdf: self.assertNotIn('Uncertain',pdf[0].get_text())

    def test_visual_hint_creates_source_crop_without_overwriting_text(self):
        from extraction.models import Document,Page,Region
        from transformation.analysis.document_analysis import apply_routing_hints
        doc=Document('test.pdf','hash','test','1',pages=[Page(1,100,100,0,regions=[Region('r','text',[5,5,30,15],'heading')])])
        apply_routing_hints(doc,{'visual_pages':[{'page':1,'regions':[{'kind':'text','bbox':[0,0,.4,.2]},{'kind':'signature','bbox':[.5,.5,.9,.9]}]}]})
        self.assertEqual(doc.settings['extraction_routes']['1'][0]['route'],'native_text')
        self.assertEqual(doc.settings['extraction_routes']['1'][1]['route'],'ocr_candidate')
        self.assertEqual(doc.pages[0].regions[0].text,'heading')

    def test_visual_model_cannot_override_source_page_id(self):
        from transformation.analysis.document_analysis import analyze_pdf
        with pymupdf.open() as pdf:
            pdf.new_page(); data=pdf.tobytes()
        response=json.dumps({'page':999,'purpose':'report','regions':[]})
        with patch('transformation.llm.ollama_native.chat',return_value=(response,{})):
            self.assertEqual(analyze_pdf(data,'unused','test',True)['visual_pages'][0]['page'],1)


if __name__=='__main__': unittest.main()
