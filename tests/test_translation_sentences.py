"""A translation that drops a sentence is refused; the paragraph is then translated sentence by sentence."""
import unittest
from unittest.mock import patch

from transformation.llm.translategemma import translate
from transformation.llm.translation_guard import omitted_sentence, split_sentences, validate_candidate

SOURCE = ('Maintenance on the main production line is scheduled for the first weekend of each month. '
          'Revenue rose by 9 percent this quarter, mainly because of new contracts in the logistics segment. '
          'Revenue rose by 11 percent this quarter, mainly because of new contracts in the logistics segment.')
DROPPED = '本季度，由于物流板块的新合同，收入增长了9%。本季度，由于物流板块的新合同，收入增长了11%。'
PIECES = ['主生产线的维护工作定于每月第一个周末进行。', '本季度收入增长了9%，主要得益于物流业务的新合同。',
          '本季度收入增长了11%，主要得益于物流业务的新合同。']


class SentenceTests(unittest.TestCase):
    def test_a_dropped_sentence_is_refused(self):
        # Held-out English to Chinese report, 2026-11-24: the first sentence was missing.
        self.assertTrue(omitted_sentence(SOURCE, DROPPED))
        with self.assertRaisesRegex(ValueError, 'omitted a source sentence'):
            validate_candidate(SOURCE, DROPPED)
        validate_candidate(SOURCE, ''.join(PIECES))

    def test_pieces_cut_at_a_paragraph_edge_are_not_sentences(self):
        self.assertFalse(omitted_sentence('La maintenance est prévue le premier week-end de chaque mois. Le',
                                          'Maintenance is scheduled for the first weekend of each month'))
        self.assertFalse(omitted_sentence('Keep it dry.', 'Gardez-le au sec.'))

    def test_sentences_are_cut_after_their_end_and_keep_every_character(self):
        text = 'Revenue rose by 9.5 percent, e.g. in logistics. Mr. Lee agreed! 本季度。收入增长'
        self.assertEqual(split_sentences(text), ['Revenue rose by 9.5 percent, e.g. in logistics. ', 'Mr. Lee agreed! ',
                                                 '本季度。', '收入增长'])
        self.assertEqual(''.join(split_sentences(text)), text)

    def test_a_paragraph_that_keeps_dropping_a_sentence_is_translated_sentence_by_sentence(self):
        # Every answer for the whole paragraph drops the first sentence; each sentence alone is kept.
        sentences = [p.strip() for p in split_sentences(SOURCE)]

        def chat(endpoint, model, prompt, *args, **kwargs):
            request = ' '.join(m['content'] for m in prompt)
            if all(s in request for s in sentences):
                return DROPPED, {'eval_count': 1}
            for sentence, piece in zip(sentences, PIECES):
                if sentence in request:
                    return piece, {'eval_count': 1}
            return '{"faithful": true, "issues": []}', {'eval_count': 1}
        with patch.dict('os.environ', {'TRANSFORMX_PII_ENDPOINT': 'http://second', 'TRANSFORMX_PII_MODEL': 'second'}), \
             patch('transformation.llm.ollama_native.chat', side_effect=chat):
            result, stats = translate('unused', 'translategemma:12b', SOURCE, 'Chinese (Simplified)')
        self.assertEqual(result, ''.join(PIECES))
        self.assertEqual(stats['recovery'], 'sentence_by_sentence')


if __name__ == '__main__':
    unittest.main()
