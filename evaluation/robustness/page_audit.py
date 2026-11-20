"""Page-by-page audit of a translated PDF against its source, for long documents a person cannot read.

    python -m evaluation.robustness.page_audit SOURCE.pdf OUTPUT.pdf --source en --target ko \
        [--json audit.json] [--render DIR]

For every page it reports:
  - missing: the output page has no text although the source page has some;
  - untranslated: output lines still in the source language (for a source written in Latin letters,
    lines of at least four lowercase words; for other sources, lines mostly in the source's script);
  - collisions: output text lines that overlap each other where the source's lines did not (text drawn
    over text, the usual sign of a translation that did not fit);
  - over_pictures: output text lines over a picture, more than the source had over that picture
    (labels in a translated diagram drawn across it).
Checks compare the output with its own source page, so a busy source page is not counted against the
translation. With --render, each flagged page is saved as source|output side by side for review.
Nothing here is specific to one document.
"""
import argparse
import json
import re
from pathlib import Path
import unicodedata

import pymupdf

LATIN_WORDS = re.compile(r'\b[a-z]{2,}\b')
SCRIPTS = {'ko': 'HANGUL', 'ja': ('HIRAGANA', 'KATAKANA', 'CJK'), 'zh': 'CJK', 'ar': 'ARABIC', 'hi': 'DEVANAGARI',
           'bn': 'BENGALI'}


def text_lines(page):
    out = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).strip()
            if text:
                out.append({'text': text, 'bbox': pymupdf.Rect(line['bbox']),
                            'size': max(s['size'] for s in line['spans'])})
    return out


def pictures(page):
    boxes = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info['bbox']) & page.rect
        # A scanned page is one picture covering the page; its text is rebuilt on top on purpose.
        if r.width > 20 and r.height > 20 and r.get_area() < 0.6 * page.rect.get_area():
            boxes.append(r)
    return boxes


def overlap(a, b):
    i = a & b
    if i.is_empty:
        return 0.0
    return i.get_area() / max(1e-6, min(a.get_area(), b.get_area()))


def collisions(lines, threshold=0.3):
    found = []
    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            if a['text'] == b['text']:
                continue
            # Lines of one paragraph touch at their edges; a collision covers a real part of one line.
            if overlap(a['bbox'], b['bbox']) >= threshold and (a['bbox'] & b['bbox']).height > 0.35 * min(
                    a['bbox'].height, b['bbox'].height):
                found.append((a, b))
    return found


def in_script(text, code):
    names = SCRIPTS.get(code)
    if not names:
        return False
    names = (names,) if isinstance(names, str) else names
    letters = [c for c in text if c.isalpha()]
    hits = sum(any(unicodedata.name(c, '').startswith(n) or n in unicodedata.name(c, '') for n in names)
               for c in letters)
    return bool(letters) and hits / len(letters) > 0.5


def untranslated(line, source, target):
    text = line['text']
    if source in ('en', 'fr', 'de', 'es', 'pt') and target not in ('en', 'fr', 'de', 'es', 'pt'):
        # Web addresses, paths and e-mail addresses are kept as written; they are not prose.
        prose = re.sub(r'(?:https?://|www\.)\S+|\S+@\S+|\S*[/\\]\S+', ' ', text)
        return len(LATIN_WORDS.findall(prose)) >= 4
    if source in SCRIPTS:
        return in_script(text, source) and len(text) >= 4
    return False


def picture_share(lines, picture):
    covered = [l['bbox'] & picture for l in lines]
    return sum(r.get_area() for r in covered if not r.is_empty)


def audit_page(src, out, source, target):
    s_lines, o_lines = text_lines(src), text_lines(out)
    result = {'page': src.number + 1, 'issues': []}
    if s_lines and not o_lines:
        result['issues'].append({'kind': 'missing', 'detail': 'output page has no text'})
        return result
    left = [l['text'][:90] for l in o_lines if untranslated(l, source, target)]
    if left:
        result['issues'].append({'kind': 'untranslated', 'count': len(left), 'examples': left[:3]})
    source_pairs = len(collisions(s_lines))
    pairs = collisions(o_lines)
    if len(pairs) > source_pairs:
        result['issues'].append({'kind': 'collisions', 'count': len(pairs) - source_pairs,
                                 'examples': [[a['text'][:50], b['text'][:50]] for a, b in pairs[:3]]})
    over = []
    for picture in pictures(src):
        before, after = picture_share(s_lines, picture), picture_share(o_lines, picture)
        if after > before * 1.3 + 0.02 * picture.get_area():
            over.append({'picture': [round(v) for v in picture], 'source_text_area': round(before),
                         'output_text_area': round(after)})
    if over:
        result['issues'].append({'kind': 'over_pictures', 'count': len(over), 'examples': over[:3]})
    return result


def render(src_doc, out_doc, number, path, dpi=60):
    from PIL import Image
    images = []
    for doc in (src_doc, out_doc):
        pix = doc[number - 1].get_pixmap(dpi=dpi)
        images.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
    width = images[0].width + images[1].width + 10
    canvas = Image.new('RGB', (width, max(i.height for i in images)), 'white')
    canvas.paste(images[0], (0, 0))
    canvas.paste(images[1], (images[0].width + 10, 0))
    canvas.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source_pdf')
    parser.add_argument('output_pdf')
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--json')
    parser.add_argument('--render')
    args = parser.parse_args()
    src_doc, out_doc = pymupdf.open(args.source_pdf), pymupdf.open(args.output_pdf)
    report = {'source': args.source_pdf, 'output': args.output_pdf, 'pages': len(src_doc),
              'output_pages': len(out_doc), 'page_results': []}
    for number in range(min(len(src_doc), len(out_doc))):
        report['page_results'].append(audit_page(src_doc[number], out_doc[number], args.source, args.target))
    flagged = [p for p in report['page_results'] if p['issues']]
    kinds = {}
    for p in flagged:
        for issue in p['issues']:
            kinds.setdefault(issue['kind'], []).append(p['page'])
    report['summary'] = {'pages_flagged': len(flagged), 'by_kind': {k: len(v) for k, v in kinds.items()},
                         'pages_by_kind': kinds}
    print(f"{len(src_doc)} source pages, {len(out_doc)} output pages, {len(flagged)} flagged")
    for kind, pages in kinds.items():
        print(f"  {kind}: {len(pages)} pages: {pages[:40]}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    if args.render:
        folder = Path(args.render)
        folder.mkdir(parents=True, exist_ok=True)
        for p in flagged:
            render(src_doc, out_doc, p['page'], folder / f"page-{p['page']:03d}.png")


if __name__ == '__main__':
    main()
