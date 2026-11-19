"""Translated text shrinks to the most common scale of its own page, not of the whole document."""
import unittest
from types import SimpleNamespace as NS

from transformation.layout import page_scale


def para(text, scale):
    return NS(unicode=text, optimal_scale=None, needed=scale)


class PageScaleTests(unittest.TestCase):
    def test_a_page_that_fits_keeps_its_size_and_a_dense_page_stays_alike(self):
        class Typesetting:
            def _get_optimal_scale(self, paragraph, page, units, use_english_line_break=True):
                return paragraph.needed

            def preprocess_document(self, document, pbar):
                # BabelDOC: every paragraph's scale, then all capped at the document's most common one.
                scales = []
                for page in document.page:
                    for p in page.pdf_paragraph:
                        p.optimal_scale = self._get_optimal_scale(p, page, [])
                        scales += [p.optimal_scale] * len(p.unicode)
                mode = min(s for s in set(scales) if scales.count(s) == max(map(scales.count, set(scales))))
                for page in document.page:
                    for p in page.pdf_paragraph:
                        p.optimal_scale = min(p.optimal_scale, mode)

        page_scale.install(Typesetting); page_scale.install(Typesetting)
        light = NS(pdf_paragraph=[para('Legal information', 1.0), para('Edition 2, 12/2022', 1.0),
                                  para('The only warranties for HP products are set forth here.', 0.95)])
        dense = NS(pdf_paragraph=[para('x' * 400, 0.7), para('y' * 300, 0.7), para('Heading', 1.0)])
        Typesetting().preprocess_document(NS(page=[light, dense]), None)
        # The page is set alike at its own most common scale, not at the document's 0.7.
        self.assertEqual([p.optimal_scale for p in light.pdf_paragraph], [0.95, 0.95, 0.95])
        self.assertEqual([p.optimal_scale for p in dense.pdf_paragraph], [0.7, 0.7, 0.7])

    def test_the_most_common_scale_weighs_characters(self):
        self.assertEqual(page_scale.page_mode([(1.0, 10), (0.8, 50), (0.9, 50)]), 0.8)
        self.assertIsNone(page_scale.page_mode([]))


if __name__ == '__main__':
    unittest.main()
