"""Controlled sequential server comparison; keeps every result in its own folder."""
import json
import time
from pathlib import Path
from transformation.workflows import run_workflow


def main():
    root=Path('runs/runtime-optimization/20261018/server-pacing')
    source=json.loads((root/'source-job.json').read_text())
    remote=json.loads(Path('configs/server.local.json').read_text())['ssh']
    rows=[]
    for profile,rate,spinning,artifacts in [('baseline',1,True,'full'),('optimized',8,False,'review')]:
        start=time.monotonic();last=0
        for event in run_workflow(Path(source['source']).read_bytes(),'Sample01.pdf',
            execution_remote=remote,root=root/profile,source='en',target='ko',
            translation_model='translategemma:12b',timeout=1200,ignore_cache=True,
            translation_qps=rate,layout_idle_spinning=spinning,artifact_mode=artifacts):
            if time.monotonic()-last>25 or event['status'] in {'generated','failed'}:
                print(profile,event.get('stage'),event.get('status'),round(time.monotonic()-start,1),flush=True)
                last=time.monotonic()
        rows.append({'profile':profile,'result':event})
        (root/(profile+'-result.json')).write_text(json.dumps(event,ensure_ascii=False,indent=2))
        if event['status']!='generated':raise RuntimeError(event.get('error','Server comparison failed'))
    (root/'comparison-raw.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
