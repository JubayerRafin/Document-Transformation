"""Phase 1 PII fixes: phone false positives, single-model OCR, cropped pages,
form fields/comments/bookmarks, output language and raster resolution.

Synthetic only: no network, GPU, Ollama or real documents.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf

from tests.temp_workspace import temporary_workspace
from transformation.validation.privacy import detect
from transformation.validation.value_redaction import redact_pdf, native_pattern_regions, literal_spans


def phones(text):
    return [f['value'] for f in detect([{'id':'r','text':text}],['Phone numbers'],'','',False)]


class PhonePatternTests(unittest.TestCase):
    def test_columns_of_years_and_prices_are_not_phone_numbers(self):
        for text in ('Year\n2023\n2024\n2025','Price\n1200\n3400\n5600','Fiscal 2023 2024 2025','2023\n2024 2025'):
            self.assertEqual(phones(text),[],text)

    def test_real_phone_numbers_are_still_found(self):
        self.assertEqual(phones('Call +82 10 1234 5678 today'),['+82 10 1234 5678'])
        self.assertEqual(phones('Phone: 010-1234-\n5678'),['010-1234-\n5678'])
        self.assertEqual(phones('Tel +1 415\n555 0100'),['+1 415\n555 0100'])
        self.assertEqual(phones('Office 02 1234\n5678'),['02 1234\n5678'])


class SingleModelOcrTests(unittest.TestCase):
    def test_repeat_reads_by_one_model_are_not_independent_agreement(self):
        from tools.ocr.recognize_paddle_boxes import bounded_recognition,independent_literal_evidence
        class Model:
            def predict(self,image):return [{'rec_text':'ID 4471-2290','rec_score':.99}]
        model=Model()
        best,review,decision,reads=bounded_recognition(np.full((20,160,3),255,np.uint8),model,lambda:model,'ko')
        self.assertTrue(review)
        self.assertEqual(decision,'sensitive_literal_single_model')
        self.assertEqual({r['model'] for r in reads},{'PP-OCRv5_server_rec'})
        self.assertFalse(independent_literal_evidence(reads,best['text']))

    def test_english_still_uses_two_distinct_models(self):
        from tools.ocr.recognize_paddle_boxes import bounded_recognition
        class Model:
            def predict(self,image):return [{'rec_text':'ID 4471-2290','rec_score':.99}]
        primary,secondary=Model(),Model()
        _,review,decision,reads=bounded_recognition(np.full((20,160,3),255,np.uint8),primary,lambda:secondary,'en')
        self.assertFalse(review);self.assertEqual(decision,'cross_model_exact_agreement')
        self.assertEqual({r['model'] for r in reads},{'en_PP-OCRv5_mobile_rec','PP-OCRv5_server_rec'})


class RedactionTests(unittest.TestCase):
    def native_finding(self,data,value,category):
        regions=native_pattern_regions(data)
        region=next(r for r in regions if literal_spans(r['text'],value))
        a,b=literal_spans(region['text'],value)[0]
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            units=[{'number':i+1,'regions':[]} for i in range(len(pdf))]
        return {'units':units,'pii_source_regions':regions},[{'region_id':region['id'],'value':region['text'][a:b],'category':category}]

    def test_ocr_value_on_page_with_offset_crop_box_is_redacted(self):
        with pymupdf.open() as pdf:
            page=pdf.new_page(width=600,height=800)
            page.draw_rect(pymupdf.Rect(100,100,500,700),color=(.9,.9,.9),fill=(.95,.95,.95))
            page.set_cropbox(pymupdf.Rect(100,100,500,700))
            data=pdf.tobytes()
        region={'id':'u1:r1','kind':'text','text':'ID 4471-2290','bbox':[10,10,120,26]}
        ir={'units':[{'id':'u1','number':1,'regions':[region]}]}
        output,audit=redact_pdf(data,ir,[{'region_id':'u1:r1','value':'ID 4471-2290','category':'Identifiers'}])
        self.assertEqual(audit[0]['method'],'ocr_value_spans')
        with pymupdf.open(stream=output,filetype='pdf') as pdf:
            pix=pdf[0].get_pixmap(clip=pymupdf.Rect(20,14,110,22))
            self.assertLessEqual(max(pix.samples),12)

    def test_form_field_values_are_flattened_and_removed(self):
        with pymupdf.open() as pdf:
            page=pdf.new_page()
            page.insert_text((72,100),'Email: jane.doe@example.com',fontsize=11)
            page.insert_text((72,140),'Contact field:',fontsize=11)
            widget=pymupdf.Widget();widget.field_name='contact';widget.field_type=pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.rect=pymupdf.Rect(160,128,400,146);widget.field_value='jane.doe@example.com';page.add_widget(widget)
            data=pdf.tobytes()
        ir,findings=self.native_finding(data,'jane.doe@example.com','Email addresses')
        output,_=redact_pdf(data,ir,findings)
        with pymupdf.open(stream=output,filetype='pdf') as pdf:
            text=pdf[0].get_text()
            self.assertNotIn('jane.doe',text)
            self.assertIn('Email:',text);self.assertIn('Contact field:',text)
            self.assertEqual(list(pdf[0].widgets() or []),[])
        from transformation.validation.output_validation import inspect_output
        self.assertEqual(inspect_output(output,'pdf',['jane.doe@example.com'])['known_sensitive_residuals'],[])

    def test_comments_links_and_bookmarks_with_selected_values_are_removed(self):
        with pymupdf.open() as pdf:
            page=pdf.new_page()
            page.insert_text((72,100),'Email: jane.doe@example.com',fontsize=11)
            page.insert_text((72,160),'Keep this note',fontsize=11)
            note=page.add_text_annot((300,300),'Ask jane.doe@example.com');note.update()
            other=page.add_text_annot((300,400),'Unrelated reviewer note');other.update()
            page.insert_link({'kind':pymupdf.LINK_URI,'from':pymupdf.Rect(72,90,250,104),'uri':'mailto:jane.doe@example.com'})
            pdf.set_toc([[1,'Contact jane.doe@example.com',1],[1,'Overview',1]])
            data=pdf.tobytes()
        ir,findings=self.native_finding(data,'jane.doe@example.com','Email addresses')
        output,_=redact_pdf(data,ir,findings)
        with pymupdf.open(stream=output,filetype='pdf') as pdf:
            notes=[a.info['content'] for a in pdf[0].annots()]
            self.assertEqual(notes,['Unrelated reviewer note'])
            self.assertEqual(pdf[0].get_links(),[])
            self.assertEqual([t[1] for t in pdf.get_toc()],['[REDACTED]','Overview'])
            self.assertIn('Keep this note',pdf[0].get_text())

    def test_output_validation_reports_values_left_in_form_fields_and_bookmarks(self):
        from transformation.validation.output_validation import inspect_output
        with pymupdf.open() as pdf:
            page=pdf.new_page()
            widget=pymupdf.Widget();widget.field_name='id';widget.field_type=pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.rect=pymupdf.Rect(72,72,300,90);widget.field_value='EMP-44712';page.add_widget(widget)
            pdf.set_toc([[1,'Account EMP-44712',1]])
            data=pdf.tobytes()
        locations={r['location'] for r in inspect_output(data,'pdf',['EMP-44712'])['known_sensitive_residuals']}
        self.assertIn('page 1 form field',locations);self.assertIn('PDF bookmarks',locations)


class VerificationTests(unittest.TestCase):
    def test_privacy_check_uses_output_language_and_a_sharp_raster(self):
        from transformation.validation import privacy_verification
        with pymupdf.open() as pdf:
            pdf.new_page(width=612,height=792).insert_text((72,72),'Hello',fontsize=8)
            data=pdf.tobytes()
        seen={}
        def run_ocr(source,folder,python,timeout,source_language):
            seen['language']=source_language
            with pymupdf.open(source) as raster:
                seen['image_width']=raster[0].get_image_info()[0]['width']
            yield {'recognition_dir':str(folder)}
        def prepare(data,recognition,folder,name,allow_unreadable=False):
            (Path(folder)/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':[]}]}),encoding='utf-8')
        with temporary_workspace() as root, \
             patch('transformation.ocr.paddle_pipeline.run_ocr',side_effect=run_ocr), \
             patch('transformation.ocr.paddle_handoff.prepare',side_effect=prepare):
            result=list(privacy_verification.verify(data,'pdf',root,[],['Email addresses'],'','',False,
                                                    {'ocr_python':'python','target':'ko'},language='en'))[-1]
        self.assertEqual(seen['language'],'en')
        self.assertGreater(seen['image_width'],1200)  # 72 dpi would be 612 px
        self.assertTrue(result['passed'])


if __name__=='__main__':unittest.main()
