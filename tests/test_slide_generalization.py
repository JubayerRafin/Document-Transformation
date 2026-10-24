import unittest
from unittest.mock import patch
from transformation.llm.translation_guard import validate_candidate
from transformation.llm.translategemma import translate


class SlideGeneralizationTests(unittest.TestCase):
    def test_em_dash_months_use_source_backed_calendar_values(self):
        for source,target in [('Season (Mar—May)','계절 (3월—5월)'),
                              ('Season (Mar—May)','계절 (3월~5월)'),
                              ('May—June','5월—6월'),('September—May','9월—5월')]:
            with self.subTest(source=source):validate_candidate(source,target)
        for source,target in [('Season (Mar—May)','계절 (5월—3월)'),
                              ('We may—leave','우리는 5월에 떠날 수 있다'),
                              ('May—June','5월—7월'),
                              ('Season (Mar—May); ID AB-991','계절 (3월—5월); ID AB-992')]:
            with self.subTest(source=source),self.assertRaises(ValueError):validate_candidate(source,target)

    def test_valid_month_localization_needs_only_one_request(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('봄 (3월—5월)',{})) as chat:
            result,_=translate('unused','translategemma:12b','Spring (Mar—May)','Korean')
            self.assertEqual(result,'봄 (3월—5월)');self.assertEqual(chat.call_count,1)

    def test_failed_standalone_heading_retries_without_document_context(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[('이동\n추가된 내용',{}),('이동',{})]) as chat:
            result,_=translate('unused','translategemma:12b','MOBILITY','Korean',context='NEIGHBOR_ONLY_SENTINEL')
            self.assertEqual(result,'이동');self.assertEqual(chat.call_count,2)
            self.assertIn('NEIGHBOR_ONLY_SENTINEL',chat.call_args_list[0].args[2][0]['content'])
            self.assertNotIn('NEIGHBOR_ONLY_SENTINEL',chat.call_args_list[1].args[2][0]['content'])
            self.assertIn('single isolated heading',chat.call_args_list[1].args[2][0]['content'])

    def test_successful_heading_keeps_first_request_context_and_call_count(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('이동',{})) as chat:
            result,_=translate('unused','translategemma:12b','MOBILITY','Korean',context='NEIGHBOR_ONLY_SENTINEL')
            self.assertEqual(result,'이동');self.assertEqual(chat.call_count,1)
            self.assertIn('NEIGHBOR_ONLY_SENTINEL',chat.call_args.args[2][0]['content'])

    def test_bad_heading_retry_is_not_trimmed_into_acceptance(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('이동\n추가된 내용',{})) as chat:
            with self.assertRaisesRegex(ValueError,'added lines'):
                translate('unused','translategemma:12b','MOBILITY','Korean')
            self.assertEqual(chat.call_count,2)

    def test_failed_multiword_label_retry_isolates_source_from_neighbors(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[('정보\n추가',{}),('정보',{})]) as chat:
            translate('unused','translategemma:12b','General information','Korean',context='BACKGROUND_SENTINEL')
            self.assertNotIn('BACKGROUND_SENTINEL',chat.call_args.args[2][0]['content'])
            self.assertIn('single isolated heading',chat.call_args.args[2][0]['content'])


if __name__=='__main__':unittest.main()
