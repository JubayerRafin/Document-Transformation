"""Rerun shared PDF extraction without translation, model inference or paid pods."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import time

import pymupdf

from extraction.coordinator import content_ir, extract_pdf, text_regions
from extraction.figures import enrich_figures
from extraction.pipeline import render_assets


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def run(config,output,strategy,defer_figures=False):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    versions={}
    for name in ('pymupdf','pdfplumber','camelot-py','docling','pypdf'):
        try: versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name]=None
    report={'purpose':'extraction integration verification; no translation or PII inference',
            'python':sys.executable,'versions':versions,'strategy':strategy,'defer_legacy_figures':defer_figures,'documents':[],
            'limitations':['Native-token agreement is a parser comparison, not independently annotated extraction accuracy.',
                            'Raster-only pages still require the separate PaddleOCR route; this probe does not run OCR.',
                            'Figure crops and table candidates require visual review; execution does not prove correctness.']}
    project=Path(__file__).resolve().parents[2]
    report['extraction_code_sha256']={str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted((project/'extraction').rglob('*.py'))}
    for item in config['documents']:
        source=Path(item['path']).resolve()
        folder=output/item['id']; folder.mkdir()
        started=time.monotonic()
        record={'id':item['id'],'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'input_notes':item.get('notes','')}
        print('Starting '+item['id']+' / '+strategy,flush=True)
        try:
            document=extract_pdf(source,folder,strategy=strategy,defer_figures=defer_figures)
            document.validate()
            save(folder/'document.json',document.to_dict())
            ir=content_ir(document)
            enrich_figures(source,ir,folder/'figures')
            save(folder/'source-ir.json',ir)
            render_assets(source,document,folder,lambda message:print(item['id']+': '+message,flush=True))
            save(folder/'document.json',document.to_dict())
            counts=Counter(r['kind'] for unit in ir['units'] for r in unit['regions'])
            methods=Counter(r.get('metadata',{}).get('engine',r.get('metadata',{}).get('method','unknown')) for unit in ir['units'] for r in unit['regions'])
            pages=[]
            with pymupdf.open(source) as pdf:
                for unit,page in zip(ir['units'],pdf):
                    tokenize=lambda text:Counter(re.findall(r'\w+',text))
                    expected=tokenize(page.get_text())
                    extracted=tokenize('\n'.join(r['text'] for r in text_regions({'units':[unit]})))
                    pages.append({'page':unit['number'],'native_tokens':sum(expected.values()),
                        'matched_native_tokens':sum((expected&extracted).values()),
                        'native_token_recall':sum((expected&extracted).values())/sum(expected.values()) if expected else None,
                        'native_token_precision':sum((expected&extracted).values())/sum(extracted.values()) if extracted else None,
                        'missing_native_tokens':list((expected-extracted).elements()),
                        'additional_tokens':list((extracted-expected).elements()),
                        'requires_separate_ocr':not bool(expected)})
            evidence=document.settings.get('extraction_evidence',{})
            record.update(status='completed',backend=document.backend,region_counts=dict(counts),
                selected_methods=dict(methods),figure_assets=len(ir.get('assets',[])),pages=pages,
                extraction_evidence=evidence,warnings=document.warnings)
        except Exception as exc:
            record.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        record['seconds']=round(time.monotonic()-started,2)
        report['documents'].append(record)
        save(output/'report.json',report)
        print(item['id']+': '+record['status']+' / '+str(record['seconds'])+'s'+(' / '+record['error'] if record.get('error') else ''),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('runs/extraction-integration')/datetime.now().strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--strategy',choices=['auto','native','docling'],default='docling')
    parser.add_argument('--defer-legacy-figures',action='store_true',help='Use the production single spatial figure pass')
    args=parser.parse_args()
    report=run(json.loads(args.config.read_text(encoding='utf-8')),args.output,args.strategy,args.defer_legacy_figures)
    return int(any(d['status']=='failed' for d in report['documents']))


if __name__=='__main__':sys.exit(main())
