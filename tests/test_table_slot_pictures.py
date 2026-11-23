"""Text slots in a table cell holding a picture stay off the picture."""
import unittest
from types import SimpleNamespace as NS


def block(pid, bottom, top):
    return {'paragraph_id': pid, 'text': pid, 'bbox': [105, bottom, 300, top], 'fraction_inside_cell': 1.0}


class TableSlotPictureTests(unittest.TestCase):
    def plan(self, images):
        from transformation.pdf.babeldoc_worker import plan_table_text_slots
        cell = {'page': 1, 'table': 0, 'row': 1, 'column': 1, 'cell_bbox': [100, 500, 400, 620],
                'overlapping_blocks': [block('title', 600, 612), block('caution', 585, 595), block('text', 505, 528)]}
        return {s['paragraph_id']: s['bbox'] for s in plan_table_text_slots([cell], images=images)[0]['slots']}

    def test_text_below_a_warning_triangle_starts_below_it(self):
        triangle = NS(box=NS(x=110, y=540, x2=130, y2=580))
        slots = self.plan({0: [triangle]})
        self.assertLessEqual(slots['text'][3], 529.5)        # top of the text slot: the text's own top
        self.assertGreaterEqual(slots['caution'][1], 580)    # the label's slot ends above the triangle

    def test_cells_without_pictures_keep_equal_shares(self):
        slots = self.plan({})
        heights = {k: round(v[3] - v[1], 1) for k, v in slots.items()}
        self.assertEqual(len(set(heights.values())), 1)


if __name__ == '__main__':
    unittest.main()
