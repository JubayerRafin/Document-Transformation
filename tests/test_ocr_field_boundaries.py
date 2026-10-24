import unittest
from types import SimpleNamespace as NS
import numpy as np

from tools.ocr.recognize_paddle_boxes import bounded_recognition,refine_recognition,tight_source_crop,source_word_segments,source_detail_crop
from extraction.backends.raster_tables import grid_boxes
from transformation.pdf.ir_bridge import lock_ocr_regions, map_document
from babeldoc.format.pdf.document_il.il_version_1 import (
    Box, PdfCharacter, PdfStyle, PdfParagraph, PdfParagraphComposition,
)


class RecognitionTests(unittest.TestCase):
    def test_targeted_all_views_agree_with_confident_verification_engine(self):
        primary=self.Model([('Q8.4N',.88)]*2)
        secondary=self.Model([('Q8.4N',.97)]*2)
        self.assertFalse(refine_recognition(np.full((20,100,3),255,np.uint8),primary,lambda:secondary,'en',[])[1])

    def test_targeted_retry_requires_model_agreement_and_respects_strong_conflicts(self):
        previous=[{'text':'INV-7201','score':.85}]
        primary=self.Model([('INV-7202',.98)]*2)
        secondary=self.Model([('INV-7202',.98)]*2)
        crop=np.full((20,100,3),255,np.uint8)
        best,review,_,reads=refine_recognition(crop,primary,lambda:secondary,'en',previous)
        self.assertFalse(review); self.assertEqual(best['text'],'INV-7202')
        self.assertEqual(len(reads),4)
        previous[0]['score']=.99
        primary=self.Model([('INV-7202',.98)]*2); secondary=self.Model([('INV-7202',.98)]*2)
        self.assertTrue(refine_recognition(crop,primary,lambda:secondary,'en',previous)[1])

    def test_targeted_retry_does_not_resolve_conflicting_identifier_models(self):
        primary=self.Model([('sk_demo123',.99)]*2)
        secondary=self.Model([('s_demo123',.99)]*2)
        self.assertTrue(refine_recognition(np.full((20,100,3),255,np.uint8),primary,lambda:secondary,'en',[])[1])

    def test_tight_crop_keeps_single_pixel_punctuation(self):
        crop=np.full((30,150,3),255,np.uint8)
        crop[10:18,20:60]=0; crop[17,130]=0
        tight=tight_source_crop(crop)
        self.assertGreater(tight.shape[1],110)
        self.assertTrue(np.any(np.all(tight[:,-2]==0,axis=1)))

    def test_dark_panel_is_not_mistaken_for_a_solid_text_glyph(self):
        import cv2
        crop=np.full((60,220,3),255,np.uint8)
        crop[10:50,4:216]=40
        cv2.putText(crop,'Console',(15,35),cv2.FONT_HERSHEY_SIMPLEX,.5,(230,230,230),1)
        tight=tight_source_crop(crop)
        self.assertLess(tight.shape[0],40)

    def test_segmentation_preserves_source_pixels_and_call_budget(self):
        crop=np.full((14,900,3),255,np.uint8)
        for x in range(10,880,40): crop[2:12,x:x+25]=0
        parts=source_word_segments(crop)
        self.assertLessEqual(len(parts),4)
        self.assertTrue(np.array_equal(np.concatenate(parts,axis=1),crop))

    def test_high_resolution_image_is_read_at_its_source_density(self):
        import pymupdf,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.pdf'
            pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,400,400),False); pix.clear_with(255)
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=100,height=100); page.insert_image(page.rect,pixmap=pix); pdf.save(path)
            crop=source_detail_crop(path,[20,20,40,40],np.zeros((20,20,3),np.uint8))
            self.assertEqual(crop.shape[:2],(96,96))

    class Model:
        def __init__(self, responses): self.responses=iter(responses); self.calls=0
        def predict(self, image):
            self.calls+=1
            text,score=next(self.responses)
            return [{'rec_text':text,'rec_score':score}]

    def test_agreeing_source_views_skip_expensive_verification(self):
        model=self.Model([('Invoice total',.99)]*2)
        def unexpected(): raise AssertionError('No verification needed')
        best,review,_,reads=bounded_recognition(np.full((20,160,3),255,np.uint8),model,unexpected,'en')
        self.assertFalse(review); self.assertEqual(best['text'],'Invoice total')
        self.assertEqual(len(reads),2)

    def test_conflicting_identifiers_remain_unresolved_with_bounded_calls(self):
        primary=self.Model([('API Key: sk_abc12',.97),('API Key: s_abc12',.96),('API Key: sk_abc13',.98)])
        secondary=self.Model([('API Key: sk_abc14',.97)]*2)
        _,review,_,reads=bounded_recognition(np.full((20,160,3),255,np.uint8),primary,lambda:secondary,'en')
        self.assertTrue(review); self.assertEqual(len(reads),5)
        self.assertEqual(primary.calls+secondary.calls,5)


class GridTests(unittest.TestCase):
    def test_nested_and_locked_context_cannot_leak_selected_pii(self):
        import json
        from extraction.context_bridge import masked_context_ir
        region={'text':'Contact: secret@example.org','metadata':{'row_context':{'row_values':['secret@example.org']}}}
        ir={'units':[{'regions':[region],'locked_regions':[region]}]}
        safe=masked_context_ir(ir,['secret@example.org'])
        self.assertNotIn('secret@example.org',json.dumps(safe))
        self.assertIn('secret@example.org',json.dumps(ir))

    def test_independent_validation_catches_readability_regression(self):
        import pymupdf
        from transformation.validation.output_validation import inspect_output
        with pymupdf.open() as pdf:
            page=pdf.new_page(); page.insert_text((40,60),'Tiny translated label',fontsize=6.5)
            result=inspect_output(pdf.tobytes(),'pdf',min_font_size=8)
        self.assertFalse(result['acceptable'])
        self.assertTrue(any(r['reason']=='text below configured font limit' for r in result['geometry_blockers']))

    def test_general_grid_and_callout_distinction(self):
        import cv2
        image=np.full((500,650,3),255,np.uint8)
        for x in (90,210,330,450): cv2.line(image,(x,160),(x,400),(0,0,0),2)
        for y in (160,240,320,400): cv2.line(image,(90,y),(450,y),(0,0,0),2)
        cv2.rectangle(image,(100,30),(500,100),(0,0,0),2)
        tables=grid_boxes(image)
        self.assertEqual(len(tables),1)
        xs,ys=tables[0]
        self.assertEqual((len(xs)-1,len(ys)-1),(3,3))
        self.assertAlmostEqual(xs[0],90,delta=3)


class FieldLockTests(unittest.TestCase):
    def fixture(self):
        chars=[]
        for word,start in [('Alpha',10),('Beta',80)]:
            for i,letter in enumerate(word):
                chars.append(PdfParagraphComposition(pdf_character=PdfCharacter(
                    char_unicode=letter,box=Box(x=start+i*6,y=80,x2=start+i*6+5,y2=90),
                    pdf_style=PdfStyle(font_size=10))))
        paragraph=PdfParagraph(box=Box(x=10,y=80,x2=110,y2=90),unicode='Alpha Beta',
            debug_id='merged',pdf_paragraph_composition=chars)
        document=NS(page=[NS(page_number=0,pdf_paragraph=[paragraph])])
        fields=[{'id':'a','text':'Alpha','bbox':[10,10,40,20],'slot_bbox':[8,8,45,23]},
                {'id':'b','text':'Beta','bbox':[80,10,104,20],'slot_bbox':[78,8,110,23]}]
        ir={'units':[{'number':1,'height':100,'regions':[],'locked_regions':fields}]}
        return document,ir

    def test_merged_fields_separate_without_losing_characters(self):
        doc,ir=self.fixture(); inputs,slots,report=lock_ocr_regions(doc,ir)
        self.assertEqual(list(inputs.values()),['Alpha','Beta'])
        self.assertEqual(len(doc.page[0].pdf_paragraph),2)
        self.assertTrue(all(s['min_font_size']==8 for s in slots.values()))
        self.assertTrue(all(r['status']=='mapped' for r in map_document(doc,ir)))

    def test_source_disagreement_rejects_instead_of_rewriting(self):
        doc,ir=self.fixture(); ir['units'][0]['locked_regions'][0]['text']='AlpHa'
        with self.assertRaisesRegex(ValueError,'association needs review'): lock_ocr_regions(doc,ir)
        self.assertEqual(doc.page[0].pdf_paragraph[0].unicode,'Alpha Beta')

    def test_unassigned_glyphs_reject_instead_of_disappearing(self):
        doc,ir=self.fixture(); ir['units'][0]['locked_regions'].pop()
        with self.assertRaisesRegex(ValueError,'Unassociated native glyphs'): lock_ocr_regions(doc,ir)


if __name__=='__main__': unittest.main()
