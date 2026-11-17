"""Combined translation + PII jobs check every figure for PII first, then run each figure in full,
reusing those answers (one GPU model switch instead of two per figure). No models needed."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from transformation.pipeline import _pii_memo_path


class MemoKeyTests(unittest.TestCase):
    def test_same_inputs_in_different_sub_runs_share_answers_and_any_difference_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder, 'runs', 'a1'), Path(folder, 'runs', 'b2')
            ir = lambda run: {'units': [{'ocr_folder': str(run / 'ocr'), 'regions': [{'id': 'r1', 'text': 'Mina Park'}]}]}
            key = lambda run, data=b'pdf', ir_=None, **kw: _pii_memo_path(
                folder, run, data, ir_ or ir(run), kw.get('categories', ('Names',)), 'm', True, kw.get('known', []))
            self.assertEqual(key(first), key(second))                       # the sub-run folder is not part of the key
            self.assertNotEqual(key(first), key(first, data=b'other'))
            self.assertNotEqual(key(first), key(first, ir_={'units': []}))
            self.assertNotEqual(key(first), key(first, categories=('Names', 'Email addresses')))
            self.assertNotEqual(key(first), key(first, known=[{'value': 'x', 'category': 'Names'}]))
            self.assertIsNone(_pii_memo_path(None, first, b'pdf', {}, (), 'm', True, []))


class FirstPassTests(unittest.TestCase):
    def figure_document(self, figures):
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=400, height=400)
            ir = {'units': [{'number': 1, 'regions': []}]}
            for n in range(figures):
                box = (20, 20 + 120 * n, 200, 120 + 120 * n)
                page.draw_rect(box, fill=(0.2, 0.4, 0.8))
                ir['units'][0]['regions'].append({'id': f'f{n}', 'kind': 'figure', 'bbox': list(box)})
            return pdf.tobytes(), ir

    def run_process(self, figures, translate, redact):
        from transformation.ocr import pdf_assets
        calls = []
        def fake(payload, run, translate_, redact_, categories, assist, options):
            calls.append(dict(options)); return {'status': 'failed'}
        data, ir = self.figure_document(figures)
        with tempfile.TemporaryDirectory() as run, patch.object(pdf_assets, '_run_figure', side_effect=fake), \
                patch.object(pdf_assets, 'apply_results', return_value=(data, [])):
            list(pdf_assets.process(data, ir, run, translate, redact, ('Names',), True, {'timeout': 600}))
        return calls

    def test_combined_jobs_check_all_figures_for_pii_before_any_full_run(self):
        calls = self.run_process(3, translate=True, redact=True)
        self.assertEqual([c.get('stop_after_pii', False) for c in calls], [True] * 3 + [False] * 3)
        self.assertEqual(len({c['pii_memo'] for c in calls}), 1)          # one memo folder per job

    def test_single_figure_and_single_operation_jobs_are_unchanged(self):
        for figures, translate, redact in ((1, True, True), (3, True, False), (3, False, True)):
            calls = self.run_process(figures, translate, redact)
            self.assertEqual(len(calls), figures, (figures, translate, redact))
            self.assertFalse(any('pii_memo' in c or 'stop_after_pii' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
