import unittest
from unittest.mock import patch

from transformation.llm.toc import rebuild, split, width


class ContentsEntryTests(unittest.TestCase):
    def test_contents_lines_are_recognised(self):
        self.assertEqual(split("Check the sender's fax machine" + '.' * 40 + '183'),
                         ("Check the sender's fax machine", '.' * 40, '183'))
        self.assertEqual(split('Solve fax problems ' + '.' * 30)[2], '')
        self.assertEqual(split('Appendix C Regulatory information' + '.' * 20 + ' 215')[2], '215')
        self.assertEqual(split('Introduction ' + '. ' * 10 + 'xii')[2], 'xii')
        self.assertEqual(split('Network setup' + '.' * 12 + '{v3}')[2], '{v3}')
        for text in ('The total was 5.00 dollars.', 'Wait...', 'Version 1.2.3.4.5.6', '.......' + '12'):
            self.assertIsNone(split(text), text)

    def test_the_rebuilt_line_keeps_its_length_leader_and_number(self):
        title, leader, tail = split('Solve wired network problems' + '.' * 60 + '183')
        line = rebuild(title, '유선 네트워크 문제 해결.', leader, tail)
        self.assertTrue(line.startswith('유선 네트워크 문제 해결 ····'))
        self.assertTrue(line.endswith(' 183'))
        self.assertLessEqual(abs(width(line) - width(title + leader + tail)), 4)
        # BabelDOC collapses runs of 20+ ". " into one full stop; the rebuilt leader must survive it.
        import re
        self.assertEqual(re.sub(r'[. 。…，]{20,}', '.', line), line)

    def test_only_the_title_is_sent_for_translation(self):
        from transformation.llm import ollama_native
        sent = []

        def translate(endpoint, model, text, *args, **kwargs):
            sent.append(text)
            return '팩스 문제 해결', {'prompt_eval_count': 1, 'eval_count': 1}
        with patch.object(ollama_native, '_translate_paragraph', side_effect=translate):
            result, stats = ollama_native.translate_paragraph('unused', 'translategemma:12b',
                                                              'Solve fax problems' + '.' * 50 + '183', 'Korean')
        self.assertEqual(sent, ['Solve fax problems'])
        self.assertTrue(result.startswith('팩스 문제 해결 ···') and result.endswith(' 183'))
        self.assertTrue(stats['contents_entry'])


if __name__ == '__main__':
    unittest.main()
