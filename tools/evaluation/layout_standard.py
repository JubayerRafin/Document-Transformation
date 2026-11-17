"""Supplementary IoU/SSIM preservation metrics; never overwrites earlier reports."""
import argparse
from collections import Counter
import csv
import hashlib
import html
from importlib.metadata import version
import json
from pathlib import Path
import sys
import numpy as np
import pymupdf
from scipy.optimize import linear_sum_assignment


def iou(a,b):
    a,b=pymupdf.Rect(a),pymupdf.Rect(b)
    intersection=max(0,min(a.x1,b.x1)-max(a.x0,b.x0))*max(0,min(a.y1,b.y1)-max(a.y0,b.y0))
    union=max(0,a.width)*max(0,a.height)+max(0,b.width)*max(0,b.height)-intersection
    return intersection/union if union>0 else 0.0


def match_boxes(reference,predicted,threshold):
    """Maximum-cardinality matching first, then maximum IoU; no duplicate credit."""
    matrix=np.array([[iou(a,b) for b in predicted] for a in reference],dtype=float)
    pairs=[]
    if reference and predicted:
        bonus=min(len(reference),len(predicted))+1
        rows,cols=linear_sum_assignment(-((matrix>=threshold)*bonus+matrix))
        pairs=[{'reference_index':int(r),'output_index':int(c),'iou':float(matrix[r,c])}
               for r,c in zip(rows,cols) if matrix[r,c]>=threshold]
    tp=len(pairs);fp=len(predicted)-tp;fn=len(reference)-tp
    precision=tp/(tp+fp) if tp+fp else None
    recall=tp/(tp+fn) if tp+fn else None
    f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
    return {'threshold':threshold,'tp':tp,'fp':fp,'fn':fn,'precision':precision,'recall':recall,'f1':f1,
            'mean_matched_iou':sum(p['iou'] for p in pairs)/tp if tp else None,'matches':pairs}


def gray(page,dpi,clip=None):
    p=page.get_pixmap(matrix=pymupdf.Matrix(dpi/72,dpi/72),colorspace=pymupdf.csGRAY,alpha=False,clip=clip)
    return np.frombuffer(p.samples,dtype=np.uint8).reshape(p.height,p.width)


def ssim(a,b):
    from skimage.metrics import structural_similarity
    if a.shape!=b.shape:return {'score':None,'reason':'Rendered shapes differ; no resizing applied.'}
    if min(a.shape)<11:return {'score':None,'reason':'Crop too small for the 11-pixel Gaussian window.'}
    value=structural_similarity(a,b,data_range=255,gaussian_weights=True,sigma=1.5,use_sample_covariance=False)
    return {'score':float(value),'shape':list(a.shape)}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate(config,dpi=150):
    root=Path(config['output_root']); destination=root/'layout-standard-metrics';destination.mkdir(exist_ok=True)
    previous=root/'layout-reconstruction/report.json'
    previous_hash=digest(previous)
    dataset={d['id']:d for d in json.loads((root/'dataset.json').read_text(encoding='utf-8'))['documents']}
    geometry=json.loads((root/'extraction-reconstruction/report.json').read_text(encoding='utf-8'))
    geo={d['document']:d for d in geometry['documents']}
    records=json.loads((root/'translation/index.json').read_text(encoding='utf-8'))['records']
    rows=[]
    for record in records:
        name=record['document_id'];doc=dataset[name]
        manifest=json.loads(Path(record['manifest']).read_text(encoding='utf-8'))
        review=json.loads((root/'ground-truth'/f"{name}.{record['target']}.json").read_text(encoding='utf-8'))
        if digest(doc['path'])!=doc['sha256'] or digest(manifest['output_file'])!=review['output_sha256']:
            raise ValueError('Source/output changed: '+name)
        row={'document':name,'source_sha256':doc['sha256'],'output_sha256':review['output_sha256'],
             'pages':[],'confirmed_visual_findings':review.get('layout_issues',[])}
        with pymupdf.open(doc['path']) as src,pymupdf.open(manifest['output_file']) as out:
            row['source_pages']=len(src);row['output_pages']=len(out)
            for n,page in enumerate(src):
                if n>=len(out):row['pages'].append({'page':n+1,'status':'missing_output_page'});continue
                result=out[n]
                page_result={'page':n+1,'status':'measured','page_ssim':ssim(gray(page,dpi),gray(result,dpi)),
                             'categories':{},'source_image_crop_ssim':[]}
                grids=geo[name]['extraction']['pages'][n]['source_strict_line_grids']
                output_grids=geo[name]['reconstruction']['pages'][n]['output_strict_line_grids']
                categories={'embedded_image_placements':([list(i['bbox']) for i in page.get_image_info()],
                                                          [list(i['bbox']) for i in result.get_image_info()])}
                if isinstance(grids,list) and isinstance(output_grids,list):
                    categories['native_table_grids']=([t['bbox'] for t in grids],[t['bbox'] for t in output_grids])
                for category,(reference,predicted) in categories.items():
                    page_result['categories'][category]={'reference_boxes':reference,'output_boxes':predicted,
                        'metrics':[match_boxes(reference,predicted,t) for t in (.5,.75)]}
                for i,image in enumerate(page.get_image_info()):
                    bbox=list(image['bbox']);box=pymupdf.Rect(bbox)&page.rect&result.rect
                    if box.is_empty:continue
                    crop={'source_image_index':i,'bbox':bbox,**ssim(gray(page,dpi,box),gray(result,dpi,box)),
                          'scope':'Rendered source-location crop. Includes overlays and any image text; translated image text legitimately changes this score.'}
                    page_result['source_image_crop_ssim'].append(crop)
                row['pages'].append(page_result)
        rows.append(row);print('Measured',name,flush=True)
    aggregate=[]
    for category in ['native_table_grids','embedded_image_placements']:
        for threshold in [.5,.75]:
            counts=Counter()
            for row in rows:
                for page in row['pages']:
                    item=page.get('categories',{}).get(category)
                    if item:
                        metric=next(x for x in item['metrics'] if x['threshold']==threshold)
                        counts.update({k:metric[k] for k in ['tp','fp','fn']})
            tp,fp,fn=(counts[k] for k in ['tp','fp','fn'])
            aggregate.append({'category':category,'threshold':threshold,**dict(counts),
                              'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
                              'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None})
    report={'method':'Standard metric definitions applied to reconstruction preservation; not a public-dataset detection benchmark.',
        'sources':{'SSIM':'https://scikit-image.org/docs/stable/api/skimage.metrics.html#skimage.metrics.structural_similarity',
                   'COCO_IoU_thresholds':'https://cocodataset.org/#detection-eval',
                   'DocLayNet_protocol':'https://ds4sd.github.io/icdar23-doclaynet/task/'},
        'rendering':{'dpi':dpi,'colorspace':'grayscale','resizing':'none','ssim_data_range':255,'gaussian_weights':True,'sigma':1.5,'use_sample_covariance':False},
        'versions':{k:version(k) for k in ['pymupdf','numpy','scipy','scikit-image']},
        'limitations':['Reference table boxes are strict-line detector proxies, not independently annotated ground truth.',
            'Image boxes come from PDF raster placements. They exclude semantic vector figures and cannot establish visible preservation.',
            'IoU precision/recall/F1 are geometric matching metrics. A retained but obscured image can pass.',
            'Whole-page SSIM is confounded by translation, reflow and font changes; it is not layout accuracy or a pass/fail rule.',
            'Crop SSIM catches visual changes but does not distinguish desirable translation from damage. Review each crop.',
            'No mAP is reported: independent class annotations and meaningful detector confidence rankings are unavailable.',
            'No claim of DocLayNet or COCO benchmark performance; their annotated datasets were not evaluated.'],
        'previous_report':{'path':str(previous),'sha256':previous_hash,'preserved':digest(previous)==previous_hash},
        'aggregate_geometry_metrics':aggregate,'documents':rows}
    qa_path=destination/'rendering-review.json'
    qa=json.loads(qa_path.read_text(encoding='utf-8')) if qa_path.exists() else None
    if qa:report['supplementary_rendering_review']=qa
    (destination/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (destination/'summary.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=['document','page','page_ssim','image_crop_count','lowest_image_crop_ssim'])
        writer.writeheader()
        for row in rows:
            for page in row['pages']:
                crops=[c['score'] for c in page.get('source_image_crop_ssim',[]) if c['score'] is not None]
                writer.writerow({'document':row['document'],'page':page['page'],'page_ssim':page.get('page_ssim',{}).get('score'),
                                 'image_crop_count':len(crops),'lowest_image_crop_ssim':min(crops) if crops else None})
    body='<h1>Supplementary layout metrics: IoU and SSIM</h1><p>'+html.escape(report['method'])+'</p>'
    body+='<p><a href="../layout-reconstruction/report.html">Original geometry checks and confirmed visual review — preserved</a></p>'
    if qa:
        body+='<aside style="background:#fff1ce;padding:18px"><h2>Supplementary rendering review</h2>'
        body+=''.join('<p>'+html.escape(qa.get(k,''))+'</p>' for k in ['evidence','interpretation','next_check'])
        body+=' · '.join('<a href="'+html.escape(v,quote=True)+'">'+html.escape(k)+'</a>' for k,v in qa.get('previews',{}).items())+'</aside>'
    body+='<h2>IoU geometry matching</h2><table><tr><th>Category</th><th>IoU threshold</th><th>TP / FP / FN</th><th>Precision</th><th>Recall</th><th>F1</th></tr>'
    fmt=lambda v:'N/A' if v is None else f'{v:.3f}'
    for r in aggregate:
        body+='<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [r['category'],r['threshold'],f"{r.get('tp',0)} / {r.get('fp',0)} / {r.get('fn',0)}",fmt(r['precision']),fmt(r['recall']),fmt(r['f1'])])+'</tr>'
    body+='</table><h2>Limits of interpretation</h2><ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in report['limitations'])+'</ul>'
    for row in rows:
        body+='<h2>'+row['document']+'</h2>'
        for p in row['pages']:
            body+=f'<h3>Page {p["page"]}: rendered-page SSIM {fmt(p.get("page_ssim",{}).get("score"))}</h3>'
            body+='<table><tr><th>Source image crop</th><th>Bounding box</th><th>SSIM</th></tr>'
            for c in p.get('source_image_crop_ssim',[]):body+='<tr><td>'+str(c['source_image_index'])+'</td><td>'+html.escape(str([round(x,1) for x in c['bbox']]))+'</td><td>'+fmt(c['score'])+'</td></tr>'
            body+='</table>'
        body+='<ul>'+''.join('<li>'+html.escape(f['note'])+'</li>' for f in row['confirmed_visual_findings'])+'</ul>'
    body+='<h2>Method references</h2><ul>'+''.join('<li><a href="'+v+'">'+k+'</a></li>' for k,v in report['sources'].items())+'</ul>'
    (destination/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;max-width:1150px;margin:30px auto}table{border-collapse:collapse}td,th{padding:10px;border:1px solid #ccd;text-align:left}</style>'+body,encoding='utf-8')
    print(destination/'report.html');return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True);parser.add_argument('--dpi',type=int,default=150)
    args=parser.parse_args()
    if not 72<=args.dpi<=300:parser.error('Use a rendering DPI between 72 and 300.')
    evaluate(json.loads(Path(args.config).read_text(encoding='utf-8-sig')),args.dpi)
