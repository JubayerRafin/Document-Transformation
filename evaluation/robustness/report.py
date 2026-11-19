"""Summarise mass-test runs into one folder: a results table, the problems per document, and sample
source/output pairs with side-by-side images for reading.

    python -m evaluation.robustness.report OUT_DIR --run LABEL=RESULTS:SOURCES:TARGET:SOURCE_LANG ...
        [--before LABEL=RESULTS] [--samples 3]

RESULTS is a pod_samples output folder, SOURCES the folder of source PDFs. With --before, the same
documents from an earlier run are counted too, for a before/after table.
"""
import argparse
import json
import shutil
from pathlib import Path

from evaluation.robustness.check_outputs import check, problems, side_by_side


def summarise(rows):
    generated = sum(r['status'] == 'generated' for r in rows)
    clean = sum(not problems(r) for r in rows)
    seconds = [r['seconds'] for r in rows if r.get('seconds')]
    return {'documents': len(rows), 'generated': generated, 'without_problems': clean,
            'median_seconds': round(sorted(seconds)[len(seconds) // 2], 1) if seconds else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out')
    parser.add_argument('--run', action='append', default=[], help='LABEL=RESULTS:SOURCES:TARGET:SOURCE_LANG')
    parser.add_argument('--before', action='append', default=[], help='LABEL=RESULTS of an earlier run')
    parser.add_argument('--samples', type=int, default=3, help='source/output pairs copied per group')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    before = dict(item.split('=', 1) for item in args.before)
    report = []
    for item in args.run:
        label, spec = item.split('=', 1)
        results, sources, target, source = spec.split(':')
        rows = check(results, sources, target, source)
        entry = {'group': label, 'target': target, 'source': source, **summarise(rows),
                 'documents_with_problems': [{'doc': r['doc'], 'problems': problems(r)} for r in rows if problems(r)]}
        if label in before and Path(before[label]).is_dir():
            earlier = [r for r in check(before[label], sources, target, source)
                       if r['doc'] in {row['doc'] for row in rows}]
            entry['before'] = summarise(earlier)
        # Sample pairs: problem-free documents first (what good output looks like), then problems.
        ordered = sorted((r for r in rows if r['status'] == 'generated'), key=lambda r: (bool(problems(r)), r['doc']))
        picks = ordered[:args.samples] + [r for r in ordered if problems(r)][:1]
        folder = out / label
        for row in picks:
            name = row['doc']
            source_pdf = Path(sources) / f'{name}.pdf'
            result = Path(results) / name
            output = result / ('redacted.pdf' if (result / 'redacted.pdf').exists() else 'output.pdf')
            if not source_pdf.exists() or not output.exists():
                continue
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_pdf, folder / f'{name}.source.pdf')
            shutil.copy2(output, folder / f'{name}.{target}.pdf')
            side_by_side(source_pdf, output, folder, name, pages=1, dpi=70)
        entry['samples'] = sorted(p.name for p in folder.glob('*')) if folder.exists() else []
        report.append(entry)
        print(f"{label}: generated {entry['generated']}/{entry['documents']}, without problems "
              f"{entry['without_problems']}" + (f" (before: {entry['before']['generated']} generated, "
                                                f"{entry['before']['without_problems']} without problems)"
                                                if 'before' in entry else ''))
    (out / 'report.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
