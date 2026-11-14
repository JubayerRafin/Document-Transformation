"""Format checks, opt-in dates of birth and context-echo handling. Synthetic data only."""
import json
import unittest
from unittest.mock import patch

from tests.test_pii_document import pdf, stub
from transformation.validation.pii_document import detect_document
from transformation.validation.privacy import _semantic_proposals, detect, format_values, iban_valid, luhn_valid, offered_categories


class FormatCheckTests(unittest.TestCase):
    def test_checksums_decide(self):
        self.assertTrue(iban_valid('DE89 3704 0044 0532 0130 00')); self.assertFalse(iban_valid('DE88 3704 0044 0532 0130 00'))
        self.assertTrue(luhn_valid('4111 1111 1111 1111')); self.assertFalse(luhn_valid('4111 1111 1111 1112'))
        self.assertFalse(luhn_valid('0000 0000 0000 0000'))

    def test_found_without_the_model_and_only_for_selected_categories(self):
        text = 'Pay to DE89 3704 0044 0532 0130 00 REF2 or card 4111-1111-1111-1111; server 10.42.7.19, build 1.2.300.4'
        found = {(v, c) for v, c, _ in format_values(text, ['Bank accounts', 'Identifiers'])}
        self.assertEqual(found, {('DE89 3704 0044 0532 0130 00', 'Bank accounts'), ('4111-1111-1111-1111', 'Bank accounts'),
                                 ('10.42.7.19', 'Identifiers')})
        self.assertEqual(format_values(text, ['Names']), [])
        findings = detect([{'id': 'r', 'text': text}], ['Bank accounts'], 'http://127.0.0.1:1/v1', 'm', assist=False)
        self.assertEqual({f['value'] for f in findings}, {'DE89 3704 0044 0532 0130 00', '4111-1111-1111-1111'})


class DateOfBirthTests(unittest.TestCase):
    def run_detect(self, data, categories, chat):
        with patch('transformation.llm.ollama_native.chat', side_effect=chat) as mock:
            result = detect_document(data, {'units': [{'number': 1, 'regions': []}]}, categories, 'http://127.0.0.1:1/v1', 'm', True, workers=2)
        return result, mock

    def test_offered_to_the_model_only_when_selected(self):
        self.assertNotIn('Dates of birth', offered_categories(['Names']))
        self.assertIn('Dates of birth', offered_categories(['Names', 'Dates of birth']))
        data = pdf([(20, 40, 'Name: Ann Moor'), (20, 60, 'Date of birth: 1984-03-12'), (20, 80, 'Start date: 2025-01-02')])
        (findings, _, _), mock = self.run_detect(data, ['Names'], stub(labels={'Name': 'Names'}))
        prompts = ' '.join(json.dumps(c.args[2]) for c in mock.call_args_list)
        self.assertNotIn('Dates of birth', prompts)
        (findings, _, _), _ = self.run_detect(data, ['Names', 'Dates of birth'],
                                              stub(labels={'Name': 'Names', 'Date of birth': 'Dates of birth', 'Start date': 'none'}))
        values = {(f['value'], f['category']) for f in findings}
        self.assertIn(('1984-03-12', 'Dates of birth'), values); self.assertNotIn('2025-01-02', {v for v, _ in values})


class ContextEchoTests(unittest.TestCase):
    def test_a_value_copied_from_the_context_is_dropped_without_a_retry(self):
        calls = []
        def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
            calls.append(purpose)
            return json.dumps({'findings': [{'value': 'K-200', 'category': 'Identifiers'}, {'value': 'Ann Moor', 'category': 'Names'}]}), {}
        region = {'id': 'c', 'text': 'Ann Moor', 'metadata': {'column_header': 'Holder', 'row_values': ['K-200', 'Ann Moor']}}
        with patch('transformation.llm.ollama_native.chat', side_effect=chat):
            found = _semantic_proposals(region, region['text'], ['Names', 'Identifiers'], 'e', 'm')
        self.assertEqual(found, [('Ann Moor', 'Names', 'model_proposal')])
        self.assertEqual(calls, ['pii_unit'])

    def test_a_value_in_neither_text_nor_context_still_forces_the_retry(self):
        calls = []
        def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
            calls.append(purpose)
            value = 'Ann Moore' if purpose == 'pii_unit' else 'Ann Moor'
            return json.dumps({'findings': [{'value': value, 'category': 'Names'}]}), {}
        region = {'id': 'c', 'text': 'Ann Moor', 'metadata': {'column_header': 'Holder', 'row_values': ['K-200', 'Ann Moor']}}
        with patch('transformation.llm.ollama_native.chat', side_effect=chat):
            found = _semantic_proposals(region, region['text'], ['Names'], 'e', 'm')
        self.assertEqual(found, [('Ann Moor', 'Names', 'model_proposal')]); self.assertEqual(calls, ['pii_unit', 'pii_unit_retry'])


if __name__ == '__main__':
    unittest.main()


class NoSpaceScriptTests(unittest.TestCase):
    def test_korean_particles_and_japanese_suffixes_do_not_hide_a_name(self):
        from transformation.validation.value_redaction import literal_spans
        self.assertEqual(literal_spans('변경 사항은 정시우에게 서면으로', '정시우'), [(7, 10)])
        self.assertEqual(literal_spans('担当は田中さんです', '田中'), [(3, 5)])
        # Latin identifiers still never match inside a longer token.
        self.assertEqual(literal_spans('K-1000 and K-100', 'K-100'), [(11, 16)])
        self.assertEqual(literal_spans('AnnMoor', 'Ann'), [])

    def test_a_korean_name_followed_by_a_particle_is_removed_from_the_pdf(self):
        import pymupdf
        from tests.test_masking_style import findings_for
        from transformation.validation.value_redaction import redact_pdf
        with pymupdf.open() as doc:
            page = doc.new_page(); page.insert_text((40, 60), '담당: 정시우', fontname='korea', fontsize=12)
            page.insert_text((40, 100), '변경 사항은 정시우에게 서면으로 확인받으십시오.', fontname='korea', fontsize=12)
            data = doc.tobytes()
        ir, findings = findings_for(data, [('정시우', 'Names')])
        output, _ = redact_pdf(data, ir, findings[:1])
        with pymupdf.open(stream=output, filetype='pdf') as doc:
            text = doc[0].get_text()
        self.assertNotIn('정시우', text); self.assertIn('에게', text)


class MinorFixTests(unittest.TestCase):
    def test_phone_pattern_skips_dates_and_ibans(self):
        t = 'Account: GB67 NWBK 0591 0081 0047 71  Period: 2025-09-14 - 2025-10-01  Tel +44 20 7946 0321'
        found = {f['value'] for f in detect([{'id': 'r', 'text': t}], ['Phone numbers'], 'x', 'm', assist=False)}
        self.assertEqual(found, {'+44 20 7946 0321'})

    def test_a_date_is_only_personal_as_a_date_of_birth(self):
        from transformation.validation.privacy import _validated_proposals
        answer = json.dumps({'findings': [{'value': '2025-04-03', 'category': 'Identifiers'}, {'value': 'K-77', 'category': 'Identifiers'}]})
        self.assertEqual([v for v, _, _ in _validated_proposals(answer, 'Due 2025-04-03 for K-77', ['Identifiers'])], ['K-77'])
        answer = json.dumps({'findings': [{'value': '1984-03-12', 'category': 'Dates of birth'}]})
        self.assertEqual(len(_validated_proposals(answer, 'Born 1984-03-12', ['Dates of birth'])), 1)

    def test_a_user_handle_is_not_a_written_name(self):
        data = pdf([(20, 40, 'Mail minjun.kim@example-corp.com'), (20, 80, 'Login: lee.sj on host 7')])
        region = lambda text: [{'value': v, 'category': 'Names'} for v in ('minjun.kim', 'lee.sj') if v in text and '@' not in text.split(v)[1][:1]]
        with patch('transformation.llm.ollama_native.chat', side_effect=stub(region=region)):
            findings, _, _ = detect_document(data, {'units': [{'number': 1, 'regions': []}]}, ['Names', 'Identifiers', 'Email addresses'],
                                             'http://127.0.0.1:1/v1', 'm', True, workers=2)
        got = {(f['value'], f['category']) for f in findings}
        self.assertNotIn(('minjun.kim', 'Names'), got); self.assertNotIn(('minjun.kim', 'Identifiers'), got)  # only inside an email
        self.assertIn(('lee.sj', 'Identifiers'), got)                                                        # a user name on its own

    def test_postal_block_lines_join_the_address_and_stop_at_the_name(self):
        from transformation.validation.pii_document import address_block_lines
        lines = {'n': (1, [50, 120, 126, 127], 'Mei Ramanathan'), 's': (1, [50, 136, 127, 145], '445 Juniper Lane'),
                 'u': (1, [50, 152, 79, 161], 'Apt 13'), 'c': (1, [50, 167, 142, 177], 'Springfield, IL 62704'),
                 'r': (1, [50, 200, 300, 210], 'Our reference: K-07888'), 'x': (1, [300, 152, 400, 161], 'Gate 3')}
        self.assertEqual(address_block_lines({'s'}, lines), {'u': 'Apt 13', 'c': 'Springfield, IL 62704'})


class DateColumnTests(unittest.TestCase):
    def test_a_due_date_column_is_never_masked_as_an_identifier(self):
        rows = [['Task', 'Owner', 'Due'], ['Book venue', 'Ann Moor', '2025-05-14']]
        ir = {'units': [{'number': 1, 'regions': [{'id': 't', 'kind': 'table', 'text': '', 'bbox': [0, 0, 400, 60], 'cells': rows}]}]}
        data = pdf([(20, 40 + 20 * i, '   '.join(r)) for i, r in enumerate(rows)])
        with patch('transformation.llm.ollama_native.chat', side_effect=stub(labels={'Task': 'none', 'Owner': 'Names', 'Due': 'Identifiers'})):
            findings, _, _ = detect_document(data, ir, ['Names', 'Identifiers'], 'http://127.0.0.1:1/v1', 'm', True, workers=2)
        values = {f['value'] for f in findings}
        self.assertIn('Ann Moor', values); self.assertNotIn('2025-05-14', values)


class LabelledFormatTests(unittest.TestCase):
    def test_a_labelled_email_or_phone_must_have_that_format(self):
        from transformation.validation.pii_document import format_ok
        self.assertTrue(format_ok('priya.achebe@kestrel.example', 'Email addresses'))
        self.assertFalse(format_ok('jamal lindqvist@kestrel.example\n_', 'Email addresses'))
        self.assertTrue(all(format_ok(v, 'Phone numbers') for v in ('(365) 555-1153', '+44 20 7946 0321', '010-5550-9235')))
        self.assertFalse(format_ok('Nadia Kowalski, +44 20 7946 1226', 'Phone numbers'))
        self.assertTrue(format_ok('Ann Moor', 'Names'))
