"""Compare frozen PDF batches without copying output-specific human approvals."""
import argparse
from collections import Counter
import csv
import hashlib
import html
from importlib.metadata import version
import json
from pathlib import Path

import pymupdf

from tools.evaluation.layout_standard import gray, match_boxes, ssim
from tools.evaluation.structure import table_grids


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def measure(source, output, dpi):
    pages = []
    with pymupdf.open(source) as src, pymupdf.open(output) as out:
        sizes_match = len(src) == len(out)
        for n, page in enumerate(src):
            if n >= len(out):
                pages.append({'page': n + 1, 'status': 'missing_output_page'})
                continue
            result = out[n]
            sizes_match &= list(page.rect) == list(result.rect)
            grids, output_grids = table_grids(page), table_grids(result)
            categories = {'embedded_image_placements': (
                [list(i['bbox']) for i in page.get_image_info()],
                [list(i['bbox']) for i in result.get_image_info()])}
            errors = []
            if isinstance(grids, list) and isinstance(output_grids, list):
                categories['native_table_grids'] = ([t['bbox'] for t in grids], [t['bbox'] for t in output_grids])
            else:
                errors.append({'table_detection': {'source': grids, 'output': output_grids}})
            crops = []
            for i, image in enumerate(page.get_image_info()):
                box = pymupdf.Rect(image['bbox']) & page.rect & result.rect
                if not box.is_empty:
                    crops.append({'index': i, 'bbox': list(box), **ssim(gray(page, dpi, box), gray(result, dpi, box))})
            pages.append({'page': n + 1, 'status': 'measured',
                          'page_ssim': ssim(gray(page, dpi), gray(result, dpi)),
                          'image_crops': crops, 'errors': errors,
                          'geometry': {c: [match_boxes(a, b, t) for t in (.5, .75)] for c, (a, b) in categories.items()}})
        return {'source_pages': len(src), 'output_pages': len(out), 'page_sizes_match': bool(sizes_match), 'pages': pages}


def aggregates(documents):
    metrics = []
    for category in ('native_table_grids', 'embedded_image_placements'):
        for threshold in (.5, .75):
            counts = Counter()
            for doc in documents:
                for page in doc.get('measurements', {}).get('pages', []):
                    for item in page.get('geometry', {}).get(category, []):
                        if item['threshold'] == threshold:
                            counts.update({k: item[k] for k in ('tp', 'fp', 'fn')})
            tp, fp, fn = (counts[k] for k in ('tp', 'fp', 'fn'))
            metrics.append({'category': category, 'threshold': threshold, 'tp': tp, 'fp': fp, 'fn': fn,
                            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None})
    return metrics


def evaluate(baseline, candidate, destination, dpi=150):
    destination.mkdir(parents=True, exist_ok=True)
    report = {'dpi': dpi, 'versions': {p: version(p) for p in ('pymupdf', 'numpy', 'scipy', 'scikit-image')},
              'limitations': [
                  'Both batches are remeasured using identical code and settings; previous evaluations remain unchanged.',
                  'This is an observational before/after comparison. Code, GPU, OCR caches and model digests may differ; runtime is not an isolated Deep Doc benchmark.',
                  'IoU uses PDF image placements and strict-line table proxies, not independently annotated semantic layout ground truth.',
                  'SSIM includes legitimate translation changes and cannot be interpreted as accuracy or used alone to accept a document.',
                  'Baseline human reviews and COMET scores do not transfer to new outputs. New translation/CER/WER scores are pending independent alignment and review.',
                  'Extraction evidence records selected candidates; BabelDOC still uses its own geometry for PDF reconstruction.'
              ], 'batches': {}}
    snapshots = {}
    source_hashes = {}
    for label, root in [('fallback', baseline), ('deep_doc', candidate)]:
        dataset = {d['id']: d for d in read(root / 'dataset.json')['documents']}
        index_path = root / 'translation/index.json'
        index = read(index_path)
        snapshots[str(index_path)] = digest(index_path)
        old_report = root / 'translation/detailed/report.json'
        prior = {r['document']: r for r in read(old_report)['rows']} if old_report.exists() else {}
        if old_report.exists():
            snapshots[str(old_report)] = digest(old_report)
        docs = []
        for record in index['records']:
            name = record['document_id']
            source = dataset[name]
            key = (name, record['target'])
            actual = digest(source['path'])
            if actual != source['sha256'] or actual != record['source_sha256']:
                raise ValueError('Source hash mismatch: ' + name)
            if key in source_hashes and source_hashes[key] != actual:
                raise ValueError('Sources differ between batches: ' + name)
            source_hashes[key] = actual
            manifest_path = Path(record['manifest'])
            manifest = read(manifest_path)
            snapshots[str(manifest_path)] = digest(manifest_path)
            doc = {'document': name, 'target': record['target'], 'source_sha256': actual,
                   'status': record['status'], 'seconds': manifest['seconds'], 'gpu_verified': record.get('gpu_verified'),
                   'device_evidence': record.get('device_evidence'), 'stage_seconds': record.get('stage_seconds'),
                   'manifest': str(manifest_path), 'output': manifest.get('output_file'),
                   'scan_blockers': manifest.get('scan_acceptance_blockers', []),
                   'translation_errors': manifest.get('translation_errors'),
                   'layout_candidates': manifest.get('validation', {}).get('independent_checks', {}).get('layout_review_candidates', []),
                   'prior_review': prior.get(name)}
            ir_path = manifest.get('source_ir')
            if ir_path:
                ir = read(ir_path)
                doc['extraction_backend'] = ir.get('backend')
                doc['extraction_warnings'] = ir.get('warnings')
            evidence = manifest_path.parent / 'deep-doc/extraction-evidence.json'
            doc['extraction_evidence'] = read(evidence) if evidence.exists() else None
            if record['status'] == 'generated' and doc['output']:
                doc['output_sha256'] = digest(doc['output'])
                snapshots[doc['output']] = doc['output_sha256']
                doc['measurements'] = measure(source['path'], doc['output'], dpi)
            docs.append(doc)
            print('Measured', label, name, flush=True)
        report['batches'][label] = {'root': str(root), 'configuration_sha256': index['configuration_sha256'],
                                  'phase_complete': index['phase_complete'], 'documents': docs,
                                  'total_seconds': sum(d['seconds'] for d in docs), 'geometry': aggregates(docs)}
    if {(d['document'], d['target']) for d in report['batches']['fallback']['documents']} != {(d['document'], d['target']) for d in report['batches']['deep_doc']['documents']}:
        raise ValueError('Batch document sets differ.')
    report['preserved_inputs'] = [{'path': p, 'sha256': h, 'unchanged': digest(p) == h} for p, h in snapshots.items()]
    if not all(p['unchanged'] for p in report['preserved_inputs']):
        raise ValueError('Input changed during evaluation.')
    (destination / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = []
    body = '<h1>Fallback vs Deep Doc: saved translation batches</h1><p>Automatic comparison; new outputs have not been human approved.</p>'
    body += '<ul>' + ''.join('<li>' + html.escape(s) + '</li>' for s in report['limitations']) + '</ul>'
    for label, batch in report['batches'].items():
        body += '<h2>' + label + '</h2><p>Total processing: %.1f seconds</p>' % batch['total_seconds']
        body += '<h3>Aggregate geometric matching</h3><pre>' + html.escape(json.dumps(batch['geometry'], indent=2)) + '</pre>'
        body += '<table><tr><th>PDF</th><th>Seconds</th><th>Mean page SSIM</th><th>Lowest image crop SSIM</th><th>Scan blockers</th><th>Layout candidates</th></tr>'
        for doc in batch['documents']:
            pages = doc.get('measurements', {}).get('pages', [])
            scores = [p['page_ssim']['score'] for p in pages if p.get('page_ssim', {}).get('score') is not None]
            crops = [c['score'] for p in pages for c in p.get('image_crops', []) if c.get('score') is not None]
            row = {'batch': label, 'document': doc['document'], 'seconds': doc['seconds'],
                   'mean_page_ssim': sum(scores) / len(scores) if scores else None,
                   'lowest_image_crop_ssim': min(crops) if crops else None,
                   'scan_blockers': len(doc['scan_blockers']), 'layout_candidates': len(doc['layout_candidates'])}
            rows.append(row)
            body += '<tr>' + ''.join('<td>' + html.escape(str(row[k])) + '</td>' for k in list(row)[1:]) + '</tr>'
        body += '</table>'
    with (destination / 'summary.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    (destination / 'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;max-width:1100px;margin:32px auto}table{border-collapse:collapse}th,td{border:1px solid #bbc;padding:10px}pre{background:#eef2f7;padding:15px}</style>' + body, encoding='utf-8')
    print(destination / 'report.html')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dpi', type=int, default=150)
    args = parser.parse_args()
    if not 72 <= args.dpi <= 300:
        parser.error('DPI must be between 72 and 300.')
    evaluate(args.baseline, args.candidate, args.output, args.dpi)
