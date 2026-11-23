"""Compare two runs of the same documents, to catch a fix that makes earlier results worse.

    python -m evaluation.robustness.compare_runs BEFORE_DIR AFTER_DIR CORPUS_DIR --plan to-en|from-en \
        [--render DIR] [--json FILE]

BEFORE_DIR and AFTER_DIR are run_site results folders (RESULTS/<plan>/<target>/<doc>/output.pdf). For
every document in both, it compares:
  - status: generated before and failed after is a regression;
  - checker problems (check_outputs: missing values, other scripts, untranslated sentences, meaning);
  - page audit problems (page_audit: missing text, untranslated lines, collisions, text over pictures);
  - how much of each page changed (share of pixels that differ at 40 dpi).
A document is "worse" when it has a checker or audit problem after that it did not have before, or
failed. With --render, every changed page is saved as before|after side by side for review.
"""
import argparse
import json
from pathlib import Path

import pymupdf


def outputs(root, plan):
    found = {}
    for path in Path(root, plan).glob('*/*/output.pdf'):
        found[(path.parent.parent.name, path.parent.name)] = path
    return found


def checker_problems(output, source_pdf, source, target):
    from evaluation.robustness import check_outputs
    try:
        row = check_outputs.check_document(output, source_pdf, source=source, target=target)
    except Exception as error:
        return {'checker_error': str(error)[:120]}
    return {key: value for key, value in row.get('issues', {}).items()} if isinstance(row, dict) else {}


def audit_problems(source_pdf, output, source, target):
    from evaluation.robustness.page_audit import audit_page
    src, out = pymupdf.open(source_pdf), pymupdf.open(output)
    problems = {}
    if len(src) != len(out):
        problems['page_count'] = f'{len(src)} -> {len(out)}'
    for number in range(min(len(src), len(out))):
        for issue in audit_page(src[number], out[number], source, target)['issues']:
            problems.setdefault(issue['kind'], []).append(number + 1)
    return problems


def changed_pages(before, after, dpi=40):
    a, b = pymupdf.open(before), pymupdf.open(after)
    changes = []
    for number in range(min(len(a), len(b))):
        x, y = a[number].get_pixmap(dpi=dpi), b[number].get_pixmap(dpi=dpi)
        if (x.width, x.height) != (y.width, y.height):
            changes.append((number + 1, 1.0))
            continue
        differ = sum(1 for i in range(0, len(x.samples), 3) if x.samples[i:i + 3] != y.samples[i:i + 3])
        share = differ / max(1, x.width * x.height)
        if share > 0.001:
            changes.append((number + 1, round(share, 4)))
    return changes


def side_by_side(before, after, number, path, dpi=60):
    from PIL import Image
    images = []
    for name in (before, after):
        pix = pymupdf.open(name)[number - 1].get_pixmap(dpi=dpi)
        images.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
    canvas = Image.new('RGB', (images[0].width + images[1].width + 10, max(i.height for i in images)), 'white')
    canvas.paste(images[0], (0, 0))
    canvas.paste(images[1], (images[0].width + 10, 0))
    canvas.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('corpus')
    parser.add_argument('--plan', required=True, choices=['to-en', 'from-en'])
    parser.add_argument('--render')
    parser.add_argument('--json')
    args = parser.parse_args()
    manifest = json.loads(Path(args.corpus, 'manifest.json').read_text(encoding='utf-8'))
    languages = {Path(d['file']).stem: d['lang'] for d in manifest['documents']}
    before, after = outputs(args.before, args.plan), outputs(args.after, args.plan)
    rows, worse = [], []
    for key in sorted(set(before) | set(after)):
        target, doc = key
        source = languages.get(doc, 'en')
        source_pdf = Path(args.corpus, doc + '.pdf')
        row = {'target': target, 'doc': doc}
        if key not in after:
            row['status'] = 'failed after' if key in before else 'missing'
            worse.append(row) if key in before else None
            rows.append(row)
            continue
        if key not in before:
            row['status'] = 'new or fixed'
            rows.append(row)
            continue
        old = audit_problems(source_pdf, before[key], source, target)
        new = audit_problems(source_pdf, after[key], source, target)
        added = {k: v for k, v in new.items() if k not in old or (isinstance(v, list) and len(v) > len(old[k]))}
        removed = {k: v for k, v in old.items() if k not in new}
        row.update(audit_added=added, audit_removed=removed, changed_pages=changed_pages(before[key], after[key]))
        if added:
            worse.append(row)
        if args.render and row['changed_pages']:
            folder = Path(args.render)
            folder.mkdir(parents=True, exist_ok=True)
            for number, _ in row['changed_pages']:
                side_by_side(before[key], after[key], number, folder / f'{target}-{doc}-p{number}.png')
        rows.append(row)
    changed = [r for r in rows if r.get('changed_pages')]
    print(f'{len(rows)} documents compared, {len(changed)} with changed pages, {len(worse)} worse')
    for row in worse:
        print('  WORSE', row['target'], row['doc'], row.get('status', ''), row.get('audit_added', ''))
    for row in rows:
        if row.get('audit_removed'):
            print('  better', row['target'], row['doc'], row['audit_removed'])
    if args.json:
        Path(args.json).write_text(json.dumps({'rows': rows, 'worse': worse}, ensure_ascii=False, indent=1),
                                   encoding='utf-8')


if __name__ == '__main__':
    main()
