"""Headings in capitals: their words are words, not codes or Roman numerals (EMA templates, 2026-11-24)."""
import unittest

from transformation.llm.translation_guard import literal_inventory, validate_candidate


def accepted(source, target):
    try:
        validate_candidate(source, target)
        return True
    except ValueError:
        return False


class CapitalHeadingTests(unittest.TestCase):
    def test_a_hyphenated_word_in_capitals_is_not_a_code(self):
        self.assertTrue(accepted('INDIVIDUELLES ERKENNUNGSMERKMAL – VOM MENSCHEN LESBARES FORMAT',
                                 'INDIVIDUAL IDENTIFICATION FEATURE – HUMAN-READABLE FORMAT'))
        self.assertEqual(literal_inventory('Code HR-2024, SKU-PR, UTF-8', ()), {'HR-2024': 1, 'SKU-PR': 1, 'UTF-8': 1})
        self.assertFalse(accepted('Code SKU-PR', 'Código'))

    def test_letters_of_an_accented_word_are_not_a_roman_numeral(self):
        self.assertTrue(accepted('4.CLINICAL PARTICULARS', '4. DETALLES CLÍNICOS'))
        self.assertTrue(accepted('ANNEX III', 'ANEXO III'))
        self.assertFalse(accepted('ANNEX III', 'ANEXO II'))


if __name__ == '__main__':
    unittest.main()
