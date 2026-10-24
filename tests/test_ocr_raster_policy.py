import unittest
import hashlib
import json
import tempfile
from pathlib import Path
import numpy as np
import pymupdf
from tools.ocr.raster_policy import render_page, render_verified_page, preserve_background, MAX_SIDE, MAX_PIXELS


class RasterPolicyTests(unittest.TestCase):
    def document(self, width=400, height=250, points=(136,85)):
        pdf=pymupdf.open()
        page=pdf.new_page(width=points[0],height=points[1])
        y,x=np.indices((height,width))
        pixels=np.stack((x%256,y%256,(x+y)%256),axis=-1).astype(np.uint8)
        page.insert_image(page.rect,pixmap=pymupdf.Pixmap(pymupdf.csRGB,width,height,pixels.tobytes(),False))
        return pdf

    def test_native_density_survives_crop_pdf_and_worker(self):
        with self.document() as pdf:
            pix,meta=render_page(pdf[0])
            self.assertGreaterEqual(pix.width,400)
            self.assertGreaterEqual(pix.height,250)
            with pymupdf.open() as crop:
                p=crop.new_page(width=136,height=85)
                p.insert_image(p.rect,pixmap=pix)
                worker,proof=render_page(p)
                self.assertGreaterEqual(worker.width,400)
                self.assertEqual(proof['point_bounds'],[0,0,136,85])
                self.assertEqual(render_verified_page(p,proof).samples,worker.samples)

    def test_existing_one_pixel_per_point_scan_unchanged(self):
        with self.document(1000,1400,(1000,1400)) as pdf:
            pix,meta=render_page(pdf[0])
            self.assertEqual(meta['scale'],1)
            self.assertEqual(pix.samples,pdf[0].get_pixmap(alpha=False).samples)

    def test_vector_and_large_pages_bounded(self):
        with pymupdf.open() as pdf:
            p=pdf.new_page(width=100,height=80)
            pix,meta=render_page(p)
            self.assertEqual((pix.width,pix.height),(300,240))
            p=pdf.new_page(width=10000,height=10000)
            pix,meta=render_page(p)
            self.assertLessEqual(max(pix.width,pix.height),MAX_SIDE)
            self.assertLessEqual(pix.width*pix.height,MAX_PIXELS)
            self.assertTrue(meta['limited'])

    def test_tampered_proof_rejected_and_legacy_supported(self):
        with self.document() as pdf:
            pix,meta=render_page(pdf[0])
            meta['scale']+=.01
            with self.assertRaisesRegex(ValueError,'geometry'):
                render_verified_page(pdf[0],meta)
            meta['policy']='untrusted'
            with self.assertRaisesRegex(ValueError,'Unknown'):
                render_verified_page(pdf[0],meta)
            self.assertEqual(render_verified_page(pdf[0],None).width,136)

    def test_native_background_preserved_outside_committed_mask(self):
        with self.document() as pdf:
            p=pdf[0]
            original,meta=render_page(p)
            native=np.frombuffer(original.samples,dtype=np.uint8).reshape(original.height,original.width,3)
            analysis=np.full((85,136,3),255,dtype=np.uint8)
            mask=np.zeros((85,136),dtype=bool)
            out,_=preserve_background(p,analysis,mask)
            self.assertEqual(out.samples,original.samples)
            mask[20:30,40:60]=True
            out,_=preserve_background(p,analysis,mask)
            pixels=np.frombuffer(out.samples,dtype=np.uint8).reshape(original.height,original.width,3)
            xs=np.minimum(((np.arange(original.width)+.5)/(original.width/136)).astype(int),135)
            ys=np.minimum(((np.arange(original.height)+.5)/(original.height/85)).astype(int),84)
            highmask=mask[ys[:,None],xs[None,:]]
            self.assertTrue(np.array_equal(pixels[~highmask],native[~highmask]))
            self.assertTrue(np.all(pixels[highmask]==255))

    def test_invalid_clip_rejected(self):
        with self.document() as pdf:
            for box in ((0,0,0,0),(-1,0,40,40),(0,0,1000,1000)):
                with self.assertRaises(ValueError):render_page(pdf[0],box)

    def test_high_density_handoff_keeps_pdf_coordinates_and_source_pixels(self):
        from transformation.ocr.paddle_handoff import prepare
        with tempfile.TemporaryDirectory() as folder,self.document() as pdf:
            root=Path(folder)
            pix,proof=render_page(pdf[0])
            pix.save(root/'page-1.png')
            (root/'page-1_res.json').write_text('{}')
            recognition=root/'recognition';recognition.mkdir()
            line={'id':'line1','text':'Example','bbox':[10,10,100,30],
                  'recognition_score':.99,'requires_review':False}
            (recognition/'lines.jsonl').write_text(json.dumps(line)+'\n')
            metadata={'status':'completed','detection_run':str(root),'lines':1,'raster':proof,
                'versions':{'paddleocr':'3.7.0'},
                'image_sha256':hashlib.sha256((root/'page-1.png').read_bytes()).hexdigest(),
                'detection_sha256':hashlib.sha256((root/'page-1_res.json').read_bytes()).hexdigest()}
            (recognition/'manifest.json').write_text(json.dumps(metadata))
            result=prepare(pdf.tobytes(),recognition,root/'out','unseen.pdf')
            ir=json.loads(Path(result['document_ir']).read_text())
            self.assertEqual(ir['pages'][0]['regions'][0]['bbox'],line['bbox'])
            with pymupdf.open(result['input_pdf']) as output:
                self.assertEqual(render_page(output[0])[0].samples,pix.samples)

    def test_cache_identifies_native_detail_and_policy(self):
        from transformation.ocr.cache import location
        from unittest.mock import patch
        with self.document() as pdf:
            data=pdf.tobytes()
            before=location(data,'en','gpu:0','cache')
            with patch('transformation.ocr.cache.hashlib.sha256',wraps=hashlib.sha256) as digest:
                self.assertEqual(before,location(data,'en','gpu:0','cache'))
                self.assertGreater(len(digest.call_args_list[-1].args[0]),400*250*3)

    def test_tiny_label_does_not_abort_other_verified_lines(self):
        from tests.test_partial_field_handoff import PartialFieldTests
        from transformation.ocr.paddle_handoff import prepare
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            data,recognition,line=PartialFieldTests().fixture(root)
            tiny=dict(id='tiny',text='Small label',bbox=[10,10,45,12],
                      recognition_score=.99,requires_review=False)
            (recognition/'lines.jsonl').write_text(json.dumps(line)+'\n'+json.dumps(tiny)+'\n')
            meta=json.loads((recognition/'manifest.json').read_text());meta['lines']=2
            (recognition/'manifest.json').write_text(json.dumps(meta))
            result=prepare(data,recognition,root/'out','unseen.pdf')
            ir=json.loads(Path(result['document_ir']).read_text())
            region=ir['pages'][0]['regions'][-1]
            self.assertTrue(region['metadata']['source_layer_omitted'])
            self.assertFalse(region['metadata']['requires_review'])
            with pymupdf.open(result['input_pdf']) as output:
                self.assertIn('Token:',output[0].get_text())
                self.assertNotIn('Small label',output[0].get_text())
