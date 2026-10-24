import unittest
from unittest.mock import patch
import pymupdf
from transformation.validation.privacy import detect
from transformation.pipeline import redact_pdf


class PiiCredentialTests(unittest.TestCase):
    def test_explicit_credential_fields_have_exact_source_spans(self):
        for text,value in [('API Key: demo_live_93ab..sample','demo_live_93ab..sample'),
            ('access_token = "tok_abcd9876"','tok_abcd9876'),
            ('Client Secret: abcdefghijklmnop','abcdefghijklmnop'),
            ('Authentication token: abc-12345678','abc-12345678')]:
            with self.subTest(text=text),patch('transformation.llm.ollama_native.chat',return_value=('{"findings":[]}',{})):
                findings=detect([{'id':'r','text':text}],['Identifiers'],'unused','model')
                self.assertEqual(len(findings),1)
                finding=findings[0];self.assertEqual(finding['value'],value)
                self.assertEqual(finding['method'],'credential_label_pattern')
                for start,end in finding['spans']:self.assertEqual(text[start:end],value)

    def test_rule_respects_selected_categories_and_no_filename_assumption(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('{"findings":[]}',{})):
            self.assertEqual(detect([{'id':'r','text':'API Key: demo_123456789'}],['Names'],'unused','model'),[])
            self.assertEqual(detect([{'id':'r','text':'Store your API key securely. API Key: required'}],['Identifiers'],'unused','model'),[])

    def test_uncertain_scan_field_is_removed_even_if_model_omits_it(self):
        with pymupdf.open() as drawing:
            p=drawing.new_page(width=400,height=150);p.insert_text((30,60),'API Key: demo_live_93ab')
            png=p.get_pixmap().tobytes('png')
        with pymupdf.open() as pdf:
            p=pdf.new_page(width=400,height=150);p.insert_image(p.rect,stream=png);source=pdf.tobytes()
        region={'id':'scan','text':'API Key: demo_live_93ab','bbox':[25,45,210,65],'metadata':{'requires_review':True}}
        with patch('transformation.llm.ollama_native.chat',return_value=('{"findings":[]}',{})):
            findings=detect([region],['Identifiers'],'unused','model')
        result,audit=redact_pdf(source,{'units':[{'number':1,'regions':[region]}]},findings)
        self.assertEqual(audit[0]['method'],'whole_ocr_line')
        self.assertTrue(audit[0]['review_required'])
        with pymupdf.open(stream=result,filetype='pdf') as pdf:
            pix=pdf[0].get_pixmap(clip=pymupdf.Rect(region['bbox']))
            self.assertEqual(max(pix.samples),0)

    def test_native_credential_is_removed_with_label_and_other_text_preserved(self):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40,60),'API Key: demo_live_93ab; keep context');source=pdf.tobytes()
        region={'id':'r','text':'API Key: demo_live_93ab; keep context','bbox':[35,40,300,70]}
        with patch('transformation.llm.ollama_native.chat',return_value=('{"findings":[]}',{})):
            findings=detect([region],['Identifiers'],'unused','model')
        result,_=redact_pdf(source,{'units':[{'number':1,'regions':[region]}]},findings)
        with pymupdf.open(stream=result,filetype='pdf') as pdf:
            self.assertNotIn('demo_live_93ab',pdf[0].get_text())
            self.assertIn('API Key:',pdf[0].get_text())
            self.assertIn('keep context',pdf[0].get_text())


if __name__=='__main__':unittest.main()
