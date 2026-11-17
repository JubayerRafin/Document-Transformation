"""Translate accepted OCR lines by stable region ID, without geometric reassignment."""
import json
import sys
import re
import hashlib
from pathlib import Path
import pymupdf
from transformation.llm.ollama_native import translate_paragraph
from transformation.pdf.runner import LANGUAGES


def observed_protected_values(text, values):
    result = list(values)
    for value in values:
        # Permit OCR whitespace variation, but never substitute different letters.
        pattern = r'(?<!\w)' + r'\s*'.join(re.escape(c) for c in value if not c.isspace()) + r'(?!\w)'
        result.extend(m.group(0) for m in re.finditer(pattern, text, re.I))
    return list(dict.fromkeys(result))


def main(run):
    from transformation.llm.ollama_native import record_progress
    record_progress('scan_worker_starting')
    run = Path(run)
    manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    ir = json.loads((run / 'ocr-document.json').read_text(encoding='utf-8'))
    language = next(k for k, v in LANGUAGES.items() if v == manifest['target_language'])
    records = {}
    checkpoint_path = run / 'region-translations.json'
    previous = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else {}
    errors = {}
    field_interpretations={}
    term_memory=dict(manifest.get('terminology',{}))
    from transformation.llm.entities import product_names
    try:
        inferred,entity_report=product_names(manifest['endpoint'],manifest['model'],ir['pages'][0]['regions'])
    except (ValueError,TimeoutError,ConnectionError,OSError) as exc:
        inferred=[]
        entity_report={'status':'failed','error':str(exc),'product_names':[]}
    (run/'entity-proposals.json').write_text(json.dumps(entity_report,ensure_ascii=False,indent=2),encoding='utf-8')
    from transformation.analysis.structure import structure_regions
    structure = structure_regions(ir['pages'][0]['regions'])
    (run/'document-structure.json').write_text(json.dumps(structure,indent=2),encoding='utf-8')
    (run/'transformation-plan.json').write_text(json.dumps({
        'schema_version':'1','source_sha256':ir['source_sha256'] if 'source_sha256' in ir else manifest.get('source_sha256'),
        'strategy':'region_translation_with_neighbor_context',
        'limitations':'Geometry supplies context hints, not inferred table cells or paragraphs.',
        'regions':[{'id':r['id'],'status':'blocked_ocr' if r.get('metadata',{}).get('requires_review') else 'pending'} for r in ir['pages'][0]['regions']]
    },indent=2),encoding='utf-8')
    for region in sorted(ir['pages'][0]['regions'],key=lambda r:(r.get('bbox',[0,0,0,0])[1],r.get('bbox',[0,0,0,0])[0])):
        if region.get('metadata', {}).get('requires_review'):
            continue
        # Protect observed case variants without silently rewriting OCR spelling.
        preserved=[v for v in inferred if term_memory.get(v,v)==v]
        values = observed_protected_values(region['text'], list(dict.fromkeys(manifest['protected_values']+preserved)))
        from transformation.llm.policy import expected_unchanged
        if expected_unchanged(region['text'],values):
            records[region['id']]={'source_text':region['text'],'text':region['text'],
                                  'stats':{'skipped_model':True,'preservation_reason':'exact_literal'},'context_region_ids':[]}
            from transformation.llm.ollama_native import record_progress
            record_progress('skipped',reason='exact_literal')
            (run/'region-translations.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            continue
        context = manifest['document_context']
        links=[r for r in structure['relationships'] if region['id'] in r['region_ids']]
        from transformation.llm.policy import nearby_context, column_examples
        neighbors = nearby_context(region, ir['pages'][0]['regions'])
        examples=column_examples(region,ir['pages'][0]['regions'])
        from transformation.llm.field_context import infer_field_context, needs_scope_context
        try:
            field_context,field_report=infer_field_context(region['text'],manifest['endpoint'],manifest['model'],manifest['document_context'])
            if field_context is not None:
                context=field_context
                field_interpretations[region['id']]=field_report
        except (ValueError,TimeoutError,ConnectionError,OSError) as exc:
            field_interpretations[region['id']]={'error':str(exc),'review_status':'requires_review'}
        (run/'field-interpretations.json').write_text(json.dumps(field_interpretations,ensure_ascii=False,indent=2),encoding='utf-8')
        # Raw neighboring text is evidence for structure review, never text passed
        # directly to the translator. It previously leaked into short labels.
        context=manifest['document_context']
        derived_label_type=None
        interpretation=field_interpretations.get(region['id'],{})
        if interpretation.get('value_kind') not in (None,'unknown'):
            context+='\nThe field value represents: '+interpretation['value_kind']
        elif examples and len(region['text'].split())<=6 and re.fullmatch(r'[A-Za-z][A-Za-z /()-]{0,60}',region['text']) and any(link['type']=='same_row_candidate' and len(link['region_ids'])>=3 for link in links):
            try:
                context,header_report=infer_field_context('Column: '+'; '.join(r['text'] for r in examples),
                    manifest['endpoint'],manifest['model'],manifest['document_context'])
                field_interpretations[region['id']]=header_report
                if header_report.get('value_kind')=='unknown':
                    context=manifest['document_context']
                elif header_report.get('review_status')=='structural_type':
                    derived_label_type=header_report['value_kind']
            except (ValueError,TimeoutError,ConnectionError,OSError):
                context=manifest['document_context']
        elif neighbors and manifest.get('source_language','en')=='en' and needs_scope_context(region['text']):
            try:
                from transformation.llm.field_context import infer_scope
                hint,scope_report=infer_scope(region['text'],neighbors,manifest['endpoint'],manifest['model'])
                if hint:
                    context+='\nInterpretive scope only: '+hint
                    field_interpretations[region['id']]=scope_report
            except (ValueError,TimeoutError,ConnectionError,OSError) as exc:
                field_interpretations[region['id']]={'error':str(exc),'review_status':'requires_review'}
        active_terms={k:v for k,v in term_memory.items() if re.search(r'(?<!\w)'+re.escape(k)+r'(?!\w)',region['text'])}
        translation_source=region['text']
        if manifest.get('source_language','en')=='en':
            translation_source=field_interpretations.get(region['id'],{}).get('resolved_source',translation_source)
        if derived_label_type and re.fullmatch(r'[A-Z]{2,4}',region['text'].strip()) and region['text'] not in manifest.get('terminology',{}):
            # Localize the observed value type while retaining the original
            # opaque heading. This does not guess an acronym's expansion.
            translation_source=derived_label_type.replace('_',' ')+' ('+region['text'].strip()+')'
            values=list(dict.fromkeys(values+[region['text'].strip()]))
        if active_terms:
            context+='\nUse these previously translated document terms consistently: '+json.dumps(active_terms,ensure_ascii=False)
        from transformation.llm.ollama_native import translation_policy_digest
        identity = hashlib.sha256(json.dumps(['scan-checkpoint-v8',translation_policy_digest(),manifest.get('source_sha256'),translation_source,
            manifest['endpoint'], manifest['model'], language, region['text'], values, context],
            ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        cached = previous.get(region['id'], {})
        if not manifest.get('ignore_translation_cache') and cached.get('identity') == identity and cached.get('text', '').strip():
            records[region['id']] = cached
            if examples and region['text'] not in manifest.get('terminology',{}) and (derived_label_type or re.fullmatch(r'[A-Za-z]+(?: [A-Za-z]+){1,4}',region['text'])) and cached['text'].strip()!=region['text'].strip():
                term_memory[region['text']]=cached['text']
            print('Reused region ' + region['id'], flush=True)
            continue
        attempts=[]
        try:
            from transformation.llm.translation_guard import validate_candidate, audit_candidate
            from transformation.llm.policy import expected_unchanged
            audits=[]
            retry_context=context
            for quality_attempt in range(2):
                text, stats = translate_paragraph(manifest['endpoint'], manifest['model'], translation_source,
                                                  language, values, retry_context, structured=len(region['text'])<=200,terminology=active_terms)
                try:
                    if not text.strip():
                        raise ValueError('Empty translation')
                    validate_candidate(region['text'],text,values)
                    if any(value not in text for value in active_terms.values()):
                        raise ValueError('Translation did not retain the established document terminology.')
                    if not expected_unchanged(translation_source,values):
                        audit=audit_candidate(manifest['endpoint'],manifest['model'],translation_source,text,language,context=context,values=values)
                        audits.append(audit)
                        if not audit['faithful']:
                            raise ValueError('Semantic review: '+'; '.join(audit['issues']))
                    break
                except ValueError as exc:
                    attempts.append({'candidate':text,'reason':str(exc),'audits':list(audits)})
                    if quality_attempt==1:
                        raise
                    retry_context=context+'\nPrior translation failed validation: '+str(exc)+'. Translate only the current source, keeping every descriptive word and all exact literals. Do not add surrounding facts.'
            stats={**stats,'translation_audits':audits}
        except (ValueError, TimeoutError, ConnectionError, OSError) as exc:
            errors[region['id']] = {'type':type(exc).__name__, 'message':str(exc),'attempts':attempts}
            (run/'region-errors.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
            print('Translation requires review: ' + region['id'], flush=True)
            continue
        records[region['id']] = {'identity':identity, 'source_text': region['text'], 'translation_source':translation_source,
                                 'text': text, 'stats': stats, 'context_region_ids':[r['id'] for r in neighbors]}
        if examples and region['text'] not in manifest.get('terminology',{}) and (derived_label_type or re.fullmatch(r'[A-Za-z]+(?: [A-Za-z]+){1,4}',region['text'])) and text.strip()!=region['text'].strip():
            term_memory[region['text']]=text
            (run/'document-terminology.json').write_text(json.dumps(term_memory,ensure_ascii=False,indent=2),encoding='utf-8')
        (run / 'region-translations.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Translated region ' + region['id'], flush=True)
    (run/'field-interpretations.json').write_text(json.dumps(field_interpretations,ensure_ascii=False,indent=2),encoding='utf-8')
    checkpoint_path.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    (run/'region-errors.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
    if errors and not records:
        raise RuntimeError('All attempted region translations failed. See region-errors.json.')
    # Geometry carrier only; final PDF is built from the ID-keyed records.
    with pymupdf.open(run / 'source.pdf') as pdf:
        pdf.save(run / 'output' / 'scan-regions.mono.pdf')


if __name__ == '__main__':
    main(sys.argv[1])
