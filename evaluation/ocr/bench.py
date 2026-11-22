"""OCR benchmark on generated scans whose true text is known.

    python -m evaluation.ocr.bench CORPUS OUT --python PADDLE_PYTHON [--langs en,ko,...] [--label NAME]

CORPUS is a make_fullpage.py folder: <set>-<lang>-scan.pdf is a tilted, noisy JPEG copy of
<set>-<lang>-invoice.pdf, whose text layer is the truth. For every language the scan is read with the
same OCR the engine uses (transformation.ocr.paddle_pipeline.run_ocr) and compared with the truth:

  char_recall / char_precision   multiset of non-space characters (reading order does not matter)
  token_recall                   whitespace tokens of the truth found verbatim in the OCR text
  value_recall                   tokens with a digit (codes, amounts, dates) found verbatim
  review_share                   OCR lines left for review (kept as source pixels, not translated)
  seconds                        OCR wall time

OUT/<label>/<lang>/ keeps the OCR evidence; OUT/<label>/scores.json the numbers. Measure on the dev
set; use the held-out set only to confirm a change, never to choose one.
"""
import argparse
import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf


def normalize(text):
    return unicodedata.normalize('NFKC', text)


def truth_text(pdf):
    with pymupdf.open(pdf) as doc:
        return normalize('\n'.join(page.get_text() for page in doc))


def ocr_lines(recognition_dir):
    path = Path(recognition_dir) / 'lines.jsonl'
    return [json.loads(row) for row in path.read_text(encoding='utf-8').splitlines() if row.strip()]


def ocr_text(recognition_dir):
    return normalize('\n'.join(line.get('text', '') for line in ocr_lines(recognition_dir)))


def review_share(recognition_dir):
    """Share of OCR lines left for review: they keep their source pixels and are not translated."""
    lines = [l for l in ocr_lines(recognition_dir) if l.get('text', '').strip()]
    return round(sum(bool(l.get('requires_review')) for l in lines) / max(1, len(lines)), 4)


def split_scripts(text):
    # A PDF text layer can glue a right-to-left word to the number drawn beside it ("نحو30"); the
    # comparison splits where letters of another script meet digits or Latin letters.
    return re.sub(r'(?<=[^\W\d_])(?=[0-9A-Za-z])(?<![A-Za-z])|(?<=[0-9A-Za-z])(?=[^\W\d_])(?![A-Za-z])', ' ', text)


def score(truth, found):
    truth, found = split_scripts(truth), split_scripts(found)
    t = Counter(c for c in truth if not c.isspace())
    f = Counter(c for c in found if not c.isspace())
    hit = sum((t & f).values())
    tokens = [w.strip('.,:;()') for w in truth.split()]
    tokens = [w for w in tokens if w]
    found_compact = re.sub(r'\s+', '', found)
    values = [w for w in tokens if any(ch.isdigit() for ch in w)]
    return {
        'char_recall': round(hit / max(1, sum(t.values())), 4),
        'char_precision': round(hit / max(1, sum(f.values())), 4),
        'token_recall': round(sum(w in found for w in tokens) / max(1, len(tokens)), 4),
        'value_recall': round(sum(re.sub(r'\s+', '', w) in found_compact for w in values) / max(1, len(values)), 4),
        'truth_chars': sum(t.values()), 'ocr_chars': sum(f.values()),
        'missed_values': [w for w in values if re.sub(r'\s+', '', w) not in found_compact][:12],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('corpus'); ap.add_argument('out')
    ap.add_argument('--python', required=True)
    ap.add_argument('--langs', default='en,ko,ja,zh,ar,hi,bn,fr,de,es,pt')
    ap.add_argument('--label', default='base')
    args = ap.parse_args()
    from transformation.ocr.paddle_pipeline import run_ocr
    corpus = Path(args.corpus)
    prefix = next(corpus.glob('*-en-invoice.pdf')).name.split('-en-')[0]
    root = Path(args.out) / args.label
    results = {}
    for lang in args.langs.split(','):
        scan, truth = corpus / f'{prefix}-{lang}-scan.pdf', corpus / f'{prefix}-{lang}-invoice.pdf'
        folder = root / lang
        if folder.exists():
            import shutil
            shutil.rmtree(folder)
        started = time.monotonic()
        done = None
        try:
            for event in run_ocr(scan, folder, args.python, source_language=lang):
                done = event
            found = ocr_text(done['recognition_dir'])
            row = score(truth_text(truth), found)
            row['review_share'] = review_share(done['recognition_dir'])
        except Exception as exc:  # one language failing must not hide the others
            row = {'error': f'{type(exc).__name__}: {exc}'[:300]}
        row['seconds'] = round(time.monotonic() - started, 1)
        results[lang] = row
        print(lang, json.dumps({k: v for k, v in row.items() if k != 'missed_values'}, ensure_ascii=False), flush=True)
    keys = ['char_recall', 'char_precision', 'token_recall', 'value_recall']
    ok = [r for r in results.values() if 'error' not in r]
    results['_mean'] = {k: round(sum(r[k] for r in ok) / max(1, len(ok)), 4) for k in keys}
    (root / 'scores.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    print('mean', results['_mean'])


if __name__ == '__main__':
    main()
