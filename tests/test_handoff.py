import unittest
from pathlib import Path

from tools.handoff.initialize_pii import relocate


class HandoffTests(unittest.TestCase):
    def test_paths_relocated_without_rewriting_document_prose(self):
        old = r'C:\old\project'
        source = r'C:\outside\Sample.pdf'
        result = relocate({'file': old + r'\runs\x.json', 'source': source,
                           'text': 'Mention ' + old + ' as document text'}, old, Path('new'), {source: 'samples/Sample.pdf'})
        self.assertEqual(result['file'], str(Path('new/runs/x.json')))
        self.assertEqual(result['source'], str(Path('new/samples/Sample.pdf')))
        self.assertEqual(result['text'], 'Mention ' + old + ' as document text')
