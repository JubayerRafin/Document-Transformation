"""Content-addressed source extraction only; OCR and translations are never cached here."""
from copy import deepcopy
import hashlib
from importlib.metadata import version,PackageNotFoundError
import json
import os
from pathlib import Path
from uuid import uuid4
from extraction.models import Document,Page,Region


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def identity(path,strategy,analysis):
    root=Path(__file__).resolve().parent
    code={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.py'))}
    route=root.parent/'transformation/analysis/document_analysis.py'
    code['document_analysis.py']=hashlib.sha256(route.read_bytes()).hexdigest()
    packages={}
    for name in ('pymupdf','pdfplumber','docling','docling-core','pypdf','camelot-py','torch','transformers'):
        try:packages[name]=version(name)
        except PackageNotFoundError:packages[name]=None
    hub=Path(os.environ.get('HF_HUB_CACHE',str(Path(os.environ.get('HF_HOME',str(Path.home()/'.cache/huggingface')))/'hub')))
    models={}
    for directory in hub.glob('models--*docling*'):
        for item in directory.rglob('*'):
            if item.is_file():
                stat=item.stat();models[str(item.relative_to(hub))]=[stat.st_size,stat.st_mtime_ns]
    return {'schema':'source-extraction-cache-1','source_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'strategy':strategy,'analysis':analysis,'code':code,'packages':packages,'model_files':models,'defer_figures':True}


def hydrate(payload):
    data=deepcopy(payload)
    pages=data.pop('pages')
    result=Document(**data)
    for page in pages:
        regions=page.pop('regions')
        target=Page(**page)
        if target.preview:raise ValueError('External preview assets cannot be cached.')
        target.regions=[Region(**region) for region in regions]
        if any(r.asset for r in target.regions):raise ValueError('External region assets cannot be cached.')
        result.pages.append(target)
    result.validate()
    return result


def extract(path,folder,strategy,analysis,cache_root):
    from extraction.coordinator import extract_pdf
    policy=identity(path,strategy,analysis)
    key=digest(policy);entry=Path(cache_root)/key/'document.json'
    try:
        envelope=json.loads(entry.read_text(encoding='utf-8'))
        payload=envelope['document']
        if envelope['policy']!=policy or envelope['payload_sha256']!=digest(payload):
            raise ValueError('Source extraction cache integrity mismatch.')
        result=hydrate(payload)
        if result.source_sha256!=policy['source_sha256']:raise ValueError('Cached source hash mismatch.')
        result.source_name=Path(path).name
        status='hit'
    except (OSError,ValueError,KeyError,TypeError):
        result=extract_pdf(path,folder,strategy,analysis,defer_figures=True)
        payload=result.to_dict()
        status='miss'
        try:
            hydrate(payload)  # Never publish payloads referring to external assets.
            envelope={'policy':policy,'payload_sha256':digest(payload),'document':payload}
            entry.parent.mkdir(parents=True,exist_ok=True)
            temporary=entry.with_name('document-'+uuid4().hex+'.tmp')
            temporary.write_text(json.dumps(envelope,ensure_ascii=False),encoding='utf-8')
            temporary.replace(entry)
        except (OSError,ValueError,TypeError):
            status='bypass_not_published'
    evidence={'status':status,'key':key,'source_sha256':policy['source_sha256'],
              'scope':'Source text/table geometry before OCR, figure enrichment or translation.',
              'historical_extractor_timings':status=='hit',
              'quality_approval_reused':False}
    # Restore the usual per-run evidence artifact too, without touching the cache.
    output=Path(folder)/'deep-doc';output.mkdir(parents=True,exist_ok=True)
    (output/'extraction-evidence.json').write_text(json.dumps(result.settings.get('extraction_evidence',{}),indent=2),encoding='utf-8')
    result.settings['source_extraction_cache']=evidence
    return result,evidence
