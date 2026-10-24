import unittest
from transformation.llm.translation_guard import validate_candidate
from transformation.llm.protected_text import protect_values,restore_values


class TranslationGuardTests(unittest.TestCase):
    def test_korean_particles_do_not_hide_unchanged_dates_codes_or_numbers(self):
        validate_candidate('Effective 2031-05-09; code AB-9912; count 42',
                           '2031-05-09에 적용되며 AB-9912를 사용하고 수량은 42개입니다.')
        with self.assertRaises(ValueError):
            validate_candidate('Effective 2031-05-09', '2031-05-10에 적용됩니다.')
        with self.assertRaises(ValueError):
            validate_candidate('Code AB-9912', 'AB-99120를 사용합니다.')

    def test_sentence_punctuation_does_not_change_numeric_literals(self):
        validate_candidate('Device: unit-0072 Owner: maya@example.org',
                           '장치: unit-0072, 소유자: maya@example.org.')
        validate_candidate('Total USD12,450.50; count 42',
                           '합계 USD12,450.50, 수량 42.')
        validate_candidate('Cost $120', '가격 $120,')
        with self.assertRaises(ValueError):
            validate_candidate('Count 12,450', '수량 12450,')
        with self.assertRaises(ValueError):
            validate_candidate('Device: unit-0072', '장치: unit-0073,')

    def test_complete_timestamp_is_one_exact_literal(self):
        source='Captured 2025-04-12 16:27 UTC'
        protected,tokens=protect_values(source)
        self.assertIn('2025-04-12 16:27 UTC',tokens.values())
        self.assertEqual(restore_values(protected,tokens),source)
        with self.assertRaises(ValueError):
            validate_candidate(source,'촬영 2025-04-12 16: 27 UTC')

    def test_roman_pattern_never_protects_empty_or_invalid_numerals(self):
        protected,tokens=protect_values('ID: SECRET-001; role IV; CID label')
        self.assertNotIn('',tokens.values())
        self.assertNotIn('ID',tokens.values())
        self.assertIn('IV',tokens.values())
    def test_currency_and_quantities_roundtrip_exactly(self):
        source='Total USD12,450.50; limit 25%; reference ZX-109.'
        protected,tokens=protect_values(source)
        self.assertNotIn('USD12,450.50',protected)
        self.assertEqual(restore_values(protected,tokens),source)
        with self.assertRaises(ValueError):
            validate_candidate('USD12,450.50','12,450.50 dollars')

    def test_literal_only_output_cannot_drop_label(self):
        with self.assertRaisesRegex(ValueError,'descriptive'):
            validate_candidate('Pass ZX-109','ZX-109')
        validate_candidate('Pass ZX-109','출입증 ZX-109')

    def test_neighbor_literals_and_extra_lines_rejected(self):
        with self.assertRaises(ValueError):
            validate_candidate('Warning','경고 ZX-109')
        with self.assertRaises(ValueError):
            validate_candidate('Warning','경고\n다른 내용')

    def test_legitimate_expansion_without_new_facts(self):
        validate_candidate('ABC','제품 코드 (ABC)')
        validate_candidate('User report','사용자 보고서')
