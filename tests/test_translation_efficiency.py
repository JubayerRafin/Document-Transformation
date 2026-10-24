import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from transformation.llm.ollama_native import translate_paragraph, needs_no_translation, record_progress
from transformation.progress import read_progress


class EfficiencyTests(unittest.TestCase):
    def test_prompt_policy_changes_invalidate_translator_cache_identity(self):
        from transformation.llm.ollama_native import translation_policy_digest
        original=translation_policy_digest()
        self.assertEqual(original,translation_policy_digest())
        with patch('pathlib.Path.read_bytes',return_value=b'changed policy'):
            self.assertNotEqual(original,translation_policy_digest())

    def test_exact_literals_bypass_model_without_changing_spacing(self):
        with patch('transformation.llm.ollama_native.chat') as chat:
            for source in [' Kim Min-jun ', 'privacy@example.com', '2024-03-18', '04512', '68,000,000', 'KRW 100', 'USD12.50']:
                result, stats = translate_paragraph('http://localhost:11434', 'test', source,
                                                   'Korean', ['Kim Min-jun'])
                self.assertEqual(result, source)
                self.assertEqual(stats['prompt_eval_count'], 0)
                self.assertEqual(stats['eval_count'], 0)
            chat.assert_not_called()

    def test_prose_and_partial_matches_are_not_skipped(self):
        for source in ['Title', 'Field', 'Exceeds', 'Call 010-2345-6789', 'Kim Min-jun joined', 'A-', 'Price: KRW 100']:
            self.assertFalse(needs_no_translation(source, ['Kim Min-jun']))
        with patch('transformation.llm.ollama_native.chat', return_value=('translated', {})) as chat:
            translate_paragraph('http://localhost:11434', 'test', 'Title', 'Korean')
            chat.assert_called_once()

    def test_progress_survives_incomplete_line_and_terminal_state(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict('os.environ', {'TRANSFORMX_RUN_DIR': folder}):
                record_progress('request_started')
                record_progress('request_completed', prompt_tokens=20, output_tokens=5)
                record_progress('skipped', reason='protected_or_numeric')
                record_progress('request_started')
            path = Path(folder) / 'translation-progress.jsonl'
            with path.open('a', encoding='utf-8') as file:
                file.write('{"event":')
            report = read_progress(folder)
            self.assertEqual(report['requests_completed'], 1)
            self.assertEqual(report['fragments_skipped'], 1)
            self.assertEqual(report['prompt_tokens'], 20)
            self.assertEqual(report['output_tokens'], 5)
            self.assertIsNotNone(report['active_seconds'])
            self.assertIsNone(read_progress(folder, processing=False)['active_seconds'])


if __name__ == '__main__':
    unittest.main()
