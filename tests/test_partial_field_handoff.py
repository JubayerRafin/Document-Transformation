import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pymupdf
from transformation.ocr.paddle_handoff import prepare
from tools.ocr.recognize_paddle_boxes import confirmed_label_prefix


class PartialFieldTests(unittest.TestCase):
    def fixture(self,root):
        image=np.full((200,500,3),255,dtype=np.uint8)
        pix=pymupdf.Pixmap(pymupdf.csRGB,500,200,image.tobytes(),False)
        with pymupdf.open() as pdf:
            page=pdf.new_page(width=500,height=200)
            page.insert_image(page.rect,pixmap=pix)
            data=pdf.tobytes()
            page.get_pixmap(alpha=False).save(root/'page-1.png')
        (root/'page-1_res.json').write_text('{}')
        recognition=root/'recognition'
        recognition.mkdir()
        words=[dict(text='Token:',score=.99,start=0,end=60),dict(text='uncertain',score=.6,start=60,end=160)]
        line=dict(id='line1',text='Token: uncertain',bbox=[20,30,180,48],recognition_score=.6,requires_review=True,
            candidates=[dict(variant=v,text='Token: uncertain',words=words) for v in ('word_gap_primary','word_gap_english')],
            confirmed_label=dict(text='Token:',score=.99,end=60,bbox=[20,30,80,48]))
        (recognition/'lines.jsonl').write_text(json.dumps(line)+'\n')
        meta=dict(status='completed',detection_run=str(root),lines=1,versions={'paddleocr':'3.7.0'},
            image_sha256=hashlib.sha256((root/'page-1.png').read_bytes()).hexdigest(),
            detection_sha256=hashlib.sha256((root/'page-1_res.json').read_bytes()).hexdigest())
        (recognition/'manifest.json').write_text(json.dumps(meta))
        return data,recognition,line

    def test_label_can_translate_while_value_stays_in_original_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            data,recognition,_=self.fixture(root)
            result=prepare(data,recognition,root/'out','unseen.pdf')
            ir=json.loads(Path(result['document_ir']).read_text(encoding='utf-8'))
            label,value=ir['pages'][0]['regions']
            self.assertEqual(label['text'],'Token:')
            self.assertFalse(label['metadata']['requires_review'])
            self.assertTrue(value['metadata']['requires_review'])
            self.assertTrue(value['metadata']['source_value_preserved'])
            self.assertEqual(label['bbox'][2],value['bbox'][0])
            with pymupdf.open(stream=data,filetype='pdf') as source,pymupdf.open(result['input_pdf']) as output:
                self.assertEqual(source[0].get_pixmap().samples,output[0].get_pixmap().samples)

    def test_prefix_requires_confident_agreement(self):
        first=[dict(text='Token:',score=.99,end=40)]
        self.assertIsNotNone(confirmed_label_prefix(first,first))
        self.assertIsNone(confirmed_label_prefix(first,[dict(text='Other:',score=.99,end=40)]))
        self.assertIsNone(confirmed_label_prefix(first,[dict(text='Token:',score=.8,end=40)]))

    def test_invalid_label_geometry_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            data,recognition,line=self.fixture(root)
            line['confirmed_label']['bbox']=[0,30,80,48]
            (recognition/'lines.jsonl').write_text(json.dumps(line)+'\n')
            with self.assertRaisesRegex(ValueError,'field-label'):
                prepare(data,recognition,root/'out','unseen.pdf')
