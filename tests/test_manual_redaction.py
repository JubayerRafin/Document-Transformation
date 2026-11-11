import hashlib
import io
import json
import unittest
from unittest.mock import patch

import pymupdf

from tests.temp_workspace import temporary_workspace
from transformation.validation.manual_redaction import redact_regions, validate_regions


class ManualRedactionTests(unittest.TestCase):
    def fixture(self, root, rotation=0, crop=False, scan=False, form=False):
        doc=pymupdf.open();page=doc.new_page(width=400,height=500)
        if scan:
            raster=pymupdf.open();p=raster.new_page(width=400,height=500)
            p.insert_text((70,110),'SYNTHETIC SECRET',fontsize=16)
            page.insert_image(page.rect,stream=p.get_pixmap().tobytes('png'))
            raster.close()
        else:page.insert_text((70,110),'SYNTHETIC SECRET',fontsize=16)
        page.insert_text((70,260),'KEEP THIS CONTENT',fontsize=16)
        if form:
            widget=pymupdf.Widget();widget.field_name='private';widget.field_type=pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.field_value='FORM SECRET';widget.rect=pymupdf.Rect(70,320,280,350);page.add_widget(widget)
        page.add_text_annot((300,420),'SYNTHETIC SECRET IN COMMENT')
        doc.set_metadata({'subject':'SYNTHETIC SECRET'})
        doc.embfile_add('private.txt',b'SYNTHETIC SECRET')
        if crop:page.set_cropbox(pymupdf.Rect(30,40,370,470))
        page.set_rotation(rotation)
        source=root/'source.pdf';doc.save(source);doc.close()
        return source

    def region(self, source, native=pymupdf.Rect(60,85,280,120)):
        with pymupdf.open(source) as doc:
            page=doc[0]
            # Native fixture coordinates are media-relative; page text geometry
            # is crop-relative in PyMuPDF.
            r=pymupdf.Rect(native)
            r.x0-=page.cropbox_position.x;r.x1-=page.cropbox_position.x
            r.y0-=page.cropbox_position.y;r.y1-=page.cropbox_position.y
            r=r*page.rotation_matrix
            return {'page':1,'x':r.x0/page.rect.width,'y':r.y0/page.rect.height,
                    'width':r.width/page.rect.width,'height':r.height/page.rect.height}

    def run_case(self, rotation=0,crop=False,scan=False,form=False):
        with temporary_workspace() as root:
            source=self.fixture(root,rotation,crop,scan,form);before=source.read_bytes();dest=root/'result.pdf'
            region=self.region(source)
            if form:region=self.region(source,pymupdf.Rect(60,310,290,360))
            report=redact_regions(source,dest,[region],hashlib.sha256(before).hexdigest())
            self.assertEqual(source.read_bytes(),before)
            with pymupdf.open(dest) as doc:
                self.assertEqual(doc[0].rotation,rotation)
                text=doc[0].get_text()
                self.assertIn('KEEP THIS CONTENT',text)
                self.assertNotIn('FORM SECRET' if form else 'SYNTHETIC SECRET',text)
                self.assertFalse(list(doc[0].widgets() or []));self.assertFalse(list(doc[0].annots() or []))
                self.assertEqual(doc.embfile_count(),0)
                self.assertFalse(doc.metadata.get('subject'))
            self.assertEqual(report['selected_pixels_check'],'passed')

    def test_native_text_and_other_content_preserved(self):self.run_case()
    def test_scanned_pixels_removed(self):self.run_case(scan=True)
    def test_form_value_removed(self):self.run_case(form=True)
    def test_rotations_and_crop_boxes(self):
        for angle in (0,90,180,270):
            for crop in (False,True):
                with self.subTest(angle=angle,crop=crop):self.run_case(angle,crop)

    def test_unselected_image_pixels_preserved(self):
        with temporary_workspace() as root:
            source=self.fixture(root,scan=True);dest=root/'result.pdf';r=self.region(source)
            redact_regions(source,dest,[r],hashlib.sha256(source.read_bytes()).hexdigest())
            for path in (source,dest):
                with pymupdf.open(path) as doc:
                    pix=doc[0].get_pixmap(clip=pymupdf.Rect(0,180,390,290))
                    if path==source:before=pix.samples
                    else:self.assertEqual(before,pix.samples)

    def test_selected_image_pixels_are_removed_from_the_embedded_image(self):
        with temporary_workspace() as root:
            source=self.fixture(root,scan=True);dest=root/'result.pdf';r=self.region(source)
            redact_regions(source,dest,[r],hashlib.sha256(source.read_bytes()).hexdigest())
            with pymupdf.open(source) as original,pymupdf.open(dest) as result:
                a=pymupdf.Pixmap(original,original[0].get_images()[0][0])
                b=pymupdf.Pixmap(result,result[0].get_images()[0][0])
                self.assertTrue(any(min(a.pixel(x,y)[:3])<200 for x in range(70,270) for y in range(90,115)))
                self.assertTrue(all(min(b.pixel(x,y)[:3])>245 for x in range(70,270) for y in range(90,115)))

    def test_multiple_pages_and_repeated_regions(self):
        with temporary_workspace() as root:
            source=self.fixture(root)
            with pymupdf.open(source) as doc:
                doc.new_page(width=600,height=300).insert_text((60,100),'SECOND SECRET')
                b=doc.tobytes()
            source.write_bytes(b)
            regs=[self.region(source),{'page':2,'x':.08,'y':.24,'width':.5,'height':.15}]
            report=redact_regions(source,root/'result.pdf',regs,hashlib.sha256(b).hexdigest())
            self.assertEqual(report['pages'],2)
            with pymupdf.open(root/'result.pdf') as doc:self.assertNotIn('SECOND SECRET',doc[1].get_text())

    def test_invalid_regions_fail_closed(self):
        good={'page':1,'x':.1,'y':.1,'width':.2,'height':.1}
        for bad in [[],[dict(good,page=True)],[dict(good,x=float('nan'))],
                    [dict(good,width=-1)],[dict(good,x=.9)],
                    [dict(good,unexpected='x')],[dict(good,page=201)], [good]*501]:
            with self.subTest(bad=str(bad)[:80]),self.assertRaises(ValueError):validate_regions(bad)

    def test_thin_glyph_edge_is_removed_and_unrelated_text_preserved(self):
        with temporary_workspace() as root:
            source=self.fixture(root);dest=root/'result.pdf'
            with pymupdf.open(source) as doc:
                page=doc[0]
                char=page.get_text('rawdict',flags=pymupdf.TEXTFLAGS_RAWDICT|pymupdf.TEXT_ACCURATE_BBOXES)['blocks'][0]['lines'][0]['spans'][0]['chars'][0]
                box=pymupdf.Rect(char['bbox'])
                r={'page':1,'x':box.x0/page.rect.width,'y':(box.y1-.06)/page.rect.height,
                   'width':box.width/page.rect.width,'height':8/page.rect.height}
            audit=redact_regions(source,dest,[r],hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(audit['selected_text_check'],'passed')
            with pymupdf.open(dest) as doc:
                self.assertNotIn('SYNTHETIC SECRET',doc[0].get_text())
                self.assertIn('KEEP THIS CONTENT',doc[0].get_text())

    def test_font_line_box_overlap_does_not_remove_untouched_glyphs(self):
        with temporary_workspace() as root:
            source=self.fixture(root);dest=root/'result.pdf'
            with pymupdf.open(source) as doc:
                page=doc[0];flags=pymupdf.TEXTFLAGS_RAWDICT|pymupdf.TEXT_ACCURATE_BBOXES
                chars=page.get_text('rawdict',flags=flags)['blocks'][0]['lines'][0]['spans'][0]['chars']
                bottom=max(c['bbox'][3] for c in chars)
                r={'page':1,'x':.1,'y':(bottom+.3)/page.rect.height,'width':.7,'height':8/page.rect.height}
            redact_regions(source,dest,[r],hashlib.sha256(source.read_bytes()).hexdigest())
            with pymupdf.open(dest) as doc:self.assertIn('SYNTHETIC SECRET',doc[0].get_text())

    def test_verifier_still_rejects_content_if_redaction_does_nothing(self):
        with temporary_workspace() as root:
            source=self.fixture(root);dest=root/'result.pdf';r=self.region(source)
            with patch.object(pymupdf.Page,'apply_redactions',return_value=False):
                with self.assertRaisesRegex(ValueError,'Could not apply selected areas'):
                    redact_regions(source,dest,[r],hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertFalse(dest.exists())

    def test_hash_missing_page_and_same_path_fail_without_output(self):
        with temporary_workspace() as root:
            source=self.fixture(root);b=source.read_bytes();dest=root/'result.pdf';r=self.region(source)
            for hash_,regions,out in [('0'*64,[r],dest),(hashlib.sha256(b).hexdigest(),[dict(r,page=2)],dest),(hashlib.sha256(b).hexdigest(),[r],source)]:
                with self.assertRaises(ValueError):redact_regions(source,out,regions,hash_)
            self.assertFalse(dest.exists());self.assertEqual(source.read_bytes(),b)

    def test_worker_manual_path_has_no_llm_or_pipeline_calls(self):
        from tools.service.cloud_worker import execute
        with temporary_workspace() as root:
            source=self.fixture(root);data=source.read_bytes();j={'id':'11111111-1111-4111-8111-111111111111','lease':'lease','input_url':'https://storage.example/input','source_sha256':hashlib.sha256(data).hexdigest(),'filename':'example.pdf','options':{'operation':'manual_redaction','regions':[self.region(source)]}}
            with patch('tools.service.cloud_worker.urlopen',return_value=io.BytesIO(data)),patch('tools.service.cloud_worker.rpc') as rpc,patch('transformation.workflows.run_workflow',side_effect=AssertionError('Pipeline must not start')):
                execute({'supabase_url':'https://storage.example','engine_profile':{}},j,root)
            self.assertEqual(rpc.call_count,2)
            self.assertTrue(rpc.call_args.kwargs['raw'].startswith(b'--tx'))
            report=json.loads((root/j['id']/'manual-redaction-audit.json').read_text())
            self.assertEqual(report['selected_text_check'],'passed')


if __name__=='__main__':unittest.main()
