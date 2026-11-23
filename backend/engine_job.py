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


def input_problem(source):
    """INPUT_REJECTED for a file the engine cannot work on (not a PDF, Word or PowerPoint file,
    damaged, password-protected or without pages), so callers that do not check it themselves
    (engine service, test harness) report the real reason instead of ENGINE_FAILED."""
    kind=Path(source).suffix.lower().lstrip('.')
    try:
        if kind in {'doc','ppt'}:
            # Word and PowerPoint 97-2003 (compound file); converted by the pipeline.
            return None if Path(source).read_bytes()[:4]==b'\xd0\xcf\x11\xe0' else 'INPUT_REJECTED'
        if kind in {'docx','pptx'}:
            from transformation.office.package_ir import open_package
            open_package(Path(source).read_bytes(),kind).close()
            return None
        import pymupdf
        with pymupdf.open(source,filetype='pdf') as pdf:
            if not pdf.is_pdf or pdf.needs_pass or not len(pdf):return 'INPUT_REJECTED'
    except Exception:
        return 'INPUT_REJECTED'
    return None


def main():
    request_path,events_path,terminal_path=map(Path,sys.argv[1:4])
    request=json.loads(request_path.read_text(encoding='utf8'))
    rejected=input_problem(request['source'])
    if rejected:
        terminal_path.write_text(json.dumps({'status':'failed','stage':'failed','failure_code':rejected}),encoding='utf8')
        return
    if request['profile'].get('ocr_device'):
        os.environ['TRANSFORMX_OCR_DEVICE']=request['profile']['ocr_device']
    from transformation.workflows import run_workflow
    cfg=request['profile'];opts=request['options'];folder=request_path.parent
    operation={'translation':'Translation only','pii':'PII only','combined':'Translation + PII'}[opts['operation']]
    options=dict(cfg.get('engine_options',{}),root=folder/'engine',source=opts['source_language'],
        target=opts['target_language'],output_kind={'doc':'docx','ppt':'pptx'}.get(Path(request['source']).suffix.lower().lstrip('.'),Path(request['source']).suffix.lower().lstrip('.')) or 'pdf',protected_values=opts['protected_values'],
        document_context=opts['document_context'],pii_categories=opts['pii_categories'],
        timeout=cfg.get('timeout_seconds',3600),translation_model='translategemma:12b',pii_model='qwen3.5:9b')
    for key in ('pii_style','pii_keep'):
        if opts.get(key):options[key]=opts[key]
    # The account's glossary for this language pair, attached by the hosted API (never by a browser).
    if isinstance(opts.get('terminology'),dict) and opts['terminology']:
        from transformation.llm.policy import validate_terminology
        options['terminology']=validate_terminology(opts['terminology'])
    if cfg.get('execution_remote'):options['execution_remote']=cfg['execution_remote']
    else:
        options=local_cache_options(options,cfg,folder)
        for key in ('python','ocr_python','translation_endpoint','pii_endpoint','pii_model','translation_model'):
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
    except Exception as exc:
        (folder/'engine-error.log').write_text(traceback.format_exc(),encoding='utf8')
        terminal={'status':'failed','stage':'failed',
                  'failure_code':'ENGINE_TIMEOUT' if isinstance(exc,TimeoutError) else 'ENGINE_FAILED'}
    terminal_path.write_text(json.dumps(terminal or {'status':'failed'},ensure_ascii=False,default=str),encoding='utf8')
if __name__=='__main__':main()
