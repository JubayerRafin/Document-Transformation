"""Score translation segments (evaluation/suite/translation.py) with COMET and chrF++.

    python comet_score.py SEGMENTS.jsonl OUT.json [--comet DIR] [--kiwi DIR] [--batch 32]

Runs on a GPU machine with unbabel-comet and sacrebleu installed (a separate environment).

  kiwi     COMET-KiWi (Unbabel/wmt22-cometkiwi-da), reference-free: every segment
  comet22  COMET-22 (Unbabel/wmt22-comet-da) against the reference, where there is one
  chrf     chrF++ (sacrebleu, word order 2) against the reference, segment and corpus level

DIR is a downloaded model folder (comet.download_model). Scores are averaged per language pair and
per document kind; segments with the source left unchanged are counted separately (untranslated).
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def checkpoint(folder):
    return next(Path(folder).rglob('model.ckpt'))


def predict(folder, data, batch):
    from comet import load_from_checkpoint
    model = load_from_checkpoint(str(checkpoint(folder)))
    return model.predict(data, batch_size=batch, gpus=1, progress_bar=False).scores


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('segments'); ap.add_argument('out')
    ap.add_argument('--comet'); ap.add_argument('--kiwi'); ap.add_argument('--batch', type=int, default=32)
    args = ap.parse_args()
    rows = [json.loads(l) for l in Path(args.segments).read_text(encoding='utf-8').splitlines() if l.strip()]
    if args.kiwi:
        for row, score in zip(rows, predict(args.kiwi, [{'src': r['src'], 'mt': r['mt']} for r in rows], args.batch)):
            row['kiwi'] = round(float(score), 4)
    referenced = [r for r in rows if r.get('ref')]
    if args.comet and referenced:
        data = [{'src': r['src'], 'mt': r['mt'], 'ref': r['ref']} for r in referenced]
        for row, score in zip(referenced, predict(args.comet, data, args.batch)):
            row['comet22'] = round(float(score), 4)
    from sacrebleu.metrics import CHRF
    chrf = CHRF(word_order=2)
    for row in referenced:
        row['chrf'] = round(chrf.sentence_score(row['mt'], [row['ref']]).score, 2)
    groups = defaultdict(list)
    for row in rows:
        row['unchanged'] = row['src'] == row['mt']
        groups[('pair', f"{row['source']}-{row['target']}")].append(row)
        groups[('kind', row.get('kind') or '?')].append(row)
        groups[('all', 'all')].append(row)
    summary = {}
    for (axis, name), items in sorted(groups.items()):
        mean = lambda key: (round(sum(r[key] for r in items if key in r) / n, 4)
                            if (n := sum(key in r for r in items)) else None)
        refs = [r for r in items if r.get('ref')]
        summary.setdefault(axis, {})[name] = {
            'segments': len(items), 'with_reference': len(refs),
            'kiwi': mean('kiwi'), 'comet22': mean('comet22'), 'chrf_segment': mean('chrf'),
            'chrf_corpus': round(chrf.corpus_score([r['mt'] for r in refs], [[r['ref'] for r in refs]]).score, 2) if refs else None,
            'unchanged_share': round(sum(r['unchanged'] for r in items) / len(items), 4)}
    Path(args.out).write_text(json.dumps({'summary': summary, 'segments': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary.get('all'), indent=1))


if __name__ == '__main__':
    main()
