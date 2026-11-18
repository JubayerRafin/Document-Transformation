import dataclasses
import json
import sys
import unittest
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
