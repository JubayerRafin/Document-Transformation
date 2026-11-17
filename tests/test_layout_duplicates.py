"""Duplicate text boxes from the layout model are dropped before paragraphs are found. No BabelDOC needed."""
import unittest
from types import SimpleNamespace as NS

from transformation.pdf.layout_duplicates import duplicates, install, prune


def box(x, y, x2, y2):
    return NS(x=x, y=y, x2=x2, y2=y2)


def layout(id, cls, *coords):
    return NS(id=id, class_name=cls, box=box(*coords))


class LayoutDuplicateTests(unittest.TestCase):
    def test_a_shorter_box_inside_the_same_line_is_a_duplicate(self):
        line = layout(12, 'plain text', 47, 703, 524, 718)
        inner = layout(10, 'plain text', 138, 704, 511, 717)
        title = layout(6, 'title', 66, 717, 544, 741)
        page = NS(page_layout=[title, inner, line])
        self.assertEqual(prune(page), 1)
        self.assertEqual([l.id for l in page.page_layout], [6, 12])

    def test_paragraphs_stacked_inside_a_region_other_classes_and_partial_overlaps_are_kept(self):
        region = layout(1, 'plain text', 50, 100, 550, 400)
        first = layout(2, 'plain text', 50, 300, 550, 400)        # a third of the region's height
        table = layout(3, 'table', 60, 120, 540, 200)              # another class inside it
        side = layout(4, 'plain text', 400, 380, 600, 420)         # half outside
        self.assertEqual(duplicates([region, first, table, side]), set())

    def test_install_prunes_each_page_before_the_original_step_once(self):
        seen = []
        class Finder:
            def process_page(self, page): seen.append([l.id for l in page.page_layout])
        self.assertTrue(install(Finder)); self.assertTrue(install(Finder))
        Finder().process_page(NS(page_layout=[layout(1, 'title', 0, 0, 100, 10), layout(2, 'title', 10, 0, 90, 10)]))
        self.assertEqual(seen, [[1]])


if __name__ == '__main__':
    unittest.main()
