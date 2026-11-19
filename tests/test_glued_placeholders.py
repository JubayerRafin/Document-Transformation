import unittest
from unittest.mock import patch

from transformation.llm.translation_guard import glued_placeholders, validate_candidate


class GluedPlaceholderTests(unittest.TestCase):
    SOURCE = 'Document ID: SEC-2024-{v1} Classification: Internal'

    def test_an_identifier_split_by_a_moved_placeholder_is_rejected(self):
        self.assertEqual(glued_placeholders(self.SOURCE), ['SEC-2024-{v1}'])
        with self.assertRaisesRegex(ValueError, 'protected literals'):
            validate_candidate(self.SOURCE, '문서 ID: SEC-2024 분류: 내부 {v1}')
        validate_candidate(self.SOURCE, '문서 ID: SEC-2024-{v1} 분류: 내부')

    def test_only_identifier_like_starts_are_held_together(self):
        # Letters alone, or a word without digits, may be translated around the placeholder.
        self.assertEqual(glued_placeholders('PQ{v1} and see{v2} or B{v3}'), [])
        self.assertEqual(glued_placeholders('Due 2024-03-{v1}, ref HR-2024-{v2}.'), ['2024-03-{v1}', 'HR-2024-{v2}'])
        self.assertEqual(glued_placeholders('Value {v1} alone'), [])

    def test_each_occurrence_is_needed(self):
        source = 'SEC-2024-{v1} / SEC-2024-{v1}'
        with self.assertRaisesRegex(ValueError, 'protected literals'):
            validate_candidate(source, 'SEC-2024-{v1} / SEC-2024 {v1}')

    def test_a_moved_placeholder_is_retried_and_kept_together(self):
        from transformation.llm.translategemma import translate
        answers = [('문서 ID: SEC-2024 분류: 내부 {v1}', {}), ('문서 ID: SEC-2024-{v1} 분류: 내부', {})]
        with patch('transformation.llm.ollama_native.chat', side_effect=answers) as call:
            result, _ = translate('unused', 'gpt-5-mini', self.SOURCE, 'Korean')
        self.assertEqual(result, '문서 ID: SEC-2024-{v1} 분류: 내부')
        self.assertEqual(call.call_count, 2)


if __name__ == '__main__':
    unittest.main()
