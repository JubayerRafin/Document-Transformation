"""Layout-only report over the frozen baseline and confirmed visual findings."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path


def grid_matches(source, output, tolerance=1.0):
    """Match each detected grid once by shape and coordinates."""
    used=set(); matched=0
    for table in source:
        for i,other in enumerate(output):
            if i not in used and table['rows']==other['rows'] and table['columns']==other['columns'] and all(abs(a-b)<=tolerance for a,b in zip(table['bbox'],other['bbox'])):
                used.add(i); matched+=1; break
    return matched


def evaluate(config):
    root=Path(config['output_root'])
    data=json.loads((root/'extraction-reconstruction/report.json').read_text(encoding='utf-8'))
    destination=root/'layout-reconstruction';destination.mkdir(exist_ok=True)
    rows=[]; total_pages=matching_sizes=inside_pages=expected_grids=matched_grids=0
    for document in data['documents']:
        name=document['document']
        review=json.loads((root/'ground-truth'/f'{name}.ko.json').read_text(encoding='utf-8'))
        if hashlib.sha256(Path(document['output']).read_bytes()).hexdigest()!=review['output_sha256']:
            raise ValueError('Output changed: '+name)
        row={'document':name,'reviewer':review.get('review_provenance',{}),
             'opens':document['reconstruction']['opens'],
             'page_count_matches':document['reconstruction']['page_count_matches'],
             'pages':[], 'visual_findings':[], 'excluded_semantic_findings':[]}
        for issue in review.get('layout_issues',[]):
            finding=dict(issue)
            if 'Unsupported tiny text' in issue['note']:
                row['excluded_semantic_findings'].append({**finding,'reason':'Added content belongs to translation/content-integrity evaluation, not layout preservation.'})
                continue
            if 'warning-label body and app screenshot remain English' in issue['note']:
                finding['note']='Badge text below its heading is blurred/too small. Image text legibility is reduced.'
                finding['scope_note']='Untranslated warning-label and app-console text is evaluated separately as translation coverage.'
            row['visual_findings'].append(finding)
        for source,output in zip(document['extraction']['pages'],document['reconstruction']['pages']):
            grids=source['source_strict_line_grids']; reconstructed=output['output_strict_line_grids']
            table_result={'status':'not_measured','reason':'Strict-line detector unavailable or raster-only table; no numerical grid score.'}
            if isinstance(grids,list) and isinstance(reconstructed,list) and grids:
                count=grid_matches(grids,reconstructed)
                table_result={'status':'measured','source_grids':len(grids),'matched_grids':count,'output_grids':len(reconstructed),
                    'scope':'Detected native grids: same rows/columns and bbox within 1 PDF point. Not cell text/merged-cell semantics.'}
                expected_grids+=len(grids);matched_grids+=count
            elif isinstance(grids,list) and not grids and not source['table_ir_grids']:
                table_result={'status':'not_applicable','reason':'No source table detected or represented in IR.'}
            total_pages+=1;matching_sizes+=bool(output['page_size_matches']);inside_pages+=not output['outside_page_spans']
            row['pages'].append({'page':output['page'],'page_size_matches':output['page_size_matches'],
                'outside_page_spans':output['outside_page_spans'],'table_grid':table_result,
                'source_vector_paths':output['source_vector_paths'],'matching_vector_paths':output['matching_vector_paths'],
                'minimum_output_font_size':output['minimum_output_font_size'],
                'preview_source':f'../review-bundles/{name}/ko/source-{output["page"]}.png',
                'preview_output':f'../review-bundles/{name}/ko/output-{output["page"]}.png'})
        row['status']='major_layout_defects' if any(i['severity'] in {'major','critical'} for i in row['visual_findings']) else 'minor_fidelity_defects' if row['visual_findings'] else 'requires_review'
        rows.append(row)
    report={'scope':'Layout reconstruction only. Frozen translated PDFs; no reruns, production changes, extraction or PII scoring.',
        'component_results':{'pdfs_open':{'passed':sum(r['opens'] for r in rows),'total':len(rows)},
            'page_counts_match':{'passed':sum(r['page_count_matches'] for r in rows),'total':len(rows)},
            'page_dimensions_match':{'passed':matching_sizes,'total':total_pages},
            'no_outside_page_text':{'passed':inside_pages,'total':total_pages},
            'native_table_grids_match':{'passed':matched_grids,'total':expected_grids}},
        'visual_status_counts':dict(Counter(r['status'] for r in rows)),
        'limitations':['Component pass percentages are not overall layout accuracy.',
                       'Outside-page checks cannot detect every within-cell clipping or overlap.',
                       'Stored vector/image objects can be obscured by overlays; visual findings take precedence.',
                       'Raster tables, detailed merged-cell structure and exact font/style fidelity do not have independent numerical ground truth.',
                       'Visual findings come from the recorded review confirmed by Jubayer. No new human sign-off is invented.'],
        'documents':rows}
    (destination/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    body='<h1>Layout reconstruction evaluation</h1><p>'+html.escape(report['scope'])+'</p><h2>Component checks</h2><table><tr><th>Check</th><th>Pass / total</th><th>Rate</th></tr>'
    for key,value in report['component_results'].items():
        a,b=value['passed'],value['total'];body+=f'<tr><td>{html.escape(key.replace("_"," "))}</td><td>{a}/{b}</td><td>{100*a/b:.1f}%</td></tr>' if b else ''
    body+='</table><p>These structural passes do not override the visible defects below.</p><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in report['limitations'])+'</ul>'
    for row in rows:
        name=row['document'];body+=f'<h2>{name}: {html.escape(row["status"].replace("_"," "))}</h2>'
        body+=f'<p><a href="../review-pdfs/{name}_original.pdf">Original PDF</a> · <a href="../review-pdfs/{name}_Korean.pdf">Korean PDF</a></p><ul>'
        for issue in row['visual_findings']:body+=f'<li><strong>Page {issue["page"]}, {issue["severity"]}:</strong> '+html.escape(issue['note'])+'</li>'
        body+='</ul>'
        for page in row['pages']:
            body+=f'<h3>Page {page["page"]}</h3><div class="pair">'
            for label,key in [('Original','preview_source'),('Reconstruction','preview_output')]:
                body+='<figure><figcaption>'+label+'</figcaption><a target="_blank" href="'+page[key]+'"><img src="'+page[key]+'"></a></figure>'
            body+='</div>'
    (destination/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;margin:30px;max-width:1600px}table{border-collapse:collapse}td,th{border:1px solid #ccd;padding:10px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}figure{margin:0}img{width:100%;border:1px solid #ccd}figcaption{font-weight:bold;margin:8px 0}</style>'+body,encoding='utf-8')
    print(destination/'report.html');return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();evaluate(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))
