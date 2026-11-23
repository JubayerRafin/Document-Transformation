"""Chart category and series names of Word and PowerPoint charts are translated in the chart's cache."""
import io
import unittest
import zipfile

from transformation.office.native import translate_chart_labels

CHART = ('<?xml version="1.0"?><c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><c:chart><c:plotArea><c:barChart><c:ser>'
         '<c:tx><c:strRef><c:f>Sheet1!$B$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>Total due</c:v></c:pt>'
         '</c:strCache></c:strRef></c:tx><c:cat><c:strRef><c:f>Sheet1!$A$2:$A$4</c:f><c:strCache><c:ptCount val="3"/>'
         '<c:pt idx="0"><c:v>Barcode scanner</c:v></c:pt><c:pt idx="1"><c:v>Tools &amp; parts</c:v></c:pt>'
         '<c:pt idx="2"><c:v>2025</c:v></c:pt></c:strCache></c:strRef></c:cat>'
         '<c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>12</c:v></c:pt></c:numCache></c:numRef></c:val>'
         '</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>')


def package(chart=CHART):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('ppt/presentation.xml', '<p/>')
        z.writestr('ppt/charts/chart1.xml', chart)
        z.writestr('ppt/embeddings/Microsoft_Excel_Worksheet.xlsx', b'PK-workbook')
    return out.getvalue()


class ChartLabelTests(unittest.TestCase):
    def test_cached_names_are_translated_and_nothing_else_changes(self):
        words = {'Total due': '합계', 'Barcode scanner': '바코드 스캐너', 'Tools & parts': '공구 & 부품'}
        calls = []

        def translate(text):
            calls.append(text)
            return words[text]
        data, pairs, failed = translate_chart_labels(package(), translate)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            chart = z.read('ppt/charts/chart1.xml').decode('utf-8')
            self.assertEqual(z.read('ppt/embeddings/Microsoft_Excel_Worksheet.xlsx'), b'PK-workbook')
        self.assertIn('<c:v>합계</c:v>', chart)
        self.assertIn('<c:v>바코드 스캐너</c:v>', chart)
        self.assertIn('<c:v>공구 &amp; 부품</c:v>', chart)          # escaped again
        self.assertIn('<c:v>2025</c:v>', chart)                      # no letters: not sent
        self.assertIn('<c:v>12</c:v>', chart)                        # numbers cache untouched
        self.assertIn('<c:f>Sheet1!$A$2:$A$4</c:f>', chart)
        self.assertEqual(sorted(calls), sorted(words))
        self.assertEqual(failed, [])
        self.assertEqual(len(pairs), 3)

    def test_a_name_that_fails_its_checks_keeps_its_text(self):
        def translate(text):
            if text == 'Barcode scanner':
                raise ValueError('Every translation attempt failed its checks; the source text is kept.')
            return text.upper()
        data, pairs, failed = translate_chart_labels(package(), translate)
        chart = zipfile.ZipFile(io.BytesIO(data)).read('ppt/charts/chart1.xml').decode('utf-8')
        self.assertIn('<c:v>Barcode scanner</c:v>', chart)
        self.assertIn('<c:v>TOTAL DUE</c:v>', chart)
        self.assertEqual(failed, ['Barcode scanner'])

    def test_a_package_without_charts_is_returned_as_it_is(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('word/document.xml', '<w/>')
        self.assertEqual(translate_chart_labels(out.getvalue(), lambda t: t)[0], out.getvalue())


if __name__ == '__main__':
    unittest.main()
