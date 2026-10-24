"""Page orchestration over the existing native and scanned PDF workers."""
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4
import pymupdf
from transformation.analysis.document_profile import profile_pdf
from transformation.pdf.runner import run_pdf, write_manifest


def run_document(data, filename, **options):
    page_mode = options.pop('pdf_page_mode', 'page')
    if page_mode not in {'page', 'document'}:
        raise ValueError('pdf_page_mode must be page or document.')
    profile = profile_pdf(data)
    preserve_pages={u['number']-1 for u in (options.get('external_ir') or {}).get('units',[])
                    if u.get('preserve_source_only') and 1<=u['number']<=len(profile['pages']) and profile['pages'][u['number']-1]['kind']=='image_only'}
    with pymupdf.open(stream=data,filetype='pdf') as inspected:
        blank_pages={i for i,p in enumerate(inspected) if not p.get_text().strip() and not p.get_image_info() and not p.get_drawings()}
    if len(blank_pages)==len(profile['pages']):
        root=Path(options.get('root','runs/transformation')).absolute()
        run=root/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]); (run/'output').mkdir(parents=True)
        output=run/'output/translated.pdf'; output.write_bytes(data)
        info={'status':'generated','run_dir':str(run),'output_pdf':str(output),'pages':len(blank_pages),'blank_pages_preserved':True}
        write_manifest(run,info); yield info; return
    if len(profile['pages']) == 1:
        if 0 in preserve_pages:
            root=Path(options.get('root','runs/transformation')).absolute()
            run=root/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]);(run/'output').mkdir(parents=True)
            output=run/'output/source-preserved.pdf';output.write_bytes(data)
            info={'status':'generated','run_dir':str(run),'output_pdf':str(output),'pages':1,
                  'review_status':'requires_human_review','source_pages_preserved_for_review':[1],
                  'warnings':{'translation_quality_validated':False,'reason':'All detected text is uncertain; source retained without translation.'}}
            write_manifest(run,info);yield info;return
        if profile['pages'][0]['kind'] in {'digital','mixed'} and not options.get('ocr_recognition_dir'):
            options = dict(options)
            options.pop('ocr_python',None)
            options.pop('ocr_remote',None)
        yield from run_pdf(data, filename, **options)
        return
    if options.get('ocr_recognition_dir'):
        raise ValueError('Saved single-page OCR cannot be reused for a multi-page document.')
    needs_ocr = any(p['kind'] == 'image_only' and i not in preserve_pages for i,p in enumerate(profile['pages']))
    if needs_ocr and not (options.get('ocr_python') or options.get('ocr_remote')):
        raise ValueError('This document contains scanned pages. Select a fresh PaddleOCR mode.')
    if any((p['kind'] == 'vector_or_empty' and i not in blank_pages) or (p['kind']=='image_only' and p['rotation']) for i,p in enumerate(profile['pages'])):
        raise ValueError('Vector-only, empty or rotated scanned pages need review before processing.')
    # BabelDOC already handles multi-page native PDFs. Keep original page IDs in
    # external_ir and load its parser/layout model once for the whole document.
    if (page_mode == 'document' and not blank_pages and not preserve_pages
            and all(p['kind'] in {'digital','mixed'} and not p['rotation'] for p in profile['pages'])):
        for key in ('ocr_python','ocr_remote','ocr_recognition_dir'):
            options.pop(key,None)
        for event in run_pdf(data, filename, **options):
            if event.get('status') == 'generated':
                try:
                    with pymupdf.open(event['output_pdf']) as output:
                        if len(output) != len(profile['pages']):
                            raise ValueError('Document output page count mismatch.')
                except Exception as exc:
                    event=dict(event,status='failed',error=str(exc))
                    event.pop('output_pdf',None)
                    write_manifest(Path(event['run_dir']),event)
            yield event
        return
    root = Path(options.get('root', 'runs/transformation')).resolve()
    run = root / (time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
    (run/'output').mkdir(parents=True)
    (run/'source.pdf').write_bytes(data)
    info = dict(run_id=run.name, run_dir=str(run), source_name=filename,
                source_sha256=hashlib.sha256(data).hexdigest(), pages=len(profile['pages']),
                status='starting', review_status='requires_human_review', pii_redaction=False,
                page_runs=[], document_profile=profile)
    started = time.monotonic()
    def save():
        info['seconds'] = round(time.monotonic()-started,1)
        write_manifest(run,info)
        return dict(info)
    yield save()
    try:
        with pymupdf.open(stream=data,filetype='pdf') as source, pymupdf.open() as merged:
            for i, page_profile in enumerate(profile['pages']):
                page_options = dict(options, root=run/'pages')
                if options.get('external_ir'):
                    from copy import deepcopy
                    subset=deepcopy(options['external_ir'])
                    subset['units']=[u for u in subset['units'] if u.get('number')==i+1]
                    for unit in subset['units']: unit['number']=1
                    page_options['external_ir']=subset
                remaining = options.get('timeout',3600)-(time.monotonic()-started)
                if remaining <= 0:
                    raise TimeoutError('Document run exceeded its total time budget.')
                page_options['timeout'] = remaining
                if page_profile['kind'] != 'image_only':
                    for key in ('ocr_python','ocr_remote','ocr_recognition_dir'):
                        page_options.pop(key,None)
                with pymupdf.open() as single:
                    single.insert_pdf(source,from_page=i,to_page=i)
                    page_data = single.tobytes()
                if i in blank_pages or i in preserve_pages:
                    merged.insert_pdf(source,from_page=i,to_page=i)
                    info['page_runs'].append(dict(page=i+1,status='source_preserved_requires_review' if i in preserve_pages else 'blank_preserved'))
                    if i in preserve_pages:info.setdefault('source_pages_preserved_for_review',[]).append(i+1)
                    yield save(); continue
                info['current_page'] = i+1
                for event in run_pdf(page_data,f'page-{i+1}.pdf',**page_options):
                    info['status'] = 'processing'
                    info['page_progress'] = event
                    info['translation_progress'] = event.get('translation_progress',{})
                    yield save()
                info['page_runs'].append(dict(page=i+1,run_dir=event['run_dir'],status=event['status']))
                if event['status'] != 'generated':
                    raise RuntimeError(f"Page {i+1} failed: {event.get('error','no output')}")
                with pymupdf.open(event['output_pdf']) as output:
                    merged.insert_pdf(output)
            destination = run/'output'/'translated.pdf'
            merged.save(destination)
        with pymupdf.open(destination) as verified:
            if len(verified) != len(profile['pages']):
                raise ValueError('Merged output page count mismatch.')
        info.update(status='generated',output_pdf=str(destination),structural_checks={'opens':True,'page_count_matches':True})
        info['warnings'] = {'translation_quality_validated':False,'cross_page_context_preserved':False}
        (run/'page-review.json').write_text(json.dumps(info['page_runs'],indent=2),encoding='utf-8')
        yield save()
    except Exception as exc:
        info.update(status='failed',error=str(exc))
        yield save()
    finally:
        if info['status'] in {'starting','processing'}:
            info.update(status='interrupted',error='Document processing stopped before all pages completed.')
            save()
