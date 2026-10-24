import json
import unittest
from unittest.mock import patch
from transformation.validation.privacy import detect


def response(value,category='Names'):
    return json.dumps({'findings':[{'value':value,'category':category}]}),{}


class PrivacyProposalTests(unittest.TestCase):
    def test_neighbor_value_retried_without_context_not_silently_accepted(self):
        region={'id':'cell','text':'Employee ID','metadata':{'row_values':['Employee ID','Maya Stone']}}
        with patch('transformation.llm.ollama_native.chat',side_effect=[response('Maya Stone'),('{"findings":[]}',{})]) as chat:
            self.assertEqual(detect([region],['Names'],'unused','model'),[])
        first=json.loads(chat.call_args_list[0].args[2][1]['content'])
        retry=json.loads(chat.call_args_list[1].args[2][1]['content'])
        self.assertIn('extraction_context',first)
        self.assertNotIn('extraction_context',retry)
        self.assertNotIn('Maya Stone',json.dumps(chat.call_args_list[1].args[2]))
        self.assertEqual(retry['text'],region['text'])

    def test_valid_retry_retains_exact_source_offsets_and_repeated_values(self):
        region={'id':'cell','text':'Maya Stone / Maya Stone','metadata':{'row_values':['Other Person']}}
        with patch('transformation.llm.ollama_native.chat',side_effect=[response('Other Person'),response('Maya Stone')]):
            findings=detect([region],['Names'],'unused','model')
        self.assertEqual(findings[0]['spans'],[[0,10],[13,23]])
        for start,end in findings[0]['spans']:self.assertEqual(region['text'][start:end],findings[0]['value'])

    def test_two_invalid_responses_fail_closed_without_pattern_only_success(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[response('Absent'),response('Absent')]) as chat:
            with self.assertRaisesRegex(ValueError,'validation after retry'):
                detect([{'id':'r','text':'mail: test@example.org'}],['Names','Email addresses'],'unused','model')
        self.assertEqual(chat.call_count,2)

    def test_mixed_valid_and_invalid_list_cannot_be_partially_accepted(self):
        mixed=json.dumps({'findings':[{'value':'Maya','category':'Names'},{'value':'Invented','category':'Names'}]})
        with patch('transformation.llm.ollama_native.chat',return_value=(mixed,{})):
            with self.assertRaises(ValueError):detect([{'id':'r','text':'Maya'}],['Names'],'unused','model')

    def test_normalized_or_reclassified_values_still_rejected(self):
        cases=[response('Maya Stone'),response('Maya\u00a0Stone','Addresses'),('{"findings":"wrong"}',{}),('not json',{})]
        for result in cases:
            with self.subTest(result=result),patch('transformation.llm.ollama_native.chat',return_value=result):
                with self.assertRaises(ValueError):detect([{'id':'r','text':'Maya\u00a0Stone'}],['Names'],'unused','model')

    def test_valid_first_response_needs_one_call(self):
        with patch('transformation.llm.ollama_native.chat',return_value=response('Maya')) as chat:
            findings=detect([{'id':'r','text':'Maya'}],['Names'],'unused','model')
        self.assertEqual(chat.call_count,1)
        self.assertEqual(findings[0]['value'],'Maya')

    def test_patterns_and_assist_disabled_make_no_model_call(self):
        with patch('transformation.llm.ollama_native.chat') as chat:
            self.assertEqual(detect([{'id':'r','text':'Maya'}],['Names'],'unused','model',assist=False),[])
            matches=detect([{'id':'r','text':'test@example.org'}],['Email addresses'],'unused','model')
        chat.assert_not_called()
        self.assertEqual(matches[0]['value'],'test@example.org')


if __name__=='__main__':unittest.main()
