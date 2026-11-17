import unittest
from collections import Counter
import pymupdf
import numpy as np
from unittest.mock import patch,Mock
import json
from pathlib import Path
from tests.temp_workspace import temporary_workspace
from transformation.pipeline import redact_pdf
from transformation.validation.privacy import detect,mask,mask_runs
from transformation.validation.value_redaction import (
    RedactionLocalizationError,compact,literal_spans,native_pattern_regions,verified_ocr_words,
)


class ValueRedactionTests(unittest.TestCase):
    def test_pipeline_recovers_native_pattern_missing_from_extraction(self):
        from transformation.pipeline import run_pipeline
        from transformation.validation.privacy import detect as real_detect
        data=self.source([(30,40,'Email: alex@example-'),(30,54,'corp.test')])
        calls=0
        def detector(*args):
            nonlocal calls
            calls+=1
            return [] if calls==1 else real_detect(*args)
        with temporary_workspace() as root,patch('transformation.pipeline.detect',side_effect=detector):
            event=list(run_pipeline(data,'unseen.pdf',translate=False,redact=True,
                pii_categories=['Email addresses'],pii_assist=False,table_strategy='native',root=root))[-1]
            self.assertEqual(event['status'],'generated',event.get('error'))
            evidence=json.loads((Path(event['run_dir'])/'source-ir.json').read_text(encoding='utf-8'))
            self.assertTrue(evidence['pii_source_regions'])
            with pymupdf.open(event['output_file']) as pdf:
                self.assertIn('Email:',pdf[0].get_text())
                self.assertNotIn('corp.test',pdf[0].get_text())

    def test_fresh_ocr_words_require_two_models_and_preserve_transcription(self):
        from tools.ocr.recognize_paddle_boxes import redaction_word_candidates
        crop=np.full((20,100,3),255,np.uint8)
        record={'text':'Name: Ann','polygon_pixels':[[0,0],[100,0],[100,20],[0,20]]}
        class Model:
            def __init__(self):self.calls=0
            def predict(self,image):
                self.calls+=1
                return [{'rec_text':('Name:' if self.calls==1 else 'Ann'),'rec_score':.99}]
        first,second=Model(),Model()
        with patch('tools.ocr.recognize_paddle_boxes.split_word_crops',return_value=[crop[:,:40],crop[:,40:]]):
            candidates=redaction_word_candidates(crop,record,first,lambda:second,'en')
        self.assertEqual(len(candidates),2)
        self.assertEqual(first.calls+second.calls,4)
        self.assertEqual(record['text'],'Name: Ann')
        line=dict(record,bbox=[0,0,100,20],candidates=candidates)
        self.assertEqual(len(verified_ocr_words(line,line['text'],line['bbox'])),2)

    def test_plain_ocr_prose_and_unsupported_geometry_do_not_load_word_models(self):
        from tools.ocr.recognize_paddle_boxes import redaction_word_candidates
        model=Mock();secondary=Mock()
        self.assertEqual(redaction_word_candidates(np.zeros((10,100,3)),{'text':'Ordinary translated paragraph'},model,secondary,'en'),[])
        secondary.assert_not_called();model.predict.assert_not_called()

    def source(self, lines, rotate=0, crop=False):
        with pymupdf.open() as pdf:
            p=pdf.new_page(width=400,height=250)
            for x,y,text in lines:p.insert_text((x,y),text,fontsize=11)
            if crop:p.set_cropbox(pymupdf.Rect(10,10,390,240))
            p.set_rotation(rotate)
            return pdf.tobytes()

    def apply(self,data,value,category='Email addresses'):
        regions=native_pattern_regions(data)
        region=next(r for r in regions if literal_spans(r['text'],value))
        # Detector findings retain exact source whitespace rather than invented spelling.
        a,b=literal_spans(region['text'],value)[0]
        finding={'region_id':region['id'],'value':region['text'][a:b],'category':category}
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            units=[{'number':i+1,'regions':[]} for i in range(len(pdf))]
        return redact_pdf(data,{'units':units,'pii_source_regions':regions},[finding])[0]

    def test_table_cell_clipping_an_overflowing_value_is_covered_by_the_whole_value(self):
        value='kim.lee@harbor-post.example'
        data=self.source([(30,40,'Owner'),(120,40,value),(30,80,'Keep this line')])
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            hit=pdf[0].search_for(value)[0]
        # The extractor's cell ends inside the address and holds only its first part.
        cell={'id':'t:cell-1-1','text':'kim.lee@harbor-post.exa','bbox':[hit.x0-2,hit.y0,hit.x0+(hit.width*.8),hit.y1]}
        native=next(r for r in native_pattern_regions(data) if literal_spans(r['text'],value))
        findings=[{'region_id':cell['id'],'value':cell['text'],'category':'Email addresses'},
                  {'region_id':native['id'],'value':value,'category':'Email addresses'}]
        ir={'units':[{'number':1,'regions':[cell]}],'pii_source_regions':[native]}
        output=redact_pdf(data,ir,findings)[0]
        with pymupdf.open(stream=output,filetype='pdf') as pdf:
            text=pdf[0].get_text()
        self.assertNotIn('harbor',text); self.assertIn('Owner',text); self.assertIn('Keep this line',text)

    def test_clipped_cell_value_without_a_whole_value_still_requires_review(self):
        value='kim.lee@harbor-post.example'
        data=self.source([(120,40,value)])
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            hit=pdf[0].search_for(value)[0]
        cell={'id':'t:cell-1-1','text':'kim.lee@harbor-post.exa','bbox':[hit.x0-2,hit.y0,hit.x0+(hit.width*.8),hit.y1]}
        with self.assertRaises(RedactionLocalizationError):
            redact_pdf(data,{'units':[{'number':1,'regions':[cell]}]},[{'region_id':cell['id'],'value':cell['text'],'category':'Email addresses'}])

    def test_wrapped_email_repeated_in_table_style_block_is_fully_removed(self):
        value='alex.river@example-corp.test'
        data=self.source([(30,35,'Contact: '+value),(30,90,'Email:'),
                          (120,90,'alex.river@example-'),(120,104,'corp.test'),(30,140,'Phone: keep this')])
        output=self.apply(data,value)
        with pymupdf.open(stream=output,filetype='pdf') as pdf:
            text=pdf[0].get_text()
            self.assertNotIn('alex.river',text);self.assertNotIn('corp.test',text)
            for label in ('Contact:','Email:','Phone: keep this'):self.assertIn(label,text)

    def test_wrapped_only_email_is_detected_with_exact_offsets(self):
        text='Email: alex@example-\ncorp.test'
        findings=detect([{'id':'r','text':text}],['Email addresses'],'','',False)
        self.assertEqual(len(findings),1)
        self.assertEqual(findings[0]['value'],'alex@example-\ncorp.test')
        for a,b in findings[0]['spans']:self.assertEqual(text[a:b],findings[0]['value'])

    def test_label_on_previous_line_is_not_part_of_email(self):
        findings=detect([{'id':'r','text':'Email\nalex@example.test'}],['Email addresses'],'','',False)
        self.assertEqual(findings[0]['value'],'alex@example.test')

    def test_wrapped_phone_is_removed_without_its_label(self):
        text='Phone: 010-1234-\n5678'
        findings=detect([{'id':'r','text':text}],['Phone numbers'],'','',False)
        self.assertEqual(findings[0]['value'],'010-1234-\n5678')
        data=self.source([(30,40,'Phone: 010-1234-'),(30,54,'5678')])
        result=self.apply(data,'010-1234-5678','Phone numbers')
        with pymupdf.open(stream=result,filetype='pdf') as pdf:
            self.assertIn('Phone:',pdf[0].get_text())
            self.assertNotIn('5678',pdf[0].get_text())

    def test_names_do_not_remove_titles_or_substrings_of_other_words(self):
        data=self.source([(30,40,'Name: Ann Lee; annual review'),(30,90,'Reviewer: Ann Lee')])
        output=self.apply(data,'Ann Lee','Names')
        with pymupdf.open(stream=output,filetype='pdf') as pdf:
            text=pdf[0].get_text()
            self.assertNotIn('Ann Lee',text)
            for label in ('Name:','annual review','Reviewer:'):self.assertIn(label,text)
        self.assertEqual(literal_spans('Annual report; Ann signed','Ann'),[(15,18)])

    def test_masking_across_style_runs_and_wraps_retains_context(self):
        self.assertEqual(mask('Email: alex@example-\ncorp.test; keep',['alex@example-corp.test']),
                         'Email: [REDACTED]; keep')
        result=mask_runs(['Email: alex@','example-\n','corp.test; keep'],['alex@example-corp.test'])
        self.assertEqual(''.join(result),'Email: [REDACTED]; keep')

    def test_native_style_runs_and_table_rules_survive(self):
        with pymupdf.open() as pdf:
            p=pdf.new_page(width=400,height=200)
            p.draw_line((20,85),(380,85));p.draw_line((20,115),(380,115))
            p.insert_text((30,100),'Email: ')
            p.insert_text((100,100),'alex@',fontname='hebo')
            p.insert_text((127,100),'example.test')
            data=pdf.tobytes()
        output=self.apply(data,'alex@example.test')
        with pymupdf.open(stream=output,filetype='pdf') as pdf:
            self.assertIn('Email:',pdf[0].get_text())
            self.assertNotIn('alex',pdf[0].get_text())
            self.assertEqual(len(pdf[0].get_drawings()),3) # two rules plus the black mask

    def test_rotated_and_cropped_native_page_keeps_its_label(self):
        for angle in (0,90,180,270):
            data=self.source([(30,60,'Email: alex@example.test')],rotate=angle,crop=True)
            output=self.apply(data,'alex@example.test')
            with pymupdf.open(stream=output,filetype='pdf') as pdf:
                self.assertIn('Email:',pdf[0].get_text())
                self.assertNotIn('alex',pdf[0].get_text())
                self.assertEqual(pdf[0].rotation,angle)

    def test_scan_without_value_geometry_requires_review_instead_of_erasing_label(self):
        with pymupdf.open() as pdf:
            p=pdf.new_page(width=200,height=100)
            pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,200,100),False);pix.clear_with(255)
            p.insert_image(p.rect,pixmap=pix);data=pdf.tobytes()
        ir={'units':[{'number':1,'regions':[{'id':'r','text':'Name: Ann Lee','bbox':[20,30,150,50]}]}]}
        with self.assertRaises(RedactionLocalizationError) as error:
            redact_pdf(data,ir,[{'region_id':'r','value':'Ann Lee'}])
        self.assertEqual(error.exception.regions[0]['reason'],'missing_verified_word_geometry')

    def test_native_supplement_cannot_be_forged(self):
        data=self.source([(30,40,'No sensitive value')])
        regions=native_pattern_regions(data);regions[0]['text']='Injected Name'
        with self.assertRaisesRegex(ValueError,'does not match'):
            redact_pdf(data,{'units':[{'number':1,'regions':[]}],'pii_source_regions':regions},
                       [{'region_id':regions[0]['id'],'value':'Injected Name'}])

    def test_verified_ocr_geometry_needs_agreement_and_real_crop_boundaries(self):
        words=[{'text':'Name:','score':.99,'start':0,'end':40},
               {'text':'Ann','score':.99,'start':40,'end':70},
               {'text':'Lee','score':.99,'start':70,'end':100}]
        line={'text':'Name: Ann Lee','bbox':[10,20,110,40],
              'polygon_pixels':[[0,0],[100,0],[100,20],[0,20]],
              'candidates':[{'variant':v,'words':[dict(w) for w in words]}
                            for v in ('word_gap_primary','word_gap_english')]}
        result=verified_ocr_words(line,line['text'],line['bbox'])
        self.assertEqual([r['text'] for r in result],['Name:','Ann','Lee'])
        self.assertEqual(result[1]['bbox'],[50.,20.,80.,40.])
        line['candidates'][1]['words'][1]['text']='Ana'
        self.assertEqual(verified_ocr_words(line,line['text'],line['bbox']),[])


if __name__=='__main__':unittest.main()
