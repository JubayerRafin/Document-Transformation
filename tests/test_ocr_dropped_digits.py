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


class DateLineTests(unittest.TestCase):
    def test_a_line_without_the_languages_letters_is_not_checked(self):
        crop = np.zeros((10, 40, 3), np.uint8)
        for text in ('2025-07-16', 'USD 591,597.21', 'HID-2024-48150'):
            self.assertEqual(dropped_digits(crop, {'text': text, 'score': 1.0}, Reader('2025-07-18')), (None, False))


class LatinRereadTests(unittest.TestCase):
    """Bengali invoice, 2026-11-23: codes read by Tesseract just under the confidence bar."""

    class Model:
        def __init__(self, text, score):
            self.text, self.score = text, score

        def predict(self, image):
            return [{'rec_text': self.text, 'rec_score': self.score}]

    def test_a_code_line_two_latin_models_read_alike_is_accepted(self):
        from tools.ocr.recognize_paddle_boxes import latin_line_reread
        crop = np.zeros((10, 40, 3), np.uint8)
        reads = [{'text': '5৬1-2027-14498', 'score': .88}, {'text': 'SVI-2027-14498', 'score': .8}]
        best, ok = latin_line_reread(crop, reads, lambda: self.Model('SVI-2027-14498', .99), lambda: self.Model('SVI-2027-14498', .97))
        self.assertTrue(ok)
        self.assertEqual(best['text'], 'SVI-2027-14498')

    def test_own_script_letters_or_disagreeing_models_are_left_alone(self):
        from tools.ocr.recognize_paddle_boxes import latin_line_reread
        crop = np.zeros((10, 40, 3), np.uint8)
        self.assertEqual(latin_line_reread(crop, [{'text': 'পণ্য 88', 'score': .5}], lambda: self.Model('88', .99),
                                           lambda: self.Model('88', .99)), (None, False))
        self.assertEqual(latin_line_reread(crop, [{'text': 'APC-2019-37861', 'score': .87}], lambda: self.Model('APC-2019-37861', .99),
                                           lambda: self.Model('APC-2019-37881', .99))[1], False)


class TesseractConsensusTests(unittest.TestCase):
    def test_unanimous_tesseract_views_count_from_0_85_and_paddle_views_from_0_90(self):
        from tools.ocr.recognize_paddle_boxes import single_model_consensus
        crop = np.zeros((10, 40, 3), np.uint8)
        views = lambda model, score: [{'text': 'চালান হিসাব', 'score': score, 'variant': v, 'model': model}
                                      for v in ('detector_bounds', 'detector_bounds_2x', 'contrast_source')]
        self.assertIsNotNone(single_model_consensus(views('tesseract_ben', .86), crop, None, False))
        self.assertIsNone(single_model_consensus(views('bengali_rec', .86), crop, None, False))
        mixed = views('tesseract_ben', .86); mixed[0] = dict(mixed[0], text='চালান হিসেব')
        self.assertIsNone(single_model_consensus(mixed, crop, None, False))
