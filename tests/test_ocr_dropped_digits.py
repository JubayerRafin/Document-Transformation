"""Arabic lines whose digits PaddleOCR's Arabic model drops (Arabic scan, 2026-11-23)."""
import unittest

import numpy as np

from tools.ocr.recognize_paddle_boxes import dropped_digits
from transformation.ocr.read_alike import reads_consistently

LINE = 'تتراوح بين 5 و30 درجة. سيفتتح المستودع الجديد في الربيع المقبل وسيوظف نحو 618 شخص.'
PADDLE = 'تتراوح بين و درجةسيفتتح المستودع الجديد في الربيع المقبل وسيوظف نحوشخص'


class Reader:
    """Stand-in for TesseractRecognizer: the first and the enlarged view give these texts."""
    code = 'ara+eng'

    def __init__(self, first, second=None):
        self.texts = [first, second if second is not None else first]

    def predict(self, image):
        text = self.texts[0] if image.shape[1] == 40 else self.texts[1]
        return [{'rec_text': text, 'rec_score': .9}]


class DroppedDigitTests(unittest.TestCase):
    crop = np.zeros((10, 40, 3), np.uint8)

    def test_a_line_read_whole_by_two_views_replaces_the_line_without_its_numbers(self):
        read, dropped = dropped_digits(self.crop, {'text': PADDLE, 'score': .95}, Reader(LINE + '‏'))
        self.assertTrue(dropped)
        self.assertEqual(read['text'], LINE)                   # direction marks removed
        self.assertEqual(read['model'], 'tesseract_ara+eng')

    def test_digits_the_views_disagree_on_or_another_line_are_only_reported_missing(self):
        read, dropped = dropped_digits(self.crop, {'text': PADDLE, 'score': .95}, Reader(LINE, LINE.replace('618', '613')))
        self.assertEqual((read, dropped), (None, True))
        read, dropped = dropped_digits(self.crop, {'text': PADDLE, 'score': .95}, Reader('افصل مصدر الطاقة 5 و30'))
        self.assertEqual((read, dropped), (None, True))

    def test_a_line_with_its_numbers_or_without_numbers_is_left_alone(self):
        self.assertEqual(dropped_digits(self.crop, {'text': LINE, 'score': .95}, Reader(LINE)), (None, False))
        plain = 'سيفتتح المستودع الجديد في الربيع المقبل'
        self.assertEqual(dropped_digits(self.crop, {'text': plain, 'score': .95}, Reader(plain)), (None, False))

    def test_a_line_whose_digits_are_missing_is_never_rebuilt_from_its_readings(self):
        model = 'arabic_PP-OCRv5_mobile_rec'
        region = {'text': PADDLE, 'metadata': {'requires_review': True,
                  'recognition_decision': 'digits_dropped_by_language_model',
                  'candidates': [{'model': model, 'text': PADDLE, 'score': .95}] * 5}}
        self.assertFalse(reads_consistently(region))
        region['metadata']['recognition_decision'] = 'unresolved_source_disagreement'
        self.assertTrue(reads_consistently(region))

    def test_a_line_read_whole_by_tesseract_is_rebuilt_but_stays_in_the_pii_review_queue(self):
        from transformation.ocr.read_alike import read_alike
        model = 'arabic_PP-OCRv5_mobile_rec'
        region = {'text': LINE, 'metadata': {'requires_review': True,
                  'recognition_decision': 'sensitive_literal_independent_evidence_not_established',
                  'candidates': [{'model': model, 'text': PADDLE, 'score': .95}] * 4
                  + [{'model': 'tesseract_ara+eng', 'variant': 'tesseract_digits', 'text': LINE, 'score': .9}]}}
        self.assertTrue(reads_consistently(region))
        self.assertFalse(read_alike(region))


if __name__ == '__main__':
    unittest.main()
