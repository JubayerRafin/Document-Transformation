"""Shared document engine, executed locally or in an isolated server job."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4
import pymupdf

from extraction.coordinator import extract_pdf,content_ir,text_regions
from transformation.office.package_ir import inspect_package,extract_package,write_package
from transformation.validation.privacy import detect,mask
from transformation.pdf.runner import write_manifest,LANGUAGES
from transformation.profiling import timed


def save_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def redact_pdf(data,ir,findings):
    regions={r['id']:r for r in text_regions(ir)}
    for finding in findings:
        if finding['region_id'] not in regions or not finding.get('value') or finding['value'] not in regions[finding['region_id']]['text']:
            raise ValueError('PII redaction requires an exact source-region finding.')
    values=sorted({f['value'] for f in findings},key=len,reverse=True)
    audit=[]
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        for unit in ir['units']:
            page=pdf[unit['number']-1]
            native=bool(page.get_text().strip())
            unit_regions=text_regions({'units':[unit]})
            if native:
                # A selected value stays sensitive at every occurrence, including
                # pages where the model did not propose it a second time.
                for value in values:
                    matches=page.search_for(value)
                    expected=[r for r in unit_regions if value in r.get('text','')]
                    if expected and not matches:raise ValueError('PII value could not be located safely in the PDF.')
                    for box in matches:page.add_redact_annot(box,fill=(0,0,0))
                    for region in expected:
                        audit.append({'region_id':region['id'],'method':'exact_search','review_required':True})
            else:
                for region in unit_regions:
                    selected=[v for v in values if v in region.get('text','')]
                    if not selected:continue
                    box=(pymupdf.Rect(region['bbox'])+(-2,-2,2,2))&page.rect
                    if box.is_empty:raise ValueError('PII value could not be located safely in the PDF.')
                    page.add_redact_annot(box,fill=(0,0,0))
                    audit.append({'region_id':region['id'],'method':'whole_ocr_line',
                                  'selected_value_occurrences':sum(region['text'].count(v) for v in selected),
                                  'propagated_exact_value':not any(f['region_id']==region['id'] for f in findings),
                                  'review_required':True})
            page.apply_redactions(images=2,graphics=2,text=0)
        pdf.set_metadata({}); pdf.del_xml_metadata()
        for name in pdf.embfile_names(): pdf.embfile_del(name)
        result=pdf.tobytes(garbage=4,deflate=True,clean=True)
    with pymupdf.open(stream=result,filetype='pdf') as verified:
        if any(page.search_for(v) for page in verified for v in values):
            raise ValueError('Selected sensitive text remains searchable after redaction.')
    return result,audit


def run_pipeline(data,filename,*,output_kind='pdf',redact=False,pii_categories=(),pii_assist=True,
                 visual_analysis=False,table_strategy='auto',translate=True,root=Path('runs/transformation'),**options):
    if not isinstance(data,(bytes,bytearray,memoryview)) or not len(data):
        raise ValueError('No document bytes are available. Remove the old upload and upload the document again before starting.')
    data=bytes(data)
    source_extraction_cache=options.pop('source_extraction_cache',None)
    isolated_figure_slots=options.pop('isolated_figure_slots',False)
    figure_engine=options.pop('figure_engine','babeldoc')
    if figure_engine not in {'babeldoc','ocr-slots'}:
        raise ValueError('figure_engine must be babeldoc or ocr-slots.')
    if isolated_figure_slots and redact:
        raise ValueError('Figure slot mode supports translation only.')
    nativeized_ir_mode=options.pop('nativeized_ir_mode','reextract')
    if nativeized_ir_mode not in {'reextract','reuse'}:
        raise ValueError('nativeized_ir_mode must be reextract or reuse.')
    kind=Path(filename).suffix.lower().lstrip('.')
    if kind not in {'pdf','docx','pptx'} or output_kind not in {'pdf','docx','pptx'}:
        raise ValueError('Choose PDF, DOCX or PPTX.')
    if not translate and not redact: raise ValueError('Select translation, PII redaction, or both.')
    if redact and not pii_categories: raise ValueError('Select at least one PII category.')
    if redact and not pii_assist and any(c not in {'Email addresses','Phone numbers'} for c in pii_categories):
        raise ValueError('Names, addresses, identifiers and bank accounts require model-assisted detection.')
    started=time.monotonic(); limit=options.get('timeout',3600)
    run=Path(root).resolve()/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]); (run/'output').mkdir(parents=True)
    source=run/('source.'+kind); source.write_bytes(data)
    info={'run_id':run.name,'run_dir':str(run),'source_name':filename,'source_format':kind,'output_format':output_kind,
          'source_sha256':hashlib.sha256(data).hexdigest(),'status':'starting','stage':'analysis',
          'integration_version':'separate-workflows-v3-deep-doc','review_status':'requires_human_review',
          'pii_redaction':redact,'warnings':[],'operations':{'translate':translate,'redact':redact},
          'protected_values':options.get('protected_values',[]),'document_context':options.get('document_context','')}
    def emit(stage):
        if stage!='failed' and time.monotonic()-started>limit: raise TimeoutError('Document exceeded the run time limit.')
        info.update(stage=stage,seconds=round(time.monotonic()-started,1)); write_manifest(run,info); return deepcopy(info)
    endpoint=options.get('translation_endpoint',options.get('endpoint','http://127.0.0.1:11434/v1'))
    model=options.get('translation_model',options.get('model','translategemma:12b'))
    pii_endpoint=options.get('pii_endpoint',options.get('endpoint','http://127.0.0.1:11434/v1'))
    pii_model=options.get('pii_model',options.get('model','qwen3.5:9b'))
    options=dict(options,model=model,endpoint=endpoint,translation_model=model,translation_endpoint=endpoint,
                 pii_model=pii_model,pii_endpoint=pii_endpoint)
    info['workflow']='translation_and_pii' if translate and redact else 'translation' if translate else 'pii'
    info['figure_translation_engine']=figure_engine
    info['isolated_figure_slots']=bool(isolated_figure_slots)
    info['source_language']=options.get('source','en')
    info['target_language']=options.get('target','ko') if translate else None
    info['models']={'translation':{'model':model,'endpoint':endpoint,'enabled':translate},
                    'pii':{'model':pii_model,'endpoint':pii_endpoint,'enabled':redact}}
    yield emit('analysis')
    try:
        from transformation.analysis.document_analysis import analyze_pdf
        visual_endpoint,visual_model=(endpoint,model) if translate else (pii_endpoint,pii_model)
        info['models']['visual_analysis']={'model':visual_model,'endpoint':visual_endpoint,'enabled':visual_analysis}
        analysis=analyze_pdf(data,visual_endpoint,visual_model,visual_analysis) if kind=='pdf' else inspect_package(data,kind)
        if kind!='pdf' and visual_analysis:
            from transformation.office.office import to_pdf
            preview=to_pdf(data,filename)
            analysis['visual_analysis']=analyze_pdf(preview,visual_endpoint,visual_model,True)
        save_json(run/'document-analysis.json',analysis)
        info['status']='processing'; yield emit('extraction')
        with timed(run,'source_extraction',strategy=table_strategy):
            if kind=='pdf' and source_extraction_cache:
                from extraction.source_cache import extract as extract_cached
                document,evidence=extract_cached(source,run,table_strategy,analysis,source_extraction_cache)
                info['source_extraction_cache']=evidence
                ir=content_ir(document)
            else:
                ir=content_ir(extract_pdf(source,run,table_strategy,analysis,defer_figures=True)) if kind=='pdf' else extract_package(data,filename)
        # Scanned pages receive OCR before transformation and PII detection.
        if kind=='pdf':
            from transformation.analysis.document_profile import profile_pdf
            profile=profile_pdf(data)
            for unit,profile_page in zip(ir['units'],profile['pages']):
                if profile_page['kind'] not in {'image_only','vector_or_empty'}: continue
                folder=run/'ocr'/unit['id']; folder.mkdir(parents=True)
                with pymupdf.open(stream=data,filetype='pdf') as pdf, pymupdf.open() as single:
                    original_page=pdf[unit['number']-1]
                    if profile_page['kind']=='vector_or_empty' and not original_page.get_drawings():
                        unit['blank_page']=True; continue
                    # Outlined text and rotated scans become an upright raster
                    # for the same Paddle route; original upload stays untouched.
                    if original_page.rotation or profile_page['kind']=='vector_or_empty':
                        raster=single.new_page(width=original_page.rect.width,height=original_page.rect.height)
                        raster.insert_image(raster.rect,pixmap=original_page.get_pixmap(alpha=False))
                    else: single.insert_pdf(pdf,from_page=unit['number']-1,to_page=unit['number']-1)
                    page_data=single.tobytes(); (folder/'source.pdf').write_bytes(page_data)
                recognition=options.get('ocr_recognition_dir')
                cache_folder=None
                if not recognition and options.get('ocr_cache_root'):
                    from transformation.ocr.cache import location,lookup
                    import os
                    cache_folder=location(page_data,options.get('source','en'),'gpu' if options.get('ocr_remote') or os.environ.get('TRANSFORMX_OCR_DEVICE')=='gpu:0' else 'cpu',options['ocr_cache_root'])
                    recognition=lookup(cache_folder)
                    if recognition:
                        info['ocr_progress']={'stage':'reused_verified_source_evidence','recognition_dir':recognition}
                        yield emit('extraction')
                if not recognition:
                    ocr_output=folder/'paddle'
                    if cache_folder:
                        cache_folder.mkdir(parents=True,exist_ok=True)
                        ocr_output=cache_folder/('attempt-'+uuid4().hex[:8])
                    if options.get('ocr_remote'):
                        from transformation.ocr.remote_ocr import run_remote
                        events=run_remote(folder/'source.pdf',ocr_output,options['ocr_remote'],timeout=max(1,min(int(limit-(time.monotonic()-started)),1200)),source_language=options.get('source','en'))
                    elif options.get('ocr_python'):
                        from transformation.ocr.paddle_pipeline import run_ocr
                        events=run_ocr(folder/'source.pdf',ocr_output,options['ocr_python'],timeout=max(1,min(int(limit-(time.monotonic()-started)),1200)),source_language=options.get('source','en'))
                    else: raise ValueError('Scanned pages require a PaddleOCR mode before transformation.')
                    for progress in events:
                        info['ocr_progress']=progress; yield emit('extraction')
                    recognition=progress['recognition_dir']
                elif options.get('ocr_recognition_dir') and len(ir['units'])!=1: raise ValueError('Saved OCR supports only a matching single-page upload.')
                from transformation.ocr.paddle_handoff import prepare
                try:
                    with timed(run,'ocr_handoff',page=unit['number']):
                        prepare(page_data,recognition,folder,filename)
                except ValueError as exc:
                    # The handoff emits this only after verifying source image
                    # and detector hashes. Reuse validated empty evidence too.
                    if cache_folder and str(exc).startswith('No OCR text detected'):
                        from transformation.ocr.cache import publish
                        publish(cache_folder,recognition)
                    raise
                if cache_folder:
                    from transformation.ocr.cache import publish
                    publish(cache_folder,recognition)
                observed=json.loads((folder/'ocr-document.json').read_text(encoding='utf-8'))
                unit['regions']=observed['pages'][0]['regions']
                for region in unit['regions']: region['id']=unit['id']+':'+region['id']
                unit['ocr_recognition_dir']=str(recognition)
                unit['ocr_folder']=str(folder)
                ir['warnings'].extend(observed['warnings'])
        if kind=='pdf' and any(u.get('ocr_folder') for u in ir['units']):
            from extraction.backends.raster_tables import enrich as enrich_raster_tables
            enrich_raster_tables(data,ir)
        if kind=='pdf':
            from extraction.figures import enrich_figures
            with timed(run,'spatial_figures'):
                enrich_figures(source,ir,run/'extraction')
        from transformation.llm.terminology import schema_terms
        terms,proposals=schema_terms(ir,options.get('terminology'))
        options['terminology']=terms
        info['terminology']=terms
        save_json(run/'document-terminology.json',{'terms':terms,'source_proposals':proposals,
            'policy':'preserve_model_names_in_explicit_product_columns; configured terms take precedence',
            'review_status':'source_schema_evidence; not human-approved'})
        save_json(run/'source-ir.json',ir)
        info['components']={'analysis':'visual_and_structural' if visual_analysis else 'structural',
            'extraction':ir['backend'],'shared_ir':'transformx-content-1',
            'native_office_reconstruction':'original_ooxml_package',
            'pdf_reconstruction':'babeldoc_with_guarded_ocr_nativeization',
            'office_images':'same_paddle_and_translation_pipeline_when_configured'}
        safe_data=data; findings=[]
        if kind!='pdf' and ir['assets']:
            if options.get('ocr_python') or options.get('ocr_remote'):
                from transformation.office.office_assets import process
                for asset_event in process(safe_data,filename,run,translate,redact,pii_categories,pii_assist,options):
                    if 'result' in asset_event: safe_data=asset_event['result']; info['image_processing']=asset_event['reports']
                    else: info['image_progress']=asset_event['part']; yield emit('image_ocr')
            else: info['warnings'].append('Office images require a fresh PaddleOCR mode; image translation and PII coverage are blocked.')
        if redact:
            yield emit('pii_detection')
            findings=detect(text_regions(ir),pii_categories,pii_endpoint,pii_model,pii_assist)
            save_json(run/'pii-findings.json',findings)
            values=[f['value'] for f in findings]
            if kind=='pdf':
                safe_data,redaction_audit=redact_pdf(data,ir,findings)
                save_json(run/'redaction-audit.json',redaction_audit)
            else:
                updates={r['id']:mask(r['text'],values) for r in text_regions(ir)}
                safe_data,notes=write_package(safe_data,kind,updates,values); info['warnings'].extend(notes)
            info['warnings'].append('PII detection is provisional. Image text, OCR errors, embedded objects and detector omissions require review.')
        safe_source=run/('processing-source.'+kind); safe_source.write_bytes(safe_data)
        purposes=[p['purpose'] for p in analysis.get('visual_pages',[])]
        safe_context=mask(options.get('document_context','')+('\nProvisional visual purpose: '+json.dumps(purposes,ensure_ascii=False)[:500] if purposes else ''),[f['value'] for f in findings])
        if kind=='pdf':
            if translate:
                if any(u.get('ocr_folder') for u in ir['units']):
                    from transformation.pdf.scan_native import prepare_native
                    from transformation.ocr.source_lines import group_words
                    ocr_ir,source_groups=group_words(ir,[f['value'] for f in findings])
                    if source_groups:
                        save_json(run/'ocr-source-groups.json',source_groups)
                        save_json(run/'ocr-grouped-source-ir.json',ocr_ir)
                    safe_data,scan_blockers=prepare_native(safe_data,ocr_ir,run,[f['value'] for f in findings])
                    info['scan_acceptance_blockers']=scan_blockers
                    native_path=run/'ocr-nativeized.pdf'; native_path.write_bytes(safe_data)
                    with timed(run,'nativeized_extraction',strategy=table_strategy,mode=nativeized_ir_mode):
                        if nativeized_ir_mode=='reuse':
                            from transformation.pdf.nativeized_ir import refresh
                            native_ir=refresh(native_path,run,ocr_ir,analysis)
                        else:
                            native_ir=content_ir(extract_pdf(native_path,run,table_strategy,analysis))
                    for original_unit,native_unit in zip(ocr_ir['units'],native_ir['units']):
                        native_unit['regions'].extend(deepcopy(r) for r in original_unit['regions'] if r['kind']=='table' and r.get('metadata',{}).get('method')=='source_raster_ruled_grid')
                        if original_unit.get('ocr_folder'):
                            report=json.loads((Path(original_unit['ocr_folder'])/'native/scan-reconstruction.json').read_text(encoding='utf-8'))
                            by_id={r['id']:r for r in original_unit['regions']}
                            native_unit['locked_regions']=[dict(by_id[r['id']],text=r['translated_text'],
                                slot_bbox=r['fitting_bbox'],source_font_size=r['font_size'],font_policy=r.get('font_policy',{})) for r in report['regions'] if r['status']=='replaced_requires_review']
                    with pymupdf.open(stream=safe_data,filetype='pdf') as native_pdf:
                        for original_unit,native_unit in zip(ir['units'],native_ir['units']):
                            native_unit['preserve_source_only']=bool(original_unit.get('ocr_folder')) and not native_pdf[original_unit['number']-1].get_text().strip()
                    # Preserve OCR uncertainty alongside the nativeized content.
                    native_ir['ocr_acceptance_blockers']=scan_blockers
                    save_json(run/'ocr-nativeized-ir.json',native_ir)
                from transformation.pdf.document_runner import run_document
                work_options=dict(options,root=run/'engine',document_context=safe_context,translate=True)
                if redact: work_options['protected_values']=list(options.get('protected_values',[]))+['[REDACTED]']
                for key in ('translation_model','translation_endpoint','pii_model','pii_endpoint','ocr_cache_root'): work_options.pop(key,None)
                from extraction.context_bridge import build,masked_context_ir
                context_ir=masked_context_ir(native_ir if any(u.get('ocr_folder') for u in ir['units']) else ir,
                    [f['value'] for f in findings])
                work_options['external_context']=build(context_ir)
                work_options['external_ir']=context_ir
                work_options['timeout']=max(1,int(limit-(time.monotonic()-started)))
                if any(u.get('ocr_folder') for u in ir['units']):
                    for key in ('ocr_recognition_dir','ocr_python','ocr_remote'): work_options.pop(key,None)
                if isolated_figure_slots:
                    if redact or len(ir['units'])!=1 or not ir['units'][0].get('ocr_folder'):
                        raise ValueError('Figure slot mode requires one verified OCR page and translation only.')
                    from transformation.pdf.figure_runner import run_figure
                    engine_events=run_figure(source.read_bytes(),ir['units'][0],native_ir['units'][0].get('locked_regions',[]),
                                             run/'engine',work_options)
                else:
                    engine_events=run_document(safe_data,filename,**work_options)
                for event in engine_events:
                    info['engine_run']=event; info['translation_progress']=event.get('translation_progress',{})
                    yield emit('translation_and_reconstruction')
                if event['status']!='generated': raise RuntimeError(event.get('error','PDF engine failed.'))
                processed=Path(event['output_pdf']).read_bytes(); info['engine_acceptance']=event.get('acceptance')
                engine_runs=[Path(event['run_dir'])]+[Path(p['run_dir']) for p in event.get('page_runs',[]) if p.get('run_dir')]
                from transformation.pdf.ir_bridge import collect_translations
                save_json(run/'ir-translations.json',collect_translations(context_ir,engine_runs))
                info['translation_errors']=sum(len(json.loads((p/'region-errors.json').read_text(encoding='utf-8'))) for p in engine_runs if (p/'region-errors.json').exists())
                info['warnings'].append('PDF translation uses native BabelDOC geometry with shared-IR context and a page/spatial association report. Ambiguous associations remain unassigned; external boxes do not overwrite native glyph geometry.')
            else: processed=safe_data
            if any(r['kind']=='figure' for u in ir['units'] if not u.get('ocr_folder') for r in u['regions']):
                if options.get('ocr_python') or options.get('ocr_remote'):
                    from transformation.ocr.pdf_assets import process
                    image_options=dict(options,figure_engine=figure_engine,timeout=max(1,int(limit-(time.monotonic()-started))))
                    for asset_event in process(processed,ir,run,translate,redact,pii_categories,pii_assist,image_options):
                        if 'result' in asset_event: processed=asset_event['result']; info['image_processing']=asset_event['reports']
                        else:
                            info['image_progress']=asset_event['region_id']
                            info['image_stage']=asset_event.get('progress',{}).get('stage')
                            info['image_ocr_progress']=asset_event.get('progress',{}).get('ocr_progress',{})
                            yield emit('image_ocr')
                else: info['warnings'].append('Mixed-page image text requires PaddleOCR; image coverage remains unverified.')
            final_source=run/'output'/'transformed.pdf'; final_source.write_bytes(processed)
            final_ir=content_ir(extract_pdf(final_source,run,'native'))
            if output_kind=='pdf': artifact=processed
            else:
                from transformation.office.layout_export import export
                artifact=export(processed,final_ir,output_kind)
                info['warnings'].append('Fixed-page Office export retains artwork as a raster background and editable text at page coordinates. Original vector objects and semantic table editing require review.')
        else:
            final_ir=extract_package(safe_data,filename); updates={}; errors=[]
            yield emit('translation' if translate else 'reconstruction')
            from transformation.llm.ollama_native import translate_paragraph
            from transformation.llm.translation_guard import validate_candidate,audit_candidate
            language=next((name for name,code in LANGUAGES.items() if code==options.get('target','ko')),options.get('target','ko'))
            terms=options.get('terminology',{})
            for region in text_regions(final_ir):
                transformed=region['text']; styled=None
                if translate:
                    try:
                        context=safe_context+('\nPreferred terminology: '+json.dumps(terms,ensure_ascii=False) if terms else '')
                        run_texts=region.get('metadata',{}).get('run_texts',[])
                        styled=None
                        for attempt in range(2):
                            if len(run_texts)>1:
                                from transformation.office.styled_translation import translate_runs
                                styled=translate_runs(endpoint,model,run_texts,language,list(options.get('protected_values',[]))+['[REDACTED]'],context,source_language=options.get('source','en'),terminology=terms)
                                transformed=''.join(styled); stats={}
                            else:
                                transformed,stats=translate_paragraph(endpoint,model,region['text'],language,
                                    list(options.get('protected_values',[]))+['[REDACTED]'],context,structured=len(region['text'])<=200,source_language=options.get('source','en'),terminology=terms)
                            validate_candidate(region['text'],transformed,list(options.get('protected_values',[]))+['[REDACTED]'])
                            if stats.get('skipped_model'): break
                            if model.startswith('translategemma:'): break
                            audit=audit_candidate(pii_endpoint,pii_model,region['text'],transformed,language,safe_context,list(options.get('protected_values',[]))+['[REDACTED]'])
                            if audit['faithful']: break
                            context+='\nPrior translation failed review: '+'; '.join(audit['issues'])
                        else: raise ValueError('Semantic review rejected this paragraph.')
                    except (ValueError,OSError,TimeoutError) as exc:
                        transformed=region['text']; styled=None; errors.append({'region_id':region['id'],'reason':str(exc)})
                updates[region['id']]=styled if styled is not None else transformed
                region['transformed_text']=transformed
                save_json(run/'transformed-ir.json',final_ir)
                info['translation_progress']={'requests_completed':len(updates)}; yield emit('translation')
            save_json(run/'region-errors.json',errors)
            if errors:
                info['warnings'].append(f'{len(errors)} paragraphs failed translation validation and retain their processing-source text.')
                if len(errors)==len(updates): raise RuntimeError('Every paragraph failed translation. See region-errors.json; no translated output was accepted.')
            from transformation.office.package_ir import refresh_tables
            refresh_tables(final_ir)
            yield emit('reconstruction')
            native,notes=write_package(safe_data,kind,updates,[f['value'] for f in findings]); info['warnings'].extend(notes)
            (run/'output'/('transformed.'+kind)).write_bytes(native)
            if output_kind==kind: artifact=native
            else:
                if output_kind=='pdf':
                    from transformation.office.office import to_pdf
                    artifact=to_pdf(native,filename)
                else:
                    from transformation.office.office import to_pdf
                    from transformation.office.layout_export import export as layout_export
                    rendered=to_pdf(native,filename); rendered_path=run/'office-rendered.pdf'; rendered_path.write_bytes(rendered)
                    rendered_ir=content_ir(extract_pdf(rendered_path,run,'native'))
                    artifact=layout_export(rendered,rendered_ir,output_kind)
                info['warnings'].append('Office cross-format conversion uses the rendered pagination. Artwork becomes a raster background; native same-format output retains original package objects.')
            info['translation_errors']=len(errors)
        yield emit('validation')
        if output_kind=='pptx':
            try:
                from transformation.office.office import fit_pptx
                artifact,fitted_preview,fit_report=fit_pptx(artifact)
                (run/'output'/'office-preview.pdf').write_bytes(fitted_preview)
                save_json(run/'office-fitting.json',fit_report)
            except (ValueError,RuntimeError,TimeoutError) as exc:
                info['warnings'].append('PowerPoint text fitting could not be measured: '+str(exc))
        destination=run/'output'/('result.'+output_kind); destination.write_bytes(artifact)
        if output_kind=='pdf':
            with pymupdf.open(stream=artifact,filetype='pdf') as pdf: info['pages']=len(pdf)
        else:
            inspect_package(artifact,output_kind)
            from transformation.office.package_ir import open_package
            with open_package(artifact,output_kind) as package:
                for name in package.namelist():
                    if name.endswith('.xml'):
                        from xml.etree.ElementTree import fromstring
                        fromstring(package.read(name))
            info['pages']=len(final_ir['units'])
        save_json(run/'transformed-ir.json',final_ir)
        from transformation.validation.output_validation import inspect_output
        all_values={f['value'] for f in findings}
        intended_overlays=[placement for item in info.get('image_processing',[]) for placement in item.get('intended_text_placements',[])]
        for child in run.rglob('pii-findings.json'):
            all_values.update(f['value'] for f in json.loads(child.read_text(encoding='utf-8')))
        with timed(run,'independent_output_validation'):
            if kind=='pdf' and output_kind=='pdf' and translate and any(u.get('ocr_folder') for u in ir['units']):
                font_regions=[{'page':u['number'],'bbox':r['slot_bbox'],'minimum':r['font_policy']['minimum']}
                              for u in native_ir['units'] for r in u.get('locked_regions',[]) if r.get('font_policy')]
                independent=inspect_output(artifact,output_kind,all_values,min_font_size=8.0,font_regions=font_regions,intended_overlays=intended_overlays)
            else:
                independent=inspect_output(artifact,output_kind,all_values,intended_overlays=intended_overlays)
        save_json(run/'independent-validation.json',independent)
        if independent['known_sensitive_residuals']:
            raise ValueError('Independent output validation found selected sensitive values in the artifact.')
        if output_kind!='pdf':
            try:
                from transformation.office.office import render
                preview,layout=render(artifact,'result.'+output_kind)
                (run/'output'/'office-preview.pdf').write_bytes(preview)
                save_json(run/'office-layout.json',layout)
                independent['office_overflow']=layout['overflow']
                independent['rendered_layout']=inspect_output(preview,'pdf')
            except (ValueError,RuntimeError,TimeoutError) as exc:
                independent['office_render_error']=str(exc)
        if redact and independent['uninspected_content'] and (options.get('ocr_python') or options.get('ocr_remote')):
            from transformation.validation.privacy_verification import verify
            for verification in verify(artifact,output_kind,run,all_values,pii_categories,pii_endpoint,pii_model,pii_assist,options):
                if 'reports' in verification: independent['rendered_privacy']=verification
                else: info['privacy_verification_page']=verification['page']; yield emit('privacy_verification')
            save_json(run/'privacy-verification.json',verification)
            if any(p['known_residual_values'] for p in verification['reports']):
                raise ValueError('Fresh output OCR found a selected sensitive value; the result is blocked.')
            if verification['passed']:
                independent['ocr_checked_content']=[c for c in independent['uninspected_content'] if c['kind'] in {'image','raster'}]
                independent['uninspected_content']=[c for c in independent['uninspected_content'] if c['kind'] not in {'image','raster'}]
        independent['acceptable']=not (independent['known_sensitive_residuals'] or independent['geometry_blockers'] or independent['layout_review_candidates'] or independent.get('office_overflow') or independent.get('office_render_error') or independent.get('rendered_layout',{}).get('geometry_blockers') or independent.get('rendered_layout',{}).get('layout_review_candidates') or info.get('translation_errors') or info.get('scan_acceptance_blockers') or independent['uninspected_content'])
        save_json(run/'independent-validation.json',independent)
        info['warnings']=list(dict.fromkeys(ir['warnings']+info['warnings']))
        info.update(status='generated',output_file=str(destination),
            source_ir=str(run/'source-ir.json'),transformed_ir=str(run/'transformed-ir.json'),
            validation={'opens':True,'human_approval':False,'pii_findings':len(findings),
                        'translation_errors':info.get('translation_errors',0),'pii_detection_complete':False if redact else None,
                        'independent_checks':independent,
                        'limitations':'Structural validation is not visual, semantic, or complete PII approval.'})
        if output_kind=='pdf': info['output_pdf']=str(destination)
        save_json(run/'validation.json',info['validation'])
        yield emit('complete')
    except Exception as exc:
        info.update(status='failed',error=str(exc)); yield emit('failed')
