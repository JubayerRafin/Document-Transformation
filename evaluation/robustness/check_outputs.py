"""Check a folder of results from evaluation/pii/pod_samples.py, for any PDFs and any language.

    python -m evaluation.robustness.check_outputs RESULTS_DIR SOURCES_DIR --target ko
        [--source en] [--review DIR] [--json FILE]

Per document: whether it was generated (else its failure code and reason), page count, protected
values found in the source (the engine's own literal detector: codes, emails, dates, amounts, long
numbers) and whether each is still in the output, letters in a script that is neither the source's
nor the target's, source sentences left untranslated, replacement characters (missing glyphs) and
text placed outside the page. Nothing here knows any particular test file. With --review, writes
side-by-side images (source | output) of the first pages for reading the results.
"""
import argparse
import glob
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

SCRIPTS = {'ko': {'HANGUL', 'CJK'}, 'ja': {'HIRAGANA', 'KATAKANA', 'CJK'}, 'zh': {'CJK'}, 'ar': {'ARABIC'},
           'hi': {'DEVANAGARI'}, 'bn': {'BENGALI'}}
ALL = ('HANGUL', 'HIRAGANA', 'KATAKANA', 'CJK', 'ARABIC', 'DEVANAGARI', 'BENGALI', 'CYRILLIC', 'GREEK', 'HEBREW', 'THAI')
PUNCTUATION = set('।॥')  # danda: the full stop of Hindi and Bengali alike


def script(c):
    if c in PUNCTUATION:
        return None
    name = unicodedata.name(c, '')
    if 'CJK' in name and ('IDEOGRAPH' in name or 'UNIFIED' in name):
        return 'CJK'
    for s in ALL:
        if name.startswith(s):
            return s
    return None


def normal(text):
    """Extraction-only differences: non-breaking spaces and hyphens from some CJK fonts, all whitespace."""
    return re.sub(r'\s+', '', text.replace(' ', ' ').replace('‑', '-').replace('‐', '-'))


def text_of(path):
    with pymupdf.open(path) as doc:
        return [page.get_text() for page in doc]


def significant(literal):
    """Values whose exact survival matters and that a translation never needs to rewrite."""
    if '@' in literal:
        return True
    letters, digits = any(c.isalpha() for c in literal), sum(c.isdigit() for c in literal)
    if letters and digits:
        return digits >= 2 and len(literal) >= 5 and not re.fullmatch(r'[A-Za-z]\d', literal)
    return digits >= 4


def literals(text):
    from transformation.llm.protected_text import protect_values
    _, tokens = protect_values(text, [])
    return Counter(v for v in tokens.values() if significant(v))


def untranslated_lines(source_pages, output_text, source):
    """Source lines of ordinary text (5+ words) that appear unchanged in the output."""
    if source not in ('en', 'fr', 'de', 'es', 'pt'):
        return []
    out = normal(output_text)
    lines = []
    for page in source_pages:
        for line in page.splitlines():
            # Ordinary prose: at least five lower-case words. Names, e-mail lines and commands
            # ("sudo setup --device /dev/ttyUSB0") correctly stay as they are.
            words = re.findall(r'(?<![\w@./-])[a-z]{3,}(?![\w@./-])', line)
            if re.search(r'(?:^|\s)--?\w|/\w+/|\bsudo\b', line):
                continue
            if len(words) >= 5 and normal(line) in out:
                lines.append(line.strip())
    return lines


def off_page_spans(path):
    count = 0
    with pymupdf.open(path) as doc:
        for page in doc:
            area = page.rect + (-3, -3, 3, 3)
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        if span['text'].strip() and not area.contains(pymupdf.Rect(span['bbox'])):
                            count += 1
    return count


def side_by_side(source, output, target_dir, stem, pages=2, dpi=60):
    from PIL import Image
    target_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        for index in range(min(pages, len(a), len(b))):
            images = []
            for doc in (a, b):
                pix = doc[index].get_pixmap(dpi=dpi)
                images.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
            sheet = Image.new('RGB', (images[0].width + images[1].width + 12, max(i.height for i in images)), 'white')
            sheet.paste(images[0], (0, 0))
            sheet.paste(images[1], (images[0].width + 12, 0))
            path = target_dir / f'{stem}-p{index + 1}.png'
            sheet.save(path)
            written.append(str(path))
    return written


def check(results, sources, target, source='en', review=None):
    rows = []
    for summary_path in sorted(Path(results).glob('*/summary.json')):
        folder = summary_path.parent
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        name = summary.get('doc') or folder.name
        source_pdf = next(iter(sorted(Path(sources).glob(glob.escape(name) + '.pdf'))), None)
        row = {'doc': name, 'status': summary.get('status'), 'failure_code': summary.get('failure_code'),
               'error': (summary.get('error') or '')[:300], 'seconds': summary.get('wall_seconds')}
        output = folder / ('redacted.pdf' if (folder / 'redacted.pdf').exists() else 'output.pdf')
        if row['status'] != 'generated' or not output.exists() or source_pdf is None:
            rows.append(row)
            continue
        source_pages = text_of(source_pdf)
        if not ''.join(source_pages).strip():
            # A scan: the values come from the engine's own OCR rebuild of it, if it kept one.
            rebuilt = sorted(folder.glob('job/engine/*/ocr-nativeized.pdf'))
            source_pages = text_of(rebuilt[0]) if rebuilt else []
            row['source_text'] = 'ocr' if rebuilt else 'none'
        output_pages = text_of(output)
        output_text = '\n'.join(output_pages)
        expected = literals('\n'.join(source_pages))
        found = normal(output_text)
        missing = {}
        with pymupdf.open(output) as doc:
            for value, count in expected.items():
                # Text order can interleave characters placed one by one, so positions are searched too.
                hits = max(found.count(normal(value)), sum(len(page.search_for(value)) for page in doc))
                if hits < count:
                    missing[value] = count
        # Scripts the source itself uses (a Greek letter in a formula) are not foreign in the output.
        allowed = SCRIPTS.get(target, set()) | SCRIPTS.get(source, set()) | {script(c) for c in '\n'.join(source_pages)} - {None}
        foreign = [c for c in output_text if script(c) and script(c) not in allowed]
        row.update(pages=[len(source_pages) or None, len(output_pages)], values=sum(expected.values()),
                   values_missing=missing, foreign_letters=''.join(foreign[:20]), foreign_count=len(foreign),
                   untranslated=untranslated_lines(source_pages, output_text, source) if target != source else [],
                   replacement_chars=output_text.count('�'), off_page_spans=off_page_spans(output))
        if review:
            row['review_images'] = side_by_side(source_pdf, output, Path(review), name)
        rows.append(row)
    return rows


def problems(row):
    found = []
    if row['status'] != 'generated':
        found.append(f"{row['status']} {row.get('failure_code') or ''} {row.get('error', '')[:120]}".strip())
        return found
    if row.get('pages') and row['pages'][0] and row['pages'][0] != row['pages'][1]:
        found.append(f"pages {row['pages'][0]} -> {row['pages'][1]}")
    if row.get('values_missing'):
        found.append('values missing: ' + ', '.join(f'{k} ({v})' for k, v in list(row['values_missing'].items())[:6]))
    if row.get('foreign_count'):
        found.append(f"{row['foreign_count']} letters of another script: {row['foreign_letters']}")
    if row.get('untranslated'):
        found.append(f"{len(row['untranslated'])} lines untranslated, e.g. {row['untranslated'][0][:70]!r}")
    if row.get('replacement_chars'):
        found.append(f"{row['replacement_chars']} replacement characters")
    if row.get('off_page_spans'):
        found.append(f"{row['off_page_spans']} text spans outside the page")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('results')
    parser.add_argument('sources')
    parser.add_argument('--target', required=True)
    parser.add_argument('--source', default='en')
    parser.add_argument('--review')
    parser.add_argument('--json')
    args = parser.parse_args()
    rows = check(args.results, args.sources, args.target, args.source, args.review)
    generated = sum(r['status'] == 'generated' for r in rows)
    clean = 0
    for row in rows:
        issues = problems(row)
        clean += not issues
        print(f"{row['doc']}: {'OK' if not issues else '; '.join(issues)}")
    print(f'== {args.target}: generated {generated}/{len(rows)}, without problems {clean}/{len(rows)}')
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
