import os
import unittest
from unittest.mock import patch

from transformation.llm import translategemma

SOURCE = 'Customer satisfaction rose for the third quarter in a row and reached a record high.'
MIXED = 'গ্রাহক সন্তুষ্টি তৃতীয় ত্রৈমাসিক подряд বৃদ্ধি পেয়েছে এবং একটি রেকর্ড উচ্চতায় পৌঁছেছে।'
CLEAN = 'গ্রাহক সন্তুষ্টি টানা তৃতীয় ত্রৈমাসিকে বৃদ্ধি পেয়েছে এবং একটি রেকর্ড উচ্চতায় পৌঁছেছে।'


def server(answers):
    def chat(endpoint, model, messages, max_output=4096, response_format=None, purpose=None):
        return answers[model], {'prompt_eval_count': 10, 'eval_count': 10}
    return chat


class SecondModelTests(unittest.TestCase):
    def run_translation(self, answers, env):
        with patch('transformation.llm.ollama_native.chat', server(answers)), \
             patch('transformation.llm.ollama_native.record_progress'), \
             patch.dict(os.environ, env, clear=False):
            for name in ('TRANSFORMX_PII_ENDPOINT', 'TRANSFORMX_PII_MODEL'):
                if name not in env:
                    os.environ.pop(name, None)
            return translategemma.translate('http://127.0.0.1:8010/v1', 'translategemma:12b', SOURCE, 'Bengali')

    def test_the_other_model_translates_a_paragraph_that_still_mixes_in_a_third_script(self):
        result, stats = self.run_translation(
            {'translategemma:12b': MIXED, 'qwen3.5-9b-fp8': CLEAN},
            {'TRANSFORMX_PII_ENDPOINT': 'http://127.0.0.1:8000/v1', 'TRANSFORMX_PII_MODEL': 'qwen3.5-9b-fp8'})
        self.assertEqual(result, CLEAN)
        self.assertTrue(stats['second_model_after_foreign_script'])

    def test_without_a_clean_second_answer_the_mixed_one_is_kept_and_flagged(self):
        result, stats = self.run_translation(
            {'translategemma:12b': MIXED, 'qwen3.5-9b-fp8': MIXED},
            {'TRANSFORMX_PII_ENDPOINT': 'http://127.0.0.1:8000/v1', 'TRANSFORMX_PII_MODEL': 'qwen3.5-9b-fp8'})
        self.assertEqual(result, MIXED)
        self.assertTrue(stats['foreign_script_review_required'])
        result, stats = self.run_translation({'translategemma:12b': MIXED}, {})
        self.assertTrue(stats['foreign_script_review_required'])


class SecondModelAfterValidationTests(unittest.TestCase):
    def test_the_other_model_gets_one_try_when_both_answers_change_a_value(self):
        source = 'The following examples depict letter-size paper with the short edge first.'
        wrong = '다음 예시는 짧은 변이 먼저 들어가는 A4 용지를 보여줍니다.'
        right = '다음 예시는 짧은 변이 먼저 들어가는 레터 크기 용지를 보여줍니다.'
        answers = {'translategemma:12b': wrong, 'qwen3.5-9b-fp8': right}
        with patch('transformation.llm.ollama_native.chat', server(answers)),              patch('transformation.llm.ollama_native.record_progress'),              patch.dict(os.environ, {'TRANSFORMX_PII_ENDPOINT': 'http://127.0.0.1:8000/v1',
                                     'TRANSFORMX_PII_MODEL': 'qwen3.5-9b-fp8'}):
            result, stats = translategemma.translate('http://127.0.0.1:8010/v1', 'translategemma:12b', source, 'Korean')
        self.assertEqual(result, right)
        self.assertTrue(stats['second_model_after_validation'])

    def test_without_a_second_model_the_failure_is_raised_as_before(self):
        source = 'The following examples depict letter-size paper with the short edge first.'
        with patch('transformation.llm.ollama_native.chat', server({'translategemma:12b': '다음 A4 용지를 보여줍니다.'})),              patch('transformation.llm.ollama_native.record_progress'),              patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TRANSFORMX_PII_ENDPOINT', None); os.environ.pop('TRANSFORMX_PII_MODEL', None)
            with self.assertRaises(ValueError):
                translategemma.translate('http://127.0.0.1:8010/v1', 'translategemma:12b', source, 'Korean')


if __name__ == '__main__':
    unittest.main()
