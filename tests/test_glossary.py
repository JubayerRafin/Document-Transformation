"""The per-document translation glossary. No models needed."""
import json
import unittest
from unittest.mock import patch

from transformation.llm import glossary

IR = {'units': [{'regions': [
    {'text': 'Quarterly Review\nThis review records the redaction pipeline results. The redaction pipeline '
             'keeps the text layer.\nTitle: Software Engineer II'},
    {'cells': [['Area', 'Rating'], ['Delivery', 'Exceeds'], ['Teamwork', 'Meets']]},
    {'text': 'Contact: kim@example.com\nRef HR-2024-00417'}]}]}


class CandidateTests(unittest.TestCase):
    def test_labels_cells_and_recurring_terms_are_collected_with_their_role(self):
        found = glossary.candidates(IR)
        self.assertIn('Exceeds', found); self.assertIn('column "Rating"', found['Exceeds'])
        self.assertEqual(found['Title'].split(' ')[:2], ['field', 'label'])
        self.assertIn('Contact', found)
        self.assertIn('Quarterly Review', found)
        self.assertIn('redaction pipeline', found)
        for value in ('kim@example.com', 'HR-2024-00417', 'Software Engineer II results'):
            self.assertNotIn(value, found)


class ApplyTests(unittest.TestCase):
    G = {'Meets': 'Conforme aux attentes', 'Title': 'Poste', 'redaction': 'masquage', 'redaction pipeline': 'chaîne de masquage',
         'HP': 'HP', 'ID': 'ID', 'LaserJet Pro X1': 'LaserJet Pro X1', 'Kim Min-jun': 'Kim Min-jun', 'W1.2M': 'W1.2M'}

    def test_a_whole_label_paragraph_gets_its_glossary_translation_and_keeps_its_colon(self):
        self.assertEqual(glossary.label('Meets', self.G), 'Conforme aux attentes')
        self.assertEqual(glossary.label(' Title: ', self.G), 'Poste:')
        self.assertIsNone(glossary.label('Title: Engineer', self.G))
        self.assertIsNone(glossary.label('It meets the bar', self.G))

    def test_terms_in_a_sentence_are_listed_once_longest_match_first(self):
        found = glossary.required_terms('The Redaction pipeline and one more redaction pass.', self.G)
        self.assertEqual(found, [('redaction pipeline', 'chaîne de masquage')])
        self.assertEqual(glossary.required_terms('No terms here.', self.G), [])

    def test_unchanged_entries_are_the_ones_to_lock(self):
        self.assertEqual(glossary.kept_unchanged(self.G), {'LaserJet Pro X1': 'LaserJet Pro X1', 'Kim Min-jun': 'Kim Min-jun', 'W1.2M': 'W1.2M'})


class BuildTests(unittest.TestCase):
    def test_only_requested_short_answers_are_kept(self):
        answer = {'terms': [{'source': 'Exceeds', 'translation': 'Dépasse les attentes'},
                            {'source': 'Meets', 'translation': 'Conforme aux attentes'},
                            {'source': 'invented', 'translation': 'inventé'},
                            {'source': 'Overall', 'translation': 'Общий'},
                            {'source': 'Contact', 'translation': 'Coordonnées ' * 20}]}
        seen = {}
        def fake_chat(endpoint, model, messages, **kwargs):
            seen['request'] = json.loads(messages[1]['content']); seen['schema'] = kwargs.get('response_format')
            return json.dumps(answer), {}
        with patch('transformation.llm.ollama_native.chat', side_effect=fake_chat):
            result = glossary.build(IR, 'French', 'http://127.0.0.1:8000/v1', 'qwen')
        self.assertEqual(result, {'Exceeds': 'Dépasse les attentes', 'Meets': 'Conforme aux attentes'})
        self.assertIn('Quarterly Review', seen['request']['document_opening'])
        self.assertEqual(seen['schema'], glossary.SCHEMA)


class PendingTests(unittest.TestCase):
    def test_a_glossary_being_written_is_waited_for_once_and_a_missing_one_is_empty(self):
        import os, tempfile, threading, time
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'g.json')
            load = glossary.pending(path, wait=5)
            def write():  # written whole, then renamed into place, like the pipeline does
                with open(path + '.tmp', 'w', encoding='utf-8') as f: f.write('{"Meets": "Erfüllt"}')
                os.replace(path + '.tmp', path)
            threading.Timer(0.3, write).start()
            started = time.monotonic()
            self.assertEqual(glossary.resolve(load), {'Meets': 'Erfüllt'})
            self.assertGreater(time.monotonic() - started, 0.2)
            self.assertIs(load(), load())
            self.assertEqual(glossary.pending(os.path.join(folder, 'none.json'), wait=0.2)(), {})
        self.assertEqual(glossary.resolve({'a': 'b'}), {'a': 'b'}); self.assertEqual(glossary.resolve(None), {})


if __name__ == '__main__':
    unittest.main()
