"""OCR and transform isolated image regions in otherwise digital PDF pages."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pymupdf
import time

# Figures of a PII-only job are independent: their sub-runs overlap OCR (serial in
# the shared OCR session, but short) with model calls (parallel in Ollama).
# Translation keeps one figure at a time (BabelDOC sub-runs are heavy).
PII_FIGURE_WORKERS = 6


def _run_figure(payload, run, translate, redact, categories, assist, suboptions):
    from transformation.pipeline import run_pipeline
    # An unprocessed figure keeps its pixels, so in a redaction job one transient local
    # I/O failure (e.g. a busy OCR socket) gets exactly one fresh retry before blocking.
    for attempt in range(2 if redact else 1):
        event = None
        try:
            for event in run_pipeline(payload, 'figure.pdf', root=Path(run) / 'figure-runs', translate=translate,
                                      redact=redact, pii_categories=categories, pii_assist=assist,
                                      table_strategy='native', **suboptions):
                pass
            return event
        except OSError:
            if attempt or not redact: raise
            time.sleep(.5)


def overlaps_text(word,box,share=.3):
    """True when more than `share` of a word's box lies inside the figure box."""
    area=word.get_area()
    return area>0 and (word&box).get_area()>share*area


def merge_overlapping(regions,page_rect):
    """Figure candidates whose areas overlap become one candidate covering their union.

    Two pictures that touch or overlap (badges, stacked screenshots) used to leave all but
    the first unprocessed, so their pixels kept any text. One crop of the union is OCR'd and
    written back as a whole; the union is still refused where it overlaps native text.
    """
    groups=[]
    for region in regions:
        box=pymupdf.Rect(region['bbox'])&page_rect
        if box.is_empty:
            groups.append({'box':box,'regions':[region]});continue
        joined=[g for g in groups if not g['box'].is_empty and (g['box']&box).get_area()>0]
        for g in joined:
            groups.remove(g);box|=g['box']
        groups.append({'box':box,'regions':[r for g in joined for r in g['regions']]+[region]})
        # A union can now reach a group it did not overlap before: merge until stable.
        changed=True
        while changed:
            changed=False
            last=groups[-1]
            for g in groups[:-1]:
                if not g['box'].is_empty and (g['box']&last['box']).get_area()>0:
                    groups.remove(g);last['box']|=g['box'];last['regions']=g['regions']+last['regions'];changed=True;break
    merged=[]
    for g in groups:
        first=g['regions'][0]
        if len(g['regions'])==1:merged.append(first);continue
        merged.append(dict(first,bbox=list(g['box']),merged_region_ids=[r['id'] for r in g['regions']]))
    return merged


def process(data,ir,run,translate,redact,categories,assist,options):
    reports=[]
    started=time.monotonic()
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        jobs=[]
        for unit in ir['units']:
            if unit.get('ocr_folder'): continue
            page=pdf[unit['number']-1]
            candidates=merge_overlapping([r for r in unit['regions'] if r['kind']=='figure'],page.rect)
            for region in candidates:
                box=pymupdf.Rect(region['bbox']) & page.rect
                if box.is_empty:
                    reports.append({'region_id':region['id'],'status':'blocked','source_preserved':True,
                                    'reason':'Figure has no visible area inside the page.'}); continue
                # A word box includes empty space above its letters, so a caption just below a
                # picture can touch its edge. Only text meaningfully inside the figure (more
                # than 30% of the word's box) means the figure overlaps native text.
                if any(overlaps_text(pymupdf.Rect(w[:4]),box) for w in page.get_text('words')):
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
                # The parent document's rendered-output privacy check covers this figure on
                # its page; a second fresh-OCR check per figure only repeats that work.
                suboptions['privacy_verification']=False
                # Concurrent sub-runs must not change process-wide settings.
                suboptions['llm_metrics']=False
                jobs.append({'unit':unit,'region':region,'box':box,'raster':raster,'payload':payload,'options':suboptions})
        workers=PII_FIGURE_WORKERS if (redact and not translate) else 1
        results={}
        if jobs:
            with ThreadPoolExecutor(max_workers=max(1,min(workers,len(jobs)))) as pool:
                futures={pool.submit(_run_figure,job['payload'],run,translate,redact,categories,assist,job['options']):index
                         for index,job in enumerate(jobs)}
                for future in as_completed(futures):
                    index=futures[future];results[index]=future.result()
                    yield {'region_id':jobs[index]['region']['id'],'progress':{'stage':'figure_completed','completed':len(results),'total':len(jobs)}}
    result,reports=apply_results(data,jobs,results,translate,redact,reports)
    yield {'result':result,'reports':reports}


def apply_results(processed,jobs,results,translate,redact,reports):
    """Apply figure results to the processed PDF in document order."""
    with pymupdf.open(stream=processed,filetype='pdf') as pdf:
        # Results are applied in document order, one page edit at a time.
        for index,job in enumerate(jobs):
            event,region,box,unit,raster=results[index],job['region'],job['box'],job['unit'],job['raster']
            page=pdf[unit['number']-1]
            if event is None or event['status']!='generated':
                reason=(event or {}).get('error','Image OCR failed.')
                reports.append({'region_id':region['id'],'status':'no_text_detected_requires_review' if reason.startswith('No OCR text detected') else 'blocked',
                                'source_preserved':True,'reason':reason}); continue
            if redact and not translate and not event.get('validation',{}).get('pii_findings'):
                # Nothing selected in this image: keep the original image object untouched
                # instead of replacing it with a re-rendered copy.
                reports.append({'region_id':region['id'],'status':'no_selected_values','source_preserved':True,
                                'run_dir':event['run_dir'],'raster':raster})
                continue
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
        return pdf.tobytes(garbage=4,deflate=True),reports
