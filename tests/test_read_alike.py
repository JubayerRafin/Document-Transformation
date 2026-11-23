"""OCR lines flagged only over spacing or letter case count as reliably read."""
import unittest

from transformation.ocr.read_alike import literal_line, read_alike, reads_consistently


def region(text, readings, **meta):
    return {'text': text, 'metadata': {'requires_review': True, **meta,
            'candidates': [{'model': m, 'text': t, 'score': s} for m, t, s in readings]}}


class ReadAlikeTests(unittest.TestCase):
    def test_spacing_or_case_differences_between_two_models(self):
        self.assertTrue(read_alike(region('1. Images with baked-in text',
                                          [('mobile', '1. Images with baked-in text', .99), ('server', '1.Images with baked-in text', .95)])))
        self.assertTrue(read_alike(region('LaserJet Pro X1', [('mobile', 'LaserJet Pro X1', .98), ('server', 'Laserjet Pro X1', .97)])))

    def test_any_character_doubt_or_missing_evidence_keeps_the_line_uncertain(self):
        for item in (region('Screenshot captured', [('mobile', 'Screenshot captured', .99), ('server', 'Screenshct captured', .94)]),
                     region('Ref 840', [('mobile', 'Ref 840', .99), ('mobile', 'Ref 840', .98)]),
                     region('Ref 840', []), region('', [('a', '', .9), ('b', '', .9)]),
                     region('x', [('a', 'x', .99), ('b', 'x', .99)], ocr_unreadable=True)):
            self.assertFalse(read_alike(item), item)

    def test_a_minority_of_differing_crop_readings_does_not_stop_a_scanned_line_being_rebuilt(self):
        text = 'Document ID: SEC-1 | Format: flattened'
        readings = [('mobile', text, .98), ('mobile', 'Document ID: SEC-1|Format: flattened', .97),
                    ('server', 'Document ID: SEC-1| Format: flattened', .93), ('server', 'Document ID: SEC-1 | Fomat: flattened', .93),
                    ('mobile', 'Document ID: SEC-1 Format: flattened', .96)]
        self.assertTrue(reads_consistently(region(text, readings)))
        self.assertFalse(read_alike(region(text, readings)))          # the PII review queue still shows the doubt
        # Split vote, or agreement from one model only: the source pixels are kept.
        self.assertFalse(reads_consistently(region(text, readings[:3] + [readings[3]] * 3)))
        self.assertFalse(reads_consistently(region('owner@example-corp.com', [('mobile', 'owner@example-corp.com', .98),
                                                                               ('mobile', 'owner@example-corp.com', .97),
                                                                               ('server', 'owner@example.corp.com', .93)])))
        for item in (region('Ref 840', []), region('', [('a', '', .9), ('b', '', .9)]),
                     region('x', [('a', 'x', .99), ('b', 'x', .99)], ocr_unreadable=True)):
            self.assertFalse(reads_consistently(item), item)

    def test_weak_readings_do_not_count_against_two_confident_models(self):
        self.assertTrue(read_alike(region('Ref 840-Q7', [('a', 'Ref 840-Q7', .99), ('b', 'Ref 840-Q7', .96), ('b', 'Rf84', .5)])))

    def test_a_one_model_line_of_words_is_rebuilt_when_its_readings_differ_only_in_punctuation(self):
        """Korean scan, 2026-11-23: five readings of one model, a final period or a comma apart."""
        ko = 'korean_PP-OCRv5_mobile_rec'
        text = '말로 예정되어 있습니다.에너지 비용은 계획보다 높았지만, 운송비 절감으로'
        readings = [(ko, text, .91), (ko, '말로 예정되어 있습니다.에너지 비용은계획보다 높았지만 운송비 절감으로', .90),
                    (ko, '말로 예정되어 있습니다.에너지 비용은계획보다 높았지만, 운송비 절감으로', .90),
                    (ko, '말로 예정되어 있습니다 에너지 비용은 계획보다 높았지만, 운송비 절감으로', .88)]
        self.assertTrue(reads_consistently(region(text, readings)))
        self.assertFalse(read_alike(region(text, readings)))          # the PII review queue is unchanged
        self.assertTrue(reads_consistently(region('해야 합니다', [(ko, '해야 합니다.', .90), (ko, '해야 합니다.', .89), (ko, '해야 합니다', .94)])))

    def test_one_model_lines_with_numbers_too_few_readings_or_differing_letters_stay_uncertain(self):
        ko = 'korean_PP-OCRv5_mobile_rec'
        self.assertFalse(reads_consistently(region('합계 4,200원', [(ko, '합계 4,200원', .93)] * 4)))
        self.assertFalse(reads_consistently(region('해야 합니다', [(ko, '해야 합니다', .94), (ko, '해야 합니다.', .90)])))
        self.assertFalse(reads_consistently(region('해야 합니다', [(ko, '해야 합니다', .94), (ko, '해야 합니까', .93),
                                                                 (ko, '해야 합니까', .92), (ko, '해야 합니다', .90)])))
        # Readings from two models where only one gives the text: not a one-model language.
        self.assertFalse(reads_consistently(region('Screenshot captured', [('mobile', 'Screenshot captured', .99)] * 3
                                                   + [('server', 'Screenshct captured', .94)] * 2)))

    def test_a_line_read_as_a_literal_keeps_its_pixels_without_review(self):
        ko = 'korean_PP-OCRv5_mobile_rec'
        self.assertTrue(literal_line(region('ops11@example.com', [(ko, 'ops11@example.com', .999)] * 5)))
        self.assertTrue(literal_line(region('INV-2027-04861', [(ko, 'INV-2027-04861', .97), (ko, 'INV-2027-0486l', .9)])))
        self.assertFalse(literal_line(region('ops11@example.com', [(ko, 'ops11@example.com', .999)])))   # one reading
        self.assertFalse(literal_line(region('해야 합니다', [(ko, '해야 합니다', .95)] * 3)))
        self.assertFalse(literal_line(region('INV-2027-04861', [(ko, 'INV-2027-04861', .97), (ko, '송장 2027-04861', .9)])))


if __name__ == '__main__':
    unittest.main()
