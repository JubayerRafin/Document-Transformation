"""Compare completed PDF runtimes and nested worker evidence, without inference."""
import argparse
import csv
import html
import json
from pathlib import Path


def inventory(batch):
    index=json.loads((batch/'translation/index.json').read_text(encoding='utf-8'))
    if not index.get('phase_complete'):raise ValueError('Wait for the complete batch before comparing.')
    result={}
    for record in index['records']:
        run=Path(record['manifest']).parent
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        spans=[json.loads(l) for p in run.rglob('runtime-timing.jsonl') for l in p.read_text().splitlines() if l.strip()]
        stages=[json.loads(l) for p in run.rglob('translation-progress.jsonl') for l in p.read_text().splitlines() if l.strip()]
        report=manifest.get('validation',{}).get('independent_checks',{})
        result[record['document_id']]={
            'seconds':manifest['seconds'],'source_sha256':record['source_sha256'],
            'status':record['status'],'gpu_verified':record.get('gpu_verified'),
            'stage_seconds':record['stage_seconds'],'translation_errors':manifest.get('translation_errors'),
            'source_extraction_cache':manifest.get('source_extraction_cache'),
            'geometry_blockers':len(report.get('geometry_blockers',[])),
            'layout_review_candidates':len(report.get('layout_review_candidates',[])),
            'figure_statuses':[{k:v for k,v in item.items() if k in ('region_id','status','source_preserved','reason')}
                               for item in manifest.get('image_processing',[])],
            'worker_spans':[s for s in spans if s['stage'] in ('pdf_worker_execution','figure_slot_worker')],
            'pdf_stages':[s for s in stages if s.get('event')=='pdf_stage_end'],
            'output':manifest.get('output_file')}
    return result


def compare(before,after,output):
    old,new=inventory(before),inventory(after)
    if set(old)!=set(new):raise ValueError('Document inventories differ.')
    rows=[]
    for name in old:
        a,b=old[name],new[name]
        if a['source_sha256']!=b['source_sha256']:raise ValueError('Source changed: '+name)
        rows.append({'document':name,'before_seconds':a['seconds'],'after_seconds':b['seconds'],
                     'seconds_saved':round(a['seconds']-b['seconds'],1),
                     'percent_reduction':round(100*(a['seconds']-b['seconds'])/a['seconds'],1),
                     'status':b['status'],'gpu_verified':b['gpu_verified'],
                     'translation_errors':b['translation_errors'],'geometry_blockers':b['geometry_blockers'],
                     'layout_review_candidates':b['layout_review_candidates']})
    report={'before':str(before.resolve()),'after':str(after.resolve()),'rows':rows,'before_evidence':old,
            'after_evidence':new,'limitations':[
                'Observed timings include startup/cache/network differences; percentages are not isolated causal speedups.',
                'Renderer and cache settings belong to each batch configuration; cached extraction is repeat-use performance, not cold extraction.',
                'Structural checks and fewer worker seconds do not establish semantic accuracy or visual equivalence.',
                'Stage durations include model calls; do not add nested worker or request totals to document totals.']}
    output.mkdir(parents=True,exist_ok=True)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (output/'runtime.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    columns=list(rows[0]);body='<h1>Runtime comparison by PDF</h1><table><tr>'
    body+=''.join('<th>'+html.escape(k.replace('_',' '))+'</th>' for k in columns)+'</tr>'
    for row in rows:body+='<tr>'+''.join('<td>'+html.escape(str(row[k]))+'</td>' for k in columns)+'</tr>'
    body+='</table><ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in report['limitations'])+'</ul>'
    for name,evidence in new.items():
        body+='<h2>'+name+'</h2><pre>'+html.escape(json.dumps(evidence,indent=2))+'</pre>'
    (output/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:15px system-ui;margin:30px}table{border-collapse:collapse}td,th{padding:10px;border:1px solid #bbb}pre{white-space:pre-wrap}</style>'+body,encoding='utf-8')
    print(json.dumps(rows,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('before','after','output'):p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args();compare(args.before,args.after,args.output)
