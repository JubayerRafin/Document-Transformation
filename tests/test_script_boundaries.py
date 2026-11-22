"""A change of writing system with no space between is a word boundary (Arabic PDFs, 2026-11-23)."""
import unittest

from transformation.validation.scripts import changes
from transformation.validation.value_redaction import literal_spans
from transformation.validation.privacy import plausible, detect


class ScriptBoundaryTests(unittest.TestCase):
    def test_a_number_or_code_stored_against_an_arabic_word_is_found(self):
        self.assertEqual(literal_spans('. شخص618  درجة', '618'), [(5, 8)])
        self.assertEqual(literal_spans('الباركودQTR-2027-09733\n43', 'QTR-2027-09733'), [(8, 22)])

    def test_a_value_inside_a_longer_word_of_its_own_script_is_not(self):
        self.assertEqual(literal_spans('EMP2026 x', '2026'), [])
        self.assertEqual(literal_spans('ID 618x', '618'), [])
        self.assertFalse(changes('a', '1'))
        self.assertTrue(changes('ص', '6'))
        self.assertTrue(changes('é', 'ب'))
        self.assertFalse(changes('é', 'a'))

    def test_a_head_count_is_not_an_identifier(self):
        self.assertFalse(plausible('618', 'Identifiers'))
        self.assertTrue(plausible('61834', 'Identifiers'))

    def test_an_email_starts_after_the_label_word_stored_against_it(self):
        values = [f['value'] for f in detect([{'id': 'r', 'text': 'البريد الإلكترونيhr80@example.com'}], ['Email addresses'],
                                             'http://127.0.0.1:1/v1', 'm', assist=False)]
        self.assertIn('hr80@example.com', values)
        self.assertFalse(any('الإلكتروني' in v for v in values))


if __name__ == '__main__':
    unittest.main()
