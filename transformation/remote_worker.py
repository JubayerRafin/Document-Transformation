"""Trusted SSH job entry point. All document computation occurs on this host."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import zipfile

OPTIONS = {'output_kind','pii_categories','pii_assist','visual_analysis','table_strategy',
    'translation_model','pii_model','source','target','timeout','ignore_cache',
    'protected_values','document_context','terminology','pdf_page_mode',
    'http_keepalive','nativeized_ir_mode','babeldoc_dual_output','figure_engine','light_pdf_io','ocr_cache_mode','translation_qps','translation_workers','ollama_parallelism','artifact_mode','layout_idle_spinning'}


def server_options(options, job):
    unknown=set(options)-OPTIONS
    if unknown:raise ValueError('Unsupported remote operation options: '+', '.join(sorted(unknown)))
    options=dict(options)
    spinning=options.pop('layout_idle_spinning',False)
    if not isinstance(spinning,bool):raise ValueError('layout_idle_spinning must be boolean.')
    artifact_mode=options.pop('artifact_mode','review')
    if artifact_mode not in {'review','full'}:raise ValueError('artifact_mode must be review or full.')
    cache_mode=options.pop('ocr_cache_mode','reuse')
    if cache_mode not in {'reuse','fresh'}:raise ValueError('OCR cache mode must be reuse or fresh.')
    defaults={'pdf_page_mode':'document','http_keepalive':True,'nativeized_ir_mode':'reuse',
        'babeldoc_dual_output':False,'figure_engine':'ocr-slots','light_pdf_io':True,'translation_qps':8}
    defaults.update(options)
    rate=defaults['translation_qps']
    if isinstance(rate,bool) or not isinstance(rate,int) or not 1<=rate<=16:
        raise ValueError('translation_qps must be an integer from 1 to 16.')
    return dict(defaults,python='/workspace/transformx-babeldoc/bin/python',
        ocr_python='/workspace/paddle-env/bin/python',
        translation_endpoint='http://127.0.0.1:11434/v1',pii_endpoint='http://127.0.0.1:11434/v1',
        ocr_cache_root=Path('/workspace/transformx-cache/ocr') if cache_mode=='reuse' else None,root=Path(job)/'runs')


def save(path,value):
    path=Path(path); pending=path.with_suffix('.tmp')
    pending.write_text(json.dumps(value,ensure_ascii=False,default=str),encoding='utf-8')
    pending.replace(path)


def export_ocr_evidence(job,terminal,cache=Path('/workspace/transformx-cache/ocr')):
    """Copy only OCR attempts actually referenced by this job's OCR IR."""
    from transformation.ocr.remote_ocr import FILES
    cache=Path(cache).resolve();mapping={}
    for path in (job/'runs').rglob('ocr-document.json'):
        doc=json.loads(path.read_text(encoding='utf-8'))
        origin=doc.get('settings',{}).get('recognition_manifest',{}).get('detection_run')
        if not origin:continue
        origin=Path(origin).resolve()
        if not origin.is_relative_to(cache):continue
        relative=origin.relative_to(cache)
        if len(relative.parts)!=2 or not relative.parts[1].startswith('attempt-'):
            raise ValueError('Unexpected referenced OCR cache layout.')
        destination=job/'ocr-evidence'/relative
        for name in FILES:
            source=origin/name
            if not source.is_file() or source.is_symlink():raise ValueError('Referenced OCR evidence is incomplete.')
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        mapping[str(origin)]=str(destination)
    def relocate(value):
        if isinstance(value,dict):return {k:relocate(v) for k,v in value.items()}
        if isinstance(value,list):return [relocate(v) for v in value]
        if isinstance(value,str):
            for origin,destination in mapping.items():
                if value==origin or value.startswith(origin+os.sep):return destination+value[len(origin):]
        return value
    if mapping:
        for folder in (job/'runs',job/'ocr-evidence'):
            for path in folder.rglob('*.json'):
                save(path,relocate(json.loads(path.read_text(encoding='utf-8'))))
    return relocate(terminal)


def pack_result(job,mode='review'):
    """Keep all server files; omit disposable vendor work PDFs from download."""
    started=time.monotonic();omitted=0;omitted_bytes=0
    with zipfile.ZipFile(job/'result.tmp','w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(job/'terminal.json','terminal.json')
        for folder in (job/'runs',job/'ocr-evidence'):
            for path in folder.rglob('*'):
                if not path.is_file() or path.is_symlink():continue
                relative=path.relative_to(job)
                if mode=='review' and 'work' in relative.parts and path.suffix.lower()=='.pdf':
                    omitted+=1;omitted_bytes+=path.stat().st_size;continue
                archive.write(path,relative.as_posix())
        package={'artifact_mode':mode,'omitted_temporary_files':omitted,
            'omitted_temporary_bytes':omitted_bytes,'server_files_retained':True,
            'packaging_seconds':round(time.monotonic()-started,6)}
        archive.writestr('package-info.json',json.dumps(package))
    (job/'result.tmp').replace(job/'result.zip')


def main(job):
    job=Path(job).resolve()
    request=json.loads((job/'request.json').read_text(encoding='utf-8'))
    os.environ['TRANSFORMX_OCR_DEVICE']='gpu:0'
    terminal=None
    try:
        from transformation.workflows import run_workflow,check_models
        options=server_options(request['options'],job)
        os.environ['TRANSFORMX_LAYOUT_IDLE_SPINNING']='1' if request['options'].get('layout_idle_spinning',False) else '0'
        check_models(request['workflow'],options.get('translation_model','translategemma:12b'),
            options.get('pii_model','qwen3.5:9b'),options['translation_endpoint'],options['pii_endpoint'],
            visual_analysis=options.get('visual_analysis',False))
        for event in run_workflow((job/'source').read_bytes(),request['filename'],request['workflow'],**options):
            terminal=event
            save(job/'progress.json',{k:event[k] for k in ('stage','status','seconds','error','translation_progress') if k in event})
        if terminal is None:raise RuntimeError('Server pipeline returned no terminal result.')
    except Exception as error:
        import traceback
        traceback.print_exc()
        terminal={'status':'failed','stage':'failed','seconds':0,'error':str(error),'run_dir':str(job/'runs')}
    evidence={'execution_host':'server','hostname':platform.node(),'platform':platform.platform(),
        'engine_python':sys.executable,'ocr_device':'gpu:0','model_endpoint':'http://127.0.0.1:11434/v1',
        'translation_qps':request['options'].get('translation_qps',8),'translation_workers':request['options'].get('translation_workers',1),
        'layout_idle_spinning':request['options'].get('layout_idle_spinning',False),
        'stages_on_server':['analysis','extraction','ocr','translation','reconstruction','validation']}
    try:
        evidence['gpu']=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader'],text=True,timeout=10).strip()
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:11434/api/ps',timeout=5) as response:
            evidence['ollama_models']=json.load(response).get('models',[])
    except Exception as error:evidence['device_evidence_error']=str(error)
    terminal['execution']=evidence
    terminal=export_ocr_evidence(job,terminal)
    save(job/'terminal.json',terminal)
    save(job/'progress.json',{k:terminal[k] for k in ('stage','status','seconds','error') if k in terminal})
    pack_result(job,request['options'].get('artifact_mode','review'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('job')
    main(parser.parse_args().job)
