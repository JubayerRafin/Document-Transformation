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
