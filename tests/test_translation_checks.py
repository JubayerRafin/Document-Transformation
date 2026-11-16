"""A full stop the model adds to a label or heading that had none is removed; nothing else changes."""
import unittest

from transformation.llm.translategemma import match_terminal_punctuation as match


class TerminalPunctuationTests(unittest.TestCase):
    def test_added_full_stop_is_removed_in_any_script(self):
        for source, target, expected in (
                ('Field', 'الحقل.', 'الحقل'), ('Value', 'ক্ষেত্র.', 'ক্ষেত্র'), ('Field', 'क्षेत्र।', 'क्षेत्र'),
                ('App console screenshot', '앱 콘솔 화면 캡처.', '앱 콘솔 화면 캡처'), ('Sample', 'サンプル。', 'サンプル'),
                ('Software Engineer II', 'Ingénieur logiciel II.', 'Ingénieur logiciel II'), ('Page 1', '페이지 1. ', '페이지 1 ')):
            self.assertEqual(match(source, target), expected, source)

    def test_source_punctuation_and_other_endings_are_kept(self):
        for source, target in (('The report is ready.', 'تقرير جاهز.'), ('Ready?', '준비됐나요?'), ('Note:', 'নোট:'),
                               ('Wait', 'Attendez...'), ('Total', 'Total'), ('', '.'), ('Name', 'Nom :')):
            self.assertEqual(match(source, target), target, source)


class ForeignScriptTests(unittest.TestCase):
    def test_letters_from_a_third_script_are_found(self):
        from transformation.llm.translategemma import foreign_script as find
        self.assertEqual(find('they could not travel by train', 'তারা поездом ভ্রমণ করতে পারছিলেন না', 'Bengali'), 'Cyrillic')
        self.assertEqual(find('Hello', '你好 こんにちは', 'Chinese (Simplified)'), 'Hiragana')
        self.assertEqual(find('Hello', 'नमस्ते হ্যালো', 'hi'), 'Bengali')

    def test_target_source_and_latin_letters_are_allowed(self):
        from transformation.llm.translategemma import foreign_script as find
        for source, target, language in (('Hello', '안녕하세요 漢字', 'Korean'), ('Hello', 'こんにちは 世界', 'Japanese'),
                                         ('Witkoff said', 'Witkoff a dit', 'French'), ('Привет мир', 'Bonjour le monde', 'French'),
                                         ('Total 42', 'مجموع 42', 'Arabic'), ('Привет', 'Привет', 'Klingon')):
            self.assertEqual(find(source, target, language), '', (source, target))


class KeptSourceTests(unittest.TestCase):
    def test_only_translation_validation_failures_keep_the_source_paragraph(self):
        from transformation.llm.ollama_native import validation_failure
        from transformation.llm.protected_text import PlaceholderIntegrityError
        for error in (ValueError('Translation changed, added or omitted protected literals.'),
                      ValueError('Translation added lines to a short source label.'), PlaceholderIntegrityError([]),
                      RuntimeError('TranslateGemma failed validation.')):
            self.assertTrue(validation_failure(error), error)
        for error in (OSError('connection refused'), TimeoutError('slow'), ValueError('Choose an explicitly configured source and target language.'),
                      RuntimeError('Model connection refused')):
            self.assertFalse(validation_failure(error), error)

    def test_a_paragraph_that_fails_every_retry_stays_in_the_source_language(self):
        from unittest.mock import patch
        from transformation.llm import ollama_native
        ollama_native.TRANSLATION_FAILED.clear()
        with patch('transformation.llm.translategemma.translate', side_effect=ValueError('Translation changed, added or omitted protected literals.')):
            text, stats = ollama_native._translate_paragraph('http://127.0.0.1:1/v1', 'translategemma:12b', 'Ref 2024-03- 20', 'Korean')
        self.assertEqual(text, 'Ref 2024-03- 20'); self.assertTrue(stats['kept_source_requires_review'])
        self.assertFalse(ollama_native.TRANSLATION_FAILED.is_set())
        with patch('transformation.llm.translategemma.translate', side_effect=OSError('down')):
            with self.assertRaises(OSError):
                ollama_native._translate_paragraph('http://127.0.0.1:1/v1', 'translategemma:12b', 'Ref', 'Korean')
        self.assertTrue(ollama_native.TRANSLATION_FAILED.is_set()); ollama_native.TRANSLATION_FAILED.clear()


class LoopingAnswerTests(unittest.TestCase):
    def test_answers_are_bounded_and_a_looping_answer_is_retried_then_kept_for_review(self):
        from unittest.mock import patch
        from transformation.llm import translategemma, ollama_native
        self.assertLess(translategemma.answer_budget('Rating'), 400)
        self.assertEqual(translategemma.answer_budget('x' * 5000), 4096)
        budgets = []
        def looping(endpoint, model, prompt, max_output=4096):
            budgets.append(max_output); raise ValueError(translategemma.TRUNCATED[0])
        with patch.object(ollama_native, 'chat', side_effect=looping):
            with self.assertRaises(ValueError) as caught:
                translategemma.translate('http://127.0.0.1:1/v1', 'translategemma:12b', 'Rating', 'German')
        self.assertEqual(len(budgets), 2)                         # retried once
        self.assertTrue(ollama_native.validation_failure(caught.exception))  # source kept, flagged for review
        answers = iter([ValueError(translategemma.TRUNCATED[1]), ('Bewertung', {})])
        def second_try(endpoint, model, prompt, max_output=4096):
            answer = next(answers)
            if isinstance(answer, Exception): raise answer
            return answer
        with patch.object(ollama_native, 'chat', side_effect=second_try):
            text, _ = translategemma.translate('http://127.0.0.1:1/v1', 'translategemma:12b', 'Rating', 'German')
        self.assertEqual(text, 'Bewertung')
        with patch.object(ollama_native, 'chat', side_effect=ValueError('Ollama rejected model x (HTTP 404): missing')):
            with self.assertRaises(ValueError) as caught:
                translategemma.translate('http://127.0.0.1:1/v1', 'translategemma:12b', 'Rating', 'German')
        self.assertFalse(ollama_native.validation_failure(caught.exception))  # configuration errors still fail


class EngineReviewWarningTests(unittest.TestCase):
    def test_flagged_paragraph_counts_become_readable_warnings(self):
        from transformation.pipeline import _engine_review_warnings
        warnings = _engine_review_warnings({'status': 'requires_human_review', 'untranslated_paragraphs': 1,
                                            'mixed_script_paragraphs': 2, 'shrunk_paragraphs': 0})
        self.assertEqual(len(warnings), 2)
        self.assertTrue(warnings[0].startswith('1 paragraph(s) stayed in the source language'))
        self.assertIn('third writing system', warnings[1])
        self.assertEqual(_engine_review_warnings(None), [])


if __name__ == '__main__':
    unittest.main()
