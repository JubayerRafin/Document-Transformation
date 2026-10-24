"""Content-addressed OCR evidence shared between independent evaluations."""
import hashlib,json
from pathlib import Path
from uuid import uuid4
import pymupdf


def location(data,language,device,root):
    repo=Path(__file__).resolve().parents[2]
    profiles={name:hashlib.sha256((repo/'tools/ocr'/name).read_bytes()).hexdigest()
              for name in ('diagnose_paddle_detector.py','recognize_paddle_boxes.py','run_gpu_ocr.py','raster_policy.py')}
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        if len(pdf)!=1: raise ValueError('OCR cache requires a single page.')
        from tools.ocr.raster_policy import render_page
        page=pdf[0]; pix,raster=render_page(page)
        digest=hashlib.sha256(pix.samples+json.dumps({'size':[pix.width,pix.height],'language':language,
            'page_points':[page.rect.width,page.rect.height],
            'device':device,'profiles':profiles,'raster':raster},sort_keys=True).encode()).hexdigest()
    return Path(root).absolute()/digest


def lookup(folder):
    pointer=Path(folder)/'complete.json'
    if not pointer.exists(): return None
    try:
        record=json.loads(pointer.read_text(encoding='utf-8'))
        path=Path(record['recognition_dir'])
        if not (path/'manifest.json').is_file(): return None
        if json.loads((path/'manifest.json').read_text(encoding='utf-8')).get('status')!='completed': return None
        return str(path)
    except (OSError,ValueError,KeyError,TypeError):
        return None  # Incomplete cache evidence must cause a fresh OCR run.


def publish(folder,recognition):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    temporary=folder/('complete-'+uuid4().hex+'.tmp')
    temporary.write_text(json.dumps({'recognition_dir':str(Path(recognition).absolute())}),encoding='utf-8')
    temporary.replace(folder/'complete.json')
