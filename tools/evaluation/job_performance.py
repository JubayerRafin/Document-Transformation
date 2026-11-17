"""Summarize one saved job without inference or document-content logging."""
import argparse,collections,json
from pathlib import Path
from transformation.runtime_metrics import summarize


def report(root):
    terminal=root/'terminal.json'
    if not terminal.exists():terminal=root/'manifest.json'
    state=json.loads(terminal.read_text(encoding='utf-8'))
    events=[];stages=collections.Counter();spans=[]
    for path in root.rglob('translation-progress.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            row=json.loads(line);events.append(row)
            if row.get('event')=='pdf_stage_end':stages[row['stage']]+=row.get('seconds',0)
    for path in root.rglob('runtime-timing.jsonl'):
        spans.extend(json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip())
    reconstruction=('Typesetting','Add Fonts','Generate drawing instructions','Subset font','Save PDF')
    optional={}
    for filename in ('benchmark-metadata.json','resource-metrics.json'):
        path=root/filename
        optional[filename]=json.loads(path.read_text()) if path.exists() else None
    independent=state.get('validation',{}).get('independent_checks',{})
    ocr=[json.loads(path.read_text()) for path in root.rglob('ocr-timing.json')]
    return {'root':str(root.resolve()),'source_sha256':state.get('source_sha256'),
            'status':state.get('status'),'engine_seconds':state.get('seconds'),
            'transport':summarize(events),'babeldoc_stages':dict(stages),'runtime_spans':spans,
            'reconstruction_seconds':sum(stages[k] for k in reconstruction) if any(k in stages for k in reconstruction) else None,
            'resources_and_settings':optional,
            'ocr_requests':{'recorded':len(ocr),'seconds_sum':sum(r['total_seconds'] for r in ocr),
                'persistent_requests':sum(r.get('persistent') is True for r in ocr),
                'model_loads':sum(r.get('service_metrics',{}).get('model_loads',0) for r in ocr),
                'model_load_seconds':sum(r.get('service_metrics',{}).get('model_load_seconds',0) for r in ocr),
                'model_reuse_hits':sum(r.get('service_metrics',{}).get('model_reuse_hits',0) for r in ocr)},
            'quality':{'human_approved':False,'acceptable':independent.get('acceptable'),
                       'geometry_blockers':len(independent.get('geometry_blockers',[])),
                       'layout_review_candidates':len(independent.get('layout_review_candidates',[])),
                       'uninspected_content':len(independent.get('uninspected_content',[])),
                       'known_sensitive_residuals':len(independent.get('known_sensitive_residuals',[]))},
            'limitations':['Generated status is not human approval.','Stage/request durations may overlap.',
                          'Missing resource/sizing evidence is unknown; model coldness is not inferred.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve existing reports; use a new output path.')
    result=report(args.job);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))
    print('Saved content-free job performance report:',args.output)


if __name__=='__main__':main()
