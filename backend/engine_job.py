"""Isolated engine subprocess. Run with the established engine Python."""
import json, os, sys, traceback
from pathlib import Path


def local_cache_options(options,cfg,folder):
    """Translate the adapter cache policy into the local pipeline's cache root."""
    options=dict(options)
    mode=options.pop('ocr_cache_mode',None)
    if mode is not None:
        if mode not in {'reuse','fresh'}:
            raise ValueError('OCR cache mode must be reuse or fresh.')
        options['ocr_cache_root']=(Path(cfg.get('ocr_cache_root') or Path(folder).parent/'shared-ocr')
                                   if mode=='reuse' else None)
    return options


def main():
    request_path,events_path,terminal_path=map(Path,sys.argv[1:4])
    request=json.loads(request_path.read_text(encoding='utf8'))
    if request['profile'].get('ocr_device'):
        os.environ['TRANSFORMX_OCR_DEVICE']=request['profile']['ocr_device']
    from transformation.workflows import run_workflow
    cfg=request['profile'];opts=request['options'];folder=request_path.parent
    operation={'translation':'Translation only','pii':'PII only','combined':'Translation + PII'}[opts['operation']]
    options=dict(cfg.get('engine_options',{}),root=folder/'engine',source=opts['source_language'],
        target=opts['target_language'],output_kind='pdf',protected_values=opts['protected_values'],
        document_context=opts['document_context'],pii_categories=opts['pii_categories'],
        timeout=cfg.get('timeout_seconds',3600),translation_model='translategemma:12b',pii_model='qwen3.5:9b')
    if cfg.get('execution_remote'):options['execution_remote']=cfg['execution_remote']
    else:
        options=local_cache_options(options,cfg,folder)
        for key in ('python','ocr_python','translation_endpoint','pii_endpoint'):
            if key in cfg:options[key]=cfg[key]
    probe=options.pop('performance_probe',False)
    if type(probe) is not bool:raise ValueError('performance_probe must be boolean.')
    terminal=None
    try:
        from transformation.resource_probe import monitor,snapshot
        if probe:snapshot(folder,request['source'],options)
        with monitor(folder,probe),events_path.open('w',encoding='utf8',buffering=1) as log:
            for event in run_workflow(Path(request['source']).read_bytes(),request['filename'],operation,**options):
                terminal=event
                log.write(json.dumps({'stage':event.get('stage','processing'),'elapsed_seconds':event.get('seconds',0)},default=str)+'\n')
    except Exception:
        (folder/'engine-error.log').write_text(traceback.format_exc(),encoding='utf8')
        terminal={'status':'failed','stage':'failed'}
    terminal_path.write_text(json.dumps(terminal or {'status':'failed'},ensure_ascii=False,default=str),encoding='utf8')
if __name__=='__main__':main()
