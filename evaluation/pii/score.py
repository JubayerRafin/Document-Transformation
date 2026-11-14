"""Score a PII evaluation run against the answer key.

usage: python -m evaluation.pii.score <set_dir> <label>
Writes <set_dir>/runs/<label>/scores.json and prints an aggregate table.

Per expected value: detected 'full' (a finding contains it), 'partial' (a finding covers >=60%),
'ocr_variant' (>=85% similar, OCR misread), 'by_words' (every word found separately) or 'missed'.
Leaks: a value still in the output text layer, or a word of a multi-word value left behind.
Over-masking: a must-stay-visible value found as PII, or removed from the output text.
Image values are judged against a fresh OCR of the output when ocr_leaks.py has run; otherwise by detection only.
"""
import difflib, json, re, sys
from collections import defaultdict
from pathlib import Path

import pymupdf


def compact(text): return re.sub(r'[\s­]+', '', str(text)).casefold()


def load(path):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError): return None


def match(expected, value):
    e, v = compact(expected), compact(value)
    if not e or not v: return None
    if e == v or e in v: return 'full'
    if v in e and len(v) >= .6 * len(e): return 'partial'
    return None


def similar(a, b): return difflib.SequenceMatcher(None, compact(a), compact(b)).ratio() >= .85


def score_doc(spec, summary, source, run_dir=None):
    row = {'status': summary.get('status'), 'failure_code': summary.get('failure_code'), 'error': (summary.get('error') or '')[:200],
           'server_seconds': summary.get('server_seconds'), 'pages': spec['pages'], 'variant': spec['variant'], 'language': spec['language'],
           'kind': spec['kind'].split(' /')[0]}
    run = Path(summary['run_dir']) if summary.get('run_dir') else None
    findings = []
    if run and run.exists():
        for path in run.rglob('pii-findings.json'): findings += [f for f in (load(path) or []) if isinstance(f, dict)]
    values = sorted({(f.get('category'), f.get('value')) for f in findings if f.get('value')}, key=str)
    generated = row['status'] == 'generated'
    output = Path(summary['output_file']) if generated and summary.get('output_file') else None
    out_text = ''
    if output and output.is_file():
        with pymupdf.open(output) as pdf: out_text = ' '.join(p.get_text() for p in pdf)
    with pymupdf.open(source) as pdf: src_text = ' '.join(p.get_text() for p in pdf)
    verification = load(run / 'privacy-verification.json') if run else None
    row['privacy_verification'] = ('passed' if verification.get('passed') else 'flagged') if verification else 'not_run'
    # Image values: judged against a fresh OCR of the output when ocr_leaks.py has produced one.
    ocr = load(run_dir / 'output-ocr.json') if run_dir else None
    ocr_text = ' '.join((ocr or {}).get('pages', {}).values())
    ocr_lines = [l for l in '\n'.join((ocr or {}).get('pages', {}).values()).split('\n') if l.strip()]
    row['image_ocr_checked'] = bool(ocr) and generated
    rows = []
    for category, value, where in spec['expected']:
        hits = [m for _, v in values if (m := match(value, v))]
        detected = 'full' if 'full' in hits else 'partial' if hits else 'missed'
        words = [w for w in value.split() if w]
        if detected == 'missed':
            if any(similar(value, v) for _, v in values): detected = 'ocr_variant'
            elif len(words) > 1 and all(any(compact(w) in compact(v) for _, v in values) for w in words): detected = 'by_words'
        leak = None
        if generated and where == 'image' and ocr:
            leak = compact(value) in compact(ocr_text) or (len(compact(value)) >= 6 and any(similar(value, l) for l in ocr_lines))
        if generated and where == 'text':
            leak = compact(value) in compact(out_text) or any(
                len(w) >= 4 and src_text.count(w) and src_text.count(w) == out_text.count(w) for w in words if len(words) > 1)
        rows.append({'category': category, 'value': value, 'where': where, 'detected': detected, 'leak': leak})
    row['expected'] = rows
    known = [v for _, v, _ in spec['expected']] + spec.get('optional', [])
    related = lambda k, v: match(k, v) or match(v, k) or similar(k, v) or (compact(v) and compact(v) in compact(k))
    row['unexpected_findings'] = [[c, v] for c, v in values if not any(related(k, v) for k in known)]
    row['negatives_found'] = sorted({n for n in spec['negatives'] for _, v in values if match(n, v)})
    row['negatives_removed'] = sorted(n for n in spec['negatives'] if generated and compact(n) in compact(src_text)
                                      and compact(n) not in compact(out_text)) if out_text or not src_text.strip() else []
    return row


def rate(n, d): return round(n / d, 3) if d else None


def main():
    set_dir, label = Path(sys.argv[1]), sys.argv[2]
    key = json.loads((set_dir / 'answer_key.json').read_text(encoding='utf-8'))
    docs = {}
    for name, spec in key.items():
        if name.startswith('_'): continue
        summary = load(set_dir / 'runs' / label / name / 'summary.json')
        if summary: docs[name] = score_doc(spec, summary, set_dir / 'pdfs' / f'{name}.pdf', set_dir / 'runs' / label / name)
    groups = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # group -> key -> [found, total]
    totals = {'docs': len(docs), 'generated': 0, 'expected': 0, 'found_any': 0, 'found_full': 0, 'text_values': 0, 'text_leaks': 0, 'image_values': 0, 'image_leaks': 0,
              'negatives': 0, 'negatives_found': 0, 'negatives_removed': 0, 'unexpected': 0, 'seconds': 0.0, 'pages': 0}
    for name, row in docs.items():
        totals['generated'] += row['status'] == 'generated'
        if row['server_seconds']: totals['seconds'] += row['server_seconds']; totals['pages'] += row['pages']
        for r in row['expected']:
            found = r['detected'] != 'missed'
            totals['expected'] += 1; totals['found_any'] += found; totals['found_full'] += r['detected'] == 'full'
            if r['where'] == 'text' and r['leak'] is not None: totals['text_values'] += 1; totals['text_leaks'] += bool(r['leak'])
            if r['where'] == 'image' and r['leak'] is not None: totals['image_values'] += 1; totals['image_leaks'] += bool(r['leak'])
            for g, k in (('category', r['category']), ('variant', row['variant']), ('language', row['language']), ('kind', row['kind'])):
                groups[g][k][0] += found; groups[g][k][1] += 1
        totals['negatives'] += len(key[name]['negatives']); totals['negatives_found'] += len(row['negatives_found'])
        totals['negatives_removed'] += len(row['negatives_removed']); totals['unexpected'] += len(row['unexpected_findings'])
    summary = {'label': label, 'documents': totals['docs'], 'generated': totals['generated'],
               'recall_any': rate(totals['found_any'], totals['expected']), 'recall_full': rate(totals['found_full'], totals['expected']),
               'expected_values': totals['expected'], 'missed_values': totals['expected'] - totals['found_any'],
               'text_values_checked': totals['text_values'], 'text_leaks': totals['text_leaks'],
               'image_values_checked_by_ocr': totals['image_values'], 'image_leaks': totals['image_leaks'],
               'negatives': totals['negatives'], 'negatives_flagged_as_pii': totals['negatives_found'],
               'negatives_removed_from_text': totals['negatives_removed'], 'unexpected_findings': totals['unexpected'],
               'server_seconds_per_page': rate(totals['seconds'], totals['pages']),
               'recall_by': {g: {k: rate(f, t) for k, (f, t) in sorted(v.items())} for g, v in groups.items()},
               'counts_by': {g: {k: t for k, (f, t) in sorted(v.items())} for g, v in groups.items()}}
    out = set_dir / 'runs' / label / 'scores.json'
    out.write_text(json.dumps({'summary': summary, 'documents': docs}, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('recall_by', 'counts_by')}, ensure_ascii=False))
    for g, v in summary['recall_by'].items(): print(f'{g:9}', ', '.join(f'{k} {r}' for k, r in v.items()))
    for name, row in docs.items():
        missed = [r['value'] for r in row['expected'] if r['detected'] == 'missed']
        leaks = [r['value'] for r in row['expected'] if r['leak']]
        if row['status'] != 'generated' or missed or leaks or row['negatives_found'] or row['negatives_removed']:
            print(f"{name:34} {row['status']!s:10} {row['failure_code'] or '':24} missed={missed} leaks={leaks} "
                  f"neg_flagged={row['negatives_found']} neg_removed={row['negatives_removed']}")


if __name__ == '__main__':
    main()
