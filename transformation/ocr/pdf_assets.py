"""OCR and transform isolated image regions in otherwise digital PDF pages."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pymupdf
import time

# Figures of a PII-only job are independent: their sub-runs overlap OCR (serial in
# the shared OCR session, but short) with model calls (parallel in Ollama).
PII_FIGURE_WORKERS = 6
# Translated figures each run BabelDOC (mostly single-threaded CPU work: layout, font subsetting,
# saving). One at a time made a page with six pictures take about 45 s on an L40S server with slow
# cores (2026-11-19). Where both models stay loaded they run in parallel; where the models take turns
# on one GPU (TRANSFORMX_GPU_SWAP) parallel figures would switch models back and forth, so one at a time.
TRANSLATION_FIGURE_WORKERS = 3


def translation_figure_workers():
    import os
    value = os.environ.get('TRANSFORMX_FIGURE_WORKERS', '')
    if value.isdigit() and int(value) > 0:
        return int(value)
    return 1 if os.environ.get('TRANSFORMX_GPU_SWAP') else TRANSLATION_FIGURE_WORKERS


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


def figure_candidates(figures,words,page_rect):
    """The figure regions to process: overlapping ones merged (merge_overlapping), except that a
    merged region covering native text is replaced by its members that do not.

    Two extractors can box one picture differently: the picture itself, and a wider box that also
    takes in the text beside it. Their union then covered text and the whole picture was skipped,
    its text left untranslated (a warning label in a manual, 2026-11-21 competition set)."""
    def on_text(region):
        box=pymupdf.Rect(region['bbox'])&page_rect
        return not box.is_empty and any(overlaps_text(w,box) for w in words)
    candidates=[]
    for region in merge_overlapping(figures,page_rect):
        members=[r for r in figures if r['id'] in region.get('merged_region_ids',[])]
        if members and on_text(region):
            clean=[r for r in members if not on_text(r)]
            if clean:
                candidates.extend(merge_overlapping(clean,page_rect)); continue
        candidates.append(region)
    return candidates


def process(data,ir,run,translate,redact,categories,assist,options):
    reports=[]
    started=time.monotonic()
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        jobs=[]
        for unit in ir['units']:
            if unit.get('ocr_folder'): continue
            page=pdf[unit['number']-1]
            words=[pymupdf.Rect(w[:4]) for w in page.get_text('words')]
            candidates=figure_candidates([r for r in unit['regions'] if r['kind']=='figure'],words,page.rect)
            for region in candidates:
                box=pymupdf.Rect(region['bbox']) & page.rect
                if box.is_empty:
                    reports.append({'region_id':region['id'],'status':'blocked','source_preserved':True,
                                    'reason':'Figure has no visible area inside the page.'}); continue
                # A word box includes empty space above its letters, so a caption just below a
                # picture can touch its edge. Only text meaningfully inside the figure (more
                # than 30% of the word's box) means the figure overlaps native text.
                if any(overlaps_text(w,box) for w in words):
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
        workers=PII_FIGURE_WORKERS if (redact and not translate) else translation_figure_workers()
        results={}
        if translate and redact and len(jobs)>1:
            # On a GPU shared by the PII and translation models (transformation/llm/gpu_share.py) each
            # figure would switch models twice, and waking the translation model costs about 10 s
            # (measured 2026-11-16, RTX 4090). First detect PII in every figure in parallel, keeping
            # the answers for this job only; the figures' full runs below then reuse them.
            memo=str(Path(run)/'figure-pii-memo')
            for job in jobs: job['options']['pii_memo']=memo
            with ThreadPoolExecutor(max_workers=max(1,min(PII_FIGURE_WORKERS,len(jobs)))) as pool:
                futures=[pool.submit(_run_figure,job['payload'],run,translate,redact,categories,assist,
                                     dict(job['options'],stop_after_pii=True)) for job in jobs]
                for future in as_completed(futures):
                    try: future.result()
                    except Exception: pass  # the full run below detects again and reports any failure
                    yield {'region_id':None,'progress':{'stage':'figure_pii_checked'}}
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
            masked_here=redact and event.get('validation',{}).get('pii_findings')!=[]
            if event.get('engine_run',{}).get('source_pages_preserved_for_review') and not masked_here:
                reports.append({'region_id':region['id'],'status':'source_preserved_requires_review',
                                'source_preserved':True,'run_dir':event['run_dir'],
                                'reason':'No confidently translatable OCR text; source image retained.'})
                continue
            # With masking on, values selected in this image are masked only in the figure run's output:
            # the page's own image still shows them (combined jobs mask figure text here). So even when
            # its text could not be translated, the masked figure replaces the source (2026-11-16 fix).
            untranslated=bool(event.get('engine_run',{}).get('source_pages_preserved_for_review'))
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
            reports.append({'region_id':region['id'],'status':'masked_untranslated_requires_review' if untranslated
                            else 'processed_requires_review','run_dir':event['run_dir'],
                            'raster':raster,'intended_text_placements':placements})
        return pdf.tobytes(garbage=4,deflate=True),reports
