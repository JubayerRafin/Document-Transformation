"""Translation checks: local digits, spaced percent signs, broken codes and added lines."""
import unittest

from transformation.llm.translation_guard import validate_candidate


def refused(source, target, values=()):
    try:
        validate_candidate(source, target, values)
    except ValueError as exc:
        return str(exc)
    return ''


class LocalDigitTests(unittest.TestCase):
    def test_list_number_in_local_digits_is_the_same_number(self):
        self.assertEqual(refused('1. Every change must be signed by both parties.',
                                 '১. প্রতিটি পরিবর্তনে উভয় পক্ষের স্বাক্ষর লাগবে।'), '')
        self.assertEqual(refused('4. Report any damage within two days.',
                                 '٤. أبلغ عن أي تلف خلال يومين.'), '')
        self.assertEqual(refused('About 59 people work here.', 'এখানে প্রায় ৫৯ জন কাজ করেন।'), '')

    def test_a_different_local_number_is_still_refused(self):
        self.assertTrue(refused('About 59 people work here.', 'এখানে প্রায় ৫৮ জন কাজ করেন।'))

    def test_identifier_in_local_digits_is_still_refused(self):
        self.assertTrue(refused('Invoice INV-2024 is due.', 'চালান INV-২০২৪ বকেয়া।'))


class PercentSpacingTests(unittest.TestCase):
    def test_spaced_percent_may_come_back_unspaced(self):
        self.assertEqual(refused('Los ingresos aumentaron un 19 % este trimestre.',
                                 'Revenue rose 19% this quarter.'), '')
        self.assertEqual(refused('Revenue rose 19% this quarter.',
                                 'Le chiffre d’affaires a progressé de 19 % ce trimestre.'), '')

    def test_a_changed_percentage_is_still_refused(self):
        self.assertTrue(refused('Los ingresos aumentaron un 19 % este trimestre.',
                                'Revenue rose 18% this quarter.'))


class BrokenCodeTests(unittest.TestCase):
    def test_code_broken_by_extraction_spaces_may_come_back_joined(self):
        self.assertEqual(refused('RVL- 2025- 68776', 'RVL-2025-68776'), '')

    def test_a_changed_code_is_still_refused(self):
        self.assertTrue(refused('RVL- 2025- 68776', 'RVL-2025-68777'))


class AddedLineTests(unittest.TestCase):
    def test_paragraph_without_spaces_may_put_each_sentence_on_a_line(self):
        source = '对协议的任何修改都必须以书面形式作出。本设备应存放在干燥处。审计未发现重大缺陷。'
        target = ('Any amendment to the agreement must be made in writing.\n'
                  'This equipment should be stored in a dry place.\nThe audit found no material deficiencies.')
        self.assertEqual(refused(source, target), '')

    def test_short_label_still_keeps_one_line(self):
        self.assertTrue(refused('Copperleaf Software Thomas Becker', 'Copperleaf Software\nThomas Becker'))

    def test_note_on_a_new_line_is_refused(self):
        source = '委員会は、試験プログラムを拡大することを推奨します。 委員会は、試験プ'
        target = ('The committee recommends expanding the pilot program. The committee...\n\n'
                  'Your text appears to be cut off. If you send the rest, I will translate it.')
        self.assertEqual(refused(source, target), 'Translation added sentences on new lines.')
        self.assertEqual(refused('Bewahren Sie dieses Dokument auf und wenden Sie sich an den Service-Desk.',
                                 'Keep this document and contact the service desk if details are wrong.\n\n'
                                 'Source: Provided text.'), 'Translation added sentences on new lines.')


if __name__ == '__main__':
    unittest.main()
