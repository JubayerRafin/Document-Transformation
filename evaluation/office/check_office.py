"""Check Word and PowerPoint results of a run_site run (evaluation/robustness/run_site.py).

    python -m evaluation.office.check_office RESULTS CORPUS OUT.json [--pii-key KEY.json]

For every RESULTS/<plan>/<target>/<doc>/ with output.docx or output.pptx and its source in CORPUS:

  valid              the package opens, every XML part parses, python-docx / python-pptx load it
  untranslated       paragraphs whose text came back unchanged although the source has words to
                     translate (not only names, codes, numbers, e-mail addresses or links), by where they
                     stand: body (text boxes included), table, header/footer, footnote, comment, notes,
                     master/layout, chart; share of translatable paragraphs
  chart_labels       chart category and series names (the chart's own text cache) left unchanged
  overflow           boxes the engine's fitting still found too tall (RESULTS/server/<job>/.../
                     office-fitting.json, copied by evaluation/suite/translation.py collect)

With --pii-key (a PII run: <set>/runs/<label>/<doc>/output.*) it reports metadata_leaks: names of the
key's "metadata_names" (a comment's or tracked change's author, the document's author) still in any
attribute or document property of the result.
"""
import argparse
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET

from extraction.coordinator import text_regions
from transformation.office.package_ir import extract_package

C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'


def where(region_id):
    part = region_id.split(':p')[0]
    for pattern, name in (('footnotes|endnotes', 'footnote'), ('comments', 'comment'), ('header|footer', 'header/footer'),
                          ('notesSlides', 'notes'), ('slideMasters|slideLayouts|notesMasters', 'master/layout'),
                          ('charts/', 'chart')):
        if re.search(pattern, part):
            return name
    return 'body'


def _kept_names():
    """Company names the generators write: kept as written, like codes and numbers."""
    from evaluation.office.make_office import COMPANIES
    from evaluation.pii.generate import ORGS
    return sorted(set(COMPANIES) | set(ORGS), key=len, reverse=True)


KEPT = _kept_names()


def translatable(text):
    """Text with words to translate: not only company names, codes, numbers, addresses or links."""
    for name in KEPT:
        text = text.replace(name, ' ')
    words = re.findall(r'[^\W\d_]{3,}', re.sub(r'\S+@\S+|https?://\S+|www\.\S+|\b[A-Z0-9]+-[A-Z0-9-]+\b', ' ', text))
    return len(words) >= 2


def chart_cache(data):
    out = []
    with zipfile.ZipFile(BytesIO(data)) as z:
        for name in z.namelist():
            if '/charts/chart' in name and name.endswith('.xml'):
                root = ET.fromstring(z.read(name))
                for ref in root.iter('{%s}strCache' % C):
                    out += [v.text or '' for v in ref.iter('{%s}v' % C)]
    return out


def valid(data, kind):
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            for name in z.namelist():
                if name.endswith(('.xml', '.rels')):
                    ET.fromstring(z.read(name))
        if kind == 'docx':
            import docx
            docx.Document(BytesIO(data))
        else:
            import pptx
            pptx.Presentation(BytesIO(data))
        return True, ''
    except Exception as exc:  # any failure to open is the finding
        return False, f'{type(exc).__name__}: {exc}'[:200]


def check_translation(source, output, kind):
    src_regions = {r['id']: r for r in text_regions(extract_package(source, 'a.' + kind))}
    out_regions = {r['id']: r for r in text_regions(extract_package(output, 'a.' + kind))}
    total, left = {}, {}
    for rid, region in src_regions.items():
        if rid not in out_regions or not translatable(region['text']):
            continue
        place = 'table' if region.get('metadata', {}).get('inside_table') and where(rid) == 'body' else where(rid)
        total[place] = total.get(place, 0) + 1
        if ' '.join(out_regions[rid]['text'].split()) == ' '.join(region['text'].split()):
            left.setdefault(place, []).append(region['text'][:80])
    before, after = chart_cache(source), chart_cache(output)
    unchanged_labels = [a for a, b in zip(before, after) if a == b and re.search(r'[^\W\d_]{3,}', a)]
    return {'paragraphs': sum(total.values()), 'untranslated': sum(len(v) for v in left.values()),
            'untranslated_share': round(sum(len(v) for v in left.values()) / max(1, sum(total.values())), 4),
            'untranslated_by_place': {k: f'{len(left.get(k, []))}/{n}' for k, n in sorted(total.items())},
            'untranslated_examples': [t for v in left.values() for t in v][:5],
            'chart_labels': len(before), 'chart_labels_unchanged': len(unchanged_labels),
            'chart_examples': unchanged_labels[:4]}


def metadata_leaks(output, names):
    found = []
    with zipfile.ZipFile(BytesIO(output)) as z:
        for part in z.namelist():
            if not part.endswith('.xml'):
                continue
            root = ET.fromstring(z.read(part))
            values = [v for n in root.iter() for v in n.attrib.values()]
            if part.startswith('docProps/'):
                values += [t for t in root.itertext()]
            for name in names:
                if name and any(name in v for v in values):
                    found.append({'part': part, 'name': name})
    return found


def overflow_for(job_folder):
    rows = []
    for f in job_folder.rglob('office-fitting.json'):
        report = json.loads(f.read_text(encoding='utf-8'))
        rows += report.get('still_overflowing', [])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('results'); ap.add_argument('corpus'); ap.add_argument('out'); ap.add_argument('--pii-key')
    args = ap.parse_args()
    results, corpus = Path(args.results), Path(args.corpus)
    key = json.loads(Path(args.pii_key).read_text(encoding='utf-8')) if args.pii_key else None
    rows = []
    for summary_path in sorted(results.rglob('summary.json')):
        if 'server' in summary_path.parts:
            continue
        s = json.loads(summary_path.read_text(encoding='utf-8'))
        folder = summary_path.parent
        output = next((p for p in (folder / 'output.docx', folder / 'output.pptx') if p.exists()), None)
        row = {'doc': s.get('doc'), 'source': s.get('source'), 'target': s.get('target'), 'kind': s.get('kind'),
               'status': s.get('status'), 'failure_code': s.get('failure_code'), 'pages': s.get('pages'),
               'seconds_per_page': round((s.get('server_seconds') or 0) / max(1, s.get('pages') or 1), 2),
               'checks_acceptable': (s.get('checks') or {}).get('acceptable')}
        if output is None:
            rows.append(row); continue
        kind = output.suffix.lstrip('.')
        data = output.read_bytes()
        row['valid'], row['invalid_reason'] = valid(data, kind)
        sources = [p for p in corpus.rglob(f"{s.get('doc')}.*") if p.suffix.lower() in ('.docx', '.pptx', '.doc', '.ppt')]
        source = next((p for p in sources if p.suffix.lower() == '.' + kind), None)
        if key is None and source is not None and s.get('operation', 'translation') != 'pii':
            row.update(check_translation(source.read_bytes(), data, kind))
        if s.get('job'):
            row['overflow'] = len(overflow_for(results / 'server' / s['job']))
        if key and s.get('doc') in key:
            row['metadata_leaks'] = metadata_leaks(data, key[s['doc']].get('metadata_names', []))
        rows.append(row)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    done = [r for r in rows if r.get('valid') is not None]
    print(f"{len(rows)} jobs, {sum(r['status'] == 'generated' for r in rows)} generated, "
          f"{sum(bool(r.get('valid')) for r in done)} valid packages")
    if any('untranslated' in r for r in rows):
        total = sum(r.get('paragraphs', 0) for r in rows); left = sum(r.get('untranslated', 0) for r in rows)
        print(f'untranslated paragraphs {left}/{total}; chart labels unchanged '
              f"{sum(r.get('chart_labels_unchanged', 0) for r in rows)}/{sum(r.get('chart_labels', 0) for r in rows)}; "
              f"boxes still overflowing {sum(r.get('overflow', 0) for r in rows)}")
        places = {}
        for r in rows:
            for place, frac in (r.get('untranslated_by_place') or {}).items():
                a, b = map(int, frac.split('/')); x = places.setdefault(place, [0, 0]); x[0] += a; x[1] += b
        print('by place:', {k: f'{a}/{b}' for k, (a, b) in sorted(places.items())})
    if key:
        print('metadata leaks:', sum(len(r.get('metadata_leaks', [])) for r in rows))
    for r in rows:
        if r['status'] != 'generated' or r.get('valid') is False or r.get('untranslated') or r.get('metadata_leaks'):
            print(' ', r['doc'], r.get('target'), r['status'], r.get('failure_code') or '', r.get('invalid_reason') or '',
                  r.get('untranslated_by_place') or '', r.get('untranslated_examples', [])[:2])


if __name__ == '__main__':
    main()
