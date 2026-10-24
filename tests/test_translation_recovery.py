import unittest
from unittest.mock import patch

from transformation.llm.translategemma import translate
from transformation.llm.translation_recovery import recover_literals,recovery_parts
from transformation.llm.translation_guard import validate_candidate


class TranslationRecoveryTests(unittest.TestCase):
    def test_repeated_equation_markers_and_ids_stay_outside_model(self):
        source='Inspect {v1} using AB-81 then {v1}.'
        responses=iter(['검사', '사용', '다음'])
        def chat(endpoint,model,prompt):
            request=prompt[0]['content']
            self.assertNotIn('{v1}',request)
            self.assertNotIn('AB-81',request)
            self.assertNotIn('ZXKEEP',request)
            return next(responses),{'prompt_eval_count':2,'eval_count':1}
        with patch('transformation.llm.ollama_native.chat',side_effect=chat) as calls:
            result,stats=recover_literals('unused','test',source,'Korean')
        self.assertEqual(result,'검사 {v1} 사용 AB-81 다음 {v1}.')
        self.assertEqual(calls.call_count,3)
        self.assertEqual(stats['eval_count'],3)
        validate_candidate(source,result)

    def test_failed_normal_attempts_recover_and_account_all_calls(self):
        replies=[('값 누락',{'eval_count':1})]*2+[('검사',{'eval_count':1})]
        with patch('transformation.llm.ollama_native.chat',side_effect=replies) as calls:
            result,stats=translate('unused','translategemma:12b','Inspect {v1}.','Korean')
        self.assertEqual(result,'검사 {v1}.')
        self.assertEqual(stats['eval_count'],3)
        self.assertTrue(stats['recovery_review_required'])
        self.assertEqual(calls.call_count,3)

    def test_hallucinated_atom_in_recovery_still_fails(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('검사 AB-99',{})):
            with self.assertRaisesRegex(ValueError,'protected literals'):
                recover_literals('unused','test','Inspect {v1}.','Korean')

    def test_boundary_punctuation_cannot_fuse_a_version_with_next_word(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[('점수',{}),('Next 단계.',{})]):
            result,_=recover_literals('unused','test','Score 17, next phase.','Korean')
        self.assertEqual(result,'점수 17, Next 단계.')
        validate_candidate('Score 17, next phase.',result)

    def test_mixed_case_versioned_identifiers_are_whole_atoms(self):
        from transformation.llm.protected_text import protect_values
        _,tokens=protect_values('Use Model-v7 and encoder-50; translate two-stage normally.')
        self.assertEqual(list(tokens.values()),['Model-v7','encoder-50'])
        _,tokens=protect_values('Compare Model- 27 with encoder-50.')
        self.assertEqual(list(tokens.values()),['Model- 27','encoder-50'])

    def test_short_connector_retries_without_exposing_adjacent_values(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[('or',{}),('또는',{})]) as calls:
            result,_=recover_literals('unused','test','Model-v7 or encoder-50','Korean')
        self.assertEqual(result,'Model-v7 또는 encoder-50')
        self.assertEqual(calls.call_count,2)
        self.assertIn('natural-language connecting text',calls.call_args.args[2][0]['content'])
        self.assertNotIn('Model-v7',calls.call_args.args[2][0]['content'])

    def test_connector_retries_share_one_total_request_budget(self):
        replies=[('and',{}),('및',{})]*12
        source=''.join(f'and {{v{i}}} ' for i in range(13))
        with patch('transformation.llm.ollama_native.chat',side_effect=replies) as calls:
            with self.assertRaisesRegex(ValueError,'request budget'):
                recover_literals('unused','test',source,'Korean')
        self.assertEqual(calls.call_count,24)

    def test_measurement_notation_before_a_math_subscript_is_preserved(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[('점수',{}),('에서',{}),('밀리초',{})]):
            result,_=recover_literals('unused','test','Scores 42.1 AP{v1} in 10 ms','Korean')
        self.assertEqual(result,'점수 42.1 AP{v1} 에서 10 밀리초')

    def test_source_proven_attached_unit_spacing_is_not_a_changed_number(self):
        validate_candidate('Runs in 22 ms at 28.2 mAP','22ms 동안 28.2mAP로 실행됩니다.')
        for target,values in [('23ms 동안 실행됩니다.',()),('22ms, 22ms 동안 실행됩니다.',()),
                              ('DEV-22ms에서 실행됩니다.',()),('22ms 동안 실행됩니다.',('22 ms',))]:
            with self.subTest(target=target),self.assertRaises(ValueError):
                validate_candidate('Runs in 22 ms',target,values)

    def test_bibliographic_initials_are_preserved_without_translating_names(self):
        source='{v1}A. Rivera, B. Chen, and C. D. Malik. Model-v7.'
        with patch('transformation.llm.ollama_native.chat') as calls:
            result,_=recover_literals('unused','test',source,'Korean')
        self.assertEqual(result,source)
        calls.assert_not_called()

    def test_month_before_protected_year_is_localized_as_a_date(self):
        with patch('transformation.llm.ollama_native.chat') as calls:
            result,_=recover_literals('unused','test','Dec 2031.','Korean')
        self.assertEqual(result,'12월 2031.')
        calls.assert_not_called()

    def test_numbered_section_heading_is_not_mistaken_for_an_author_list(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('가. 요약',{})) as calls:
            result,_=recover_literals('unused','test','A. Summary {v1}.','Korean')
        self.assertEqual(result,'가. 요약 {v1}.')
        calls.assert_called_once()

    def test_untranslated_or_wrong_script_recovery_is_not_success(self):
        for result in ['Inspect','Unrelated English explanation']:
            with self.subTest(result=result),patch('transformation.llm.ollama_native.chat',return_value=(result,{})):
                with self.assertRaises(ValueError):recover_literals('unused','test','Inspect {v1}.','Korean')

    def test_recovery_is_bounded_before_any_model_request(self):
        with self.assertRaisesRegex(ValueError,'budget'):
            recovery_parts(' '.join(f'word {{v{i}}}' for i in range(25)))

    def test_successful_original_response_never_uses_recovery(self):
        def response(endpoint,model,prompt):
            return prompt[0]['content'].split('\n\n\n')[-1].replace('Inspect','검사'),{}
        with patch('transformation.llm.ollama_native.chat',side_effect=response) as calls,patch(
                'transformation.llm.translation_recovery.recover_literals') as recovery:
            value,_=translate('unused','translategemma:12b','Inspect {v1}.','Korean')
        self.assertEqual(value,'검사 {v1}.')
        self.assertEqual(calls.call_count,1)
        recovery.assert_not_called()

    def test_title_case_heading_retry_cannot_use_neighbor_bibliography(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[('참고 문헌\n[18] Someone',{}),('참고 문헌',{})]) as calls:
            result,_=translate('unused','translategemma:12b','References','Korean',context='BIBLIOGRAPHY_SENTINEL')
        self.assertEqual(result,'참고 문헌')
        self.assertNotIn('BIBLIOGRAPHY_SENTINEL',calls.call_args.args[2][0]['content'])

    def test_source_backed_compound_numbers_and_ordinals(self):
        for source,target in [('Two-stage methods','2단계 방법'),('Twenty-two-stage model','22단계 모델'),
                              ('The 3rd scale','3번째 척도'),('The 21st result','21번째 결과'),
                              ('non-zero origins','0이 아닌 원점')]:
            with self.subTest(source=source):validate_candidate(source,target)
        for source,target,values in [('Two-stage methods','3단계 방법',()),
                                     ('The 3rd scale','2번째 척도',()),
                                     ('The 3rd scale','3개 척도',()),
                                     ('The 3rd scale','3번째와 3번째 척도',()),
                                     ('Two-stage methods','2단계 방법',('Two-stage',)),
                                     ('The 3rd scale','3번째 척도',('3rd',))]:
            with self.subTest(source=source,target=target),self.assertRaises(ValueError):
                validate_candidate(source,target,values)
