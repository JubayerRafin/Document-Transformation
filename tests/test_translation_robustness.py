"""Paragraphs that used to be kept in the source language although the model's answer was usable
(mass test, 2026-11-19): damaged tokens, edge placeholders, glued placeholders, a label split
into lines, spelled-out percentages. Every protected value must still come back exactly."""
import unittest
from unittest.mock import patch

from transformation.llm.protected_text import PlaceholderIntegrityError, protect_values, restore_values
from transformation.llm.translategemma import _edge_placeholders, _unglued, translate
from transformation.llm.translation_guard import validate_candidate


class DamagedTokenTests(unittest.TestCase):
    def tokens(self):
        _, tokens = protect_values('Call +44 20 7946 123 for support, ref AB-2024-001.', ['+44 20 7946 123'])
        first, second = list(tokens)
        return tokens, first[6:14], second

    def test_a_token_missing_its_prefix_or_hash_start_is_restored(self):
        tokens, digest, second = self.tokens()
        for damaged in (f'{digest}X0XZ', f'KEEP{digest}X0XZ', f'{digest[2:]}X0XZ', f'zxkeep{digest.lower()}x0xz'):
            self.assertEqual(restore_values(f'지원은 {damaged} 로, 참조 {second}.', tokens),
                             '지원은 +44 20 7946 123 로, 참조 AB-2024-001.', damaged)

    def test_unrecognisable_or_duplicated_tokens_still_fail(self):
        tokens, digest, second = self.tokens()
        first = next(iter(tokens))
        for text in (f'{digest[4:]}X0XZ {second}', f'{digest}X0XZ {first} {second}', f'{digest}X5XZ {second}'):
            with self.assertRaises(PlaceholderIntegrityError):
                restore_values(text, tokens)


class SourceEchoTests(unittest.TestCase):
    def test_a_copy_of_the_source_after_the_translation_is_removed(self):
        from transformation.llm.translategemma import without_source_echo
        source = "HP's all-inclusive help for the printer includes the following information:"
        answer = 'HP 프린터에 대한 종합적인 지원 서비스는 다음 정보를 포함합니다.\n\nSource: ' + source
        self.assertEqual(without_source_echo(source, answer), 'HP 프린터에 대한 종합적인 지원 서비스는 다음 정보를 포함합니다.')
        self.assertEqual(without_source_echo(source, '다음 정보를 포함합니다. ' + source), '다음 정보를 포함합니다.')

    def test_other_answers_are_left_alone(self):
        from transformation.llm.translategemma import without_source_echo
        source = 'Install the HP Smart app on your phone.'
        for answer in ('휴대폰에 HP Smart 앱을 설치하십시오.', source, 'Source: ' + source,
                       source + ' 휴대폰에 설치하십시오.'):
            self.assertEqual(without_source_echo(source, answer), answer)
        self.assertEqual(without_source_echo('HP Smart', '앱 HP Smart'), '앱 HP Smart')


class PlaceholderLayoutTests(unittest.TestCase):
    def test_only_markers_at_sentence_edges_are_held_out(self):
        self.assertEqual(_edge_placeholders('{v1}All shipments must be inspected.'), (4, 36))
        self.assertEqual(_edge_placeholders('{v1}이사회는 승인했습니다.'), (4, 16))
        self.assertEqual(_edge_placeholders('Done. {v3}'), (0, 5))
        self.assertEqual(_edge_placeholders('5.1 今四半期の売上は増加しました。'), (4, 19))
        self.assertEqual(_edge_placeholders('{v1}3. Payment is due.'), (7, 22))
        self.assertEqual(_edge_placeholders('(iv) The parties agree.'), (5, 23))
        for text in ('{v1} items were shipped.', 'Call {v1} for support.', 'The total was {v1}.', '{v1}', 'Due {v1}',
                     '3 個の荷物が届きました。', '3.5 kg were shipped.', '2024. Revenue grew.'):
            self.assertIsNone(_edge_placeholders(text), text)

    def test_glued_placeholders_get_spaces_but_identifiers_do_not(self):
        self.assertEqual(_unglued('Call{v1}for support.'), 'Call {v1} for support.')
        self.assertEqual(_unglued('CallZXKEEPAB12CD34X0XZfor'), 'Call ZXKEEPAB12CD34X0XZ for')
        for text in ('SEC-2024-{v1} Classification', 'PQ{v1} value'):
            self.assertEqual(_unglued(text), text)

    def test_a_leading_bullet_placeholder_is_kept_in_place(self):
        answers = [('모든 화물은 도착 시 검사해야 합니다.', {})]
        with patch('transformation.llm.ollama_native.chat', side_effect=answers) as call:
            result, _ = translate('unused', 'translategemma:12b', '{v1}All shipments must be inspected.', 'Korean')
        self.assertEqual(result, '{v1}모든 화물은 도착 시 검사해야 합니다.')
        self.assertNotIn('{v1}', call.call_args.args[2][0]['content'])

    def test_a_one_line_label_returned_on_two_lines_is_joined(self):
        with patch('transformation.llm.ollama_native.chat', return_value=('감사합니다.\n에밀리 로버츠', {})):
            result, stats = translate('unused', 'translategemma:12b', 'Yours sincerely, Emily Roberts', 'Korean')
        self.assertEqual(result, '감사합니다. 에밀리 로버츠')
        self.assertNotIn('kept_source_requires_review', stats)


class SpelledOutPercentTests(unittest.TestCase):
    def test_five_percent_may_be_written_5_percent(self):
        source = 'Results varied by less than five percent across sites.'
        validate_candidate(source, '모든 사이트에서 결과 차이는 5% 미만이었습니다.')
        validate_candidate(source, 'Die Ergebnisse schwankten um weniger als 5 % zwischen den Standorten.')
        for target in ('결과 차이는 7% 미만이었습니다.', '5% 및 5% 미만'):
            with self.assertRaises(ValueError):
                validate_candidate(source, target)
        with self.assertRaises(ValueError):
            validate_candidate('Results varied by less than five units.', '결과 차이는 5% 미만이었습니다.')


if __name__ == '__main__':
    unittest.main()


class MiddleDotTests(unittest.TestCase):
    def test_the_katakana_middle_dot_is_kept_only_in_japanese(self):
        from transformation.llm.translategemma import middle_dot
        self.assertEqual(middle_dot('Grace Kim・grace.kim@example.com', 'English'), 'Grace Kim·grace.kim@example.com')
        self.assertEqual(middle_dot('东京・大阪', 'Chinese (Simplified)'), '东京·大阪')
        self.assertEqual(middle_dot('東京・大阪', 'Japanese'), '東京・大阪')


class LongParagraphTests(unittest.TestCase):
    def test_a_paragraph_just_under_the_limit_with_glued_placeholders_is_translated(self):
        sentence = 'Call{v1}for support.'
        text = (sentence + ' ') * 70
        text = text[:1490]
        self.assertLessEqual(len(text), 1500)
        echo = lambda *a, **k: (a[2][-1]['content'].split('\n\n\n')[-1], {})   # the text, as a model would
        with patch('transformation.llm.ollama_native.chat', side_effect=echo):
            result, stats = translate('unused', 'translategemma:12b', text, 'Korean')
        self.assertIn('{v1}', result)

    def test_an_oversized_region_keeps_the_paragraph_instead_of_failing(self):
        from transformation.llm.ollama_native import validation_failure
        from transformation.llm.translategemma import messages
        with self.assertRaises(ValueError) as caught:
            messages('x' * 1801, 'Korean')
        self.assertTrue(validation_failure(caught.exception))


class ParagraphFailureScopeTests(unittest.TestCase):
    def test_content_errors_keep_the_paragraph_and_setup_errors_still_fail(self):
        from transformation.llm.ollama_native import validation_failure
        for message in ('Email addresses do not match the protected source.',
                        'Source has an indivisible token or protected span beyond the translation request limit.',
                        'Source exceeds the bounded translation segment count.', 'Invalid scope interpretation.',
                        'Translation region exceeds 1800 characters; split it upstream.'):
            self.assertTrue(validation_failure(ValueError(message)), message)
        for error in (ValueError('A model endpoint must be an http:// or https:// address.'),
                      ValueError('Choose an explicitly configured source and target language.'),
                      TimeoutError('timed out'), ConnectionError('refused')):
            self.assertFalse(validation_failure(error), error)
