"""Document-level PII detection: paragraphs, field labels, name pass, category validation.

Synthetic only; the model is replaced by a scripted stub keyed on the request type.
"""
import json
import unittest
from unittest.mock import patch

import pymupdf

from transformation.validation.pii_document import (
    detect_document, field_pairs, label_like, normalize_category,
)


def pdf(lines, size=10):
    with pymupdf.open() as doc:
        page = doc.new_page(width=420, height=300)
        for x, y, text in lines: page.insert_text((x, y), text, fontsize=size)
        return doc.tobytes()


def stub(labels=None, names=(), region=None):
    """Chat stub: label classification, document name pass, or per-region findings."""
    labels = labels or {}; region = region or (lambda text: [])
    def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
        system, user = messages[0]['content'], json.loads(messages[1]['content'])
        if 'classify document field labels' in system:
            return json.dumps({'labels': [{'label': i['label'], 'category': labels.get(i['label'], 'none')} for i in user['labels']]}), {}
        if 'List every person name' in system:
            return json.dumps({'names': [n for n in names if n.replace(' ', '') in user['text'].replace(' ', '').replace('\n', '')]}), {}
        if 'items' in user:  # batched units
            return json.dumps({'findings': [dict(f, item=i['item']) for i in user['items'] for f in region(i['text'])]}), {}
        return json.dumps({'findings': region(user['text'])}), {}
    return chat


class StructureTests(unittest.TestCase):
    def test_label_shape_is_structural_not_a_word_list(self):
        for text in ('Customer No.:', 'Kontonummer', '연락처', 'Policy holder'):
            self.assertTrue(label_like(text), text)
        for text in ('ID 4471', 'This sentence ends here.', 'x' * 60):
            self.assertFalse(label_like(text), text)

    def test_pairs_from_lines_segments_forms_and_tables(self):
        units = [{'id': 'b1', 'text': 'Ref: ZX-1 | Owner: Ann Moor\nfree text without a label'}]
        blocks = [{'id': 'l', 'text': 'Holder:', 'bbox': [10, 10, 50, 20], 'metadata': {'pii_native_page': 1}},
                  {'id': 'v', 'text': 'Ola Berg', 'bbox': [80, 11, 140, 21], 'metadata': {'pii_native_page': 1}},
                  {'id': 'far', 'text': 'Other', 'bbox': [80, 60, 140, 70], 'metadata': {'pii_native_page': 1}}]
        cells = [{'id': f't:cell-{r}-{c}', 'text': rows[c], 'metadata': {'table_parent': 't', 'row': r, 'column': c,
                  'column_header': ['Field', 'Value'][c], 'row_values': rows}}
                 for r, rows in enumerate([['Field', 'Value'], ['Badge number', 'B-77'], ['Team', 'Ops']]) for c in range(2)]
        pairs = {(p['label'], p['value']) for p in field_pairs(units, cells, blocks)}
        self.assertIn(('Ref', 'ZX-1'), pairs); self.assertIn(('Owner', 'Ann Moor'), pairs)
        self.assertIn(('Holder:', 'Ola Berg'), pairs)
        self.assertIn(('Badge number', 'B-77'), pairs); self.assertNotIn(('Value', 'B-77'), pairs)  # generic value-column header
        self.assertNotIn(('Holder:', 'Other'), pairs)

    def test_format_validators_override_model_categories(self):
        self.assertEqual(normalize_category('ann@example.test', 'Identifiers'), 'Email addresses')
        self.assertEqual(normalize_category('+44 20 7946 0958', 'Identifiers'), 'Phone numbers')
        self.assertEqual(normalize_category('10.0.0.12', 'Phone numbers'), 'Identifiers')
        self.assertEqual(normalize_category('AB-1234-5678', 'Identifiers'), 'Identifiers')


class DetectDocumentTests(unittest.TestCase):
    def run_detect(self, data, ir, categories, chat):
        with patch('transformation.llm.ollama_native.chat', side_effect=chat):
            return detect_document(data, ir, categories, 'http://127.0.0.1:1/v1', 'm', True, workers=2)

    def test_values_under_sensitive_labels_are_found_without_a_model_guess(self):
        data = pdf([(20, 40, 'Ticket ref: Q7-55120'), (20, 60, 'Status: Open')])
        findings, _, evidence = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Identifiers'],
                                                stub(labels={'Ticket ref': 'Identifiers'}))
        self.assertEqual([(f['value'], f['method']) for f in findings], [('Q7-55120', 'field_label')])
        self.assertEqual(evidence['field_labels']['Status'], 'none')

    def test_codes_in_a_non_personal_column_are_not_reported_as_identifiers(self):
        rows = [['Code', 'Holder'], ['K-100', 'Ann Moor'], ['K-200', 'Ola Berg']]
        ir = {'units': [{'number': 1, 'regions': [{'id': 't', 'kind': 'table', 'text': '', 'bbox': [0, 0, 400, 100], 'cells': rows}]}]}
        data = pdf([(20, 40, 'K-100   Ann Moor'), (20, 60, 'K-200   Ola Berg')])
        guess = lambda text: [{'value': v, 'category': 'Identifiers'} for v in ('K-100', 'K-200') if v in text]
        findings, _, _ = self.run_detect(data, ir, ['Identifiers', 'Names'],
                                         stub(labels={'Code': 'none', 'Holder': 'Names'}, region=guess))
        values = {(f['value'], f['category']) for f in findings}
        self.assertNotIn(('K-100', 'Identifiers'), values); self.assertNotIn(('K-200', 'Identifiers'), values)
        self.assertIn(('Ann Moor', 'Names'), values); self.assertIn(('Ola Berg', 'Names'), values)

    def test_name_wrapped_across_lines_is_found_as_one_value(self):
        data = pdf([(20, 40, 'The review was led by Ines'), (20, 52, 'Delacroix and approved.')])
        findings, sources, evidence = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Names'],
                                                      stub(names=['Ines Delacroix']))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['value'].split(), ['Ines', 'Delacroix'])
        self.assertIn('\n', findings[0]['value'])
        self.assertEqual(sources[0]['id'], findings[0]['region_id'])

    def test_model_name_with_space_maps_to_wrapped_source(self):
        data = pdf([(20, 40, 'Signed by Tove'), (20, 52, 'Lindgren on behalf of the board.')])
        region = lambda text: [{'value': 'Tove Lindgren', 'category': 'Names'}] if 'Tove' in text else []
        findings, _, _ = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Names'], stub(region=region))
        self.assertTrue(any(f['value'].split() == ['Tove', 'Lindgren'] and f['method'] == 'model_proposal' for f in findings))

    def test_wrapped_value_returned_without_its_line_break_is_accepted(self):
        from transformation.validation.privacy import detect
        text = 'Contact: lina.ek@north-\nwind.example today'
        reply = json.dumps({'findings': [{'value': 'lina.ek@north-wind.example', 'category': 'Identifiers'}]})
        with patch('transformation.llm.ollama_native.chat', return_value=(reply, {})):
            findings = detect([{'id': 'b', 'text': text}], ['Identifiers'], 'u', 'm')
        self.assertEqual(findings[0]['value'], 'lina.ek@north-\nwind.example')

    def test_paragraph_failing_validation_is_retried_line_by_line(self):
        from transformation.validation.privacy import detect
        text = 'Approved by Mira Holm\nfor the facility in Unit 4'
        def chat(endpoint, model, messages, max_output=0, response_format=None, purpose=None):
            unit = json.loads(messages[1]['content'])['text']
            if '\n' in unit:  # whole paragraph: an invented value, twice
                return json.dumps({'findings': [{'value': 'Mira Holmberg', 'category': 'Names'}]}), {}
            return json.dumps({'findings': [{'value': 'Mira Holm', 'category': 'Names'}] if 'Mira' in unit else []}), {}
        with patch('transformation.llm.ollama_native.chat', side_effect=chat):
            findings = detect([{'id': 'b', 'text': text}], ['Names'], 'u', 'm')
        self.assertEqual([(f['region_id'], f['value']) for f in findings], [('b', 'Mira Holm')])

    def test_name_pass_drops_fragments_of_longer_names_and_emails(self):
        data = pdf([(20, 40, 'Owner Rina Sato-Lind approved it.'), (20, 60, 'Mail rina.sato@corp.example or Sato desk.')])
        findings, _, _ = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Names'],
                                         stub(names=['Rina Sato-Lind', 'Rina', 'rina.sato', 'Sato']))
        values = sorted(f['value'] for f in findings)
        self.assertIn('Rina Sato-Lind', values)
        self.assertNotIn('rina.sato', values); self.assertNotIn('Rina', values)
        self.assertIn('Sato', values)  # stands alone in "Sato desk", so it is kept

    def test_values_known_from_the_parent_document_are_masked_in_a_figure(self):
        data = pdf([(20, 40, 'Badge PX-30117 authorized')])
        findings, _, _ = detect_document(data, {'units': [{'number': 1, 'regions': []}]}, ['Identifiers'], 'u', 'm', False,
                                         known_values=[{'value': 'PX-30117', 'category': 'Identifiers'}])
        self.assertEqual([(f['value'], f['method']) for f in findings], [('PX-30117', 'document_consistency')])

    def test_a_field_label_proposed_as_a_value_is_skipped(self):
        from transformation.validation.privacy import detect
        reply = json.dumps({'findings': [{'value': 'API key', 'category': 'Identifiers'},
                                         {'value': 'qk_77Ab90', 'category': 'Identifiers'}]})
        with patch('transformation.llm.ollama_native.chat', return_value=(reply, {})):
            findings = detect([{'id': 'r', 'text': 'API key: qk_77Ab90'}], ['Identifiers'], 'u', 'm')
        self.assertEqual({f['value'] for f in findings}, {'qk_77Ab90'})

    def test_row_labels_beat_a_generic_column_header_and_headers_are_never_values(self):
        rows = [['Field', 'Value'], ['Holder', 'Ann Moor'], ['Joined', '2023-05-01'], ['Team', 'Platform Ops']]
        ir = {'units': [{'number': 1, 'regions': [{'id': 't', 'kind': 'table', 'text': '', 'bbox': [0, 0, 400, 100], 'cells': rows}]}]}
        data = pdf([(20, 40, 'Holder  Ann Moor'), (20, 60, 'Joined  2023-05-01'), (20, 80, 'Team  Platform Ops')])
        region = lambda text: [{'value': 'Value', 'category': 'Names'}] if 'Value' in text else []
        findings, _, _ = self.run_detect(data, ir, ['Names'],
                                         stub(labels={'Value': 'Names', 'Holder': 'Names', 'Joined': 'none', 'Team': 'none'}, region=region))
        self.assertEqual({f['value'] for f in findings}, {'Ann Moor'})

    def test_two_fields_on_one_line_are_separate_values(self):
        pairs = {(p['label'], p['value']) for p in field_pairs(
            [{'id': 'l', 'text': 'Device: QX-0042 Owner: ana@corp.example'}], [], [])}
        self.assertIn(('Device', 'QX-0042'), pairs); self.assertIn(('Owner', 'ana@corp.example'), pairs)

    def test_value_column_header_is_ignored_when_a_row_label_cell_is_empty(self):
        rows = [['Field', 'Value'], ['Holder', 'Ann Moor'], ['', '2023-05-01'], ['Joined', None]]
        ir = {'units': [{'number': 1, 'regions': [{'id': 't', 'kind': 'table', 'text': '', 'bbox': [0, 0, 400, 100], 'cells': rows}]}]}
        data = pdf([(20, 40, 'Holder  Ann Moor'), (20, 60, '2023-05-01'), (20, 80, 'Joined')])
        findings, _, _ = self.run_detect(data, ir, ['Names'], stub(labels={'Value': 'Names', 'Holder': 'Names'}))
        self.assertEqual({f['value'] for f in findings}, {'Ann Moor'})

    def test_inline_value_stops_before_a_parenthetical_note(self):
        data = pdf([(20, 40, 'Locker ref: LK-2290 (east wing, level 2)')])
        findings, _, _ = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Identifiers'],
                                         stub(labels={'Locker ref': 'Identifiers'}))
        self.assertEqual([f['value'] for f in findings], ['LK-2290'])

    def test_plain_word_identifiers_and_known_name_fragments_are_dropped(self):
        data = pdf([(20, 40, 'API key'), (20, 52, 'zq_live_88Ab'), (20, 80, 'Badge holder Ona Vik')])
        region = lambda text: ([{'value': 'API key', 'category': 'Identifiers'}, {'value': 'zq_live_88Ab', 'category': 'Identifiers'}]
                               if 'API key' in text else [{'value': 'Ona Vik', 'category': 'Names'}] if 'Ona' in text else [])
        findings, _, _ = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Identifiers', 'Names'],
                                         stub(names=['Ona', 'Vik'], region=region))
        self.assertEqual(sorted(f['value'] for f in findings), ['Ona Vik', 'zq_live_88Ab'])

    def test_unverifiable_model_answer_masks_that_line_instead_of_failing(self):
        data = pdf([(20, 40, 'TOKEN=tk_9f8e7d6c5b'), (20, 80, 'Normal line')])
        region = lambda text: [{'value': 'tk_9f8e7d6c5bX', 'category': 'Identifiers'}] if 'TOKEN' in text else []
        findings, _, evidence = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Identifiers'], stub(region=region))
        self.assertEqual([(f['value'], f['method']) for f in findings], [('TOKEN=tk_9f8e7d6c5b', 'unverifiable_model_answer')])
        self.assertEqual(len(evidence['unresolved_units']), 1)
        self.assertNotIn('TOKEN=tk_9f8e7d6c5b', ' '.join(evidence['analyzed_texts']))

    def test_unselected_categories_are_dropped_after_relabeling(self):
        data = pdf([(20, 40, 'Mobile: +44 20 7946 0958'), (20, 60, 'Member: MB-90211')])
        region = lambda text: [{'value': v, 'category': 'Identifiers'} for v in ('+44 20 7946 0958', 'MB-90211') if v in text]
        findings, _, _ = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Identifiers'], stub(region=region))
        self.assertEqual({f['value'] for f in findings}, {'MB-90211'})

    def test_inline_label_misclassified_as_none_never_hides_a_model_finding(self):
        data = pdf([(20, 40, 'Member: MB-90211')])
        region = lambda text: [{'value': 'MB-90211', 'category': 'Identifiers'}] if 'MB-90211' in text else []
        findings, _, _ = self.run_detect(data, {'units': [{'number': 1, 'regions': []}]}, ['Identifiers'],
                                         stub(labels={'Member': 'none'}, region=region))
        self.assertEqual({f['value'] for f in findings}, {'MB-90211'})


if __name__ == '__main__':
    unittest.main()


class OcrParagraphTests(unittest.TestCase):
    def lines(self, rows):
        return [{'id': f'l{i}', 'text': t, 'bbox': [x, y, x + 300, y + 12]} for i, (x, y, t) in enumerate(rows)]

    def test_wrapped_prose_lines_join_and_short_lines_stay_alone(self):
        from transformation.validation.pii_document import ocr_paragraphs
        units = ocr_paragraphs(self.lines([(50, 100, 'The tenancy was approved for Mr. Lars'), (50, 114, 'Holmberg after the review of'),
                                           (50, 128, 'his references last week.'), (50, 200, 'Ref: TX-55'), (50, 214, 'Signed')]))
        self.assertEqual(len(units), 3)
        self.assertEqual(units[0]['text'].count('\n'), 2)
        self.assertEqual([u['id'] for u in units[1:]], ['l3', 'l4'])

    def test_name_across_two_ocr_lines_is_masked_on_both_lines(self):
        from transformation.validation.pii_document import ocr_paragraphs, member_findings
        units = ocr_paragraphs(self.lines([(50, 100, 'The tenancy was approved for Mr. Lars'), (50, 114, 'Holmberg after the review of')]))
        parts = member_findings({'region_id': units[0]['id'], 'value': 'Lars Holmberg', 'category': 'Names'}, units[0])
        self.assertEqual([(p['region_id'], p['value']) for p in parts], [('l0', 'Lars'), ('l1', 'Holmberg')])
