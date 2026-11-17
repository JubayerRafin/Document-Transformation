"""Per-job masking style (box, [LABEL], consistent pseudonym) and keep-visible exceptions. Synthetic data."""
import unittest

import pymupdf

from transformation.validation.value_redaction import (
    literal_spans, native_pattern_regions, redact_pdf, replacement_labels,
)


def page_pdf(lines, rotation=0, width=420, height=300):
    with pymupdf.open() as doc:
        page = doc.new_page(width=width, height=height)
        for y, text in lines: page.insert_text((30, y), text, fontsize=12)
        if rotation: page.set_rotation(rotation)
        return doc.tobytes()


def findings_for(data, items):
    regions = native_pattern_regions(data); found = []
    for value, category in items:
        for region in regions:
            for a, b in literal_spans(region['text'], value):
                found.append({'region_id': region['id'], 'value': region['text'][a:b], 'category': category})
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        units = [{'number': i + 1, 'regions': []} for i in range(len(pdf))]
    return {'units': units, 'pii_source_regions': regions}, found


class LabelTests(unittest.TestCase):
    def test_pseudonyms_are_consistent_and_a_surname_reuses_its_person(self):
        findings = [{'value': v, 'category': c} for v, c in [
            ('Thomas Becker', 'Names'), ('Ann Moor', 'Names'), ('Becker', 'Names'), ('t.becker@post.example', 'Email addresses'),
            ('ann@post.example', 'Email addresses'), ('K-100', 'Identifiers'), ('Thomas Becker', 'Names')]]
        labels = replacement_labels(findings, 'pseudonym')
        self.assertEqual(labels['Thomas Becker'], 'Person A'); self.assertEqual(labels['Becker'], 'Person A')
        self.assertEqual(labels['Ann Moor'], 'Person B')
        self.assertEqual(sorted([labels['t.becker@post.example'], labels['ann@post.example']]), ['Email 1', 'Email 2'])
        self.assertEqual(labels['K-100'], 'ID 1')

    def test_label_style_names_the_category_and_box_style_writes_nothing(self):
        findings = [{'value': 'Ann Moor', 'category': 'Names'}, {'value': 'DE89 3704', 'category': 'Bank accounts'}]
        self.assertEqual(replacement_labels(findings, 'label'), {'Ann Moor': '[NAME]', 'DE89 3704': '[ACCOUNT]'})
        self.assertEqual(replacement_labels(findings, 'box'), {})
        with self.assertRaises(ValueError): replacement_labels(findings, 'blur')


class StyledRedactionTests(unittest.TestCase):
    def check(self, data, items, style, expect):
        ir, findings = findings_for(data, items)
        output, _ = redact_pdf(data, ir, findings, style=style)
        with pymupdf.open(stream=output, filetype='pdf') as pdf:
            text = ' '.join(p.get_text() for p in pdf)
        for value, _ in items: self.assertNotIn(value, text)
        for label in expect: self.assertIn(label, text)
        return output

    def test_label_is_written_inside_the_box_and_the_value_is_gone(self):
        data = page_pdf([(60, 'Holder: Ann Moorcroft'), (100, 'Account: DE89 3704 0044')])
        self.check(data, [('Ann Moorcroft', 'Names'), ('DE89 3704 0044', 'Bank accounts')], 'label', ['[NAME]', '[ACCOUNT]'])

    def test_pseudonym_on_a_rotated_page_reads_upright(self):
        data = page_pdf([(60, 'Signed by Thomas Beckerson today')], rotation=90)
        output = self.check(data, [('Thomas Beckerson', 'Names')], 'pseudonym', ['Person A'])
        with pymupdf.open(stream=output, filetype='pdf') as pdf:
            spans = [s for b in pdf[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans'] if 'Person' in s['text']]
            line_dirs = [l['dir'] for b in pdf[0].get_text('dict')['blocks'] for l in b.get('lines', [])
                         if any('Person' in s['text'] for s in l['spans'])]
            source_dirs = [l['dir'] for b in pdf[0].get_text('dict')['blocks'] for l in b.get('lines', [])
                           if any('Signed' in s['text'] for s in l['spans'])]
        self.assertTrue(spans)
        self.assertEqual([tuple(round(c) for c in d) for d in line_dirs], [tuple(round(c) for c in d) for d in source_dirs])

    def test_a_label_too_wide_for_its_box_is_left_out_but_the_value_is_still_removed(self):
        data = page_pdf([(60, 'Code: Q7')])
        self.check(data, [('Q7', 'Identifiers')], 'label', [])


class KeepVisibleTests(unittest.TestCase):
    def test_values_inside_a_keep_entry_stay_visible_and_longer_values_are_still_masked(self):
        from transformation.validation.pii_document import split_kept
        findings = [{'value': v, 'category': 'Names'} for v in ('Northwind Supply', 'northwind  supply', 'Ann Moor, Northwind Supply', 'Ann Moor')]
        masked, kept = split_kept(findings, ['Northwind Supply Co.'])
        self.assertEqual([f['value'] for f in kept], ['Northwind Supply', 'northwind  supply'])
        self.assertEqual([f['value'] for f in masked], ['Ann Moor, Northwind Supply', 'Ann Moor'])
        self.assertEqual(split_kept(findings, ['', '   '])[1], [])


if __name__ == '__main__':
    unittest.main()
