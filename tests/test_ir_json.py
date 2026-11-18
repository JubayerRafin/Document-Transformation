import dataclasses
import json
import sys
import unittest
import unittest.mock
from typing import Optional

from transformation.pdf.ir_json import dumps, to_data


@dataclasses.dataclass
class Box:
    x: float
    y: float


@dataclasses.dataclass
class Char:
    char_unicode: str
    box: Box
    font_id: Optional[str] = None


@dataclasses.dataclass
class Paragraph:
    unicode: str
    chars: list
    extra: dict
    pair: tuple = (1, 2)


class Chain:
    """A plain object holding a long linked chain, like the value that broke dataclasses.asdict."""

    def __init__(self, length):
        node = None
        for _ in range(length):
            node = (node, {'next': node})
        self.head = node


class IrJsonTests(unittest.TestCase):
    def test_plain_models_serialise_exactly_like_asdict(self):
        doc = [Paragraph('Hello', [Char('H', Box(1.5, 2)), Char('i', Box(3, 4), 'F1')], {'k': [1, None, True]}),
               Paragraph('', [], {})]
        # Tuples come back as lists, which is the same JSON.
        self.assertEqual(json.dumps(to_data(doc)), json.dumps([dataclasses.asdict(p) for p in doc]))
        self.assertEqual(json.loads(dumps(doc[0])), json.loads(json.dumps(dataclasses.asdict(doc[0]))))

    def test_a_deep_foreign_value_does_not_exhaust_the_recursion_limit(self):
        doc = Paragraph('x', [Char('x', Box(0, 0))], {'cache': Chain(sys.getrecursionlimit() * 3)})
        with self.assertRaises(RecursionError):
            dataclasses.asdict(doc)
        data = json.loads(dumps(doc))
        self.assertEqual(data['extra']['cache'], '<Chain>')
        self.assertEqual(data['chars'][0]['char_unicode'], 'x')

    def test_copies_share_foreign_values_and_copy_the_model(self):
        from transformation.pdf.ir_json import ir_copy
        chain = Chain(sys.getrecursionlimit() * 3)
        doc = Paragraph('x', [Char('x', Box(0, 0))], {'cache': chain})
        copied = ir_copy(doc)
        self.assertIs(copied.extra['cache'], chain)
        self.assertIsNot(copied.chars[0], doc.chars[0])
        copied.chars[0].box.x = 9
        self.assertEqual(doc.chars[0].box.x, 0)

    def test_deeply_nested_plain_data_is_walked_without_recursion(self):
        nested = current = {}
        for _ in range(sys.getrecursionlimit() * 2):
            current['child'] = {}
            current = current['child']
        self.assertIn('child', to_data(Paragraph('x', [], nested))['extra'])


if __name__ == '__main__':
    unittest.main()


class SideBySideGroupingTests(unittest.TestCase):
    def test_a_label_and_its_value_on_one_line_are_not_joined(self):
        from types import SimpleNamespace as NS
        from transformation.pdf import source_grouping
        def paragraph(text, x, y, x2, y2, pid):
            run = NS(pdf_same_style_unicode_characters=NS(unicode=text, pdf_style=None), pdf_formula=None, pdf_line=None,
                     pdf_same_style_characters=None, pdf_character=None)
            return NS(box=NS(x=x, y=y, x2=x2, y2=y2), layout_label='plain text', vertical=False, unicode=text,
                      pdf_paragraph_composition=[run], debug_id=pid)
        label = paragraph('Email', 50, 637, 80, 646, 'a')
        value = paragraph('emily.roberts@example.com', 210, 637, 370, 646, 'b')
        page = NS(page_number=0, pdf_paragraph=[label, value])
        with unittest.mock.patch.object(source_grouping, 'composition_text', side_effect=lambda p: p.unicode):
            overrides, records = source_grouping.join_block_continuations(page, [((40, 630, 380, 650), 'Email emily.roberts@example.com')])
        self.assertEqual(records, [])
        self.assertEqual(len(page.pdf_paragraph), 2)
        # A real wrapped continuation on the next line is still joined.
        first = paragraph('The device should be stored in a', 50, 637, 300, 646, 'c')
        second = paragraph('dry place.', 50, 625, 100, 634, 'd')
        page = NS(page_number=0, pdf_paragraph=[first, second])
        with unittest.mock.patch.object(source_grouping, 'composition_text', side_effect=lambda p: p.unicode):
            overrides, records = source_grouping.join_block_continuations(page, [((40, 620, 380, 650), 'The device should be stored in a dry place.')])
        self.assertEqual(len(records), 1)
