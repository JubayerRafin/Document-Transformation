"""Script-specific clean-ups of a chosen OCR line: digits, Arabic punctuation, Latin islands."""
import unittest

from tools.ocr.recognize_paddle_boxes import punctuation_from_reader, script_islands, western_digits


class WesternDigitsTests(unittest.TestCase):
    def test_a_mixed_number_is_western(self):
        self.assertEqual(western_digits('भुगतान की शर्तें: 3० दिन', 'hi'), 'भुगतान की शर्तें: 30 दिन')
        self.assertEqual(western_digits('উপকরণ ৫ থেকে 3০', 'bn'), 'উপকরণ 5 থেকে 30')

    def test_a_line_in_the_script_own_digits_stays(self):
        self.assertEqual(western_digits('तापमान ५ से ३० डिग्री', 'hi'), 'तापमान ५ से ३० डिग्री')
        self.assertEqual(western_digits('ABC 30', 'ko'), 'ABC 30')


class PunctuationFromReaderTests(unittest.TestCase):
    class Reader:
        def __init__(self, text, score=.9):
            self.text, self.score = text, score

        def predict(self, crop):
            return [{'rec_text': self.text, 'rec_score': self.score}]

    def test_full_stop_and_space_of_a_reading_with_the_same_letters(self):
        best = {'text': 'أخرىيجب', 'score': .93}
        self.assertEqual(punctuation_from_reader(None, best, self.Reader('أخرى. يجب'))['text'], 'أخرى. يجب')

    def test_other_letters_or_a_weak_reading_change_nothing(self):
        best = {'text': 'أخرىيجب', 'score': .93}
        self.assertIs(punctuation_from_reader(None, best, self.Reader('أخرى. يحب')), best)
        self.assertIs(punctuation_from_reader(None, best, self.Reader('أخرى. يجب', .5)), best)


class ScriptIslandTests(unittest.TestCase):
    def views(self, *pairs):
        return [{'text': t, 'score': s, 'variant': f'v{i}'} for i, (t, s) in enumerate(pairs)]

    def test_latin_letters_between_bengali_words_take_the_bengali_reading(self):
        best = {'text': 'সেবা COCS যোগাযোগ করুন।', 'score': .87}
        reads = self.views(('সেবা COCS যোগাযোগ করুন।', .87), ('সেবা COTS যোগাযোগ করুন।', .78),
                           ('সেবা ডেস্কে যোগাযোগ করুন।', .86), ('সেবা ডেস্কে যোগাযোগ করুন।', .865))
        self.assertEqual(script_islands(best, reads, 'bn')['text'], 'সেবা ডেস্কে যোগাযোগ করুন।')

    def test_a_latin_word_no_view_reads_in_the_script_stays(self):
        best = {'text': 'মোট GBP পরিশোধ', 'score': .9}
        reads = self.views(('মোট GBP পরিশোধ', .9), ('মোট GBF পরিশোধ', .88))
        self.assertIs(script_islands(best, reads, 'bn'), best)

    def test_a_weaker_or_disagreeing_view_changes_nothing(self):
        best = {'text': 'সেবা COCS যোগাযোগ করুন।', 'score': .9}
        weak = self.views(('সেবা ডেস্কে যোগাযোগ করুন।', .8))
        other_words = self.views(('সেবা ডেস্কে যোগাযোগ করো।', .9))
        two_readings = self.views(('সেবা ডেস্কে যোগাযোগ করুন।', .9), ('সেবা ডেস্ক যোগাযোগ করুন।', .9))
        for reads in (weak, other_words, two_readings):
            self.assertIs(script_islands(best, reads, 'bn'), best)

    def test_latin_words_beside_latin_words_stay(self):
        best = {'text': 'চালান INV 2026 COCS', 'score': .9}
        reads = self.views(('চালান INV 2026 ডেস্কে', .9))
        self.assertIs(script_islands(best, reads, 'bn'), best)


if __name__ == '__main__':
    unittest.main()
