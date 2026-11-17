"""Populate source extraction cache explicitly; no OCR or model inference."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
from extraction.source_cache import extract,digest
from tools.evaluation.dataset import registry
from transformation.analysis.document_analysis import analyze_pdf


def warm(config,output):
    output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for item in registry(config):
        source=Path(item['path'])
        analysis=analyze_pdf(source.read_bytes(),'','',False)
        started=time.monotonic()
        first,evidence=extract(source,output/item['id'],config['table_strategy'],analysis,config['source_extraction_cache'])
        seconds=time.monotonic()-started
        second,check=extract(source,output/item['id'],config['table_strategy'],analysis,config['source_extraction_cache'])
        if check['status']!='hit' or digest([asdict(p) for p in first.pages])!=digest([asdict(p) for p in second.pages]):
            raise ValueError('Source cache round-trip did not preserve extracted regions: '+item['id'])
        row={'document':item['id'],'population_seconds':round(seconds,3),'initial_status':evidence['status'],
             'verified_status':check['status'],'extracted_regions_identical':True,'key':check['key']}
        rows.append(row);print(json.dumps(row),flush=True)
        (output/'report.json').write_text(json.dumps({'rows':rows,'scope':'Cache population, no OCR/translation inference.',
            'excluded_from_repeat_runtime':True},indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();warm(json.loads(args.config.read_text(encoding='utf-8')),args.output)
