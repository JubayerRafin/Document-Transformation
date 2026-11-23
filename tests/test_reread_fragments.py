"""One-letter pieces of an Arabic word, read again from the page, are removed before translation."""
import unittest
from types import SimpleNamespace as NS

from transformation.pdf.unmapped_text import drop_reread_fragments


def paragraph(pid, reread, letters, box):
    chars = [NS(char_unicode=c, pdf_style=NS(font_size=9.0)) for c in letters]
    return NS(debug_id=pid, unicode=reread, box=NS(x=box[0], y=box[1], x2=box[2], y2=box[3]), xobj_id=0,
              pdf_paragraph_composition=[NS(pdf_line=None, pdf_formula=None, pdf_character=None,
                                            pdf_same_style_characters=NS(pdf_character=chars))])


class RereadFragmentTests(unittest.TestCase):
    def test_letters_of_a_word_in_paragraphs_of_their_own_are_removed(self):
        # Arabic newsletter, 2026-11-24: letters, re-read texts and boxes as BabelDOC and MuPDF gave them.
        whole = paragraph('X6tax', 'كرسي مكتب', 'يﻣﻜﺘﺐ', (260.5, 52.2, 280.0, 69.5))
        pieces = [paragraph('c45eP', 'كرس', 'ﺮ', (272.5, 64.9, 276.3, 67.5)),
                  paragraph('V7QnX', 'رسي', 'س', (266.3, 69.3, 272.5, 70.6)),
                  paragraph('oMqzJ', 'كر', 'ﻛ', (276.0, 69.5, 280.0, 73.9))]
        other_cell = paragraph('o', 'كرسي', 'ﻛﺮﺳﻲ', (300.0, 52.2, 320.0, 69.5))     # its own word elsewhere
        line_above = paragraph('a', 'كر', 'ﻛﺮ', (262.0, 80.0, 280.0, 89.0))        # a line of its own
        latin = paragraph('l', 'Tab', 'Tab', (262.0, 60.0, 270.0, 66.0))
        page = NS(pdf_paragraph=[whole, *pieces, other_cell, line_above, latin])
        self.assertEqual(drop_reread_fragments(page), ['c45eP', 'V7QnX', 'oMqzJ'])
        self.assertEqual([p.debug_id for p in page.pdf_paragraph], ['X6tax', 'o', 'a', 'l'])

    def test_a_longer_paragraph_inside_another_is_kept(self):
        outer = paragraph('w', 'كرسي مكتب جديد', 'ﻛﺮﺳﻲﻣﻜﺘﺐﺟﺪﻳﺪ', (200.0, 50.0, 300.0, 70.0))
        inner = paragraph('i', 'مكتب', 'ﻣﻜﺘﺐ', (240.0, 55.0, 260.0, 65.0))       # four letters: a word
        page = NS(pdf_paragraph=[outer, inner])
        self.assertEqual(drop_reread_fragments(page), [])


if __name__ == '__main__':
    unittest.main()
