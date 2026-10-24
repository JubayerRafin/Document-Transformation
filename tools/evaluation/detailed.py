"""Offline review bundles and detailed scoring over the existing batch runner.

References are never passed to production inference. No server is started here.
"""
import argparse
import csv
import hashlib
import html
import json
from collections import Counter
from pathlib import Path
from tools.evaluation.dataset import registry, save, pii_scores
from extraction.coordinator import text_regions


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def evidence(config, phase):
    root = Path(config['output_root'])
    records = read(root / phase / 'index.json', {'records': []})['records']
    for document in registry(config):
        for target in (config['targets'] if phase == 'translation' else [None]):
            matches = [r for r in records if r['document_id'] == document['id'] and r.get('target') == target]
            record = matches[-1] if matches else None
            manifest = read(record['manifest'], {}) if record else {}
            if record and (record['source_sha256'] != document['sha256'] or manifest.get('source_sha256', document['sha256']) != document['sha256']):
                raise ValueError('Run/source hash mismatch: ' + document['id'])
            yield document, target, record, manifest


def make_reviews(config, phase):
    root = Path(config['output_root'])
    for document, target, record, manifest in evidence(config, phase):
        if not record or not manifest.get('source_ir'):
            continue
        run = Path(record['manifest']).parent
        source = read(manifest['source_ir'])
        regions = text_regions(source)
        mapping = read(run / 'ir-translations.json', [])
        by_id = {}
        for item in mapping:
            by_id.setdefault(item['region_id'], []).append(item)
        path = root / 'ground-truth' / (document['id'] + ('.' + target if target else '.pii') + '.json')
        if path.exists():
            previous = read(path)
            if previous['source_sha256'] != document['sha256']:
                raise ValueError('Existing review belongs to a different source: ' + str(path))
            if previous['source_ir_sha256'] != hashlib.sha256(Path(manifest['source_ir']).read_bytes()).hexdigest():
                raise ValueError('Extraction changed; preserve old reviews and use a new output_root.')
            continue  # Never overwrite human annotations.
        items = []
        for region in regions:
            from transformation.llm.protected_text import protect_values
            _, literals = protect_values(region['text'], document.get('protected_values', []))
            candidates = by_id.get(region['id'], [])
            # Only uniquely associated translations may be automatically paired.
            paired = candidates[0] if len(candidates) == 1 and candidates[0].get('translated_text') else None
            items.append({'region_id': region['id'], 'bbox': region.get('bbox'),
                'extracted_text': region['text'], 'source_reference': None, 'source_reviewed': False,
                'translation': paired['translated_text'] if paired else None,
                'association': paired.get('association') if paired else 'missing_or_ambiguous',
                'reference_translation': None, 'translation_reviewed': False,
                'errors': [], 'protected_literals': list(dict.fromkeys(literals.values())), 'pii': [], 'notes': ''})
        review = {'schema_version': 'transformx-evaluation-1', 'document_id': document['id'],
            'source_sha256': document['sha256'], 'target': target,
            'source_ir_sha256': hashlib.sha256(Path(manifest['source_ir']).read_bytes()).hexdigest(),
            'run_manifest': record['manifest'], 'regions': items,
            'output_sha256': hashlib.sha256(Path(manifest['output_file']).read_bytes()).hexdigest() if manifest.get('output_file') and Path(manifest['output_file']).exists() else None,
            'source_coverage_reviewed': False, 'missing_source_regions': [],
            'pii_ground_truth_reviewed': False, 'pii_selected_categories': config.get('pii_categories', []),
            'visual_reviewed': False, 'layout_issues': [],
            'privacy_reviewed': False, 'privacy_residuals': [], 'over_redactions': [],
            'human_accepted': False,
            'instructions': 'Verify source_reference against the ORIGINAL PDF, not OCR. PII spans use extracted_text offsets; annotate missed source entities in missing_source_regions. Translation errors use category accuracy/terminology/fluency/omission/addition/untranslated and severity minor/major/critical. Empty unreviewed fields are not perfect scores.'}
        save(path, review)
        preview = root / 'review-bundles' / document['id'] / (target or 'pii')
        preview.mkdir(parents=True, exist_ok=True)
        import pymupdf
        for label, pdf_path in [('source', document['path']), ('output', manifest.get('output_file'))]:
            if not pdf_path or not Path(pdf_path).exists(): continue
            with pymupdf.open(pdf_path) as pdf:
                for i, page in enumerate(pdf):
                    page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3), alpha=False).save(preview / f'{label}-{i+1}.png')
        with (preview / 'regions.csv').open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=['region_id', 'extracted_text', 'translation', 'association'])
            writer.writeheader()
            writer.writerows({k: item[k] for k in writer.fieldnames} for item in items)
        print('Review bundle: ' + str(path))


def edit_counts(reference, hypothesis):
    """Minimum Levenshtein edits; deterministic diagonal/delete/insert tie order."""
    previous = [(i, 0, i, 0) for i in range(len(hypothesis) + 1)]
    for i, expected in enumerate(reference, 1):
        current = [(i, 0, 0, i)]
        for j, actual in enumerate(hypothesis, 1):
            cost, s, ins, dele = previous[j-1]
            candidates = [(cost + (expected != actual), s + (expected != actual), ins, dele)]
            cost, s, ins, dele = previous[j]
            candidates.append((cost+1, s, ins, dele+1))
            cost, s, ins, dele = current[j-1]
            candidates.append((cost+1, s, ins+1, dele))
            current.append(min(candidates, key=lambda x: x[0]))
        previous = current
    edits, substitutions, insertions, deletions = previous[-1]
    return {'reference_units': len(reference), 'edits': edits, 'substitutions': substitutions,
            'insertions': insertions, 'deletions': deletions,
            'rate': edits / len(reference) if reference else (0.0 if not hypothesis else None)}


def aggregate_counts(items):
    if not items: return None
    result = {k: sum(i[k] for i in items) for k in ('reference_units', 'edits', 'substitutions', 'insertions', 'deletions')}
    result['rate'] = result['edits'] / result['reference_units'] if result['reference_units'] else (0.0 if not result['edits'] else None)
    return result


def overlapping_scores(expected, findings):
    gold = [(s['region_id'], s['start'], s['end'], s['category']) for s in expected]
    predicted = list({(f['region_id'], a, b, f['category']) for f in findings for a, b in f['spans']})
    assigned = {}
    def match(i, visited):
        g = gold[i]
        for j, p in enumerate(predicted):
            if j in visited or g[0] != p[0] or g[3] != p[3] or min(g[2], p[2]) <= max(g[1], p[1]): continue
            visited.add(j)
            if j not in assigned or match(assigned[j], visited):
                assigned[j] = i
                return True
        return False
    for i in range(len(gold)): match(i, set())
    tp = len(assigned); fp = len(predicted)-tp; fn = len(gold)-tp
    p = tp/(tp+fp) if tp+fp else (1.0 if not gold else 0.0)
    r = tp/(tp+fn) if tp+fn else 1.0
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': p, 'recall': r, 'f1': 2*p*r/(p+r) if p+r else 0.0}


def score_review(review, source, findings=(), phase='translation'):
    current = {r['id']: r['text'] for r in text_regions(source)}
    chars, words, expected = [], [], []
    errors = Counter(); severities = Counter(); preserved = total_literals = 0
    reviewed_translation = aligned = 0; metric_inputs = []; region_results=[]
    ids = set()
    for item in review['regions']:
        rid = item['region_id']
        if rid in ids or rid not in current or item['extracted_text'] != current[rid]:
            raise ValueError('Duplicate or stale review region: ' + rid)
        ids.add(rid)
        region_result={'region_id':rid,'ocr_cer':None,'ocr_wer':None,'translation_reviewed':item['translation_reviewed'],'errors':item['errors'] if item['translation_reviewed'] else None}
        if item['source_reviewed']:
            if not isinstance(item['source_reference'], str): raise ValueError('Reviewed OCR needs a source transcription.')
            # Normalize whitespace only; case and punctuation errors remain measurable.
            gold = ' '.join(item['source_reference'].split()); actual = ' '.join(item['extracted_text'].split())
            chars.append(edit_counts(gold, actual)); words.append(edit_counts(gold.split(), actual.split()))
            region_result.update(ocr_cer=chars[-1],ocr_wer=words[-1])
        if item.get('translation') is not None:
            aligned += 1
        if item['translation_reviewed']:
            reviewed_translation += 1
            for error in item['errors']:
                if error['category'] not in {'accuracy','terminology','fluency','omission','addition','untranslated'} or error['severity'] not in {'minor','major','critical'}:
                    raise ValueError('Invalid MQM-style error annotation.')
                errors[error['category']] += 1; severities[error['severity']] += 1
        if item['source_reviewed'] and item.get('translation'):
            metric_inputs.append({'region_id': rid, 'src': item['source_reference'], 'mt': item['translation'], 'ref': item.get('reference_translation')})
            for literal in item.get('protected_literals', []):
                count = item['source_reference'].count(literal)
                if not literal or not count: raise ValueError('Protected ground-truth literal absent from source.')
                total_literals += count
                preserved += count if item['translation'].count(literal) == count else 0
        for span in item.get('pii', []):
            a, b = span['start'], span['end']
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(item['extracted_text']) or item['extracted_text'][a:b] != span['value']:
                raise ValueError('Invalid ground-truth PII span: ' + rid)
            expected.append(dict(span, region_id=rid))
        region_results.append(region_result)
    result = {'ocr': {'cer': aggregate_counts(chars), 'wer': aggregate_counts(words),
        'reviewed_regions': len(chars), 'total_regions': len(current),
        'source_coverage_reviewed': review['source_coverage_reviewed'], 'missing_source_regions': review['missing_source_regions']},
        'translation': {'aligned_regions': aligned, 'total_regions': len(current), 'reviewed_regions': reviewed_translation,
            'errors_by_category': dict(errors), 'errors_by_severity': dict(severities),
            'protected_literal_occurrences': total_literals, 'preserved_occurrences': preserved,
            'preservation_rate': preserved/total_literals if total_literals else None,
            'semantic_model': None, 'semantic_scores': None},
        'visual': {'reviewed': review['visual_reviewed'], 'issues': review['layout_issues']},
        'pii': None, 'regions':region_results, 'acceptance': 'requires_review'}
    if phase == 'pii' and review['pii_ground_truth_reviewed']:
        categories = review['pii_selected_categories']
        if any(s['category'] not in categories for s in expected): raise ValueError('PII ground truth contains an unselected category.')
        selected = [f for f in findings if f['category'] in categories]
        result['pii'] = {'exact': pii_scores(expected, selected), 'overlap': overlapping_scores(expected, selected),
            'per_category': {c: pii_scores([s for s in expected if s['category']==c], [f for f in selected if f['category']==c]) for c in categories},
            'scope': 'Detection on extracted text only; image/OCR omissions require original-document review.',
            'privacy_reviewed': review['privacy_reviewed'], 'privacy_residuals': review['privacy_residuals'], 'over_redactions': review['over_redactions']}
    complete = ids == set(current) and review['source_coverage_reviewed'] and len(chars) == len(current) and review['visual_reviewed']
    if phase == 'translation': complete = complete and reviewed_translation == len(current) and aligned == len(current)
    else: complete = complete and review['pii_ground_truth_reviewed'] and review['privacy_reviewed']
    blockers = bool(review['missing_source_regions'] or review['layout_issues'] or severities['critical'] or severities['major'] or preserved < total_literals)
    if phase == 'pii': blockers = blockers or bool(review['privacy_residuals'] or review['over_redactions']) or bool(result['pii'] and result['pii']['exact']['fn'])
    if blockers: result['acceptance'] = 'requires_revision'
    elif complete and review['human_accepted']: result['acceptance'] = 'human_accepted'
    return result, metric_inputs


def group_metric_inputs(review, run):
    """Validate reviewed grouped targets against frozen engine evidence."""
    import re
    norm = lambda text: ' '.join(text.split())
    regions = {r['region_id']: r for r in review['regions']}
    targets = set()
    for path in Path(run).rglob('babeldoc-transformed-ir.json'):
        if 'figure-runs' in path.parts:
            continue
        for page in read(path)['page']:
            targets.update(norm(p.get('unicode', '')) for p in page['pdf_paragraph'])
    inputs, excluded, used = [], [], set()
    for group in review.get('translation_groups', []):
        gid = group['group_id']; ids = group['region_ids']
        if not ids or len(set(ids)) != len(ids) or any(r not in regions for r in ids) or used.intersection(ids):
            raise ValueError('Invalid or overlapping translation group: '+gid)
        used.update(ids)
        if not group.get('translation_reviewed') or any(not regions[r]['source_reviewed'] for r in ids):
            excluded.append({'group_id':gid,'reason':'Source/translation review incomplete.'}); continue
        source = norm(' '.join(regions[r]['source_reference'] for r in ids))
        if norm(group['source']) != source:
            raise ValueError('Group source does not match reviewed references: '+gid)
        target = group['observed_translation']
        if re.search(r'\{v\d+\}', target) or norm(target) not in targets:
            excluded.append({'group_id':gid,'reason':'Target is not uniquely supported as complete plain text in frozen engine IR; formula/PDF alignment needs resolution.'}); continue
        inputs.append({'region_id':gid,'src':source,'mt':target,'ref':group.get('reference_translation')})
    return inputs, excluded, used


def evaluate(config, phase, comet_model=None, gpus=0, prepare_metrics=False):
    if prepare_metrics and comet_model is None:
        comet_model = 'Unbabel/wmt22-cometkiwi-da'
    root = Path(config['output_root']); rows = []; pending = []
    for doc, target, record, manifest in evidence(config, phase):
        row = {'document': doc['id'], 'target': target, 'status': manifest.get('status', 'not_run'),
            'seconds': manifest.get('seconds'), 'stages': record.get('stage_seconds', {}) if record else {},
            'gpu_verified': record.get('gpu_verified') if record else None, 'error': manifest.get('error'),
            'output': manifest.get('output_file'), 'acceptance': 'not_run' if not record else 'requires_review'}
        path = root / 'ground-truth' / (doc['id'] + ('.' + target if target else '.pii') + '.json')
        review = read(path)
        if review and manifest.get('source_ir'):
            ir_path = Path(manifest['source_ir'])
            if review['source_sha256'] != doc['sha256'] or review['source_ir_sha256'] != hashlib.sha256(ir_path.read_bytes()).hexdigest() or review['run_manifest'] != record['manifest']:
                raise ValueError('Review provenance mismatch; generate a new review bundle for the new run.')
            if review.get('output_sha256') and (not row['output'] or hashlib.sha256(Path(row['output']).read_bytes()).hexdigest()!=review['output_sha256']):
                raise ValueError('Reviewed output changed after review bundle creation.')
            detail, inputs = score_review(review, read(ir_path), read(ir_path.parent/'pii-findings.json', []), phase)
            # Verify targets against the actual saved translation evidence, not editable review fields.
            mappings = read(ir_path.parent/'ir-translations.json', [])
            for item in review['regions']:
                if item.get('translation') is None: continue
                matches = [m for m in mappings if m['region_id']==item['region_id'] and m.get('translated_text')]
                if len(matches)!=1 or matches[0]['translated_text'] != item['translation']:
                    raise ValueError('Translation review does not match saved output evidence.')
            row['details'] = detail
            row['review_provenance'] = review.get('review_provenance', {})
            row['acceptance'] = detail['acceptance'] if manifest.get('status') == 'generated' else 'failed_not_accepted'
            checks=manifest.get('validation',{}).get('independent_checks',{})
            if manifest.get('scan_acceptance_blockers') or checks.get('geometry_blockers'):
                row['acceptance']='requires_revision' if manifest.get('status')=='generated' else 'failed_not_accepted'
            if phase == 'translation' and (comet_model or prepare_metrics):
                grouped, excluded, members = group_metric_inputs(review, Path(record['manifest']).parent)
                inputs = [x for x in inputs if x['region_id'] not in members] + grouped
                detail['translation']['semantic_excluded_groups'] = excluded
                unchanged = [x['region_id'] for x in inputs if ' '.join(x['src'].split()) == ' '.join(x['mt'].split())]
                inputs = [x for x in inputs if x['region_id'] not in unchanged]
                detail['translation']['semantic_excluded_unchanged_units'] = unchanged
                detail['translation']['semantic_eligible_units'] = len(inputs)
                reference_based = 'kiwi' not in comet_model.lower()
                eligible = [x for x in inputs if not reference_based or (isinstance(x['ref'], str) and x['ref'].strip())]
                for item in eligible: pending.append((row, item))
        row['structural_validation'] = manifest.get('validation', {})
        rows.append(row)
    destination = root / phase / 'detailed'
    if prepare_metrics:
        save(destination/'comet-inputs.json', {'model':comet_model,'pairs':[
            {'document':row['document'], **item} for row,item in pending],
            'scope':'Verified text pairs only; merged regions scored together. Exclusions are reported. No inference performed.'})
    if comet_model and pending and not prepare_metrics:
        from comet import download_model, load_from_checkpoint
        model = load_from_checkpoint(download_model(comet_model))
        batch = [{k: v for k, v in item.items() if k in ('src','mt','ref') and v is not None} for _, item in pending]
        prediction = model.predict(batch, batch_size=4, gpus=gpus)
        if len(prediction.scores) != len(pending): raise ValueError('Metric score alignment failed.')
        for (row, item), score in zip(pending, prediction.scores):
            block = row['details']['translation']; block['semantic_model'] = comet_model
            if block['semantic_scores'] is None: block['semantic_scores'] = []
            block['semantic_scores'].append({'region_id': item['region_id'], 'score': float(score)})
        for row in rows:
            block = row.get('details', {}).get('translation', {})
            if block.get('semantic_scores'):
                block['semantic_mean'] = sum(x['score'] for x in block['semantic_scores'])/len(block['semantic_scores'])
    save(destination/'report.json', {'phase': phase, 'rows': rows,
        'summary': dict(Counter(r['acceptance'] for r in rows)),
        'metric_policy': 'Whitespace-normalized Levenshtein CER/WER; case/punctuation retained. COMET scores are not percent accuracy. No automatic semantic/privacy approval.'})
    with (destination/'summary.csv').open('w', encoding='utf-8-sig', newline='') as file:
        fields = ['document','target','status','seconds','gpu_verified','acceptance','cer','wer','ocr_reviewed_regions',
                  'translation_reviewed_regions','preservation_rate','semantic_model','semantic_mean',
                  'pii_precision','pii_recall','pii_f1','error','output']
        flattened=[]
        for row in rows:
            detail=row.get('details',{}); ocr=detail.get('ocr',{}); translation=detail.get('translation',{})
            pii=(detail.get('pii') or {}).get('exact',{})
            flattened.append({**row,'cer':(ocr.get('cer') or {}).get('rate'),'wer':(ocr.get('wer') or {}).get('rate'),
                'ocr_reviewed_regions':ocr.get('reviewed_regions'),'translation_reviewed_regions':translation.get('reviewed_regions'),
                'preservation_rate':translation.get('preservation_rate'),'semantic_model':translation.get('semantic_model'),
                'semantic_mean':translation.get('semantic_mean'),**{'pii_'+k:pii.get(k) for k in ('precision','recall','f1')}})
        writer = csv.DictWriter(file, fields, extrasaction='ignore'); writer.writeheader(); writer.writerows(flattened)
    body = '<h1>TransformX '+html.escape(phase)+' evaluation</h1><p>Unreviewed results are unscored. Failed and missing runs remain visible.</p>'
    for row in rows:
        body += '<h2>'+html.escape(row['document']+' '+str(row['target'] or ''))+'</h2><pre>'+html.escape(json.dumps(row, ensure_ascii=False, indent=2))+'</pre>'
    (destination/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;max-width:1100px;margin:40px auto}pre{white-space:pre-wrap;background:#eef2f7;padding:20px}</style>'+body, encoding='utf-8')
    print(destination/'report.html')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['reviews', 'evaluate', 'prepare-metrics'])
    parser.add_argument('--config', default='configs/gpu-evaluation.local.json')
    parser.add_argument('--phase', choices=['translation','pii'], default='translation')
    parser.add_argument('--comet-model', choices=['Unbabel/wmt22-comet-da','Unbabel/wmt22-cometkiwi-da'], help='Explicit COMET checkpoint. Requires unbabel-comet; may download weights.')
    parser.add_argument('--gpus', type=int, default=0)
    args = parser.parse_args(); config = read(args.config)
    if args.action == 'reviews': make_reviews(config, args.phase)
    else: evaluate(config, args.phase, args.comet_model or ('Unbabel/wmt22-cometkiwi-da' if args.action=='prepare-metrics' else None), args.gpus, prepare_metrics=args.action=='prepare-metrics')


if __name__ == '__main__': main()
