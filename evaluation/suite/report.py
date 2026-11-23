"""Collect the evaluation results into one JSON file (and print the headline numbers).

    python -m evaluation.suite.report EVAL_DIR OUT.json [--pii LABEL] [--combined LABEL] [--office RUN] [--office-pii LABEL]

EVAL_DIR holds:
  heldout/                       run_site results of the held-out corpus (to-en and from-en)
  translation-scores.json        comet_score.py on the held-out segments
  layout.json                    layout.py on the held-out results
  combined-scores.json           comet_score.py on the combined (PII + translation) segments
  combined-layout.json           layout.py on the combined results
  ocr-heldout.json, ocr-dev.json evaluation/ocr/bench.py scores
and reads output/pii-eval-v1/runs/<label>/scores.json (evaluation/pii/score.py) for PII.

Word and PowerPoint (evaluation/office): output/office-eval/runs/<RUN> (run_site results),
EVAL_DIR/office-scores.json and office-layout.json, and output/office-eval/heldout/pii/runs/<label>.
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REVIEW_KEYS = ['known_sensitive_residuals', 'partial_name_residuals', 'geometry_blockers', 'layout_review_candidates',
               'uninspected_content', 'scan_acceptance_blockers', 'translation_errors']


def load(path):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def jobs(results):
    rows = []
    for path in sorted(Path(results).rglob('summary.json')):
        if 'server' in path.parts:
            continue
        s = json.loads(path.read_text(encoding='utf-8'))
        checks = s.get('checks') or {}
        rows.append({'doc': s.get('doc'), 'source': s.get('source'), 'target': s.get('target'), 'kind': s.get('kind'),
                     'ok': s.get('status') in ('generated', 'succeeded'),
                     'review': bool(checks) and (not checks.get('acceptable') or any((checks.get(k) or 0) > 0 for k in REVIEW_KEYS)),
                     'seconds': s.get('server_seconds'), 'pages': s.get('pages') or 1})
    return rows


def outcomes(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    return {name: {'jobs': len(items), 'success_rate': round(sum(r['ok'] for r in items) / len(items), 4),
                   'review_rate': round(sum(r['review'] for r in items) / len(items), 4),
                   'seconds_per_page': round(sum(r['seconds'] or 0 for r in items) / max(1, sum(r['pages'] for r in items)), 2)}
            for name, items in sorted(groups.items())}


def layout_summary(rows, key):
    groups = defaultdict(list)
    for r in rows or []:
        if 'error' not in r:
            groups[key(r)].append(r)
    out = {}
    for name, items in sorted(groups.items()):
        mean = lambda k: (round(statistics.mean(v), 4) if (v := [r[k] for r in items if r.get(k) is not None]) else None)
        total = lambda k: sum(r.get(k) or 0 for r in items)
        out[name] = {'documents': len(items), 'ssim': mean('ssim'), 'ssim_graphics': mean('ssim_graphics'),
                     'block_iou': mean('block_iou'), 'picture_iou': mean('picture_iou'),
                     'text_extent_iou': mean('text_extent_iou'), 'font_ratio': mean('font_ratio'),
                     'page_count_match': round(sum(r['page_count_match'] for r in items) / len(items), 4),
                     'new_collisions': total('new_collisions'), 'text_over_pictures': total('text_over_pictures'),
                     'picture_text_placements': total('picture_text_placements'),
                     'untranslated_lines': total('untranslated_lines'), 'off_page': total('off_page')}
    return out


def main(eval_dir, out, pii_label='site-final', combined_label='site-combined', office_run=None, office_pii=None):
    eval_dir = Path(eval_dir)
    pair = lambda r: f"{r['source']}-{r['target']}"
    report = {}
    heldout = jobs(eval_dir / 'heldout')
    report['translation_jobs'] = {'all': outcomes(heldout, lambda r: 'all')['all'] if heldout else None,
                                  'by_pair': outcomes(heldout, pair), 'by_kind': outcomes(heldout, lambda r: r['kind'])}
    scores = load(eval_dir / 'translation-scores.json')
    report['translation_quality'] = scores['summary'] if scores else None
    layout = load(eval_dir / 'layout.json')
    report['layout'] = {'all': layout_summary(layout, lambda r: 'all'), 'by_pair': layout_summary(layout, pair),
                        'by_kind': layout_summary(layout, lambda r: r.get('kind'))}
    pii = load(f'output/pii-eval-v1/runs/{pii_label}/scores.json')
    report['pii'] = pii.get('summary') if isinstance(pii, dict) and 'summary' in pii else pii
    combined = jobs(f'output/pii-eval-v1/runs/{combined_label}')
    report['combined_jobs'] = outcomes(combined, lambda r: 'all') if combined else None
    pii_combined = load(f'output/pii-eval-v1/runs/{combined_label}/scores.json')
    report['combined_pii'] = pii_combined.get('summary') if isinstance(pii_combined, dict) and 'summary' in pii_combined else pii_combined
    combined_scores = load(eval_dir / 'combined-scores.json')
    report['combined_quality'] = combined_scores['summary'] if combined_scores else None
    report['combined_layout'] = layout_summary(load(eval_dir / 'combined-layout.json'), lambda r: 'all')
    report['ocr'] = {'heldout': load(eval_dir / 'ocr-heldout.json'), 'dev': load(eval_dir / 'ocr-dev.json')}
    if office_run:
        office = jobs(f'output/office-eval/runs/{office_run}')
        fmt = lambda r: 'docx' if (r['kind'] or '').endswith('report') else 'pptx'
        direction = lambda r: 'into English' if r['target'] == 'en' else 'from English'
        scores = load(eval_dir / 'office-scores.json')
        layout = load(eval_dir / 'office-layout.json')
        pii_scores = load(f'output/office-eval/heldout/pii/runs/{office_pii}/scores.json') if office_pii else None
        pii_combined_scores = load(f'output/office-eval/heldout/pii/runs/{office_pii}-combined/scores.json') if office_pii else None
        report['office'] = {
            'jobs': {'all': outcomes(office, lambda r: 'all').get('all') if office else None,
                     'by_format': outcomes(office, fmt), 'by_direction': outcomes(office, direction),
                     'by_pair': outcomes(office, pair)},
            'quality': scores['summary'] if scores else None,
            'layout': {'all': layout_summary(layout, lambda r: 'all'),
                       'by_format': layout_summary(layout, lambda r: 'docx' if (r.get('kind') or '').endswith('report') else 'pptx')},
            'pii': pii_scores.get('summary') if isinstance(pii_scores, dict) and 'summary' in pii_scores else pii_scores,
            'pii_combined': (pii_combined_scores.get('summary') if isinstance(pii_combined_scores, dict)
                             and 'summary' in pii_combined_scores else pii_combined_scores)}
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('translation_jobs',)}, indent=1)[:1500])


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('eval_dir'); ap.add_argument('out')
    ap.add_argument('--pii', default='site-final'); ap.add_argument('--combined', default='site-combined')
    ap.add_argument('--office'); ap.add_argument('--office-pii')
    a = ap.parse_args()
    main(a.eval_dir, a.out, a.pii, a.combined, a.office, a.office_pii)
