"""Recognize saved PaddleOCR detection boxes without repeating detection.

Produces raw line evidence, not a table-aware evaluation prediction.
"""
import argparse
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import time
import re


def detector_polygons(value):
    import numpy as np
    polygons=np.asarray(value,dtype=np.float32)
    if isinstance(value,list) and not value:
        return np.empty((0,4,2),dtype=np.float32)
    if polygons.ndim!=3 or polygons.shape[1:]!=(4,2) or not np.isfinite(polygons).all():
        raise ValueError('Expected finite quadrilateral detection boxes.')
    return polygons


def select_candidate(candidates):
    """Whitespace-insensitive consensus; never normalize identifier characters."""
    if not candidates:
        raise ValueError('OCR returned no recognition candidates.')
    groups = {}
    for c in candidates:
        groups.setdefault(re.sub(r'\s+', '', c['text']), []).append(c)
    best = max(candidates, key=lambda c: c['score'])
    if not best['text'].strip():
        return best, True, 'empty_recognition'
    sensitive = any('@' in c['text'] or re.search(r'\b(?:sk_|API\s*Key)',c['text'],re.I) for c in candidates)
    if sensitive and len({c['text'].strip() for c in candidates}) > 1:
        return best, True, 'identifier_disagreement'
    # A majority of rescaled copies must not erase conflicting source evidence.
    if len(groups) > 1:
        return best, True, 'character_disagreement'
    agreed = [g for g in groups.values() if len({c['variant'] for c in g if c['score'] >= .90}) >= 2]
    if agreed:
        group = max(agreed, key=lambda g: (len(g), max(c['score'] for c in g)))
        # Original preserves word boundaries better than interpolated/segmented
        # crops when the recognized characters agree.
        best = next((c for c in group if c['variant'] == 'original'), max(group, key=lambda c:c['score']))
        return best, False, 'character_consensus'
    return best, best['score'] < .95 or len(groups) > 1, 'confidence_or_disagreement'


def consensus_key(text):
    """Ignore prose spacing and dash typography; keep structured literals exact."""
    literals=re.findall(r'[\w.+-]+@[\w.-]+|\b\w*_[\w._-]+|(?<!\w)[\w.+:/%–—−-]*\d[\w.,:/%–—−-]*',text)
    if '@' in text or re.search(r'\bsk_',text):
        return text.strip(),tuple(sorted(literals))
    prose=re.sub(r'\s+','',text)
    prose=re.sub('[–—−]','-',prose)
    return prose,tuple(sorted(literals))


def cross_model_candidate(primary, secondary):
    """Two confident secondary views must agree with primary source evidence."""
    confirmed={consensus_key(c['text']) for c in secondary if c['score']>=.90 and c['text'].strip()}
    if len(confirmed)!=1 or len(secondary)<2 or any(c['score']<.90 for c in secondary):
        return None
    key=next(iter(confirmed))
    matches=[c for c in primary if consensus_key(c['text'])==key and c['score']>=.90]
    return max(secondary,key=lambda c:c['score']) if matches else None


def split_long_crop(crop, aspect_limit=14):
    """Split long lines only at visible background gaps, never arbitrary letters."""
    import numpy as np
    h, w = crop.shape[:2]
    if w <= h * aspect_limit:
        return [crop]
    edge = np.concatenate([crop[0], crop[-1]])
    bg = np.median(edge, axis=0)
    ink = np.max(abs(crop.astype(float) - bg), axis=2) > 45
    empty = np.flatnonzero(ink.mean(axis=0) < .02)
    cuts, start = [0], 0
    while w - start > h * aspect_limit:
        candidates = empty[(empty > start + h * aspect_limit/2) & (empty < start + h * aspect_limit)]
        if not len(candidates):
            return [crop]  # Do not split an identifier without a real gap.
        cut = int(candidates[-1])
        cuts.append(cut)
        start = cut
    cuts.append(w)
    return [crop[:, a:b] for a, b in zip(cuts, cuts[1:])]


def split_word_crops(crop):
    """Use full-height background gaps; never split at an occupied column."""
    import numpy as np
    height,width=crop.shape[:2]
    edge=np.concatenate([crop[0],crop[-1]])
    color=np.median(edge,axis=0)
    blank=(np.max(abs(crop.astype(float)-color),axis=2)>35).mean(axis=0)<.02
    transitions=np.diff(np.r_[False,blank,False].astype(int))
    starts=np.flatnonzero(transitions==1)
    ends=np.flatnonzero(transitions==-1)
    cuts=[0]
    for start,end in zip(starts,ends):
        if start>0 and end<width and end-start>=max(2,round(height*.18)):
            cut=int((start+end)//2)
            if cut-cuts[-1]>=max(3,height*.2):
                cuts.append(cut)
    cuts.append(width)
    return [crop[:,left:right] for left,right in zip(cuts,cuts[1:])]


def confirmed_label_prefix(primary,secondary):
    """Recover a colon-terminated label only from confident word agreement."""
    if len(primary)!=len(secondary):
        return None
    label=[]
    for first,second in zip(primary[:6],secondary[:6]):
        if min(first['score'],second['score'])<.90 or consensus_key(first['text'])!=consensus_key(second['text']):
            return None
        word=second['text'].strip()
        if not word or any(c.isdigit() for c in word):
            return None
        label.append(word)
        if ':' in word:
            if not word.endswith(':') or word.count(':')!=1 or not any(c.isalpha() for c in ''.join(label)):
                return None
            return {'text':' '.join(label),'end':second['end'],
                    'score':min(part['score'] for part in primary[:len(label)]+secondary[:len(label)])}
    return None


def sensitive_literal(text):
    """Sensitive glyph decisions require another recognizer, not resized copies."""
    import unicodedata
    return any(c.isdigit() or c in '@_' or unicodedata.category(c)=='Sc' for c in text)


def independent_literal_evidence(candidates,text):
    """Verify saved decisions as well as fresh ones; flags alone are not proof."""
    import math
    if any(not isinstance(c,dict) or not isinstance(c.get('text'),str) or
           type(c.get('score')) not in (float,int) or not math.isfinite(c['score']) or not 0<=c['score']<=1 for c in candidates):
        return False
    allowed={'en_PP-OCRv5_mobile_rec','PP-OCRv5_server_rec'}
    key=consensus_key(text)
    if any(c.get('score',0)>=.95 and consensus_key(c.get('text',''))!=key for c in candidates):return False
    groups={model:[c for c in candidates if c.get('model')==model and
                   consensus_key(c.get('text',''))==key] for model in allowed}
    if any(not views for views in groups.values()):return False
    normal=[{c.get('variant') for c in views if c.get('score',0)>=.90 and c.get('variant')} for views in groups.values()]
    if min(map(len,normal))>=1 and max(map(len,normal))>=2:return True
    # Existing targeted policy: four matching views, two per actual recognizer,
    # at least two strong views, with the other views at or above .85.
    matching=[c for views in groups.values() for c in views if c.get('score',0)>=.85]
    targeted=[{c.get('variant') for c in views if c.get('score',0)>=.85 and c.get('variant')} for views in groups.values()]
    return min(map(len,targeted))>=2 and sum(c.get('score',0)>=.95 for c in matching)>=2


def bounded_recognition(crop,primary,secondary_factory,language,padded=None):
    """Use source-language recognition first; cap retries instead of word explosion."""
    import cv2
    reads=[]
    def read(model,view,name,model_name):
        output=list(model.predict(view))
        if len(output)!=1: raise ValueError('Expected one OCR result per source crop.')
        row={'text':str(output[0]['rec_text']),'score':float(output[0]['rec_score']),
             'variant':name,'model':model_name}
        reads.append(row); return row
    primary_name='en_PP-OCRv5_mobile_rec' if language=='en' else 'PP-OCRv5_server_rec'
    first=read(primary,crop,'detector_bounds',primary_name)
    second=read(primary,cv2.resize(crop,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC),'detector_bounds_2x',primary_name)
    best,review,decision=select_candidate(reads)
    sensitive=any(sensitive_literal(r['text']) for r in reads)
    if not review and min(first['score'],second['score'])>=.95 and not sensitive:
        return best,False,'language_source_view_consensus',reads
    # A contrast view is evidence, never a rewritten transcription.
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    edge=__import__('numpy').concatenate([gray[0],gray[-1]])
    if float(__import__('numpy').median(edge))<128: gray=255-gray
    view=cv2.cvtColor(cv2.resize(gray,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC),cv2.COLOR_GRAY2BGR)
    read(primary,view,'contrast_source',primary_name)
    secondary=secondary_factory(); checks=[]
    secondary_name='PP-OCRv5_server_rec' if language=='en' else 'en_PP-OCRv5_mobile_rec'
    verification_source=crop if padded is None else padded
    for name,view in [('verification_source',verification_source),('verification_source_2x',cv2.resize(verification_source,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC))]:
        checks.append(read(secondary,view,name,secondary_name))
    confirmed=cross_model_candidate(reads[:-2],checks)
    if confirmed is not None:
        conflicts=[r for r in reads if r['score']>=.95 and consensus_key(r['text'])!=consensus_key(confirmed['text'])]
        if not sensitive or not conflicts:
            return confirmed,False,'cross_model_exact_agreement',reads
    if sensitive:
        return max(reads,key=lambda r:r['score']),True,'sensitive_literal_model_disagreement',reads
    strong=[r for r in reads if r['score']>=.95 and r['text'].strip()]
    if len(strong)>=2 and len({consensus_key(r['text']) for r in strong})==1:
        return max(strong,key=lambda r:r['score']),False,'high_confidence_source_views',reads
    # Keep the strongest hypothesis for review, but never call disagreement reliable.
    return max(reads,key=lambda r:r['score']),True,'unresolved_source_disagreement',reads


def tight_source_crop(crop):
    """Remove empty margins and long rule lines, retaining source glyph pixels."""
    import cv2
    import numpy as np
    height,width=crop.shape[:2]
    edge=np.concatenate([crop[0],crop[-1],crop[:,0],crop[:,-1]])
    background=np.median(edge,axis=0)
    ink=(np.max(abs(crop.astype(float)-background),axis=2)>24).astype(np.uint8)
    _,_,panel_stats,_=cv2.connectedComponentsWithStats(ink)
    for x,y,w,h,area in panel_stats[1:]:
        if w>width*.6 and h>height*.4 and area>w*h*.8 and w>4 and h>4:
            outside=ink.copy(); outside[y:y+h,x:x+w]=0
            if not outside.any() and (w<width or h<height):
                return tight_source_crop(crop[y+1:y+h-1,x+1:x+w-1])
    if np.percentile(np.max(abs(edge.astype(float)-background),axis=1),75)>40:
        # A screenshot crop may straddle a dark panel and a light border.
        # Local contrast isolates strokes without treating the panel as ink.
        gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
        kernel=np.ones((5,5),np.uint8)
        strokes=cv2.max(cv2.morphologyEx(gray,cv2.MORPH_TOPHAT,kernel),
                        cv2.morphologyEx(gray,cv2.MORPH_BLACKHAT,kernel))
        ink=(strokes>24).astype(np.uint8)
    count,labels,stats,_=cv2.connectedComponentsWithStats(ink)
    keep=np.zeros_like(ink)
    for label in range(1,count):
        x,y,w,h,area=stats[label]
        if (w>width*.8 and h<=2 and (y<=1 or y+h>=height-1)) or (h>height*.8 and w<=1 and (x<=1 or x+w>=width-1)): continue
        keep[labels==label]=1
    ys,xs=np.nonzero(keep)
    if not len(xs): return crop
    return crop[max(0,ys.min()-1):min(height,ys.max()+2),max(0,xs.min()-1):min(width,xs.max()+2)]


def source_word_segments(crop,max_segments=4):
    """Bound aspect ratio using wide source gaps; do not cut occupied columns."""
    import numpy as np
    height,width=crop.shape[:2]
    if width<=height*7: return [crop]
    edge=np.concatenate([crop[0],crop[-1]])
    background=np.median(edge,axis=0)
    blank=(np.max(abs(crop.astype(float)-background),axis=2)>24).mean(axis=0)<.02
    transitions=np.diff(np.r_[False,blank,False].astype(int))
    gaps=[int((a+b)//2) for a,b in zip(np.flatnonzero(transitions==1),np.flatnonzero(transitions==-1))
          if a>0 and b<width and b-a>=max(3,height*.4)]
    cuts=[0]
    while width-cuts[-1]>height*7 and len(cuts)<max_segments:
        valid=[v for v in gaps if cuts[-1]+height*2<v<=cuts[-1]+height*7]
        if valid: cuts.append(max(valid))
        else:
            # A long word or identifier stays intact, even beyond the target
            # aspect ratio; the next genuine word gap is the only allowed cut.
            following=[v for v in gaps if v>cuts[-1]+height*7]
            if not following: break
            cuts.append(min(following))
    cuts.append(width)
    return [crop[:,a:b] for a,b in zip(cuts,cuts[1:])]


def refine_recognition(crop,primary,secondary_factory,language,previous):
    """A bounded second stage for unresolved small text, without text correction."""
    import cv2
    import numpy as np
    tight=tight_source_crop(crop)
    segments=source_word_segments(tight)
    reads=[]
    models=[('en_PP-OCRv5_mobile_rec' if language=='en' else 'PP-OCRv5_server_rec',primary)]
    if language=='en': models.append(('PP-OCRv5_server_rec',secondary_factory()))
    for model_name,model in models:
        for variant in ('source_tight','source_tight_contrast'):
            words=[]
            for segment in segments:
                view=cv2.resize(segment,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
                if variant.endswith('contrast'):
                    gray=cv2.cvtColor(view,cv2.COLOR_BGR2GRAY)
                    if np.median(np.r_[gray[0],gray[-1]])<128: gray=255-gray
                    low,high=np.percentile(gray,(2,98))
                    if high-low>10: gray=np.clip((gray.astype(float)-low)*255/(high-low),0,255).astype(np.uint8)
                    view=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
                output=list(model.predict(view))
                if len(output)!=1: raise ValueError('Expected one targeted OCR result per source segment.')
                words.append({'text':str(output[0]['rec_text']).strip(),'score':float(output[0]['rec_score'])})
            reads.append({'text':' '.join(w['text'] for w in words),'score':min(w['score'] for w in words),
                          'variant':variant,'model':model_name,'segments':len(segments),'words':words})
    confirmed=cross_model_candidate(reads[:2],reads[2:]) if len(models)==2 else None
    # Tight crops can produce the same literal in both engines while the small
    # language model scores it just below .90. Require all four new views to
    # agree, including two >=.95 views, rather than lowering the base threshold.
    if confirmed is None and len(models)==2 and all(r['text'] and r['score']>=.85 for r in reads):
        keys={consensus_key(r['text']) for r in reads}
        strong_views=[r for r in reads if r['score']>=.95]
        if len(keys)==1 and len(strong_views)>=2:
            confirmed=max(strong_views,key=lambda r:r['score'])
    strong={consensus_key(r['text']) for r in previous if r['score']>=.95 and r['text'].strip()}
    if confirmed and (not strong or strong=={consensus_key(confirmed['text'])}):
        return confirmed,False,'targeted_source_model_agreement',reads
    nonempty=[r for r in previous+reads if r['text'].strip()]
    if not nonempty: raise ValueError('Targeted OCR could not read any source text.')
    return max(nonempty,key=lambda r:r['score']),True,'targeted_source_unresolved',reads


def source_detail_crop(source,bbox,fallback):
    """Render a bounded clip at source image density, capped at 4x page scale."""
    import pymupdf
    import numpy as np
    import cv2
    with pymupdf.open(source) as pdf:
        page=pdf[0]; clip=pymupdf.Rect(bbox)+(-2,-2,2,2); clip &= page.rect
        density=1.0
        for image in page.get_image_info():
            rect=pymupdf.Rect(image['bbox'])
            if rect.contains(clip) and rect.width>0 and rect.height>0:
                density=max(density,min(image['width']/rect.width,image['height']/rect.height))
        # Extra rendering density preserves native high-resolution image detail;
        # it is not described as new detail for an already low-resolution scan.
        scale=min(4.0,max(1.0,density))
        if clip.is_empty or clip.width*clip.height*scale*scale>2_000_000: return fallback
        pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=clip,colorspace=pymupdf.csRGB,alpha=False)
        return cv2.cvtColor(np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3),cv2.COLOR_RGB2BGR)


def main(argv=None, model_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detection', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true', help='Validate and save crops without loading a model')
    parser.add_argument('--padding', type=int, choices=range(0, 5), default=2, help='Extra pixels around each detector box')
    parser.add_argument('--run-name', default='recognition', help='New recognition output folder name')
    parser.add_argument("--device", choices=["cpu", "gpu:0"], default="cpu")
    parser.add_argument('--source-language', default='unknown')
    parser.add_argument('--profile',choices=['bounded','exhaustive'],default='bounded',help='Bounded language-aware recognition, or the earlier exhaustive diagnostic retries')
    args = parser.parse_args(argv)
    if not args.run_name or Path(args.run_name).name != args.run_name or args.run_name in ('.', '..'):
        parser.error('--run-name must be a single folder name')
    root = args.detection.resolve()
    out = root / args.run_name
    if out.exists() and not args.prepare_only:
        raise ValueError('Recognition output already exists; choose a new --run-name.')
    report = json.loads((root / 'diagnostic.json').read_text(encoding='utf-8'))
    if report['status'] != 'completed':
        raise ValueError('Use a completed detector run.')
    import cv2
    import numpy as np
    import pymupdf
    os.environ.setdefault('FLAGS_use_mkldnn', 'false')
    os.environ.setdefault('FLAGS_enable_pir_api', '0')
    from paddlex.inference.pipelines.components.common.crop_image_regions import CropByPolys
    detection_path = root / 'page-1_res.json'
    detection = json.loads(detection_path.read_text(encoding='utf-8'))
    image = cv2.imdecode(np.frombuffer((root / 'page-1.png').read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('Detector source image cannot be decoded.')
    height, width = image.shape[:2]
    if [width, height] != report['image_pixels']:
        raise ValueError('Detector image dimensions do not match its report.')
    with pymupdf.open(report['source']) as pdf:
        rect = pdf[0].rect
        page_width, page_height = rect.width, rect.height
    polygons = detector_polygons(detection['dt_polys'])
    if len(polygons)==0 and not args.prepare_only:
        from importlib.metadata import version
        out.mkdir()
        metadata={'status':'completed','lines':0,'device':args.device,'no_text_detected':True,
            'page_width':page_width,'page_height':page_height,'raster':report.get('raster'),
            'detection_run':str(root),'detection_sha256':hashlib.sha256(detection_path.read_bytes()).hexdigest(),
            'image_sha256':hashlib.sha256((root/'page-1.png').read_bytes()).hexdigest(),
            'versions':{p:version(p) for p in ('paddleocr','paddlex')},
            'limitations':'No text boxes detected; does not establish that the image contains no text. Visual review required.'}
        (out/'manifest.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        (out/'lines.jsonl').write_text('',encoding='utf-8')
        print('No text boxes detected; preserve the source image for visual review.',flush=True)
        return
    crop_polygons = polygons.copy()
    if args.padding:
        for i, polygon in enumerate(polygons):
            center, (w, h), angle = cv2.minAreaRect(polygon)
            expanded = cv2.boxPoints((center, (w + 2*args.padding, h + 2*args.padding), angle))
            expanded[:, 0] = np.clip(expanded[:, 0], 0, width-1)
            expanded[:, 1] = np.clip(expanded[:, 1], 0, height-1)
            crop_polygons[i] = expanded
    crops = CropByPolys(det_box_type='quad')(image, crop_polygons)
    unpadded_crops = list(CropByPolys(det_box_type='quad')(image, polygons))
    crop_dir = root / (args.run_name + '-crops')
    crop_dir.mkdir(exist_ok=True)
    records = []
    for i, (poly, crop) in enumerate(zip(polygons, crops)):
        if crop.size == 0:
            raise ValueError(f'Empty crop for detector box {i+1}')
        path = crop_dir / f'line-{i+1:03}.png'
        ok, encoded = cv2.imencode('.png', crop)
        if not ok:
            raise ValueError(f'Cannot encode crop {i+1}')
        path.write_bytes(encoded.tobytes())
        records.append({'id': f'p1_line{i+1}', 'detector_index': i,
                        'polygon_pixels': poly.tolist(), 'crop': str(path),
                        'crop_polygon_pixels': crop_polygons[i].tolist(),
                        'bbox': [float(poly[:,0].min()*page_width/width), float(poly[:,1].min()*page_height/height),
                                 float(poly[:,0].max()*page_width/width), float(poly[:,1].max()*page_height/height)]})
    print(f'Prepared {len(records)} crops from saved detector boxes.', flush=True)
    if args.prepare_only:
        return
    out.mkdir()  # Preserve any earlier recognition attempt.
    with (out / 'crash.log').open('w', encoding='utf-8') as crash:
        faulthandler.enable(file=crash)
        from paddleocr import TextRecognition
        from importlib.metadata import version
        model_name = 'en_PP-OCRv5_mobile_rec' if args.source_language=='en' and args.profile=='bounded' else 'PP-OCRv5_server_rec'
        secondary_model = None
        cache = Path.home() / '.paddlex/official_models' / model_name
        if not cache.is_dir():
            raise ValueError(f'Existing recognition model not found: {cache}')
        metadata = {'status': 'processing', 'model': model_name, 'device': args.device, 'cpu_threads': 10,'profile':args.profile,
                    'targeted_retry':'source-density-tight-word-gaps-v1' if args.profile=='bounded' else None,
                    'maximum_targeted_calls_per_region':16 if args.profile=='bounded' else None,
                    'source_language':args.source_language,
                    'verification_model':'PP-OCRv5_server_rec',
                    'crop_padding_pixels': args.padding,
                    'detection_run': str(root), 'detection_sha256': hashlib.sha256(detection_path.read_bytes()).hexdigest(),
                    'image_sha256': hashlib.sha256((root/'page-1.png').read_bytes()).hexdigest(),
                    'raster':report.get('raster'),
                    'versions': {p: version(p) for p in ('paddleocr', 'paddlepaddle-gpu' if args.device.startswith('gpu') else 'paddlepaddle', 'paddlex')},
                    'coordinate_system': 'displayed-page points; top-left origin; bbox=[left,top,right,bottom]',
                    'page_width': page_width, 'page_height': page_height,
                    'limitations': 'Detector order is not validated reading order. Lines include text inside tables and figures. No table structure or figure classification is inferred. OCR confidence is not measured accuracy.'}
        status_path = out / 'manifest.json'
        status_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        started = time.monotonic()
        try:
            print(f'Loading existing recognition model on {args.device}...', flush=True)
            model = (model_factory or TextRecognition)(model_name=model_name, model_dir=str(cache), device=args.device, enable_mkldnn=False, cpu_threads=10)
            def verification_model():
                nonlocal secondary_model
                if args.profile=='bounded' and args.source_language!='en': return model
                if secondary_model is None:
                    name='PP-OCRv5_server_rec' if model_name=='en_PP-OCRv5_mobile_rec' else 'en_PP-OCRv5_mobile_rec'
                    secondary_model=(model_factory or TextRecognition)(model_name=name,device=args.device,enable_mkldnn=False,cpu_threads=10)
                return secondary_model
            with (out / 'lines.jsonl').open('w', encoding='utf-8') as evidence:
                for i, record in enumerate(records):
                    if args.profile=='bounded':
                        best,review,decision,candidates=bounded_recognition(unpadded_crops[i],model,verification_model,args.source_language,padded=crops[i])
                        if review:
                            detail=source_detail_crop(report['source'],record['bbox'],crops[i])
                            best,review,decision,retries=refine_recognition(detail,model,verification_model,args.source_language,candidates)
                            candidates.extend(retries)
                        record.update(text=best['text'],recognition_score=best['score'],candidates=candidates,
                                      requires_review=review,recognition_decision=decision)
                        evidence.write(json.dumps(record,ensure_ascii=False)+'\n'); evidence.flush()
                        print(f'Recognized {i+1}/{len(records)} ({len(candidates)} source views)',flush=True)
                        continue
                    results = list(model.predict(record['crop']))
                    if len(results) != 1:
                        raise ValueError('Expected one recognition result per crop.')
                    record.update(text=str(results[0]['rec_text']), recognition_score=float(results[0]['rec_score']))
                    # Repeat uncertain/small crops with interpolation, retaining both
                    # hypotheses. Enlargement is not recovery of missing source detail.
                    crop = cv2.imread(record['crop'])
                    candidates = [{'text': record['text'], 'score': record['recognition_score'], 'variant': 'original'}]
                    if crop.shape[0] < 32 or record['recognition_score'] < .97:
                        enlarged = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                        retry = list(model.predict(enlarged))[0]
                        candidates.append({'text': str(retry['rec_text']), 'score': float(retry['rec_score']), 'variant': '2x_cubic'})
                    pieces = split_long_crop(crop)
                    if len(pieces) > 1:
                        parts = [list(model.predict(part))[0] for part in pieces]
                        candidates.append({'text': ' '.join(str(p['rec_text']).strip() for p in parts),
                                           'score': min(float(p['rec_score']) for p in parts),
                                           'variant': 'whitespace_segments'})
                    best, review, decision = select_candidate(candidates)
                    if review:
                        # A distinct contrast-normalized view for difficult small
                        # glyphs; all candidates remain available for audit.
                        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                        normalized = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
                        normalized = cv2.resize(normalized, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                        retry = list(model.predict(cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)))[0]
                        candidates.append({'text':str(retry['rec_text']), 'score':float(retry['rec_score']), 'variant':'3x_gray_contrast'})
                        best, review, decision = select_candidate(candidates)
                    if review:
                        pieces = split_long_crop(crop, aspect_limit=7)
                        if len(pieces) > 1:
                            parts = [list(model.predict(cv2.resize(part, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)))[0] for part in pieces]
                            candidates.append({'text':' '.join(str(p['rec_text']).strip() for p in parts),
                                               'score':min(float(p['rec_score']) for p in parts), 'variant':'short_segments_2x'})
                            best, review, decision = select_candidate(candidates)
                    if review:
                        # White text on dark headers needs a light-background view.
                        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                        edge = np.concatenate([gray[0], gray[-1]])
                        if np.median(edge) < 128:
                            light = cv2.cvtColor(255-gray, cv2.COLOR_GRAY2BGR)
                            retry = list(model.predict(cv2.resize(light,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)))[0]
                            candidates.append({'text':str(retry['rec_text']), 'score':float(retry['rec_score']), 'variant':'light_background_3x'})
                        pieces = split_long_crop(crop, aspect_limit=7)
                        if len(pieces) > 1:
                            parts = []
                            for part in pieces:
                                gray = cv2.cvtColor(part, cv2.COLOR_BGR2GRAY)
                                view = cv2.normalize(gray,None,0,255,cv2.NORM_MINMAX)
                                view = cv2.resize(view,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
                                parts.append(list(model.predict(cv2.cvtColor(view,cv2.COLOR_GRAY2BGR)))[0])
                            candidates.append({'text':' '.join(str(p['rec_text']).strip() for p in parts),
                                               'score':min(float(p['rec_score']) for p in parts), 'variant':'short_segments_contrast_3x'})
                        best, review, decision = select_candidate(candidates)
                    if review:
                        # Add surrounding background, not guessed glyphs. Tight
                        # crops can clip the recognizer's sequence boundary.
                        pad=max(2,int(crop.shape[0]*.25))
                        edge=np.concatenate([crop[0],crop[-1],crop[:,0],crop[:,-1]])
                        color=tuple(float(v) for v in np.median(edge,axis=0))
                        padded=cv2.copyMakeBorder(crop,pad,pad,pad,pad,cv2.BORDER_CONSTANT,value=color)
                        retry=list(model.predict(padded))[0]
                        candidates.append({'text':str(retry['rec_text']),'score':float(retry['rec_score']),'variant':'background_padding'})
                        best,review,decision=select_candidate(candidates)
                    if review and args.padding:
                        original_crop=unpadded_crops[i]
                        for label,view in [('detector_bounds',original_crop),('detector_bounds_2x',cv2.resize(original_crop,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC))]:
                            retry=list(model.predict(view))[0]
                            candidates.append({'text':str(retry['rec_text']),'score':float(retry['rec_score']),'variant':label})
                        best,review,decision=select_candidate(candidates)
                    if review and args.source_language=='en':
                        if secondary_model is None:
                            print('Loading English OCR verification model...',flush=True)
                            secondary_model=TextRecognition(model_name='en_PP-OCRv5_mobile_rec',device=args.device)
                        checks=[]
                        for label,view in [('english_original',crop),('english_2x',cv2.resize(crop,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC))]:
                            result=list(secondary_model.predict(view))[0]
                            checks.append({'text':str(result['rec_text']),'score':float(result['rec_score']),
                                           'variant':label,'model':'en_PP-OCRv5_mobile_rec'})
                        confirmed=cross_model_candidate(candidates,checks)
                        candidates.extend(checks)
                        if confirmed is None and args.padding:
                            exact_checks=[]
                            for label,view in [('english_detector_bounds',unpadded_crops[i]),('english_detector_bounds_2x',cv2.resize(unpadded_crops[i],None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC))]:
                                result=list(secondary_model.predict(view))[0]
                                exact_checks.append({'text':str(result['rec_text']),'score':float(result['rec_score']),
                                                     'variant':label,'model':'en_PP-OCRv5_mobile_rec'})
                            exact_primary=[c for c in candidates if c['variant'] in ('detector_bounds','detector_bounds_2x')]
                            stable_primary=len(exact_primary)==2 and all(c['score']>=.90 for c in exact_primary) and len({c['text'].strip() for c in exact_primary})==1
                            if stable_primary:
                                confirmed=cross_model_candidate(exact_primary,exact_checks)
                            candidates.extend(exact_checks)
                        if confirmed is not None:
                            best,review,decision=confirmed,False,'cross_model_exact_agreement'
                        if review:
                            pieces=split_word_crops(unpadded_crops[i])
                            if 1<len(pieces)<=50:
                                word_reads=[]
                                for recognizer,label in ((model,'word_gap_primary'),(secondary_model,'word_gap_english')):
                                    parts=[list(recognizer.predict(piece))[0] for piece in pieces]
                                    offset=0
                                    words=[]
                                    for piece,part in zip(pieces,parts):
                                        words.append({'text':str(part['rec_text']),'score':float(part['rec_score']),
                                                      'start':offset,'end':offset+piece.shape[1]})
                                        offset+=piece.shape[1]
                                    word_reads.append({'text':' '.join(str(part['rec_text']).strip() for part in parts),
                                        'score':min(float(part['rec_score']) for part in parts),'variant':label,'words':words})
                                candidates.extend(word_reads)
                                if all(c['score']>=.90 for c in word_reads) and len({c['text'].strip() for c in word_reads})==1:
                                    best,review,decision=word_reads[0],False,'cross_model_word_gap_agreement'
                                elif review:
                                    prefix=confirmed_label_prefix(word_reads[0]['words'],word_reads[1]['words'])
                                    poly=polygons[i]
                                    axis_aligned=abs(poly[0,1]-poly[1,1])<1.5 and abs(poly[2,1]-poly[3,1])<1.5 and abs(poly[0,0]-poly[3,0])<1.5 and abs(poly[1,0]-poly[2,0])<1.5
                                    if prefix and axis_aligned and prefix['end']<unpadded_crops[i].shape[1]:
                                        left,top,right,bottom=record['bbox']
                                        edge=left+(right-left)*prefix['end']/unpadded_crops[i].shape[1]
                                        record['confirmed_label']={**prefix,'bbox':[left,top,edge,bottom],
                                            'method':'two_recognizers_at_detected_word_boundaries'}
                    record.update(text=best['text'], recognition_score=best['score'], candidates=candidates,
                                  requires_review=review, recognition_decision=decision)
                    evidence.write(json.dumps(record, ensure_ascii=False) + '\n')
                    evidence.flush()
                    print(f'Recognized {i+1}/{len(records)}', flush=True)
            (out / 'review.txt').write_text('\n'.join(f"{r['id']} | {r['recognition_score']:.3f} | {r['text']}" for r in records), encoding='utf-8')
            ordered = sorted(records, key=lambda r: (r['bbox'][1], r['bbox'][0]))
            (out / 'review-top-to-bottom.txt').write_text(
                'Geometric review order only; not validated paragraph, column or table reading order.\n\n' +
                '\n'.join(f"{r['id']} | {r['recognition_score']:.3f} | {r['text']}" for r in ordered), encoding='utf-8')
            metadata.update(status='completed', lines=len(records), review_status='requires_human_review')
        except Exception as exc:
            metadata.update(status='failed', error=str(exc))
            raise
        finally:
            metadata['seconds'] = round(time.monotonic()-started, 1)
            status_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
            faulthandler.disable()
    print(f'Recognition complete. Review: {out / "review.txt"}', flush=True)


if __name__ == '__main__':
    main()
