"""Prepare, run and evaluate independent dataset phases. No paid pod actions."""
import argparse,csv,hashlib,json,time
from pathlib import Path
import pymupdf
from transformation.workflows import run_workflow,check_models


def save(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp'); temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8'); temporary.replace(path)


def signature(config):
    root=Path(__file__).resolve().parents[2]
    files=[p for folder in ('transformation','extraction','tools/ocr') for p in (root/folder).rglob('*.py')]
    code={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    # A replacement pod's IP/port and local path do not change experiment policy.
    policy={k:v for k,v in config.items() if k not in {'endpoint','ocr_remote','babeldoc_python','output_root'}}
    return hashlib.sha256(json.dumps({'configuration':policy,'code':code},sort_keys=True).encode()).hexdigest()


def prepare(config):
    from transformation.analysis.document_profile import profile_pdf
    documents=[]; annotations={}
    for item in config['documents']:
        path=Path(item['path']).absolute()
        data=path.read_bytes(); profile=profile_pdf(data)
        record={**item,'path':str(path),'sha256':hashlib.sha256(data).hexdigest(),'pages':len(profile['pages']),'profile':profile}
        documents.append(record)
        annotations[item['id']]={'source_sha256':record['sha256'],
            'pii':{'reviewed':False,'spans':[],'notes':'Annotate exact region_id/start/end/category/value from source-ir.json. Empty unreviewed spans are not negative ground truth.'},
            'translation':{target:{'reviewed':False,'meaning':None,'fluency':None,'completeness':None,'layout':None,'notes':''} for target in config['targets']}}
    root=Path(config['output_root']); save(root/'dataset.json',{'documents':documents})
    if not (root/'annotations.json').exists(): save(root/'annotations.json',annotations)
    print(f'Registered {len(documents)} documents; no inference performed.')
    return documents


def registry(config):
    root=Path(config['output_root']); dataset=json.loads((root/'dataset.json').read_text(encoding='utf-8'))['documents']
    if len({r['id'] for r in dataset})!=len(dataset): raise ValueError('Duplicate dataset IDs.')
    for item in dataset:
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Dataset changed after registration: '+item['id'])
    return dataset


def preflight(config,phase):
    if not isinstance(config.get('light_pdf_io',False),bool):
        raise ValueError('light_pdf_io must be true or false.')
    if config.get('figure_engine','babeldoc') not in {'babeldoc','ocr-slots'}:
        raise ValueError('Set figure_engine to babeldoc or ocr-slots.')
    if config.get('nativeized_ir_mode','reextract') not in {'reextract','reuse'}:
        raise ValueError('Set nativeized_ir_mode to reextract or reuse.')
    if not isinstance(config.get('babeldoc_dual_output',True),bool):
        raise ValueError('babeldoc_dual_output must be true or false.')
    if not isinstance(config.get('http_keepalive',False),bool):
        raise ValueError('http_keepalive must be true or false.')
    if config.get('pdf_page_mode','page') not in {'page','document'}:
        raise ValueError('Set pdf_page_mode to page or document.')
    if config.get('ocr_cache_mode','reuse') not in {'reuse','fresh'}:
        raise ValueError('Set ocr_cache_mode to reuse or fresh.')
    if config.get('table_strategy','auto') not in {'auto','native','docling'}:
        raise ValueError('Set table_strategy to auto, native or docling.')
    documents=registry(config)
    if config.get('execution_remote'):
        if config.get('shared_layout_session'):
            raise ValueError('The local shared-layout experiment cannot be combined with full server execution.')
        from transformation.remote_pipeline import connection
        connection(config['execution_remote'])
        print('Source hashes verified. Entire pipeline will run on the SSH server; models are checked there.')
        return
    python=Path(config['babeldoc_python'])
    if phase=='translation' and not python.is_file(): raise ValueError('Set the local BabelDOC Python executable.')
    if phase=='translation':
        import subprocess
        version=subprocess.check_output([str(python),'-c',"from importlib.metadata import version; print(version('babeldoc'))"],text=True,timeout=20).strip()
        if version!='0.6.4': raise ValueError('The current PDF hooks require BabelDOC 0.6.4.')
    remote=config.get('ocr_remote')
    if any(any(p['kind']=='image_only' for p in d['profile']['pages']) for d in documents):
        if not remote or not remote.get('host'): raise ValueError('Set the new GPU SSH host, port and key before starting scanned documents.')
        import ipaddress
        ipaddress.ip_address(remote['host'])
        if not 1<=int(remote['port'])<=65535 or not Path(remote['key']).expanduser().is_file(): raise ValueError('Invalid GPU SSH port or private-key path.')
    mode='Translation only' if phase=='translation' else 'PII only'
    check_models(mode,config['translation_model'],config['pii_model'],config['endpoint'],config['endpoint'])
    print('Source hashes and selected model verified. Confirm GPU placement on the pod before running the batch.')


def gpu_state(endpoint):
    import urllib.request
    from urllib.parse import urlparse
    parsed=urlparse(endpoint)
    with urllib.request.urlopen(f'{parsed.scheme}://{parsed.netloc}/api/ps',timeout=10) as response:
        return json.load(response).get('models',[])


def run(config,phase,resume=False):
    if config.get('shared_layout_session'):
        from transformation.pdf.layout_service import shared_layout
        with shared_layout(config['babeldoc_python'], Path(config['output_root'])/'layout-runtime'):
            return _run(config,phase,resume)
    return _run(config,phase,resume)


def _run(config,phase,resume=False):
    preflight(config,phase)
    root=Path(config['output_root']); folder=root/phase
    fingerprint=signature(config)
    if phase=='pii' and not (root/'translation/index.json').exists():
        raise ValueError('Run and inspect the translation phase first; PII is a separate subsequent phase.')
    if phase=='pii':
        prior=json.loads((root/'translation/index.json').read_text(encoding='utf-8'))
        if not prior.get('phase_complete'): raise ValueError('Finish the translation batch first, including its failure inventory.')
        if prior['configuration_sha256']!=fingerprint: raise ValueError('Code/configuration changed between phases; use a new output_root for a comparable baseline.')
    index_path=folder/'index.json'
    if index_path.exists() and not resume: raise ValueError('Batch exists. Use --resume or a new output_root; previous outputs are preserved.')
    index=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'phase':phase,'configuration_sha256':fingerprint,'records':[]}
    if index['configuration_sha256']!=fingerprint: raise ValueError('Cannot resume after code/configuration changes; use a new output_root.')
    index['phase_complete']=False; save(index_path,index)
    documents=registry(config)
    jobs=[(d,t) for d in documents for t in (config['targets'] if phase=='translation' else [None])]
    for document,target in jobs:
        key=document['id']+(':'+target if target else '')
        previous=next((r for r in index['records'] if r['key']==key),None)
        if previous and previous['status']=='generated' and previous.get('gpu_verified') and Path(previous['manifest']).is_file():
            print('Resumed: preserved existing generated run '+key,flush=True); continue
        mode='Translation only' if phase=='translation' else 'PII only'
        options=dict(python=config['babeldoc_python'],translation_model=config['translation_model'],pii_model=config['pii_model'],
            translation_endpoint=config['endpoint'],pii_endpoint=config['endpoint'],source=config['source_language'],target=target or config['targets'][0],
            ocr_remote=config.get('ocr_remote'),
            ocr_cache_root=Path(config.get('ocr_cache_root',root/'shared-ocr')) if config.get('ocr_cache_mode','reuse')=='reuse' else None,
            ignore_cache=True,timeout=config['timeout_seconds'],
            pdf_page_mode=config.get('pdf_page_mode','page'),
            http_keepalive=config.get('http_keepalive',False),
            nativeized_ir_mode=config.get('nativeized_ir_mode','reextract'),
            babeldoc_dual_output=config.get('babeldoc_dual_output',True),
            figure_engine=config.get('figure_engine','babeldoc'),
            light_pdf_io=config.get('light_pdf_io',False),
            source_extraction_cache=config.get('source_extraction_cache'),
            protected_values=document.get('protected_values',[]),document_context=document.get('document_context',''),
            table_strategy=config.get('table_strategy','auto'),visual_analysis=False,pii_categories=config['pii_categories'],root=folder/'runs')
        if config.get('execution_remote'):
            options['execution_remote']=config['execution_remote']
            options['ocr_cache_mode']=config.get('ocr_cache_mode','reuse')
        print('Starting '+key,flush=True)
        stage_seconds={}; stage=None; stage_start=0; last_print=-30
        for event in run_workflow(Path(document['path']).read_bytes(),Path(document['path']).name,mode,**options):
            changed=event['stage']!=stage
            if changed:
                if stage is not None: stage_seconds[stage]=stage_seconds.get(stage,0)+event['seconds']-stage_start
                stage=event['stage']; stage_start=event['seconds']
            if changed or event['seconds']-last_print>=30 or event['status'] in {'generated','failed'}:
                print(f"{key}: {event['stage']} / {event['status']} / {event['seconds']}s",flush=True); last_print=event['seconds']
        if config.get('execution_remote'):
            devices=event.get('execution',{}).get('ollama_models',[])
        else:
            try: devices=gpu_state(config['endpoint'])
            except (OSError,ValueError): devices=[]
        model=config['translation_model'] if phase=='translation' else config['pii_model']
        gpu_verified=any(m.get('name')==model and m.get('size',0)>0 and m.get('size_vram',0)>=m['size']*.95 for m in devices)
        record={'key':key,'document_id':document['id'],'target':target,'status':event['status'],
            'source_sha256':document['sha256'],'manifest':str(Path(event['run_dir'])/'manifest.json'),
            'gpu_verified':gpu_verified,'device_evidence':devices,'stage_seconds':stage_seconds,'error':event.get('error')}
        index['records']=[r for r in index['records'] if r['key']!=key]+[record]; save(index_path,index)
        if not gpu_verified:
            if event['status']=='failed':
                raise RuntimeError('Document processing failed: '+str(event.get('error','See run manifest.'))+
                    ' GPU model inference was not confirmed for this failed attempt; inspect the processing error first.')
            raise RuntimeError('GPU inference was not confirmed; batch paused to prevent unnoticed CPU testing. Run ollama ps on the pod.')
    index['phase_complete']=True; save(index_path,index); report(config,phase)


def pii_scores(expected,findings):
    gold={(s['region_id'],s['start'],s['end'],s['category']) for s in expected}
    predicted={(f['region_id'],span[0],span[1],f['category']) for f in findings for span in f['spans']}
    tp=len(gold & predicted); fp=len(predicted-gold); fn=len(gold-predicted)
    precision=tp/(tp+fp) if tp+fp else (1.0 if not gold else 0.0)
    recall=tp/(tp+fn) if tp+fn else 1.0
    return {'tp':tp,'fp':fp,'fn':fn,'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0.0}


def report(config,phase):
    root=Path(config['output_root']); registry(config)
    index=json.loads((root/phase/'index.json').read_text(encoding='utf-8'))
    annotations=json.loads((root/'annotations.json').read_text(encoding='utf-8'))
    rows=[]
    for record in index['records']:
        manifest=json.loads(Path(record['manifest']).read_text(encoding='utf-8'))
        checked=manifest.get('validation',{}).get('independent_checks',{})
        row={'document':record['document_id'],'target':record['target'],'status':manifest['status'],'seconds':manifest['seconds'],
            'gpu_verified':record['gpu_verified'],'output':manifest.get('output_file'),
            'geometry_blockers':len(checked.get('geometry_blockers',[])),
            'layout_review_candidates':len(checked.get('layout_review_candidates',[])),
            'scan_blockers':len(manifest.get('scan_acceptance_blockers',[])),
            'translation_errors':manifest.get('translation_errors',0),'quality_status':'not_reviewed','pii_scores':None}
        row['stage_seconds']=record.get('stage_seconds',{})
        annotation=annotations[record['document_id']]
        if annotation['source_sha256']!=record['source_sha256']: raise ValueError('Annotation source hash mismatch.')
        review=annotation['pii'] if phase=='pii' else annotation['translation'][record['target']]
        if review['reviewed'] and manifest['status']!='generated': row['quality_status']='failed_not_scored'
        elif review['reviewed']:
            if phase=='pii':
                findings=json.loads((Path(record['manifest']).parent/'pii-findings.json').read_text(encoding='utf-8'))
                source=json.loads((Path(record['manifest']).parent/'source-ir.json').read_text(encoding='utf-8'))
                from extraction.coordinator import text_regions
                regions={r['id']:r['text'] for r in text_regions(source)}
                for span in review['spans']:
                    if regions.get(span['region_id'],'')[span['start']:span['end']]!=span['value']:
                        raise ValueError('Ground-truth span does not match extracted source; do not score stale annotations.')
                row['pii_scores']=pii_scores(review['spans'],findings)
                row.update(row['pii_scores'])
                if row['output']:
                    from transformation.validation.output_validation import inspect_output
                    residuals=inspect_output(Path(row['output']).read_bytes(),'pdf',[s['value'] for s in review['spans']])
                    row['expected_pii_residuals']=residuals['known_sensitive_residuals']
                    row['uninspected_content']=residuals['uninspected_content']
                row['quality_status']='annotated_span_evaluation'
            else:
                for metric in ('meaning','fluency','completeness','layout'):
                    if not isinstance(review[metric],(int,float)) or not 0<=review[metric]<=5: raise ValueError('Reviewed translation needs four scores from 0 to 5.')
                row['human_translation_scores']={k:review[k] for k in ('meaning','fluency','completeness','layout')}
                row.update(row['human_translation_scores'])
                row['quality_status']='human_reviewed'
        rows.append(row)
    save(root/phase/'evaluation.json',{'phase':phase,'phase_complete':index.get('phase_complete',False),'rows':rows,'automatic_semantic_approval':False})
    columns=['document','target','status','seconds','gpu_verified','geometry_blockers','layout_review_candidates','scan_blockers','translation_errors','quality_status',
             'meaning','fluency','completeness','layout','tp','fp','fn','precision','recall','f1','output']
    with (root/phase/'evaluation.csv').open('w',newline='',encoding='utf-8-sig') as file:
        writer=csv.DictWriter(file,fieldnames=columns,extrasaction='ignore'); writer.writeheader(); writer.writerows(rows)
    print(root/phase/'evaluation.json')
    # Detailed reviews/metrics remain independent from operational status.
    from tools.evaluation.detailed import make_reviews,evaluate
    make_reviews(config,phase)
    evaluate(config,phase)


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('action',choices=['prepare','preflight','run','report'])
    parser.add_argument('--config',default='configs/gpu-evaluation.local.json'); parser.add_argument('--phase',choices=['translation','pii'],default='translation'); parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(); config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    if args.action=='prepare': prepare(config)
    elif args.action=='preflight': preflight(config,args.phase)
    elif args.action=='run': run(config,args.phase,args.resume)
    else: report(config,args.phase)


if __name__=='__main__': main()
