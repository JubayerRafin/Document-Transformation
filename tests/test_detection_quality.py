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


class SentenceFragmentLabelTests(unittest.TestCase):
    def test_a_sentence_fragment_before_a_colon_is_not_a_field_label(self):
        from transformation.validation.pii_document import field_pairs, label_like
        self.assertFalse(label_like('this)')); self.assertFalse(label_like('(Employee ID'))
        self.assertTrue(label_like('Bank account (IBAN)')); self.assertTrue(label_like('Employee ID'))
        units = [{'id': 'u', 'text': 'the engine SHOULD catch\nthis): employee Kim Min-jun, ID EMP-2024-8831, email k@x.example'}]
        self.assertEqual(field_pairs(units, [], []), [])
        units = [{'id': 'v', 'text': 'Bank account (IBAN): DE89 3704 0044 0532 0130 00'}]
        self.assertEqual([(p['label'], p['value']) for p in field_pairs(units, [], [])], [('Bank account (IBAN)', 'DE89 3704 0044 0532 0130 00')])


class FusedWordTests(unittest.TestCase):
    def test_a_value_fused_with_its_label_gets_a_box_over_the_value_only(self):
        import re
        import pymupdf
        from transformation.validation.value_redaction import _trim_to_span
        text = 'Device:WP-0042 Owner'
        token = next(re.finditer(r'\S+', text)); box = pymupdf.Rect(0, 0, 140, 10)   # 14 characters, 10 pt each
        a = text.index('WP-0042'); trimmed = _trim_to_span(box, token, a, a + len('WP-0042'))
        self.assertAlmostEqual(trimmed.x0, (7 - .75) * 10); self.assertEqual(trimmed.x1, 140)   # covers the value, not 'Device'
        self.assertEqual(_trim_to_span(box, token, 0, 14), box)                                  # whole word stays whole
        prefix = _trim_to_span(box, token, 0, 7)                                                  # value at the start of a word
        self.assertEqual(prefix.x0, 0); self.assertAlmostEqual(prefix.x1, (7 + .75) * 10)

    def test_redacting_a_fused_label_and_value_keeps_the_label_readable(self):
        import pymupdf
        from transformation.validation.value_redaction import redact_pdf
        with pymupdf.open() as doc:
            p = doc.new_page(width=300, height=80); p.insert_text((20, 40), 'Device:WP-0042', fontsize=14, fontname='cour')
            png = p.get_pixmap(dpi=200).tobytes('png')
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=80); page.insert_image(page.rect, stream=png); data = doc.tobytes()
        region = {'id': 'u1:r1', 'kind': 'text', 'text': 'Device:WP-0042', 'bbox': [19, 28, 140, 44]}
        ir = {'units': [{'id': 'u1', 'number': 1, 'regions': [region], 'ocr_folder': 'x'}]}
        output, audit = redact_pdf(data, ir, [{'region_id': 'u1:r1', 'value': 'WP-0042', 'category': 'Identifiers'}])
        self.assertEqual(audit[0]['localization'], 'pixel_word_gaps')
        with pymupdf.open(stream=output, filetype='pdf') as doc:
            label = doc[0].get_pixmap(clip=pymupdf.Rect(20, 30, 60, 42))     # 'Devic' area keeps its ink and paper
            value = doc[0].get_pixmap(clip=pymupdf.Rect(90, 32, 135, 42))    # value area is black
        self.assertGreater(max(label.samples), 200); self.assertLessEqual(max(value.samples), 12)


class PaddingTests(unittest.TestCase):
    def test_padding_stops_at_neighbouring_lines_but_never_shrinks_the_value(self):
        import pymupdf
        from transformation.validation.value_redaction import _clip_padding
        base = pymupdf.Rect(10, 50, 100, 60); padded = base + (-.5, -2, .5, 2)
        above, below, beside = [10, 39, 80, 49.2], [10, 60.8, 80, 70], [200, 48, 260, 62]
        clipped = _clip_padding(base, padded, [above, below, beside])
        self.assertEqual((clipped.y0, clipped.y1), (49.2, 60.8))          # padding limited by the lines above/below
        self.assertEqual(_clip_padding(base, padded, [beside]), padded)   # a line beside it does not matter
        tight = _clip_padding(base, padded, [[10, 40, 80, 52]])           # a neighbour overlapping the value's own box
        self.assertLessEqual(tight.y0, base.y0)                           # the value's box is never cut


class NameAndPunctuationTests(unittest.TestCase):
    def test_a_name_with_digits_is_dropped_and_trailing_punctuation_trimmed(self):
        data = pdf([(20, 40, 'Emergency contact: Lee Soo-jin, 010-9876-5432.'), (20, 80, 'Key on file: sk_live_4f9a-REDACT_ME.')])
        region = lambda text: ([{'value': 'Lee Soo-jin, 010-9876-5432.', 'category': 'Names'}] if 'Lee' in text else []) + \
                              ([{'value': 'sk_live_4f9a-REDACT_ME.', 'category': 'Identifiers'}] if 'sk_live' in text else [])
        with patch('transformation.llm.ollama_native.chat', side_effect=stub(region=region, names=['Lee Soo-jin'])):
            findings, _, _ = detect_document(data, {'units': [{'number': 1, 'regions': []}]}, ['Names', 'Phone numbers', 'Identifiers'],
                                             'http://127.0.0.1:1/v1', 'm', True, workers=2)
        values = {(f['value'], f['category']) for f in findings}
        self.assertIn(('Lee Soo-jin', 'Names'), values); self.assertIn(('010-9876-5432', 'Phone numbers'), values)
        self.assertNotIn(('Lee Soo-jin, 010-9876-5432.', 'Names'), values)
        self.assertIn(('sk_live_4f9a-REDACT_ME', 'Identifiers'), values)
        self.assertFalse(any(v.endswith('.') for v, _ in values))


class FinalCheckShapeTests(unittest.TestCase):
    def test_labels_and_dates_are_not_findings_at_the_final_check(self):
        from transformation.validation.privacy import plausible
        self.assertFalse(plausible('API Key.', 'Identifiers'))      # the label beside a removed key
        self.assertFalse(plausible('Owner', 'Identifiers'))
        self.assertFalse(plausible('2024-03-20', 'Identifiers'))
        self.assertFalse(plausible('Kim 2', 'Names'))
        self.assertTrue(plausible('sk_live_4f9a-REDACT_ME', 'Identifiers'))
        self.assertTrue(plausible('172.16.5.114', 'Identifiers'))
        self.assertTrue(plausible('Kim Min-jun', 'Names'))
        self.assertTrue(plausible('1984-03-12', 'Dates of birth'))


class BracketedAreaCodeTests(unittest.TestCase):
    def test_bracketed_area_code_is_a_phone_pattern_without_the_model(self):
        found = lambda t: [f['value'] for f in detect([{'id': 'a', 'text': t}], ['Phone numbers'], 'e', 'm', assist=False)]
        self.assertEqual(found('reach me on (911) 555-5880. Thanks'), ['(911) 555-5880'])
        self.assertEqual(found('(+1 312 555 0147)'), ['+1 312 555 0147'])
        self.assertEqual(found('Invoice (2024) total 1,200'), [])
        self.assertEqual(found('ref (A1) 555-5880'), [])


class SoftWrapTests(unittest.TestCase):
    def test_wraps_inside_a_sentence_become_spaces_and_fields_keep_their_breaks(self):
        from transformation.validation.privacy import soft_wraps_as_spaces as flat
        self.assertEqual(flat('Witkoff insisted progress had \nbeen made'), 'Witkoff insisted progress had  been made')
        for kept in ('Full name: Ann Moor\nEmail: a@b.example', 'Jonathan \nPowell said', '정시우\n담당자'):
            self.assertEqual(flat(kept), kept)

    def test_model_sees_flat_text_and_findings_map_back_to_the_wrapped_source(self):
        text = 'The case was argued by Ms. Ann \nmoor-Lindt, who said the review \nwas done.'
        seen = []
        def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
            seen.append(json.loads(messages[1]['content'])['text'])
            return json.dumps({'findings': [{'value': 'Ann moor-Lindt', 'category': 'Names'}]}), {}
        with patch('transformation.llm.ollama_native.chat', side_effect=chat):
            found = _semantic_proposals({'id': 'a', 'text': text}, text, ['Names'], 'e', 'm')
        self.assertNotIn('\n', seen[0]); self.assertEqual(len(seen[0]), len(text))
        self.assertEqual(found, [('Ann \nmoor-Lindt', 'Names', 'model_proposal')])


class EmailWithNeighbourLineTests(unittest.TestCase):
    def validated(self, value, text, category='Email addresses'):
        from transformation.validation.privacy import _validated_proposals
        answer = json.dumps({'findings': [{'value': value, 'category': category}]})
        return [v for v, _, _ in _validated_proposals(answer, text, [category])]

    def test_joined_address_answered_as_another_category_is_dropped(self):
        # Without its space the joined text looks like one address and would be relabelled as one.
        row = '17 Warehouse kim.lee@harbor-post.example B'
        self.assertEqual(self.validated('Warehouse kim.lee@harbor-post.example', row, 'Identifiers'), [])
        self.assertEqual(self.validated('ID 17', 'ID 17 Warehouse', 'Identifiers'), ['ID 17'])

    def test_address_joined_with_a_neighbouring_cell_is_dropped(self):
        text = 'Warehouse\nkim.lee@harbor-post.example'
        self.assertEqual(self.validated(text, text), [])
        self.assertEqual(self.validated('kim.lee@harbor-post.example\nRoom 4', 'kim.lee@harbor-post.example\nRoom 4'), [])
        row = '17 Warehouse kim.lee@harbor-post.example B'  # a table row joined with spaces
        self.assertEqual(self.validated('Warehouse kim.lee@harbor-post.example', row), [])

    def test_the_address_itself_is_still_found_by_the_pattern(self):
        text = 'Warehouse\nkim.lee@harbor-post.example'
        found = {f['value'] for f in detect([{'id': 'r', 'text': text}], ['Email addresses'], 'x', 'm', assist=False)}
        self.assertEqual(found, {'kim.lee@harbor-post.example'})

    def test_a_wrapped_address_is_kept(self):
        for text in ('kim.lee@harbor-\npost.example', 'kim.lee@harbor-post.\nexample', 'kim.lee@harbor-post.example'):
            self.assertEqual(self.validated(text, text), [text])
