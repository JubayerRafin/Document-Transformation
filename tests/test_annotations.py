"""Source annotations, form fields and links copied onto translated pages."""
import unittest

import pymupdf

from transformation.pdf.annotations import carry


def source_pdf():
    pdf = pymupdf.open(); page = pdf.new_page(width=400, height=300)
    page.insert_text((40, 55), 'Timetable TT-2022-05780', fontsize=14)
    page.add_highlight_annot(pymupdf.Rect(38, 40, 220, 60))
    page.add_text_annot((20, 20), 'Reviewer note: check the totals.')
    widget = pymupdf.Widget(); widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT; widget.field_name = 'approver'
    widget.field_value = 'Grace Kim'; widget.rect = pymupdf.Rect(40, 250, 240, 270); page.add_widget(widget)
    page.insert_link({'kind': pymupdf.LINK_URI, 'from': pymupdf.Rect(40, 200, 140, 215), 'uri': 'https://example.com/'})
    return pdf.tobytes()


def translated_pdf(pages=1):
    pdf = pymupdf.open()
    for _ in range(pages):
        pdf.new_page(width=400, height=300).insert_text((40, 55), '시간표 TT-2022-05780', fontsize=14, fontname='korea')
    return pdf.tobytes()


class CarryTests(unittest.TestCase):
    def test_highlight_note_field_and_link_reach_the_translated_page(self):
        data, counts = carry(source_pdf(), translated_pdf())
        self.assertEqual(counts, {'annotations': 2, 'fields': 1, 'links': 1, 'skipped': 0})
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            page = pdf[0]
            kinds = sorted(a.type[1] for a in page.annots())
            self.assertEqual(kinds, ['Highlight', 'Text'])
            note = next(a for a in page.annots() if a.type[1] == 'Text')
            self.assertEqual(note.info['content'], 'Reviewer note: check the totals.')
            field = next(page.widgets())
            self.assertEqual((field.field_name, field.field_value), ('approver', 'Grace Kim'))
            self.assertEqual(page.get_links()[0]['uri'], 'https://example.com/')
            self.assertIn('시간표', page.get_text())

    def test_a_different_page_count_changes_nothing(self):
        output = translated_pdf(pages=2)
        data, counts = carry(source_pdf(), output)
        self.assertIs(data, output)
        self.assertEqual(counts['annotations'], 0)


if __name__ == '__main__':
    unittest.main()
