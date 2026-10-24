"""Audit saved timing evidence without model inference or changing prior runs."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path


def audit(batch, output):
    index_path=batch/'translation/index.json'
    index_bytes=index_path.read_bytes()
    index=json.loads(index_bytes)
    documents=[]
    for record in index['records']:
        manifest_path=Path(record['manifest']);run=manifest_path.parent
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        requests=[];transport=[]
        for path in run.rglob('translation-progress.jsonl'):
            for line in path.read_text(encoding='utf-8').splitlines():
                if not line.strip():continue
                row=json.loads(line)
                if row.get('event')=='request_completed':requests.append(row)
                if row.get('event')=='transport_completed':transport.append(row)
        children=[]
        for path in (run/'figure-runs').glob('*/manifest.json'):
            child=json.loads(path.read_text(encoding='utf-8'))
            children.append({'run':path.parent.name,'seconds':child['seconds'],'status':child['status']})
        spans=[]
        for path in run.rglob('runtime-timing.jsonl'):
            spans.extend(json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip())
        request_seconds=sum(row.get('seconds',0) for row in requests)
        documents.append({'document':record['document_id'],'status':record['status'],
            'total_seconds':manifest['seconds'],'stage_seconds':record.get('stage_seconds',{}),
            'completed_translation_requests':len(requests),'recorded_request_seconds':round(request_seconds,3),
            'figure_jobs':children,'spans':spans,
            'ollama_load_seconds':sum(row.get('load_duration',0) for row in transport)/1e9 if transport else None,
            'ollama_generation_seconds':sum(row.get('eval_duration',0) for row in transport)/1e9 if transport else None,
            'scope':'Request sums include nested figure runs. Unrecorded time is not assigned to a specific cause.'})
    gpu=[]
    for path in (batch/'shared-ocr').rglob('gpu-run.json'):
        item=json.loads(path.read_text(encoding='utf-8'))
        if item.get('status')=='completed':
            gpu.append({k:item.get(k) for k in ('total_seconds','detection_seconds','recognition_seconds')})
    phases=Counter()
    for doc in documents:phases.update(doc['stage_seconds'])
    report={'batch':str(batch),'index_sha256':hashlib.sha256(index_bytes).hexdigest(),
        'documents':documents,'total_seconds':sum(d['total_seconds'] for d in documents),
        'stage_seconds':dict(phases),'completed_translation_requests':sum(d['completed_translation_requests'] for d in documents),
        'recorded_request_seconds':round(sum(d['recorded_request_seconds'] for d in documents),3),
        'saved_completed_gpu_workers':gpu,
        'limitations':['Saved GPU worker totals do not include local preparation, SSH transfers, polling or child PDF reconstruction.',
            'Stage totals include nested work; do not add GPU worker time and LLM request time to those totals.',
            'Old runs lack detailed spans and Ollama load timings; absence is unknown, not zero.',
            'No new GPU run or independently scored quality result is produced by this audit.'],
        'local_changes':['Deferred the superseded legacy figure pass when the production spatial pass follows.',
            'Reuse a policy-fixed Docling converter per process; serialize access and keep page evidence local.',
            'Bundle remote OCR files and source into one authenticated upload and launch operation.',
            'Reuse source table, cell and image geometry inside each BabelDOC layout hook.',
            'Record extraction, figure and OCR handoff spans, remote transport stages and Ollama timing counters.',
            'Surface nested detector and recognizer failures without discarding the original cause.'],
        'pending':['Run GPU benchmark on the same hardware with explicit cold/warm cache conditions.',
            'Score new translations and visually review all pages; saved approvals cannot transfer.',
            'OCR identifier disagreement remains blocked. Do not invent characters or lower the acceptance threshold.',
            'Full persistent OCR serving and persistent BabelDOC processes require separate isolation and recovery design.']}
    output.mkdir(parents=True,exist_ok=True)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    body='<h1>TransformX runtime audit</h1><p>Saved-run evidence and local optimizations. GPU speedup is not yet measured.</p>'
    body+='<table><tr><th>Document</th><th>Total seconds</th><th>Extraction</th><th>Translation and reconstruction</th><th>Image processing</th><th>Requests</th><th>Recorded request seconds</th></tr>'
    for d in documents:
        values=[d['document'],d['total_seconds'],d['stage_seconds'].get('extraction'),d['stage_seconds'].get('translation_and_reconstruction'),d['stage_seconds'].get('image_ocr',0),d['completed_translation_requests'],d['recorded_request_seconds']]
        body+='<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>'
    body+='</table>'
    for key in ('limitations','local_changes','pending'):
        body+='<h2>'+key.replace('_',' ').title()+'</h2><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in report[key])+'</ul>'
    (output/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;max-width:1100px;margin:35px auto}td,th{border:1px solid #ccc;padding:10px}table{border-collapse:collapse}</style>'+body,encoding='utf-8')
    assert index_path.read_bytes()==index_bytes,'Saved batch index changed during audit.'
    print(json.dumps({k:report[k] for k in ('total_seconds','stage_seconds','completed_translation_requests','recorded_request_seconds')},indent=2))
    print(output/'report.html')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();audit(args.batch,args.output)
