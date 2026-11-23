"""Collect the evaluation results into one JSON file (and print the headline numbers).

    python -m evaluation.suite.report EVAL_DIR OUT.json

EVAL_DIR holds:
  heldout/                       run_site results of the held-out corpus (to-en and from-en)
  translation-scores.json        comet_score.py on the held-out segments
  layout.json                    layout.py on the held-out results
  combined-scores.json           comet_score.py on the combined (PII + translation) segments
  combined-layout.json           layout.py on the combined results
  ocr-heldout.json, ocr-dev.json evaluation/ocr/bench.py scores
and reads output/pii-eval-v1/runs/<label>/scores.json (evaluation/pii/score.py) for PII.
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
                     'untranslated_lines': total('untranslated_lines'), 'off_page': total('off_page')}
    return out


def main(eval_dir, out):
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
    pii = load('output/pii-eval-v1/runs/site-final/scores.json')
    report['pii'] = pii.get('summary') if isinstance(pii, dict) and 'summary' in pii else pii
    combined = jobs('output/pii-eval-v1/runs/site-combined')
    report['combined_jobs'] = outcomes(combined, lambda r: 'all') if combined else None
    pii_combined = load('output/pii-eval-v1/runs/site-combined/scores.json')
    report['combined_pii'] = pii_combined.get('summary') if isinstance(pii_combined, dict) and 'summary' in pii_combined else pii_combined
    combined_scores = load(eval_dir / 'combined-scores.json')
    report['combined_quality'] = combined_scores['summary'] if combined_scores else None
    report['combined_layout'] = layout_summary(load(eval_dir / 'combined-layout.json'), lambda r: 'all')
    report['ocr'] = {'heldout': load(eval_dir / 'ocr-heldout.json'), 'dev': load(eval_dir / 'ocr-dev.json')}
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('translation_jobs',)}, indent=1)[:1500])


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
