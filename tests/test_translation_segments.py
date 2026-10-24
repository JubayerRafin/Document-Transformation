import unittest
from unittest.mock import patch
from transformation.llm.translation_segments import bounded_segments
from transformation.llm.protected_text import protect_values
from transformation.llm.translategemma import translate


class TranslationSegmentTests(unittest.TestCase):
    def test_short_input_is_unchanged(self):
        self.assertEqual(bounded_segments('Short source.'),['Short source.'])

    def test_every_source_character_is_retained(self):
        text=('A paragraph about methods and results.\n\n' * 100)+'End.'
        parts=bounded_segments(text)
        self.assertGreater(len(parts),1)
        self.assertEqual(''.join(parts),text)
        self.assertTrue(all(len(p)<=1500 and len(protect_values(p)[0])<=1500 for p in parts))

    def test_mask_expansion_is_budgeted_even_for_short_source(self):
        text=' '.join('score '+str(n) for n in range(100))
        self.assertLess(len(text),1500)
        self.assertGreater(len(protect_values(text)[0]),1500)
        parts=bounded_segments(text)
        self.assertGreater(len(parts),1)
        self.assertEqual(''.join(parts),text)
        self.assertTrue(all(len(protect_values(p)[0])<=1500 for p in parts))

    def test_explicit_multiword_span_and_formula_marker_never_split(self):
        value='Confidential Account Name 42'
        text=('ordinary words '*90)+value+' {v18} '+('more source '*100)
        parts=bounded_segments(text,[value])
        self.assertEqual(''.join(parts),text)
        self.assertEqual(sum(value in p for p in parts),1)
        self.assertEqual(sum('{v18}' in p for p in parts),1)

    def test_indivisible_span_is_rejected_without_truncation(self):
        with self.assertRaisesRegex(ValueError,'indivisible'):
            bounded_segments('x'*1501)
        with self.assertRaisesRegex(ValueError,'indivisible'):
            bounded_segments('Start '+('a word '*300)+' end', ['a word '*300])

    def test_total_segment_budget_is_bounded(self):
        with self.assertRaisesRegex(ValueError,'segment count'):
            bounded_segments('words '*1000,max_segments=2)

    def test_numbered_entries_do_not_split_an_author_initial_chain(self):
        entries=[f'[{n}] A. Rivera, B. Chen, and C. Malik. '+('Research findings and evidence. '*5)+f'{2000+n}. '
                 for n in range(1,12)]
        text=''.join(entries);parts=bounded_segments(text)
        starts={text.index(entry) for entry in entries};offset=0
        for part in parts[:-1]:
            offset+=len(part)
            self.assertIn(offset,starts)
        self.assertEqual(''.join(parts),text)

    def test_long_adapter_requests_fit_and_final_literals_are_checked(self):
        text=' '.join('Inspect record AB-'+str(n)+'.' for n in range(150))
        # An echo is a transport/preservation fixture, not semantic approval.
        def echo(endpoint,model,messages):
            payload=messages[0]['content'].rsplit('\n\n\n',1)[1]
            self.assertLessEqual(len(payload),1500)
            return payload,{'eval_count':2,'prompt_eval_count':3}
        with patch('transformation.llm.ollama_native.chat',side_effect=echo) as calls:
            result,stats=translate('unused','translategemma:12b',text,'Korean')
        self.assertEqual(result,text)
        self.assertEqual(calls.call_count,stats['segments'])
        self.assertEqual(stats['eval_count'],2*calls.call_count)

    def test_failed_segment_cannot_return_partial_success(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=RuntimeError('transport failed')):
            with self.assertRaisesRegex(RuntimeError,'transport failed'):
                translate('unused','translategemma:12b','Sentence about the process. '*100,'Korean')

    def test_short_adapter_keeps_one_request(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('보고서',{})) as calls:
            self.assertEqual(translate('unused','translategemma:12b','Report','Korean')[0],'보고서')
        self.assertEqual(calls.call_count,1)
