"""OCR benchmark on generated scans whose true text is known.

    python -m evaluation.ocr.bench CORPUS OUT --python PADDLE_PYTHON [--langs en,ko,...] [--label NAME]

CORPUS is a make_fullpage.py folder: <set>-<lang>-scan.pdf is a tilted, noisy JPEG copy of
<set>-<lang>-invoice.pdf, whose text layer is the truth. For every language the scan is read with the
same OCR the engine uses (transformation.ocr.paddle_pipeline.run_ocr) and compared with the truth:

  char_recall / char_precision   multiset of non-space characters (reading order does not matter)
  token_recall                   whitespace tokens of the truth found verbatim in the OCR text
  value_recall                   tokens with a digit (codes, amounts, dates) found verbatim
  cer / cer_nospace              character error rate: each line of the truth (cut at table-cell gaps;
                                 right-to-left lines in runs of one direction) against its closest
                                 match anywhere in the OCR text (edit distance to the best substring
                                 of an OCR line, so reading order and the scan's tilt do not matter),
                                 summed over all truth characters; with and without word spaces
  review_share                   OCR lines flagged by the recognizer
  kept_share                     OCR lines a translation keeps as source pixels (flagged, not read
                                 consistently and not a literal): the untranslated lines a user sees
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
    # The generated Hindi and Bengali text layers repeat vowel signs ("उत्पाादन", "बढ़ीी"); a word
    # never holds the same combining sign twice in a row, so repeats are folded (truth and OCR alike).
    text = unicodedata.normalize('NFKC', text)
    return ''.join(c for i, c in enumerate(text)
                   if not (i and c == text[i - 1] and unicodedata.category(c) in ('Mn', 'Mc')))


def truth_text(pdf):
    with pymupdf.open(pdf) as doc:
        return normalize('\n'.join(page.get_text() for page in doc))


def truth_cells(pdf):
    """The truth's lines cut where characters are farther apart than their size (table cells that the
    text layer joins into one line: "ملصقات حرارية YDZ-2020-43740"), for the line-level CER."""
    cells = []
    with pymupdf.open(pdf) as doc:
        for page in doc:
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines', []):
                    chars = [c for span in line['spans'] for c in span['chars']]
                    size = max((span['size'] for span in line['spans']), default=10)
                    current, last = '', None
                    for c in chars:
                        if last is not None and c['bbox'][0] - last > size and current.strip():
                            cells.append(normalize(current)); current = ''
                        current += c['c']
                        last = c['bbox'][2]
                    if current.strip():
                        cells.append(normalize(current))
    return cells


RTL = re.compile(r'[֐-ࣿ]')


def direction_runs(line):
    """A right-to-left line in runs of one direction: a text layer may store the Arabic words and the
    numbers between them in drawing order, so runs are compared one by one, not their order."""
    if not RTL.search(line):
        return [line]
    runs, current, rtl = [], [], None
    for token in line.split():
        kind = bool(RTL.search(token))
        if current and kind != rtl:
            runs.append(' '.join(current)); current = []
        current.append(token); rtl = kind
    if current:
        runs.append(' '.join(current))
    return runs


def ocr_lines(recognition_dir):
    path = Path(recognition_dir) / 'lines.jsonl'
    return [json.loads(row) for row in path.read_text(encoding='utf-8').splitlines() if row.strip()]


def ocr_text(recognition_dir):
    return normalize('\n'.join(line.get('text', '') for line in ocr_lines(recognition_dir)))


def review_share(recognition_dir):
    """Share of OCR lines left for review: they keep their source pixels and are not translated."""
    lines = [l for l in ocr_lines(recognition_dir) if l.get('text', '').strip()]
    return round(sum(bool(l.get('requires_review')) for l in lines) / max(1, len(lines)), 4)


def kept_share(recognition_dir):
    """Share of OCR lines a translation keeps as source pixels: flagged, not read consistently enough
    to rebuild (transformation/ocr/read_alike.py) and not a literal that needs no translation."""
    from transformation.ocr.read_alike import literal_line, reads_consistently
    lines = [l for l in ocr_lines(recognition_dir) if l.get('text', '').strip()]
    kept = 0
    for l in lines:
        region = {'text': l['text'], 'metadata': {'requires_review': bool(l.get('requires_review')),
                  'recognition_decision': l.get('recognition_decision'), 'candidates': l.get('candidates', [])}}
        if l.get('requires_review') and not reads_consistently(region) and not literal_line(region):
            kept += 1
    return round(kept / max(1, len(lines)), 4)


def split_scripts(text):
    # A PDF text layer can glue a right-to-left word to the number drawn beside it ("نحو30"); the
    # comparison splits where letters of another script meet digits or Latin letters.
    return re.sub(r'(?<=[^\W\d_])(?=[0-9A-Za-z])(?<![A-Za-z])|(?<=[0-9A-Za-z])(?=[^\W\d_])(?![A-Za-z])', ' ', text)


def substring_distance(needle, haystack):
    """Smallest edit distance between needle and any substring of haystack (Sellers)."""
    if not needle: return 0
    previous = [0] * (len(haystack) + 1)
    for i, a in enumerate(needle, 1):
        current = [i] + [0] * len(haystack)
        for j, b in enumerate(haystack, 1):
            current[j] = min(previous[j - 1] + (a != b), previous[j] + 1, current[j - 1] + 1)
        previous = current
    return min(previous)


def line_cer(truth_lines, ocr_lines_text):
    """(cer, cer without spaces): every truth line against its best-matching OCR line."""
    def best(line, candidates):
        bag = Counter(line.replace(' ', ''))
        # Most shared characters first, the shortest line among equals: a long paragraph holds the
        # letters of a short table cell too.
        ranked = sorted(candidates, key=lambda c: (-sum((bag & Counter(c.replace(' ', ''))).values()), len(c)))[:8]
        return min((substring_distance(line, c) for c in ranked), default=len(line))
    lines = [run for l in truth_lines if l.strip() for run in direction_runs(' '.join(split_scripts(l).split()))]
    found = [' '.join(split_scripts(l).split()) for l in ocr_lines_text if l.strip()]
    total = sum(len(l) for l in lines)
    total_ns = sum(len(l.replace(' ', '')) for l in lines)
    errors = sum(best(l, found) for l in lines)
    errors_ns = sum(best(l.replace(' ', ''), [f.replace(' ', '') for f in found]) for l in lines)
    return round(errors / max(1, total), 4), round(errors_ns / max(1, total_ns), 4)


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
    ap.add_argument('--rescore', action='store_true', help='score the OCR already saved under OUT/LABEL again')
    args = ap.parse_args()
    from transformation.ocr.paddle_pipeline import run_ocr
    corpus = Path(args.corpus)
    prefix = next(corpus.glob('*-en-invoice.pdf')).name.split('-en-')[0]
    root = Path(args.out) / args.label
    results = {}
    for lang in args.langs.split(','):
        scan, truth = corpus / f'{prefix}-{lang}-scan.pdf', corpus / f'{prefix}-{lang}-invoice.pdf'
        folder = root / lang
        if folder.exists() and not args.rescore:
            import shutil
            shutil.rmtree(folder)
        started = time.monotonic()
        done = None
        try:
            if args.rescore:
                done = {'recognition_dir': str(next(folder.rglob('lines.jsonl')).parent)}
            else:
                for event in run_ocr(scan, folder, args.python, source_language=lang):
                    done = event
            found = ocr_text(done['recognition_dir'])
            row = score(truth_text(truth), found)
            row['cer'], row['cer_nospace'] = line_cer(truth_cells(truth),
                                                      [l.get('text', '') for l in ocr_lines(done['recognition_dir'])])
            row['review_share'] = review_share(done['recognition_dir'])
            row['kept_share'] = kept_share(done['recognition_dir'])
        except Exception as exc:  # one language failing must not hide the others
            row = {'error': f'{type(exc).__name__}: {exc}'[:300]}
        row['seconds'] = None if args.rescore else round(time.monotonic() - started, 1)
        results[lang] = row
        print(lang, json.dumps({k: v for k, v in row.items() if k != 'missed_values'}, ensure_ascii=False), flush=True)
    keys = ['char_recall', 'char_precision', 'token_recall', 'value_recall', 'cer', 'cer_nospace', 'review_share', 'kept_share']
    ok = [r for r in results.values() if 'error' not in r]
    results['_mean'] = {k: round(sum(r[k] for r in ok) / max(1, len(ok)), 4) for k in keys}
    (root / 'scores.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    print('mean', results['_mean'])


if __name__ == '__main__':
    main()
