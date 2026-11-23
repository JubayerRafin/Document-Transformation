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


class LanguageRecognizerTests(unittest.TestCase):
    def test_each_script_the_general_model_cannot_read_has_its_own_model_and_a_digit_checker(self):
        from tools.ocr.recognize_paddle_boxes import RECOGNIZERS, OWN_SCRIPT, recognition_model_name
        self.assertEqual(recognition_model_name('ar'), 'arabic_PP-OCRv5_mobile_rec')
        self.assertEqual(recognition_model_name('hi'), 'devanagari_PP-OCRv5_mobile_rec')
        self.assertEqual({recognition_model_name(l) for l in ('fr', 'de', 'es', 'pt')}, {'latin_PP-OCRv5_mobile_rec'})
        self.assertEqual(recognition_model_name('zh'), GENERAL_RECOGNIZER)
        self.assertEqual(recognition_model_name('ja'), GENERAL_RECOGNIZER)
        self.assertNotIn('en', OWN_SCRIPT)
        for language in OWN_SCRIPT:
            # Bengali is read by Tesseract where it is installed, by the general model otherwise.
            self.assertIn(recognition_model_name(language), DIGIT_CHECKERS)


class TesseractBengaliTests(unittest.TestCase):
    def test_bengali_uses_tesseract_only_when_its_model_is_installed(self):
        from unittest.mock import patch
        from tools.ocr.recognize_paddle_boxes import recognition_model_name, TESSERACT_RECOGNIZER, DIGIT_CHECKERS
        with patch('tools.ocr.tesseract_line.available', return_value=True):
            self.assertEqual(recognition_model_name('bn'), TESSERACT_RECOGNIZER)
        with patch('tools.ocr.tesseract_line.available', return_value=False):
            self.assertNotEqual(recognition_model_name('bn'), TESSERACT_RECOGNIZER)
        self.assertIn(TESSERACT_RECOGNIZER, DIGIT_CHECKERS)

    def test_tesseract_tsv_becomes_one_line_scored_by_its_words_weighted_by_length(self):
        from unittest.mock import patch, Mock
        from tools.ocr.tesseract_line import TesseractRecognizer
        rows = [['level', 'page_num', 'block_num', 'par_num', 'line_num', 'word_num', 'left', 'top', 'width', 'height', 'conf', 'text'],
                ['5', '1', '1', '1', '1', '1', '0', '0', '10', '10', '96.5', 'আমার'],
                ['5', '1', '1', '1', '1', '2', '12', '0', '10', '10', '88.0', '123']]
        tsv = '\n'.join('\t'.join(r) for r in rows) + '\n'
        import numpy as np
        with patch('tools.ocr.tesseract_line.subprocess.run', return_value=Mock(stdout=tsv.encode())):
            out = TesseractRecognizer('bn').predict(np.full((20, 60, 3), 255, np.uint8))
        self.assertEqual(out[0]['rec_text'], 'আমার 123')
        self.assertAlmostEqual(out[0]['rec_score'], (4 * .965 + 3 * .88) / 7)
