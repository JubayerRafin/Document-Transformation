"""A line of a language read by one model is accepted with a number only when a second model reads
the same digits (Korean: the general model; Chinese and Japanese: the English model)."""
import unittest

from tools.ocr.recognize_paddle_boxes import DIGIT_CHECKERS, GENERAL_RECOGNIZER, single_model_literal_evidence


def views(text, model, score=.97):
    return [{'text': text, 'score': score, 'variant': v, 'model': model}
            for v in ('detector_bounds', 'detector_bounds_2x', 'contrast_source', 'verification_source')]


def checks(text, model):
    return [{'text': text, 'score': .6, 'variant': v, 'model': model} for v in ('digit_check', 'digit_check_2x')]


class DigitCheckerTests(unittest.TestCase):
    def test_japanese_line_numbers_confirmed_by_the_english_model(self):
        line = '2.1すべての貨物は到着時に検査し、損傷があれば2営業日以内に報告してください。'
        self.assertEqual(DIGIT_CHECKERS[GENERAL_RECOGNIZER], 'en_PP-OCRv5_mobile_rec')
        good = views(line, GENERAL_RECOGNIZER) + checks('2.1 xqwz 2 yzq', 'en_PP-OCRv5_mobile_rec')
        self.assertTrue(single_model_literal_evidence(good, line))
        wrong_digits = views(line, GENERAL_RECOGNIZER) + checks('2.7 xqwz 2', 'en_PP-OCRv5_mobile_rec')
        self.assertFalse(single_model_literal_evidence(wrong_digits, line))
        own_model_only = views(line, GENERAL_RECOGNIZER) + checks('2.1 2', GENERAL_RECOGNIZER)
        self.assertFalse(single_model_literal_evidence(own_model_only, line))

    def test_korean_lines_keep_the_general_model_as_checker(self):
        line = '3분기 출하량은 2026년에 증가했습니다'
        good = views(line, 'korean_PP-OCRv5_mobile_rec') + checks('3 2026', GENERAL_RECOGNIZER)
        self.assertTrue(single_model_literal_evidence(good, line))
        self.assertFalse(single_model_literal_evidence(views(line, 'korean_PP-OCRv5_mobile_rec'), line))

    def test_emails_and_amounts_still_need_a_person(self):
        line = '連絡先 info@example.com'
        self.assertFalse(single_model_literal_evidence(views(line, GENERAL_RECOGNIZER) + checks('info@example.com', 'en_PP-OCRv5_mobile_rec'), line))


if __name__ == '__main__':
    unittest.main()
