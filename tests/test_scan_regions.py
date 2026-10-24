import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pymupdf
from tools.ocr.recognize_paddle_boxes import split_long_crop, split_word_crops, select_candidate, cross_model_candidate
from transformation.pdf.scan_worker import main, observed_protected_values
from transformation.pdf.scan_reconstruction import fit_line, fit_text, available_line_box, available_text_box, validate_placements


class RegionTests(unittest.TestCase):
    def test_short_border_inside_ocr_box_stops_expansion(self):
        pixels=np.full((100,240,3),250,dtype=np.uint8)
        pixels[20:60,119]=220
        region={'id':'a','bbox':[20,30,120,46]}
        rules=np.zeros((100,240),dtype=bool)
        box=available_text_box(region,[region],pixels,rules)
        self.assertLess(box.x1,119)

    def test_actual_writer_rejects_outside_or_clipped_text(self):
        font=pymupdf.Font('cjk')
        box=pymupdf.Rect(10,10,200,40)
        fitted=fit_text(box,'한국어 Sample',font,max_size=12)
        self.assertTrue(validate_placements(box,fitted,font))
        self.assertFalse(validate_placements(box,[((195,25),'This is outside',12)],font))

    def test_neighbor_identifiers_are_not_sent_to_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'output').mkdir()
            with pymupdf.open() as pdf:
                pdf.new_page()
                pdf.save(root/'source.pdf')
            (root/'manifest.json').write_text(json.dumps(dict(target_language='ko',endpoint='unused',model='test',protected_values=[],document_context='')))
            regions=[dict(id='a',text='Registered only.',bbox=[10,10,100,20]),
                     dict(id='b',text='ID: SECRET-001',bbox=[12,25,102,35])]
            (root/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':regions}]}))
            with patch('transformation.llm.entities.product_names',return_value=([],{})), \
                 patch('transformation.llm.field_context.infer_field_context',return_value=(None,None)), \
                 patch('transformation.llm.field_context.infer_scope',return_value=('Physical access warning',{})), \
                 patch('transformation.llm.translation_guard.audit_candidate',return_value={'faithful':True,'issues':[]}), \
                 patch('transformation.pdf.scan_worker.translate_paragraph',side_effect=[('경고',{}),('식별자: SECRET-001',{})]) as translator:
                main(root)
            context=translator.call_args_list[0].args[5]
            self.assertIn('Physical access warning',context)
            self.assertNotIn('SECRET-001',context)

    def test_prose_spacing_consensus_keeps_identifiers_exact(self):
        primary=[dict(text='Severalwords on a page',score=.97)]
        secondary=[dict(text='Several words on a page',score=.96)]*2
        self.assertIsNotNone(cross_model_candidate(primary,secondary))
        self.assertIsNone(cross_model_candidate([dict(text='Model CX-109',score=.99)],
            [dict(text='Model CX-10 9',score=.99)]*2))
        self.assertIsNone(cross_model_candidate([dict(text='A JPEG image',score=.99)],
            [dict(text='A IPEG image',score=.99)]*2))

    def test_word_crops_do_not_discard_pixels(self):
        image=np.full((20,100,3),255,dtype=np.uint8)
        image[3:17,5:35]=0
        image[3:17,55:90]=0
        pieces=split_word_crops(image)
        self.assertGreater(len(pieces),1)
        self.assertTrue(np.array_equal(np.concatenate(pieces,axis=1),image))
    def test_independent_confirmation_requires_exact_agreement(self):
        p=[dict(text='Device 82',score=.95),dict(text='Device 83',score=.97)]
        s=[dict(text='Device 82',score=.96),dict(text='Device 82',score=.94)]
        self.assertEqual(cross_model_candidate(p,s)['text'],'Device 82')
        self.assertIsNone(cross_model_candidate(p,[dict(text='Device 84',score=.99)]*2))
        self.assertIsNone(cross_model_candidate(p,[s[0],dict(text='Device 83',score=.99)]))
        self.assertIsNone(cross_model_candidate(p,[s[0],dict(text='Device 82',score=.8)]))

    def test_horizontal_growth_stops_at_borders_and_neighbors(self):
        pixels=np.full((80,200,3),255,dtype=np.uint8)
        rules=np.zeros((80,200),dtype=bool)
        region={'id':'a','bbox':[10,20,50,35]}
        other={'id':'b','bbox':[100,20,140,35]}
        box=available_text_box(region,[region,other],pixels,rules)
        self.assertGreater(box.x1,50)
        self.assertLess(box.x1,100)
        rules[:,75]=True
        self.assertLess(available_text_box(region,[region],pixels,rules).x1,75)
        pixels[:,60]=0
        self.assertLess(available_text_box(region,[region],pixels,rules).x1,60)
        fitted=fit_text(pymupdf.Rect(0,0,400,50),'Small label',pymupdf.Font('helv'),max_size=10)
        self.assertLessEqual(fitted[0][2],10)
    def test_wrapping_preserves_words_and_stays_inside_region(self):
        font = pymupdf.Font('helv')
        box = pymupdf.Rect(10,10,110,65)
        source = 'This translation has several words that need wrapping.'
        fitted = fit_text(box,source,font)
        self.assertIsNotNone(fitted)
        self.assertGreater(len(fitted),1)
        self.assertEqual(' '.join(t for _,t,_ in fitted),source)
        for (x,y), text, size in fitted:
            self.assertGreaterEqual(size,8)
            self.assertGreaterEqual(y-font.ascender*size,box.y0)
            self.assertLessEqual(y-font.descender*size,box.y1)
            self.assertLessEqual(x+font.text_length(text,fontsize=size),box.x1)
        self.assertIsNone(fit_text(pymupdf.Rect(0,0,15,60),'KEEP-THIS-IDENTIFIER-UNCHANGED',font))

    def test_majority_does_not_hide_disagreement(self):
        candidates = [dict(text=t,score=.99,variant=str(i)) for i,t in enumerate(['Invoice 123','Invoice 123','Invoice 128'])]
        self.assertTrue(select_candidate(candidates)[1])
        self.assertTrue(select_candidate([dict(text='',score=.99,variant='original')])[1])

    def test_weak_agreeing_read_does_not_veto_strong_pair(self):
        cs=[dict(text='Photo',score=s,variant=str(i)) for i,s in enumerate([.96,.87,.98])]
        self.assertFalse(select_candidate(cs)[1])

    def test_email_consensus_cannot_override_conflicting_read(self):
        cs=[dict(text=t,score=.97,variant=str(i)) for i,t in enumerate(['a@example.corp com','a@example.corp com','a@example-corp.com'])]
        self.assertTrue(select_candidate(cs)[1])

    def test_short_header_consensus_and_warning_fit(self):
        cs=[dict(text='Item',score=s,variant=str(i)) for i,s in enumerate([.82,.898,.875])]
        self.assertTrue(select_candidate(cs)[1])
        self.assertIsNotNone(fit_line(pymupdf.Rect(447,295,539,312),'허가자만 출입',pymupdf.Font('cjk')))

    def test_fitting_margin_respects_neighbor_and_rules(self):
        image=np.full((100,200,3),255,dtype=np.uint8)
        mask=np.zeros((100,200),dtype=bool)
        r={'id':'a','bbox':[20,30,180,50]}
        self.assertEqual(list(available_line_box(r,[r],image,mask)),[20,26,180,54])
        mask[29,:]=True
        self.assertEqual(list(available_line_box(r,[r],image,mask)),r['bbox'])

    def test_protection_matches_spacing_not_changed_characters(self):
        self.assertIn('EliteBook840', observed_protected_values('EliteBook840', ['EliteBook 840']))
        self.assertNotIn('EliteBook841', observed_protected_values('EliteBook841', ['EliteBook 840']))


    def test_fitted_glyphs_stay_inside_box(self):
        font = pymupdf.Font('cjk')
        box = pymupdf.Rect(10,10,250,35)
        size, baseline = fit_line(box, '한국어 테스트', font)
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_font(fontname='testfont',fontbuffer=font.buffer)
            page.insert_text(baseline,'한국어 테스트',fontname='testfont',fontsize=size)
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        self.assertTrue(box.contains(pymupdf.Rect(span['bbox'])))
    def test_whitespace_consensus_preserves_original_words(self):
        candidates = [dict(text='Scanned JPG', score=.94, variant='original'),
                      dict(text='Scann ed JPG', score=.97, variant='whitespace_segments')]
        best, review, _ = select_candidate(candidates)
        self.assertFalse(review)
        self.assertEqual(best['text'], 'Scanned JPG')

    def test_character_disagreement_still_blocks(self):
        candidates = [dict(text='EMP-0042', score=.98, variant='original'),
                      dict(text='EMP-0043', score=.99, variant='2x_cubic')]
        self.assertTrue(select_candidate(candidates)[1])

    def test_long_line_splits_only_at_blank_columns(self):
        image = np.full((20, 600, 3), 255, dtype=np.uint8)
        for x in range(10, 590, 30):
            image[3:17, x:x+20] = 0
        parts = split_long_crop(image)
        self.assertGreater(len(parts), 1)
        self.assertEqual(sum(p.shape[1] for p in parts), 600)
        self.assertTrue(np.array_equal(np.concatenate(parts, axis=1), image))

    def test_region_identity_and_uncertain_exclusion(self):
        audit_patch=patch('transformation.llm.translation_guard.audit_candidate',return_value={'faithful':True,'issues':[]})
        audit_patch.start()
        self.addCleanup(audit_patch.stop)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'output').mkdir()
            with pymupdf.open() as pdf:
                pdf.new_page()
                pdf.save(root/'source.pdf')
            (root/'manifest.json').write_text(json.dumps(dict(target_language='ko',endpoint='unused',model='test',protected_values=[],document_context='')))
            regions = [dict(id='a',text='Department',metadata={}),dict(id='b',text='uncertain',metadata={'requires_review':True}),dict(id='c',text='Issued',metadata={})]
            (root/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':regions}]}))
            with patch('transformation.pdf.scan_worker.translate_paragraph', side_effect=[('A',{}),('C',{})]) as translate:
                main(root)
            results = json.loads((root/'region-translations.json').read_text())
            self.assertEqual(set(results), {'a','c'})
            self.assertEqual(results['c']['source_text'], 'Issued')
            self.assertEqual(results['a']['text'], 'A')
            self.assertEqual(translate.call_count, 2)
            with patch('transformation.pdf.scan_worker.translate_paragraph') as translate:
                main(root)
                translate.assert_not_called()
            manifest = json.loads((root/'manifest.json').read_text())
            manifest['document_context'] = 'Changed context'
            (root/'manifest.json').write_text(json.dumps(manifest))
            with patch('transformation.pdf.scan_worker.translate_paragraph', side_effect=[ValueError('Invalid placeholder'),('New C',{})]) as translate:
                main(root)
                self.assertEqual(translate.call_count, 2)
            results = json.loads((root/'region-translations.json').read_text())
            self.assertEqual(set(results), {'c'})
            errors = json.loads((root/'region-errors.json').read_text())
            self.assertIn('a', errors)
