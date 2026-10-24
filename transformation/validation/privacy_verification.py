"""Fresh OCR of rendered output, independent of processing IR and detections."""
from pathlib import Path
import json
import pymupdf
from transformation.validation.output_validation import normalized


def verify(data,kind,run,values,categories,endpoint,model,assist,options):
    from transformation.validation.privacy import detect
    if kind!='pdf':
        from transformation.office.office import to_pdf
        data=to_pdf(data,'output.'+kind)
    reports=[]
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        for index,page in enumerate(pdf):
            folder=Path(run)/'privacy-verification'/str(index+1); folder.mkdir(parents=True,exist_ok=True)
            with pymupdf.open() as raster:
                target=raster.new_page(width=page.rect.width,height=page.rect.height)
                target.insert_image(target.rect,pixmap=page.get_pixmap(alpha=False))
                raster.save(folder/'source.pdf')
            if options.get('ocr_remote'):
                from transformation.ocr.remote_ocr import run_remote
                events=run_remote(folder/'source.pdf',folder/'paddle',options['ocr_remote'],timeout=min(options.get('timeout',1200),1200),source_language=options.get('target','ko'))
            elif options.get('ocr_python'):
                from transformation.ocr.paddle_pipeline import run_ocr
                events=run_ocr(folder/'source.pdf',folder/'paddle',options['ocr_python'],timeout=min(options.get('timeout',1200),1200),source_language=options.get('target','ko'))
            else: raise ValueError('Fresh OCR mode is required for rendered-output privacy verification.')
            for progress in events: yield {'page':index+1,'progress':progress}
            from transformation.ocr.paddle_handoff import prepare
            prepare((folder/'source.pdf').read_bytes(),progress['recognition_dir'],folder,'verification.pdf')
            ir=json.loads((folder/'ocr-document.json').read_text(encoding='utf-8'))
            regions=ir['pages'][0]['regions']; text=' '.join(r['text'] for r in regions)
            residual=[v for v in values if normalized(v) in normalized(text)]
            detected=detect(regions,categories,endpoint,model,assist)
            uncertain=[r['id'] for r in regions if r.get('metadata',{}).get('requires_review')]
            reports.append({'page':index+1,'known_residual_values':residual,'detected_pii':detected,'uncertain_regions':uncertain})
    yield {'reports':reports,'passed':not any(p['known_residual_values'] or p['detected_pii'] or p['uncertain_regions'] for p in reports),
           'limitations':'Fresh OCR can still miss content. This is an acceptance check, not a privacy guarantee.'}
