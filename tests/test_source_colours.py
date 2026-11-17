"""Source text colours BabelDOC's layout data loses are restored before typesetting."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import pymupdf

from transformation.pdf.source_colours import restore


def style(instruction):
    return NS(graphic_state=NS(passthrough_per_char_instruction=instruction), font_id='F1', font_size=10)


def paragraph(pid, box, instruction='0 0 0 rg'):
    shared = style(instruction)
    run = NS(pdf_same_style_unicode_characters=NS(pdf_style=shared, unicode='x'))
    return NS(debug_id=pid, box=NS(x=box[0], y=box[1], x2=box[2], y2=box[3]), pdf_style=shared,
              pdf_paragraph_composition=[run])


class SourceColourTests(unittest.TestCase):
    def test_white_header_on_dark_fill_keeps_its_colour(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'in.pdf')
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=300, height=200)
                page.draw_rect(pymupdf.Rect(10, 10, 290, 40), color=None, fill=(0.1, 0.2, 0.4))
                page.insert_text((20, 30), 'Photo', fontsize=10, color=(1, 1, 1))       # white header text
                page.insert_text((20, 80), 'Body text', fontsize=10, color=(0, 0, 0))  # ordinary black text
                pdf.save(path)
            # PDF coordinates (origin bottom-left) of the two lines.
            header, body, red = paragraph('h', (15, 165, 120, 185)), paragraph('b', (15, 115, 120, 135)), \
                paragraph('r', (15, 165, 120, 185), '0.6 0 0 rg')
            document = NS(page=[NS(page_number=0, pdf_paragraph=[header, body, red])])
            changed = restore(document, path)
            self.assertEqual([(c[1], c[2]) for c in changed], [('h', '#ffffff')])
            self.assertEqual(header.pdf_style.graphic_state.passthrough_per_char_instruction, '1.000 1.000 1.000 rg')
            self.assertEqual(body.pdf_style.graphic_state.passthrough_per_char_instruction, '0 0 0 rg')   # black stays
            self.assertEqual(red.pdf_style.graphic_state.passthrough_per_char_instruction, '0.6 0 0 rg')  # explicit colour stays


if __name__ == '__main__':
    unittest.main()
