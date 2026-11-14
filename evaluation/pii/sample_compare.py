"""Score PII findings for the six sample PDFs against the 23 Oct 2026 human-reviewed annotations.

The reviewed annotations are exact spans tied to that run's extraction regions, so a run with a
different extraction cannot be scored region by region. This scorer matches on what the reviewed
metric checks (category + exact value), per document:
  recall    = reviewed spans whose (category, value) was predicted / reviewed spans
  precision = distinct predicted (category, value) that are reviewed spans / distinct predictions
Values compare with whitespace removed. Scoring the 23 Oct findings this way reproduces that
report closely (see the calibration line), so the same scorer is applied to a new run.

usage: python -m evaluation.pii.sample_compare <reviewed_package_dir> <new_runs_dir>
  new_runs_dir: <dir>/<SampleXX>/summary.json with run_dir (as written by evaluation.pii.run)
"""
import json, re, sys
from pathlib import Path

CATS = ['Email addresses', 'Phone numbers', 'Names', 'Addresses', 'Identifiers', 'Bank accounts']


def key(value): return re.sub(r'\s+', '', value)


def gold_for(package, name):
    review = json.loads((package / 'results/ground-truth' / f'{name}.pii.json').read_text(encoding='utf-8'))
    gold = [(g['category'], key(g['value'])) for r in review['regions'] for g in r['pii']]
    for f in sorted((package / 'ground-truth-images' / name).glob('*.json')):
        gold += [(g['category'], key(g['value'])) for g in json.loads(f.read_text(encoding='utf-8'))['expected_spans']]
    return gold


def findings_in(folder):
    found = set()
    for f in Path(folder).rglob('pii-findings.json'):
        found |= {(x['category'], key(x['value'])) for x in json.loads(f.read_text(encoding='utf-8')) if x.get('value')}
    return found


def score(gold_by_doc, pred_by_doc):
    per = {c: {'tp': 0, 'fn': 0, 'ptp': 0, 'fp': 0} for c in CATS}
    for name, gold in gold_by_doc.items():
        pred = pred_by_doc.get(name, set()); gold_set = set(gold)
        for c, v in gold: per[c]['tp' if (c, v) in pred else 'fn'] += 1
        for c, v in pred:
            if c in per: per[c]['ptp' if (c, v) in gold_set else 'fp'] += 1
    tot = {k: sum(p[k] for p in per.values()) for k in ('tp', 'fn', 'ptp', 'fp')}
    rate = lambda a, b: a / b if b else None
    out = {c: {'recall': rate(p['tp'], p['tp'] + p['fn']), 'precision': rate(p['ptp'], p['ptp'] + p['fp']),
               'expected': p['tp'] + p['fn'], 'missed': p['fn'], 'false_positives': p['fp']} for c, p in per.items()}
    out['ALL'] = {'recall': rate(tot['tp'], tot['tp'] + tot['fn']), 'precision': rate(tot['ptp'], tot['ptp'] + tot['fp']),
                  'expected': tot['tp'] + tot['fn'], 'missed': tot['fn'], 'false_positives': tot['fp']}
    return out


def main():
    package, runs = Path(sys.argv[1]), Path(sys.argv[2])
    names = [f'Sample0{i}' for i in range(1, 7)]
    gold = {n: gold_for(package, n) for n in names}
    then = {n: findings_in(package / 'results/frozen-evidence' / n) | findings_in(package / 'results/figure-evidence' / n) for n in names}
    now = {}
    for n in names:
        s = json.loads((runs / n / 'summary.json').read_text(encoding='utf-8'))
        now[n] = findings_in(s['run_dir']) if s.get('run_dir') else set()
    result = {'then_23_oct': score(gold, then), 'now': score(gold, now),
              'misses_now': {n: sorted(v for c, v in gold[n] if (c, v) not in now[n]) for n in names},
              'false_positives_now': {n: sorted(f'{c}: {v}' for c, v in now[n] if c in CATS and (c, v) not in set(gold[n])) for n in names}}
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
