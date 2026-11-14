"""Review queue: places a person should check after automatic redaction. Synthetic data only."""
import json
import unittest
from unittest.mock import patch

import pymupdf

from tests.test_pii_document import pdf, stub
from transformation.validation.pii_document import detect_document
from transformation.validation.review_items import build
from tools.service.cloud_worker import review_items as sanitize


def detect(data, ir, categories, chat):
    with patch('transformation.llm.ollama_native.chat', side_effect=chat):
        return detect_document(data, ir, categories, 'http://127.0.0.1:1/v1', 'm', True, workers=2)


class ReviewQueueTests(unittest.TestCase):
    def test_model_and_column_label_disagreement_becomes_a_possible_miss(self):
        rows = [['Code', 'Holder'], ['K-100', 'Ann Moor']]
        ir = {'units': [{'number': 1, 'regions': [{'id': 't', 'kind': 'table', 'text': '', 'bbox': [0, 0, 400, 100], 'cells': rows}]}]}
        data = pdf([(20, 40, 'K-100   Ann Moor')])
        guess = lambda text: [{'value': 'K-100', 'category': 'Identifiers'}] if 'K-100' in text else []
        findings, _, evidence = detect(data, ir, ['Identifiers', 'Names'], stub(labels={'Code': 'none', 'Holder': 'Names'}, region=guess))
        self.assertNotIn('K-100', {f['value'] for f in findings})  # not masked automatically
        self.assertEqual([(s['value'], s['category']) for s in evidence['suppressed']], [('K-100', 'Identifiers')])
        items = build(data, ir, findings, evidence, [])
        self.assertEqual([(i['state'], i['reason'], i['category']) for i in items], [('suggested', 'rule_overruled_model', 'Identifiers')])
        # The box is the value itself, not its whole line.
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            page = doc[0]; hit = page.search_for('K-100')[0]; width = page.rect.width
        box = items[0]
        self.assertAlmostEqual(box['x'], hit.x0 / width, places=2)
        self.assertLess(box['width'], .1)

    def test_unverifiable_answer_is_listed_as_masked_as_a_precaution(self):
        data = pdf([(20, 40, 'TOKEN=tk_9f8e7d6c5b'), (20, 80, 'Normal line')])
        region = lambda text: [{'value': 'tk_9f8e7d6c5bX', 'category': 'Identifiers'}] if 'TOKEN' in text else []
        ir = {'units': [{'number': 1, 'regions': []}]}
        findings, _, evidence = detect(data, ir, ['Identifiers'], stub(region=region))
        items = build(data, ir, findings, evidence, [])
        self.assertEqual([(i['state'], i['reason']) for i in items], [('masked', 'unverified_answer')])

    def test_items_never_carry_text_and_use_the_displayed_page_frame(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=600); page.insert_text((40, 80), 'Ref Z-4471', fontsize=11)
            page.set_rotation(90); data = doc.tobytes()
        from transformation.validation.value_redaction import native_pattern_regions
        block = native_pattern_regions(data)[0]
        evidence = {'suppressed': [{'region_id': block['id'], 'value': 'Z-4471', 'category': 'Identifiers'}]}
        items = build(data, {'units': [{'number': 1, 'regions': []}]}, [], evidence, [])
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]), {'page', 'x', 'y', 'width', 'height', 'category', 'reason', 'state'})
        self.assertNotIn('Z-4471', json.dumps(items))
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            page = doc[0]; hit = page.search_for('Z-4471')[0] * page.rotation_matrix; view = page.rect
        self.assertEqual(view.width, 600)  # displayed (rotated) frame
        self.assertAlmostEqual(items[0]['x'], hit.x0 / view.width, places=2)
        self.assertAlmostEqual(items[0]['y'], hit.y0 / view.height, places=2)

    def test_a_suggestion_already_masked_another_way_is_not_listed(self):
        data = pdf([(20, 40, 'K-100   Ann Moor')])
        from transformation.validation.value_redaction import native_pattern_regions
        block = native_pattern_regions(data)[0]
        findings = [{'region_id': block['id'], 'value': 'K-100', 'category': 'Identifiers', 'method': 'document_consistency'}]
        evidence = {'suppressed': [{'region_id': block['id'], 'value': 'K-100', 'category': 'Identifiers'}]}
        self.assertEqual(build(data, {'units': [{'number': 1, 'regions': []}]}, findings, evidence, []), [])

    def test_worker_sends_only_valid_bounded_items(self):
        good = {'page': 1, 'x': .1, 'y': .2, 'width': .3, 'height': .05, 'category': 'Names', 'reason': 'whole_line', 'state': 'masked'}
        raw = [dict(good, value='Ann Moor'), dict(good, page=5), dict(good, x=.9, width=.5), dict(good, reason='x'), 'junk', good]
        self.assertEqual(sanitize(raw, pages=2), [good, good])
        self.assertEqual(len(sanitize([good] * 500, pages=2)), 200)
        self.assertEqual(sanitize(None, pages=2), [])


if __name__ == '__main__':
    unittest.main()


class RedactionMapTests(unittest.TestCase):
    def test_each_box_reports_category_and_best_method_in_the_displayed_frame(self):
        from tests.test_masking_style import findings_for, page_pdf
        from transformation.validation.review_items import redaction_map
        from transformation.validation.value_redaction import redact_pdf
        data = page_pdf([(60, 'Holder: Ann Moorcroft'), (100, 'Mail ann@post.example')], rotation=90)
        ir, findings = findings_for(data, [('Ann Moorcroft', 'Names'), ('ann@post.example', 'Email addresses')])
        findings[0]['method'] = 'model_proposal'; findings.append(dict(findings[0], method='field_label'))
        findings[1]['method'] = 'pattern'
        drawn = []; redact_pdf(data, ir, findings, boxes=drawn)
        boxes = redaction_map(data, findings, drawn)
        self.assertEqual(sorted((b['category'], b['method']) for b in boxes), [('Email addresses', 'pattern'), ('Names', 'field_label')])
        self.assertNotIn('Moorcroft', json.dumps(boxes))
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            page = doc[0]; hit = page.search_for('Ann Moorcroft')[0] * page.rotation_matrix; view = page.rect
            name_box = next(b for b in boxes if b['category'] == 'Names')
            self.assertAlmostEqual(name_box['x'], hit.x0 / view.width, delta=.02)
            self.assertAlmostEqual(name_box['y'], hit.y0 / view.height, delta=.02)

    def test_worker_keeps_only_known_methods_and_drops_text(self):
        from tools.service.cloud_worker import redaction_map as clean
        good = {'page': 1, 'x': .1, 'y': .2, 'width': .3, 'height': .05, 'category': 'Names', 'method': 'name_variant'}
        self.assertEqual(clean([dict(good, value='Ann'), dict(good, method='magic'), good], pages=1), [good, good])
