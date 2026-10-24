"""Compare saved source OCR evidence without treating confidence as accuracy."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import pymupdf


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def wrapper(path):
    path=Path(path)
    for parent in path.parents:
        if parent.name.startswith('server-') and (parent/'manifest.json').is_file():
            return parent/'manifest.json'
    return path


def evidence(manifest):
    assets=[]
    def collect(ir_path,prefix):
        if not ir_path or not Path(ir_path).is_file():return
        for unit in read(ir_path)['units']:
            folder=unit.get('ocr_recognition_dir')
            if not folder:continue
            folder=Path(folder)
            meta=read(folder/'manifest.json')
            lines=[json.loads(s) for s in (folder/'lines.jsonl').read_text(encoding='utf-8').splitlines() if s]
            image=Path(meta['detection_run'])/'page-1.png'
            pix=pymupdf.Pixmap(str(image))
            assets.append({'asset':prefix+':page-'+str(unit['number']),
                'pixels':[pix.width,pix.height],'policy':meta.get('raster',{}).get('policy','legacy-72dpi'),
                'lines':len(lines),'flagged':sum(bool(x.get('requires_review')) for x in lines),
                'source_layer_omitted':sum(bool(r.get('metadata',{}).get('source_layer_omitted')) for r in unit['regions']),
                'decisions':dict(Counter(x.get('recognition_decision','unknown') for x in lines)),
                'transcripts':[{k:x.get(k) for k in ('id','text','bbox','recognition_score','requires_review')} for x in lines],
                'image':str(image),'evidence':str(folder/'lines.jsonl')})
    collect(manifest.get('source_ir'),'document')
    for item in manifest.get('image_processing',[]):
        folder=item.get('run_dir')
        if folder:collect(Path(folder)/'source-ir.json',item['region_id'])
    return assets


def compare(before,after):
    before,after=Path(before),Path(after)
    output=after/'ocr-review';output.mkdir(exist_ok=True)
    prior={r['key']:r for r in read(before/'translation/index.json')['records']}
    external={r['id']:r for r in read(after/'baseline-index.json')} if (after/'baseline-index.json').is_file() else {}
    documents=[]
    for record in read(after/'translation/index.json')['records']:
        previous=prior.get(record['key']) or external.get(record['document_id'])
        old=read(wrapper(previous['manifest'])) if previous else {}
        new=read(wrapper(record['manifest']))
        row={'document':record['key'],'before_status':old.get('status','not_available'),'after_status':new['status'],
             'source_sha256':new['source_sha256'],
             'same_source':old.get('source_sha256')==new['source_sha256'] if old else None,
             'before':evidence(old),'after':evidence(new),
             'image_processing':new.get('image_processing',[])}
        for name in ('before','after'):
            row[name+'_totals']={key:sum(a[key] for a in row[name]) for key in ('lines','flagged','source_layer_omitted')}
        documents.append(row)
    report={'scope':'OCR input-resolution and source-evidence regression audit',
        'limitations':['Internal confidence flags are not OCR accuracy, CER, WER, precision or recall.',
            'Detection can split or find additional lines at higher resolution; line counts alone are not comparable accuracy scores.',
            'Physically tiny text can be recognized correctly and still remain untranslated because reconstruction preserves its source at the existing font limits.',
            'Model consensus can agree on a wrong glyph. Currency symbols, punctuation and identifiers still need annotated source comparison.',
            'No human approvals were copied into these fresh runs.'],
        'documents':documents,
        'quality_findings':read(after/'qa-findings.json') if (after/'qa-findings.json').exists() else {}}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    body='<h1>OCR resolution regression</h1><ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in report['limitations'])+'</ul>'
    body+='<p>Failed runs may end before figure OCR starts. Zero saved OCR lines in a failed run means no comparable completed OCR evidence, not perfect recognition or absence of source image text.</p>'
    body+='<p><a href="../regression/report.html">Finished-PDF comparison</a></p>'
    if report['quality_findings']:
        body+='<h2>Quality findings — outputs remain unapproved</h2><ul>'
        for finding in report['quality_findings'].get('findings',[]):
            body+='<li><strong>'+html.escape(finding['document']+' / '+finding['category'])+'</strong>: '+html.escape(finding['observed']+' '+finding['assessment'])+'</li>'
        body+='</ul>'
    body+='<table><tr><th>Document</th><th>Generated</th><th>Before detected / flagged</th><th>After detected / flagged</th></tr>'
    for row in documents:
        a,b=row['before_totals'],row['after_totals']
        body+=f"<tr><td>{html.escape(row['document'])}</td><td>{row['after_status']}</td><td>{a['lines']} / {a['flagged']}</td><td>{b['lines']} / {b['flagged']}</td></tr>"
    body+='</table>'
    for row in documents:
        body+='<h2>'+html.escape(row['document'])+'</h2>'
        for side in ('before','after'):
            body+='<h3>'+side.title()+'</h3>'
            if not row[side]:body+='<p>Native extraction; no OCR assets recorded.</p>'
            for asset in row[side]:
                body+='<details><summary>'+html.escape(f"{asset['asset']} — {asset['pixels']}, {asset['lines']} lines, {asset['flagged']} flagged")+'</summary>'
                body+='<img style="max-width:700px;width:100%" src="'+html.escape(Path(asset['image']).resolve().as_uri(),quote=True)+'" alt="Actual OCR input"><pre>'
                body+=html.escape(json.dumps(asset['transcripts'],ensure_ascii=False,indent=2))+'</pre></details>'
    style='body{font:16px system-ui;margin:32px;max-width:1200px;color:#182737}td,th{border:1px solid #bbc;padding:10px}table{border-collapse:collapse}pre{white-space:pre-wrap;background:#f2f4f7;padding:16px}details{margin:12px 0}'
    (output/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>'+style+'</style>'+body,encoding='utf-8')
    print(output/'report.html',flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',required=True);parser.add_argument('--after',required=True)
    args=parser.parse_args();compare(args.before,args.after)
