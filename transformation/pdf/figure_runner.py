"""Isolated lightweight jobs over already verified figure OCR evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
import pymupdf

from transformation.pdf.runner import write_manifest
from transformation.profiling import timed


def run_figure(data,unit,slots,root,options):
    if options.get('redact'):
        raise ValueError('The isolated figure translation path does not implement PII redaction.')
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        if len(pdf)!=1:raise ValueError('Figure translation requires one isolated page.')
    run=Path(root).resolve()/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
    (run/'output').mkdir(parents=True)
    (run/'source.pdf').write_bytes(data)
    from extraction.context_bridge import build
    (run/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':unit['regions']}]}),encoding='utf-8')
    (run/'verified-slots.json').write_text(json.dumps(slots),encoding='utf-8')
    (run/'extraction-context.json').write_text(json.dumps(build({'units':[unit]})),encoding='utf-8')
    info=dict(run_dir=str(run),status='processing',source_language=options.get('source','en'),
              target_language=options.get('target','ko'),model=options['model'],endpoint=options['endpoint'],
              protected_values=options.get('protected_values',[]),document_context=options.get('document_context',''),
              terminology=options.get('terminology',{}),
              engine='verified-ocr-figure-slots',review_status='requires_human_review')
    started=time.monotonic();process=None
    def save():
        info['seconds']=round(time.monotonic()-started,1);write_manifest(run,info);return dict(info)
    env=dict(os.environ,PYTHONUTF8='1',PYTHONUNBUFFERED='1',TRANSFORMX_RUN_DIR=str(run),
             TRANSFORMX_HTTP_KEEPALIVE='1' if options.get('http_keepalive') else '0')
    yield save()
    try:
        with timed(run,'figure_slot_worker'), (run/'process.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-m','transformation.pdf.figure_worker',str(run)],
                cwd=Path(__file__).resolve().parents[2],env=env,stdout=log,stderr=subprocess.STDOUT)
            while process.poll() is None:
                if time.monotonic()-started>options.get('timeout',3600):raise TimeoutError('Figure translation exceeded its budget.')
                yield save();time.sleep(.25)
        if process.returncode:raise RuntimeError('Figure translation failed; see process.log. Source preserved.')
        output=run/'output/scan-reconstructed.pdf'
        report=json.loads((run/'scan-reconstruction.json').read_text(encoding='utf-8'))
        with pymupdf.open(output) as verified,pymupdf.open(stream=data,filetype='pdf') as original:
            if len(verified)!=1 or verified[0].rect!=original[0].rect:raise ValueError('Figure output geometry mismatch.')
        info.update(status='generated',output_pdf=str(output),acceptance={'status':'requires_human_review',
            'automatic_approval':False,'review_items':len(report['acceptance_blockers'])})
        if not report['replaced']:
            info['source_pages_preserved_for_review']=[1]
        yield save()
    except Exception as exc:
        info.update(status='failed',error=str(exc));yield save()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
