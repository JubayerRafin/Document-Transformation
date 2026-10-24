"""OCR and transform isolated image regions in otherwise digital PDF pages."""
from pathlib import Path
import pymupdf
import time


def process(data,ir,run,translate,redact,categories,assist,options):
    from transformation.pipeline import run_pipeline
    reports=[]
    started=time.monotonic()
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        for unit in ir['units']:
            if unit.get('ocr_folder'): continue
            page=pdf[unit['number']-1]
            candidates=[r for r in unit['regions'] if r['kind']=='figure']
            visited=[]
            for region in candidates:
                box=pymupdf.Rect(region['bbox']) & page.rect
                if box.is_empty:
                    reports.append({'region_id':region['id'],'status':'blocked','source_preserved':True,
                                    'reason':'Figure has no visible area inside the page.'}); continue
                if any((box & b).get_area()>0 for b in visited):
                    reports.append({'region_id':region['id'],'status':'blocked','reason':'Overlapping figure candidates require disambiguation.'}); continue
                visited.append(box)
                if any((pymupdf.Rect(w[:4]) & box).get_area()>0 for w in page.get_text('words')):
                    reports.append({'region_id':region['id'],'status':'blocked','reason':'Figure overlaps native text; preserve original geometry.'}); continue
                from tools.ocr.raster_policy import render_page
                pixels,raster=render_page(page,box)
                with pymupdf.open() as image_pdf:
                    crop=image_pdf.new_page(width=box.width,height=box.height)
                    crop.insert_image(crop.rect,stream=pixels.tobytes('png')); payload=image_pdf.tobytes()
                suboptions=dict(options); suboptions.pop('ocr_recognition_dir',None)
                figure_engine=suboptions.pop('figure_engine','babeldoc')
                if figure_engine=='ocr-slots' and translate and not redact:
                    suboptions['isolated_figure_slots']=True
                remaining=options.get('timeout',3600)-(time.monotonic()-started)
                if remaining<=0:raise TimeoutError('Image processing exceeded the remaining document budget.')
                suboptions['timeout']=remaining
                for event in run_pipeline(payload,'figure.pdf',root=Path(run)/'figure-runs',translate=translate,
                        redact=redact,pii_categories=categories,pii_assist=assist,table_strategy='native',**suboptions):
                    yield {'region_id':region['id'],'progress':event}
                if event['status']!='generated':
                    reason=event.get('error','Image OCR failed.')
                    reports.append({'region_id':region['id'],'status':'no_text_detected_requires_review' if reason.startswith('No OCR text detected') else 'blocked',
                                    'source_preserved':True,'reason':reason}); continue
                if event.get('engine_run',{}).get('source_pages_preserved_for_review'):
                    reports.append({'region_id':region['id'],'status':'source_preserved_requires_review',
                                    'source_preserved':True,'run_dir':event['run_dir'],
                                    'reason':'No confidently translatable OCR text; source image retained.'})
                    continue
                with pymupdf.open(event['output_pdf']) as transformed:
                    placements=[]
                    for block in transformed[0].get_text('dict')['blocks']:
                        for line in block.get('lines',[]):
                            for span in line['spans']:
                                rect=pymupdf.Rect(span['bbox']);size=transformed[0].rect
                                mapped=[box.x0+rect.x0/size.width*box.width,box.y0+rect.y0/size.height*box.height,
                                        box.x0+rect.x1/size.width*box.width,box.y0+rect.y1/size.height*box.height]
                                placements.append({'page':unit['number'],'region_id':region['id'],'text':span['text'],
                                    'bbox':mapped,'image_bbox':list(box),'basis':'verified_figure_output_placement'})
                    # Remove underlying source pixels too; an overlay alone is
                    # not redaction and would retain recoverable sensitive data.
                    page.add_redact_annot(box,fill=(1,1,1))
                    page.apply_redactions(images=2,graphics=2,text=0)
                    page.show_pdf_page(box,transformed,0,overlay=True)
                reports.append({'region_id':region['id'],'status':'processed_requires_review','run_dir':event['run_dir'],
                                'raster':raster,'intended_text_placements':placements})
        yield {'result':pdf.tobytes(garbage=4,deflate=True),'reports':reports}
