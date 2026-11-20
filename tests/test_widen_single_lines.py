import unittest
from types import SimpleNamespace as NS

from transformation.layout.line_breaks import widen_single_lines


def para(pid, x, y, x2, y2, size=10.5):
    char = NS(pdf_style=NS(font_size=size))
    return NS(debug_id=pid, box=NS(x=x, y=y, x2=x2, y2=y2), vertical=False,
              pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=[char]))])


def page(paragraphs, curves=()):
    return NS(page_number=0, pdf_paragraph=paragraphs, pdf_curve=[NS(box=b) for b in curves],
              pdf_figure=[], pdf_form=[], cropbox=NS(box=NS(x=0, y=0, x2=595, y2=842)))


class WidenSingleLineTests(unittest.TestCase):
    def test_a_short_heading_reaches_the_right_edge_of_its_column(self):
        heading = para('h', 42, 700, 100, 711)
        body = para('b', 42, 640, 290, 690)            # the column's text below it
        other_column = para('c', 300, 640, 553, 711)   # the next column, beside it
        doc = NS(page=[page([heading, body, other_column])])
        changes = widen_single_lines(doc)
        self.assertEqual(heading.box.x2, 290)
        self.assertEqual([c['paragraph_id'] for c in changes], ['h'])

    def test_it_stops_before_text_or_a_rule_on_its_line_and_inside_its_panel(self):
        label = para('l', 42, 700, 80, 711)
        value = para('v', 150, 700, 250, 711)
        body = para('b', 42, 640, 400, 690)
        doc = NS(page=[page([label, value, body])])
        widen_single_lines(doc)
        self.assertEqual(label.box.x2, 148)
        boxed = para('p', 60, 500, 120, 511)
        panel = NS(x=50, y=490, x2=300, y2=520)          # a coloured box drawn around the line
        below = para('q', 60, 450, 500, 480)
        doc = NS(page=[page([boxed, below], curves=[panel])])
        widen_single_lines(doc)
        self.assertEqual(boxed.box.x2, 298)
        cell = para('c', 60, 300, 90, 311)
        rule = NS(x=140, y=295, x2=140.8, y2=315)        # a cell border to its right
        doc = NS(page=[page([cell, para('d', 60, 280, 400, 291)], curves=[rule])])
        widen_single_lines(doc)
        self.assertEqual(cell.box.x2, 138)

    def test_paragraphs_of_several_lines_and_skipped_ones_keep_their_box(self):
        block = para('m', 42, 600, 100, 640)             # several lines
        heading = para('h', 42, 700, 100, 711)
        doc = NS(page=[page([block, heading, para('b', 42, 650, 290, 690)])])
        widen_single_lines(doc, skip={'h'})
        self.assertEqual((block.box.x2, heading.box.x2), (100, 100))


if __name__ == '__main__':
    unittest.main()
