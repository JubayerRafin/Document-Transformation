import unittest
import pymupdf
from transformation.pipeline import redact_pdf
from transformation.validation.privacy import detect


class PiiOccurrenceTests(unittest.TestCase):
    def test_scanned_repeated_value_is_redacted_without_repeated_model_finding(self):
        # Raster fixture deliberately has no searchable text layer.
        with pymupdf.open() as drawing:
            p=drawing.new_page(width=300,height=200)
            p.draw_rect((30,30,130,50),fill=(1,0,0));p.draw_rect((30,100,130,120),fill=(1,0,0))
            png=p.get_pixmap().tobytes('png')
        with pymupdf.open() as scan:
            p=scan.new_page(width=300,height=200);p.insert_image(p.rect,stream=png)
            source=scan.tobytes()
        ir={'units':[{'number':1,'regions':[{'id':'a','text':'ID: DEMO-991','bbox':[30,30,130,50]},
            {'id':'b','text':'Badge DEMO-991','bbox':[30,100,130,120]}]}]}
        result,audit=redact_pdf(source,ir,[{'region_id':'b','value':'DEMO-991'}])
        self.assertEqual({a['region_id'] for a in audit},{'a','b'})
        self.assertTrue(next(a for a in audit if a['region_id']=='a')['propagated_exact_value'])
        with pymupdf.open(stream=result,filetype='pdf') as checked:
            pix=checked[0].get_pixmap()
            self.assertEqual(pix.pixel(60,40),(0,0,0))
            self.assertEqual(pix.pixel(60,110),(0,0,0))
            self.assertEqual(pix.pixel(200,150),(255,255,255))

    def test_native_repeated_value_on_another_page_is_removed(self):
        with pymupdf.open() as pdf:
            for _ in range(2):pdf.new_page().insert_text((40,60),'ID: DEMO-991; keep context')
            source=pdf.tobytes()
        ir={'units':[{'number':n,'regions':[{'id':str(n),'text':'ID: DEMO-991; keep context','bbox':[35,45,260,65]}]} for n in (1,2)]}
        result,_=redact_pdf(source,ir,[{'region_id':'1','value':'DEMO-991'}])
        with pymupdf.open(stream=result,filetype='pdf') as pdf:
            for page in pdf:
                self.assertNotIn('DEMO-991',page.get_text())
                self.assertIn('keep context',page.get_text())

    def test_invalid_finding_cannot_trigger_global_redaction(self):
        with self.assertRaisesRegex(ValueError,'exact source-region'):
            redact_pdf(b'invalid',{'units':[{'number':1,'regions':[{'id':'r','text':'safe'}]}]},[{'region_id':'r','value':'invented'}])

    def test_ocr_joined_date_time_is_not_a_phone(self):
        for value in ['2024-03-2009:41 KST','2024-03-20 09:41','2024-03-20T09:41:00']:
            with self.subTest(value=value):
                self.assertEqual(detect([{'id':'r','text':value}],['Phone numbers'],'unused','unused',assist=False),[])

    def test_real_phone_beside_timestamp_is_retained(self):
        text='Captured 2024-03-2009:41; call +82 10-1234-5678'
        values={f['value'] for f in detect([{'id':'r','text':text}],['Phone numbers'],'unused','unused',assist=False)}
        self.assertEqual(values,{'+82 10-1234-5678'})

    def test_invalid_date_is_not_blanket_exempted(self):
        values=detect([{'id':'r','text':'2024-13-2009:41'}],['Phone numbers'],'unused','unused',assist=False)
        self.assertEqual(values[0]['value'],'2024-13-2009')


if __name__=='__main__':unittest.main()
