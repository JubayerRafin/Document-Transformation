"""Compare new batch outputs to frozen run manifests, without copying approvals."""
import argparse
from collections import Counter,defaultdict
import csv
import difflib
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

import pymupdf
from tools.evaluation.layout_standard import gray,ssim
from tools.evaluation.compare_batches import measure
from transformation.llm.translation_guard import validate_candidate


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paragraph_text(region):
    text=region.get('unicode') or ''
    if not re.search(r'\{v\d+\}',text):return text
    # Native formula markers are IL references, not printed text. Reconstruct
    # the ordered textual compositions before checking literal preservation.
    chunks=[]
    for composition in region.get('pdf_paragraph_composition',[]):
        group=composition.get('pdf_same_style_unicode_characters')
        if group is not None:chunks.append(group['unicode']);continue
        char=composition.get('pdf_character')
        if char is not None:chunks.append(char.get('char_unicode') or '');continue
        for kind in ('pdf_formula','pdf_line','pdf_same_style_characters'):
            group=composition.get(kind)
            if group is not None:
                chunks.append(''.join(c.get('char_unicode') or '' for c in group.get('pdf_character',[])));break
        else:return text  # Unsupported non-text composition stays a review signal.
    return ''.join(chunks) if chunks else text


def paragraph_pairs(run):
    pairs=defaultdict(list);invalid=[]
    for source in run.rglob('babeldoc-source-ir.json'):
        target=source.with_name('babeldoc-transformed-ir.json')
        if not target.exists():continue
        before=read(source);after=read(target)
        targets={(p['page_number'],r['debug_id']):r for p in after['page'] for r in p['pdf_paragraph']}
        values=read(source.with_name('protected-values.json')) if source.with_name('protected-values.json').exists() else []
        for page in before['page']:
            for region in page['pdf_paragraph']:
                translated=targets.get((page['page_number'],region['debug_id']))
                if translated is None:continue
                text=paragraph_text(region);out=paragraph_text(translated)
                if not text.strip():continue
                pairs[text].append(out)
                try:validate_candidate(text,out,values)
                except ValueError as error:invalid.append({'source':text,'translation':out,'reason':str(error),'page':page['page_number']+1})
    # Lightweight figure workers store their final translations as slot records,
    # not BabelDOC IL. Include both representations so backend differences do
    # not look like omitted text in the comparison.
    for path in run.rglob('scan-reconstruction.json'):
        if not (path.parent/'verified-slots.json').exists():continue
        for region in read(path).get('regions',[]):
            source=region.get('source_text') or ''
            target=region.get('translated_text')
            if target is None and region.get('status')=='original_preserved':target=source
            if source and target is not None:pairs[source].append(target)
    return dict(pairs),invalid


def extraction_signature(path):
    ir=read(path)
    canonical=[{'number':unit['number'],'regions':[
        {**{k:region.get(k) for k in ('id','kind','text','bbox','cells')},
         'cell_records':region.get('metadata',{}).get('cell_records')}
        for region in unit['regions']]} for unit in ir['units']]
    return hashlib.sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest()


def assess(manifest,source):
    run=Path(manifest['run_dir']);output=Path(manifest.get('output_file',manifest.get('output_pdf','')))
    checks=manifest.get('validation',{}).get('independent_checks',{})
    records=[]
    for name in ('table-fitting.json','heading-fitting.json','panel-fitting.json'):
        for path in run.rglob(name):
            data=read(path);records.extend(data.get('results',[]) if isinstance(data,dict) else data)
    pairs,invalid=paragraph_pairs(run)
    figure_warnings=[];seen=set()
    for path in run.rglob('scan-reconstruction.json'):
        if 'figure-runs' not in path.parts:continue
        for region in read(path).get('regions',[]):
            if region.get('status')!='original_preserved' or not region.get('reason'):continue
            key=json.dumps({k:region.get(k) for k in ('source_text','bbox','reason')},sort_keys=True)
            if key in seen:continue
            seen.add(key);figure_warnings.append({**region,'evidence':str(path)})
    result={'status':manifest['status'],'server_seconds':manifest.get('seconds'),
        'error':manifest.get('error'),
        'total_seconds':manifest.get('transport_total_seconds'),'output':str(output),
        'transport_recovery':manifest.get('transport_recovery'),
        'output_sha256':digest(output) if output.is_file() else None,
        'translation_errors':manifest.get('translation_errors'),
        'rejected_translations':[read(path) for path in run.rglob('rejected-translations/*.json')],
        'geometry_blockers':checks.get('geometry_blockers',[]),
        'layout_flags':checks.get('layout_review_candidates',[]),
        'ocr_flags':manifest.get('scan_acceptance_blockers') or [],
        'figure_ocr_review_items':figure_warnings,
        'slot_failures':[r for r in records if r['status']!='fitted'],
        'fitted_slots':sum(r['status']=='fitted' for r in records),
        'minimum_fitted_font':min((r['rendered_min_font_size'] for r in records if 'rendered_min_font_size' in r),default=None),
        'paragraph_validation_signals':invalid,
        'extraction_signature':extraction_signature(manifest['source_ir']) if manifest.get('source_ir') else None,
        'translation_pairs':pairs,'gpu':manifest.get('execution',{}).get('gpu')}
    if manifest['status']=='generated':result['source_layout_metrics']=measure(source,output,100)
    return result


def top_manifest(path):
    path=Path(path)
    # Dataset records point at the engine manifest; the SSH wrapper also keeps
    # transfer/device timing in its enclosing server job manifest.
    for parent in path.parents:
        if parent.name.startswith('server-') and (parent/'manifest.json').is_file():return parent/'manifest.json'
    return path


def compare(root):
    root=Path(root);destination=root/'regression';destination.mkdir(exist_ok=True)
    baselines=read(root/'baseline-index.json')
    index=read(root/'translation/index.json')
    candidates={r['document_id']:r for r in index['records']}
    documents=[]
    for baseline in baselines:
        name=baseline['id'];record=candidates.get(name)
        if record is None:documents.append({'document':name,'status':'not_run'});continue
        old=read(baseline['manifest']);new=read(top_manifest(record['manifest']))
        source=Path(baseline['source'])
        if digest(source)!=baseline['source_sha256'] or new['source_sha256']!=baseline['source_sha256']:
            raise ValueError('Regression sources differ: '+name)
        print('Reviewing',name,flush=True)
        before=assess(old,source);after=assess(new,source)
        row={'document':name,'source_sha256':baseline['source_sha256'],'before':before,'after':after,'pages':[]}
        row['pdf_links']={}
        for label,path in [('original',source),('before',Path(before['output'])),('after',Path(after['output']))]:
            if label=='before' and before['status']!='generated' or label=='after' and after['status']!='generated':continue
            if not path.is_file() or path.suffix.lower()!='.pdf':continue
            folder=destination/'pdfs'/label;folder.mkdir(parents=True,exist_ok=True)
            saved=folder/(name+'.pdf');shutil.copy2(path,saved)
            row['pdf_links'][label]=saved.relative_to(destination).as_posix()
        row['same_extraction']=before['extraction_signature']==after['extraction_signature']
        changed=[]
        for text in sorted(set(before['translation_pairs'])|set(after['translation_pairs'])):
            a=before['translation_pairs'].get(text,[]);b=after['translation_pairs'].get(text,[])
            if Counter(a)!=Counter(b):changed.append({'source':text,'before':a,'after':b})
        row['changed_translation_groups']=changed
        from transformation.llm.policy import expected_unchanged
        row['new_unchanged_candidates']=[change for change in changed
            if len(re.findall(r'[A-Za-z]{2,}',change['source']))>=2
            and not expected_unchanged(change['source'])
            and change['after'].count(change['source'])>change['before'].count(change['source'])]
        if old['status']=='generated' and new['status']=='generated':
            with pymupdf.open(source) as original,pymupdf.open(before['output']) as previous,pymupdf.open(after['output']) as current:
                row['same_page_count']=len(original)==len(previous)==len(current)
                for number in range(max(len(previous),len(current))):
                    if number>=min(len(previous),len(current)):
                        row['pages'].append({'page':number+1,'status':'missing_page'});continue
                    a,b=previous[number],current[number]
                    page={'page':number+1,'same_size':list(a.rect)==list(b.rect),
                        'before_after_ssim':ssim(gray(a,100),gray(b,100)),
                        'same_extracted_text':a.get_text(sort=True)==b.get_text(sort=True)}
                    folder=destination/name;folder.mkdir(exist_ok=True)
                    labels=[('before',a),('after',b)]
                    if number<len(original):labels.insert(0,('original',original[number]))
                    for label,p in labels:
                        image=folder/(f'{number+1}-{label}.png')
                        p.get_pixmap(matrix=pymupdf.Matrix(1.4,1.4),alpha=False).save(image)
                        page[label+'_image']=image.relative_to(destination).as_posix()
                    diff='\n'.join(difflib.unified_diff(a.get_text(sort=True).splitlines(),b.get_text(sort=True).splitlines(),fromfile='before',tofile='after'))
                    (folder/f'{number+1}-text-diff.txt').write_text(diff,encoding='utf-8')
                    row['pages'].append(page)
        elif new['status']=='generated':
            folder=destination/name;folder.mkdir(exist_ok=True)
            with pymupdf.open(source) as original,pymupdf.open(after['output']) as current:
                row['same_page_count']=len(original)==len(current)
                for number in range(len(original)):
                    page={'page':number+1,'status':'prior_run_failed_no_approved_output'}
                    labels=[('original',original[number])]
                    if number<len(current):labels.append(('after',current[number]))
                    for label,p in labels:
                        image=folder/(f'{number+1}-{label}.png')
                        p.get_pixmap(matrix=pymupdf.Matrix(1.4,1.4),alpha=False).save(image)
                        page[label+'_image']=image.relative_to(destination).as_posix()
                    row['pages'].append(page)
        elif old['status']=='generated':
            folder=destination/name;folder.mkdir(exist_ok=True)
            with pymupdf.open(source) as original,pymupdf.open(before['output']) as previous:
                for number in range(len(original)):
                    page={'page':number+1,'status':'new_run_failed_no_approved_output'}
                    labels=[('original',original[number])]
                    if number<len(previous):labels.append(('before',previous[number]))
                    for label,p in labels:
                        image=folder/(f'{number+1}-{label}.png')
                        p.get_pixmap(matrix=pymupdf.Matrix(1.4,1.4),alpha=False).save(image)
                        page[label+'_image']=image.relative_to(destination).as_posix()
                    row['pages'].append(page)
        documents.append(row)
    context=read(root/'comparison-context.json') if (root/'comparison-context.json').is_file() else {}
    report={'scope':context.get('scope','Frozen prior outputs vs fresh full-server reruns after numeric-localization and adaptive-font fixes.'),
        'limitations':['No human approvals copied. Automatic checks and visual comparison are not a complete semantic accuracy evaluation.',
        'Fresh translation responses can vary; changed wording does not alone prove a regression.',
        'SSIM reflects changed text as well as layout. Image/table IoU uses native PDF placements/grid proxies, not annotated semantic ground truth.',
        'Paragraph validation signals may include native formula/line-break representations and require inspection.',
        'Verified OCR cache is reused; these times are not cold-start benchmarks. PII was not run.'],
        'batch_complete':index.get('phase_complete',False),'documents':documents,
        'quality_findings':read(root/'qa-findings.json') if (root/'qa-findings.json').is_file() else {}}
    if 'limitations' in context:report['limitations']=context['limitations']
    (destination/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['document','status','before_seconds','after_seconds','server_delta_seconds','server_change_percent','before_total_seconds','after_total_seconds','before_layout_flags','after_layout_flags','changed_translation_groups','same_extraction','same_page_count']
    summaries=[]
    for row in documents:
        if 'after' not in row:continue
        previous=row['before']['server_seconds'];current=row['after']['server_seconds']
        comparable=row['before']['status']==row['after']['status']=='generated'
        delta=round(current-previous,2) if comparable and isinstance(previous,(int,float)) and isinstance(current,(int,float)) else None
        summaries.append(dict(document=row['document'],status=row['after']['status'],before_seconds=previous,after_seconds=current,
            server_delta_seconds=delta,server_change_percent=round(100*delta/previous,1) if delta is not None and previous else None,
            before_total_seconds=row['before']['total_seconds'],after_total_seconds=row['after']['total_seconds'],
            before_layout_flags=len(row['before']['layout_flags']),after_layout_flags=len(row['after']['layout_flags']),
            changed_translation_groups=len(row['changed_translation_groups']),same_extraction=row['same_extraction'],same_page_count=row.get('same_page_count')))
    with (destination/'summary.csv').open('w',newline='',encoding='utf-8-sig') as file:
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(summaries)
    body='<h1>Document regression review</h1><p>'+html.escape(report['scope'])+'</p>'
    body+='<p><strong>Batch '+('complete' if report['batch_complete'] else 'in progress')+'</strong>: '+str(sum(d.get('after',{}).get('status')=='generated' for d in documents))+' generated; '+str(sum(d.get('after',{}).get('status')=='failed' for d in documents))+' failed. Generated does not mean accepted.</p><p><a href="summary.csv">Runtime CSV</a> | <a href="../ocr-review/report.html">Source OCR evidence</a></p>'
    if (root/'mentor-comparison/report.html').is_file():body+='<p><a href="../mentor-comparison/report.html">Supplied mentor reference comparison</a></p>'
    body+='<ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in report['limitations'])+'</ul>'
    body+='<p>Before/after seconds are server processing time. Total seconds include SSH upload, polling and artifact download. Negative change means faster. Failed runs show time until failure; no speed improvement is computed for them. Cache/model warm-up conditions are not controlled cold-run benchmarks.</p>'
    display_fields=fields[:8]
    body+='<table><tr>'+''.join('<th>'+f.replace('_',' ')+'</th>' for f in display_fields)+'</tr>'
    for row in summaries:body+='<tr>'+''.join('<td>'+html.escape('n/a' if row[f] is None else str(row[f]))+'</td>' for f in display_fields)+'</tr>'
    body+='</table>'
    for row in documents:
        body+='<h2>'+html.escape(row['document'])+'</h2>'
        if 'after' not in row:body+='<p>Not run</p>';continue
        findings=[f for f in report['quality_findings'].get('findings',[]) if f['document']==row['document']]
        if findings:body+='<ul>'+''.join('<li><strong>'+html.escape(f['category'])+'</strong>: '+html.escape(f['observed']+' '+f['assessment'])+'</li>' for f in findings)+'</ul>'
        body+='<p>Original → prior output → new output</p>'
        if row['before']['status']!='generated':body+='<p>Prior run failed; there is no approved prior output to display.</p>'
        body+='<p>'+ ' | '.join('<a href="'+path+'">'+html.escape(label.title()+' PDF')+'</a>' for label,path in row.get('pdf_links',{}).items())+'</p>'
        if row['after']['status']!='generated':body+='<p><strong>New run failed:</strong> '+html.escape(str(row['after'].get('error')))+'</p>'
        if row['after'].get('transport_recovery'):body+='<p>Recovered the completed server job after an SSH interruption without repeating inference. End-to-end timing is not comparable and is left unreported.</p>'
        body+='<p>Server: '+html.escape(str(row['before']['server_seconds']))+'s → '+html.escape(str(row['after']['server_seconds']))+'s. End-to-end: '+html.escape(str(row['before']['total_seconds']))+'s → '+html.escape('n/a' if row['after']['total_seconds'] is None else str(row['after']['total_seconds'])+'s')+'.</p>'
        for page in row['pages']:
            body+='<h3>Page '+str(page['page'])+'</h3><div class="pages">'
            for label in ('original','before','after'):
                if label+'_image' in page:body+='<a href="'+page[label+'_image']+'"><strong>'+{'original':'Original','before':'Previous output','after':'New output'}[label]+'</strong><img src="'+page[label+'_image']+'" alt="'+label+'"></a>'
            body+='</div>'
        body+='<details><summary>Changed translation groups</summary><pre>'+html.escape(json.dumps(row['changed_translation_groups'],ensure_ascii=False,indent=2))+'</pre></details>'
        if row['after']['rejected_translations']:body+='<details><summary>Rejected translation responses and reasons</summary><pre>'+html.escape(json.dumps(row['after']['rejected_translations'],ensure_ascii=False,indent=2))+'</pre></details>'
        body+='<details open><summary>New unchanged-source candidates (proper names may legitimately remain unchanged)</summary><pre>'+html.escape(json.dumps(row['new_unchanged_candidates'],ensure_ascii=False,indent=2))+'</pre></details>'
        body+='<details><summary>New-run review flags</summary><pre>'+html.escape(json.dumps({k:row['after'][k] for k in ('geometry_blockers','layout_flags','ocr_flags','figure_ocr_review_items','slot_failures','paragraph_validation_signals')},ensure_ascii=False,indent=2))+'</pre></details>'
    style='body{font:16px system-ui;margin:32px;color:#182737}table{border-collapse:collapse}td,th{padding:10px;border:1px solid #ccd}th{background:#eef1f5}.pages{display:flex;gap:8px}.pages a{width:33%}img{width:100%;border:1px solid #ccd}pre{white-space:pre-wrap;background:#f1f3f5;padding:15px}'
    (destination/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>'+style+'</style>'+body,encoding='utf-8')
    print(destination/'report.html',flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True)
    compare(parser.parse_args().root)
