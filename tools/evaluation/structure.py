"""Read-only extraction/reconstruction audit of registered saved PDF runs."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re
import pymupdf


def normalized(value):
    if isinstance(value, float): return round(value, 2)
    if isinstance(value, (pymupdf.Point, pymupdf.Rect)): return [round(v,2) for v in value]
    if isinstance(value, pymupdf.Quad): return [normalized(p) for p in value]
    if isinstance(value, (list,tuple)): return [normalized(v) for v in value]
    return value


def drawing_signatures(page):
    return Counter(json.dumps(normalized([d.get('items'),d.get('fill'),d.get('color'),d.get('width')]),sort_keys=True)
                   for d in page.get_drawings())


def overlap(a,b):
    a,b=pymupdf.Rect(a),pymupdf.Rect(b)
    return (a&b).get_area()/a.get_area() if a.get_area()>0 else 0


def table_grids(page):
    try:
        return [{'bbox':list(t.bbox),'rows':t.row_count,'columns':t.col_count}
                for t in page.find_tables(strategy='lines_strict').tables]
    except Exception as exc: return {'error':str(exc)}


def evaluate(config):
    root=Path(config['output_root']); destination=root/'extraction-reconstruction'; destination.mkdir(exist_ok=True)
    documents={d['id']:d for d in json.loads((root/'dataset.json').read_text(encoding='utf-8'))['documents']}
    records=json.loads((root/'translation/index.json').read_text(encoding='utf-8'))['records']
    rows=[]
    for record in records:
        doc=documents[record['document_id']]; manifest=json.loads(Path(record['manifest']).read_text(encoding='utf-8'))
        review=json.loads((root/'ground-truth'/f"{doc['id']}.{record['target']}.json").read_text(encoding='utf-8'))
        ir_path=Path(manifest['source_ir']); ir=json.loads(ir_path.read_text(encoding='utf-8'))
        output=Path(manifest['output_file'])
        if hashlib.sha256(Path(doc['path']).read_bytes()).hexdigest()!=doc['sha256'] or hashlib.sha256(ir_path.read_bytes()).hexdigest()!=review['source_ir_sha256'] or hashlib.sha256(output.read_bytes()).hexdigest()!=review['output_sha256']:
            raise ValueError('Changed source/IR/output: '+doc['id'])
        from tools.evaluation.detailed import score_review
        metrics,_=score_review(review,ir)
        row={'document':doc['id'],'source':doc['path'],'output':str(output),
             'review_provenance':review.get('review_provenance'),
             'extraction':{'text_regions_reviewed':metrics['ocr']['reviewed_regions'],
                 'recorded_backend':ir.get('backend'), 'recorded_backend_warnings':ir.get('warnings',[]),
                 'text_regions_total':metrics['ocr']['total_regions'],
                 'character_error_counts':metrics['ocr']['cer'],'word_error_counts':metrics['ocr']['wer'],
                 'coverage_findings':review.get('missing_source_regions',[]),
                 'unresolved_transcription':review.get('review_provenance',{}).get('pending_human_transcription_regions',[]),
                 'reading_order':'Not numerically scored: line sorting does not establish semantic reading order.',
                 'pages':[]},
             'reconstruction':{'pages':[],'human_verified_layout_findings':review.get('layout_issues',[])}}
        with pymupdf.open(doc['path']) as source,pymupdf.open(output) as result:
            row['reconstruction']['opens']=True
            row['reconstruction']['page_count_matches']=len(source)==len(result)
            for n,sp in enumerate(source):
                unit=next((u for u in ir['units'] if u['number']==n+1),None)
                regions=unit['regions'] if unit else []
                figures=[r for r in regions if r['kind']=='figure']
                tables=[r for r in regions if r['kind']=='table']
                images=sp.get_image_info(hashes=True)
                native=sp.get_text().strip()
                extraction={'page':n+1,'route':'native_text' if native else 'OCR',
                    'region_counts':dict(Counter(r['kind'] for r in regions)),
                    'figure_locations':len(figures),
                    'figure_asset_paths':sum(bool(r.get('asset')) for r in figures),
                    'figure_asset_files_exist':sum(bool(r.get('asset')) and Path(r['asset']).is_file() for r in figures),
                    'source_embedded_image_occurrences':len(images),
                    'embedded_images_localized':sum(any(overlap(im['bbox'],r['bbox'])>=.95 for r in figures) for im in images) if native else None,
                    'table_ir_grids':[{'bbox':r['bbox'],'rows':len(r['cells']),'columns':max((len(c) for c in r['cells']),default=0)} for r in tables],
                    'source_strict_line_grids':table_grids(sp)}
                if native:
                    gold=Counter(re.findall(r'\S+',native))
                    actual=Counter()
                    for r in review['regions']:
                        if r['region_id'].startswith(f'p{n+1}_'):
                            actual.update(re.findall(r'\S+',r['extracted_text']))
                    extraction['native_text_token_recall_proxy']={'matched':sum((gold&actual).values()),'expected':sum(gold.values()),
                        'rate':sum((gold&actual).values())/sum(gold.values()) if gold else None,
                        'scope':'Token multiset compared with original selectable text. Not OCR accuracy, reading order, or image-text coverage.'}
                row['extraction']['pages'].append(extraction)
                if n>=len(result):continue
                op=result[n]; out_images=op.get_image_info(hashes=True)
                matched=sum(any(im.get('digest')==oi.get('digest') and overlap(im['bbox'],oi['bbox'])>=.99 for oi in out_images) for im in images)
                original_paths=drawing_signatures(sp); rendered_paths=drawing_signatures(op)
                outside=[]; sizes=[]
                for b in op.get_text('dict')['blocks']:
                    for line in b.get('lines',[]):
                        for span in line['spans']:
                            sizes.append(span['size'])
                            box=pymupdf.Rect(span['bbox'])
                            if box.x0<-.5 or box.y0<-.5 or box.x1>op.rect.width+.5 or box.y1>op.rect.height+.5:
                                outside.append({'text':span['text'],'bbox':list(box)})
                row['reconstruction']['pages'].append({'page':n+1,'page_size_matches':list(sp.rect)==list(op.rect),
                    'source_images':len(images),'output_images':len(out_images),'stored_original_images_retained_at_position':matched,
                    'image_retention_scope':'Underlying image content only. White overlays can obscure retained images; this is NOT visual preservation accuracy.',
                    'source_vector_paths':sum(original_paths.values()),'matching_vector_paths':sum((original_paths&rendered_paths).values()),
                    'vector_scope':'Rounded path geometry, fill, stroke and width. Does not test visibility or text font fidelity.',
                    'output_strict_line_grids':table_grids(op),'outside_page_spans':outside,
                    'minimum_output_font_size':min(sizes) if sizes else None,
                    'automatic_layout_candidates':manifest.get('validation',{}).get('independent_checks',{}).get('layout_review_candidates',[]) if n==0 else []})
        row['reconstruction']['review_status']='major_defects_confirmed' if any(i.get('severity') in {'major','critical'} for i in review.get('layout_issues',[])) else 'minor_fidelity_defects_confirmed' if review.get('layout_issues') else 'requires_review'
        rows.append(row)
    report={'scope':'Frozen six-PDF baseline. No rerun, no production fixes, no PII evaluation, no invented extraction accuracy.',
            'limitations':['Native-text CER/WER is transcription agreement, not OCR accuracy. Only Sample01 is a full scanned-page OCR test.',
                          'Source image counts are embedded raster occurrences, not semantic figures. Vector icons require separate analysis.',
                          'Strict-line table detection is a geometry proxy, not independent table ground truth or a semantic table F1 score.',
                          'Stored-image/path retention does not prove visible preservation. Human-confirmed page findings take precedence.',
                          'Clipping within regions, font/emphasis losses, semantic reading order and image OCR coverage require visual/reference review.'],
            'documents':rows}
    (destination/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    body='<h1>Extraction and reconstruction evaluation</h1><p>'+html.escape(report['scope'])+'</p>'
    body+='<p><strong>Baseline limitation:</strong> inspect recorded backend warnings below. Native fallback results do not validate Deep Doc enrichment or exported image assets.</p>'
    body+='<table><tr><th>Document</th><th>Reviewed text</th><th>CER / WER</th><th>Figure locations / asset paths</th><th>Page size/count</th><th>Visual findings</th></tr>'
    for row in rows:
        e,r=row['extraction'],row['reconstruction']
        cer=(e['character_error_counts'] or {}).get('rate');wer=(e['word_error_counts'] or {}).get('rate')
        rate=lambda v:'pending' if v is None else f'{100*v:.3f}%'
        cells=[row['document'],f"{e['text_regions_reviewed']}/{e['text_regions_total']}",rate(cer)+' / '+rate(wer),
               str(sum(p['figure_locations'] for p in e['pages']))+' / '+str(sum(p['figure_asset_paths'] for p in e['pages'])),
               'match' if r['page_count_matches'] and all(p['page_size_matches'] for p in r['pages']) else 'mismatch',r['review_status']]
        body+='<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in cells)+'</tr>'
    body+='</table><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in report['limitations'])+'</ul>'
    for row in rows:
        body+='<h2>'+row['document']+'</h2><pre>'+html.escape(json.dumps(row,ensure_ascii=False,indent=2))+'</pre>'
    (destination/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;max-width:1100px;margin:32px auto}pre{white-space:pre-wrap;background:#eef2f7;padding:20px}table{border-collapse:collapse}td,th{border:1px solid #ccd;padding:10px;text-align:left}</style>'+body,encoding='utf-8')
    print(destination/'report.html')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();evaluate(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))
