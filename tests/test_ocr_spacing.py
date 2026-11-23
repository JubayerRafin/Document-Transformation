"""Word spaces of an OCR line come from the views that read the same characters."""
import unittest

from tools.ocr.recognize_paddle_boxes import voted_spacing


def views(*texts):
    return [{'text': text, 'score': .9, 'variant': f'view{i}'} for i, text in enumerate(texts)]


class VotedSpacingTests(unittest.TestCase):
    def test_spaces_most_views_agree_on_are_added(self):
        reads = views('ab cd ef', 'ab cd ef', 'abcdef')
        best = dict(reads[2], score=.97)
        self.assertEqual(voted_spacing(best, reads)['text'], 'ab cd ef')
        self.assertEqual(voted_spacing(best, reads)['score'], .97)

    def test_a_space_seen_by_a_minority_is_not_added(self):
        reads = views('ab cdef', 'abcdef', 'abcdef')
        self.assertEqual(voted_spacing(reads[1], reads)['text'], 'abcdef')

    def test_characters_never_change_and_spaces_are_never_removed(self):
        reads = views('ab cd', 'abcd', 'abcd', 'xb cd')
        self.assertEqual(voted_spacing(reads[0], reads)['text'], 'ab cd')
        self.assertEqual(voted_spacing(reads[1], reads)['text'], 'abcd')   # 1 of 3 agreeing views

    def test_no_space_inside_a_number_or_an_address(self):
        reads = views('Total: EUR 5,657.59', 'Total: EUR 5, 657.59', 'Total:EUR 5, 657.59')
        self.assertEqual(voted_spacing(reads[0], reads)['text'], 'Total: EUR 5,657.59')
        reads = views('mail a@b.com', 'mail a @b.com', 'mail a @b.com')
        self.assertEqual(voted_spacing(reads[0], reads)['text'], 'mail a@b.com')

    def test_disagreeing_characters_do_not_vote(self):
        reads = views('abcdef', 'ab cd eg', 'ab cd eg')
        self.assertEqual(voted_spacing(reads[0], reads)['text'], 'abcdef')

    def test_korean_line_gets_its_word_spaces_back(self):
        reads = views('주생산 라인의 정기 점검은매월첫째 주말로', '주생산 라인의정기 점검은매월 첫째 주말로',
                      '주생산 라인의정기 점검은매월 첫째 주말로', '주생산라인의정기점검은매월첫째주말로')
        self.assertEqual(voted_spacing(reads[3], reads)['text'], '주생산 라인의정기 점검은매월 첫째 주말로')


if __name__ == '__main__':
    unittest.main()


class GapSpacingTests(unittest.TestCase):
    """Korean scan, 2026-11-23: the Korean model dropped word spaces in every view."""

    @staticmethod
    def line(words):
        """A 30 px line: each word a block of 10 px syllables 2 px apart, words 9 px apart."""
        import numpy as np
        blocks, x = [], 4
        width = 8 + sum(12 * n for n in words) + 9 * (len(words) - 1)
        image = np.full((30, width, 3), 255, np.uint8)
        for n in words:
            for _ in range(n):
                image[6:24, x:x + 10] = 0
                x += 12
            x += 7
        return image

    class Model:
        def __init__(self, pieces):
            self.pieces = pieces

        def predict(self, images):
            assert len(images) == len(self.pieces), (len(images), self.pieces)
            return [{'rec_text': t, 'rec_score': .9} for t in self.pieces]

    def test_spaces_are_added_at_the_word_gaps_the_pieces_confirm(self):
        from tools.ocr.recognize_paddle_boxes import gap_spacing
        best = {'text': '장치는5도에서30도사이의', 'score': .95}
        spaced = gap_spacing(self.line([3, 4, 5]), best, self.Model(['장치는', '5도에서', '30도사이의']))
        self.assertEqual(spaced['text'], '장치는 5도에서 30도사이의')
        self.assertEqual(spaced['spacing'], 'image_gaps')

    def test_pieces_that_read_other_characters_or_short_lines_change_nothing(self):
        from tools.ocr.recognize_paddle_boxes import gap_spacing
        best = {'text': '장치는5도에서30도사이의', 'score': .95}
        self.assertIs(gap_spacing(self.line([3, 4, 5]), best, self.Model(['장치는', '5도애서', '30도사이의'])), best)
        short = {'text': '해야 합니다', 'score': .95}
        self.assertIs(gap_spacing(self.line([2, 3]), short, self.Model(['해야', '합니다'])), short)
