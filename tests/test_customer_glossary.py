"""Customer glossary terms reach translation and win over the automatic glossary. No models needed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from transformation.llm.terminology import prepare

TERMS = {'redaction': 'Schwärzung', 'redaction pipeline': 'Schwärzungspipeline', 'Title': 'Position'}


class PrepareTests(unittest.TestCase):
    def test_terms_match_whole_words_in_any_case_and_follow_the_original_capitals(self):
        cases = {'The redaction pipeline masks values.': 'The Schwärzungspipeline masks values.',
                 'Redaction must be checked.': 'Schwärzung must be checked.',
                 'REDACTION TEST': 'SCHWÄRZUNG TEST',
                 'Title: Engineer': 'Position: Engineer',
                 'Subtitle and entitled stay.': 'Subtitle and entitled stay.'}
        for source, expected in cases.items():
            self.assertEqual(prepare(source, TERMS)[0], expected, source)

    def test_replacements_are_returned_as_values_to_lock(self):
        _, locked, applied = prepare('A redaction and another Redaction.', TERMS)
        self.assertEqual(locked, ['Schwärzung'])
        self.assertEqual(len(applied), 2)

    def test_an_exact_case_entry_wins(self):
        terms = {'Title': 'Position', 'title': 'Titel'}
        self.assertEqual(prepare('Title and title', terms)[0], 'Position and Titel')


class EngineJobTests(unittest.TestCase):
    def test_the_job_terminology_reaches_the_pipeline_options(self):
        from backend import engine_job
        seen = {}
        def workflow(data, filename, operation, **options):
            seen.update(options); yield {'status': 'generated', 'stage': 'complete'}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'source.pdf'); source.write_bytes(b'%PDF-1.7')
            request = Path(folder, 'request.json')
            request.write_text(json.dumps({'source': str(source), 'filename': 'a.pdf', 'profile': {},
                'options': {'operation': 'translation', 'source_language': 'en', 'target_language': 'de',
                            'protected_values': [], 'document_context': '', 'pii_categories': [],
                            'terminology': {'redaction': 'Schwärzung'}}}))
            with patch('transformation.workflows.run_workflow', side_effect=workflow), \
                    patch('sys.argv', ['engine_job', str(request), str(Path(folder, 'e.jsonl')), str(Path(folder, 't.json'))]):
                engine_job.main()
        self.assertEqual(seen['terminology'], {'redaction': 'Schwärzung'})


class GlossaryPriorityTests(unittest.TestCase):
    def test_automatic_glossary_entries_for_customer_terms_are_dropped(self):
        from transformation import pipeline
        with tempfile.TemporaryDirectory() as run, \
                patch('transformation.llm.glossary.build', return_value={'Redaction': 'Zensur', 'List Price': 'Listenpreis',
                                                                     'Redaction Pipeline': 'Zensurpipeline',
                                                                     'Predaction': 'X'}):
            glossary = pipeline._document_glossary({'pii_endpoint': 'http://127.0.0.1:8000/v1', 'pii_model': 'q',
                                                    'target': 'de', 'terminology': {'redaction': 'Schwärzung'}},
                                                   {'units': []}, Path(run), {})
        # Entries containing the customer term as a word go too; a longer word that merely contains it stays.
        self.assertEqual(glossary, {'List Price': 'Listenpreis', 'Predaction': 'X'})


class RelaxedTermTests(unittest.TestCase):
    TEXT = 'Employee ID EMP-1 joined.'

    def run_with(self, answers):
        from transformation.llm import ollama_native
        calls = []
        def fake(endpoint, model, text, language, protected, context, *rest):
            calls.append({'text': text, 'protected': protected, 'context': context})
            return answers[len(calls) - 1]
        with patch.object(ollama_native, '_translate_paragraph', side_effect=fake),                 patch.object(ollama_native, 'record_progress'):
            result, stats = ollama_native.translate_paragraph('http://x', 'translategemma:12b', self.TEXT, 'German',
                                                              ('EMP-1',), terminology={'employee': 'Mitarbeitende'})
        return result, stats, calls

    def test_a_term_the_model_cannot_keep_does_not_leave_the_paragraph_in_english(self):
        kept = ('Mitarbeitende ID EMP-1 joined.', {'kept_source_requires_review': True})
        result, stats, calls = self.run_with([kept, ('Mitarbeiter-ID EMP-1 ist beigetreten.', {})])
        self.assertEqual(result, 'Mitarbeiter-ID EMP-1 ist beigetreten.')
        self.assertTrue(stats['terminology_relaxed'])
        self.assertEqual(stats['terminology_missed'], ['Mitarbeitende'])
        # The retry sends the original text, keeps protected values locked, and asks for the term.
        self.assertEqual(calls[1]['text'], self.TEXT)
        self.assertEqual(tuple(calls[1]['protected']), ('EMP-1',))
        self.assertIn('"Employee": "Mitarbeitende"', calls[1]['context'])

    def test_a_term_kept_on_the_first_try_needs_no_retry(self):
        result, stats, calls = self.run_with([('Mitarbeitende ID EMP-1 ist beigetreten.', {})])
        self.assertEqual(len(calls), 1)
        self.assertNotIn('terminology_relaxed', stats)

    def test_when_both_fail_the_source_is_kept_as_written(self):
        kept = ('Mitarbeitende ID EMP-1 joined.', {'kept_source_requires_review': True})
        result, stats, calls = self.run_with([kept, (self.TEXT, {'kept_source_requires_review': True})])
        self.assertEqual(result, self.TEXT)
        self.assertTrue(stats['kept_source_requires_review'])


if __name__ == '__main__':
    unittest.main()
