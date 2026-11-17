"""Deep Doc render-and-mask with OCR masks and spatial figure grouping.

Vendor Stage 1 files remain unchanged. Detection copies are masked; assets are
always cropped from the original rendered page.
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pymupdf


def enrich_figures(source,ir,folder):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    provenance=ir.setdefault('settings',{}).setdefault('extraction_evidence',{}).setdefault('spatial_figures',{'pages':[]})
    try:
        from extraction.deep_doc_adapter import ImageAdapter
        import cv2
        from PIL import Image
    except ImportError as exc:
        ir.setdefault('warnings',[]).append('Deep Doc figure dependencies unavailable: '+str(exc))
        provenance.update(status='unavailable',error=str(exc))
        return ir

    class SpatialImageAdapter(ImageAdapter):
        LINE_PAD=6
        def _find_content_regions(self,image):
            pixels=np.asarray(image).copy()
            scale=self.SCALE/self.DOWNSAMPLE
            for box,color in getattr(self,'table_backgrounds',[]):
                x0,y0,x1,y1=[int(round(v*scale)) for v in box]
                patch=pixels[max(0,y0):min(len(pixels),y1),max(0,x0):min(pixels.shape[1],x1)]
                if not patch.size: continue
                # Remove only the uniform fill color. Artwork drawn on top of a
                # shaded cell remains visible in the detection copy.
                match=np.max(np.abs(patch.astype(np.int16)-np.asarray(color,dtype=np.int16)),axis=2)<=12
                patch[match]=255
            # Ignore pale uniform table shading while keeping coloured artwork.
            ink=((pixels.min(axis=2)<210)|(pixels.max(axis=2)-pixels.min(axis=2)>40)).astype(np.uint8)
            ink=cv2.morphologyEx(ink,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
            _,_,stats,_=cv2.connectedComponentsWithStats(ink)
            return [(int(x),int(y),int(x+w),int(y+h)) for x,y,w,h,a in stats[1:] if a>=4]

        def _merge_regions(self,regions):
            groups=list(regions); changed=True
            while changed:
                changed=False
                for i,a in enumerate(groups):
                    for j in range(i+1,len(groups)):
                        b=groups[j]
                        if max(a[0],b[0])-min(a[2],b[2])<=self.MERGE_GAP and max(a[1],b[1])-min(a[3],b[3])<=self.MERGE_GAP:
                            groups[i]=(min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3]))
                            groups.pop(j); changed=True; break
                    if changed: break
            self.boxes=groups
            return groups

    detector=SpatialImageAdapter({'images':{'min_width':30,'min_height':30}},folder)
    evidence=[]
    with pymupdf.open(source) as pdf:
        for unit in ir['units']:
            page=pdf[unit['number']-1]
            # Replace the earlier row-band figure candidates with this spatial
            # pass; retain native embedded-image occurrences as source evidence.
            unit['regions']=[r for r in unit['regions'] if not (r['kind']=='figure' and
                r.get('metadata',{}).get('method')=='deep-doc-stage1-render-mask')]
            for index,region in enumerate(unit['regions']):
                if region['kind']!='figure': continue
                box=pymupdf.Rect(region['bbox'])
                if not region.get('asset'):
                    asset=folder/f'embedded-p{unit["number"]}-{index}.png'
                    page.get_pixmap(matrix=pymupdf.Matrix(150/72,150/72),clip=box,alpha=False).save(asset)
                    region['asset']=str(asset.absolute())
                record={'region_id':region['id'],'page':unit['number'],'bbox':list(box),
                        'path':region['asset'],'method':'source_occurrence_render'}
                ir.setdefault('assets',[]).append(record); evidence.append(record)
            chars=[]
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        chars.extend({'x0':c['bbox'][0],'top':c['bbox'][1],'x1':c['bbox'][2],'bottom':c['bbox'][3]} for c in span['chars'])
            if unit.get('ocr_folder'):
                chars.extend({'x0':r['bbox'][0],'top':r['bbox'][1],'x1':r['bbox'][2],'bottom':r['bbox'][3]}
                             for r in unit['regions'] if r.get('text') and r.get('bbox'))
            lines=[]
            table_cells=[]
            for region in unit['regions']:
                if region.get('kind')!='table': continue
                metadata=region.get('metadata',{})
                cells=metadata.get('native_grid_cell_records') or metadata.get('cell_records',[])
                table_cells.extend(cells)
                for cell in cells:
                    x0,y0,x1,y1=cell['bbox']
                    lines.extend([{'x0':x0,'top':y0,'x1':x1,'bottom':y0},{'x0':x0,'top':y1,'x1':x1,'bottom':y1},
                                  {'x0':x0,'top':y0,'x1':x0,'bottom':y1},{'x0':x1,'top':y0,'x1':x1,'bottom':y1}])
            detector.table_backgrounds=[]
            for drawing in page.get_drawings():
                fill=drawing.get('fill')
                if fill is None or len(fill)!=3: continue
                for item in drawing['items']:
                    if item[0]!='re': continue
                    box=item[1]*page.rotation_matrix
                    # A cell background spans a column and lies inside that
                    # cell. Inset icons and unrelated vector rectangles do not.
                    if any(abs(box.x0-c['bbox'][0])<=2 and abs(box.x1-c['bbox'][2])<=2
                           and box.y0>=c['bbox'][1]-2 and box.y1<=c['bbox'][3]+2 for c in table_cells):
                        detector.table_backgrounds.append((list(box),[round(v*255) for v in fill]))
            def render(resolution):
                pix=page.get_pixmap(matrix=pymupdf.Matrix(resolution/72,resolution/72),colorspace=pymupdf.csRGB,alpha=False)
                return NS(original=Image.frombytes('RGB',[pix.width,pix.height],pix.samples))
            proxy=NS(width=page.rect.width,height=page.rect.height,chars=chars,lines=lines,rects=[],to_image=render)
            try: figures=detector._extract_page(proxy,unit['number'])
            except Exception as exc:
                unit.setdefault('warnings',[]).append('Deep Doc figure extraction requires review: '+str(exc))
                provenance['pages'].append({'page':unit['number'],'status':'failed','error':str(exc)})
                continue
            provenance['pages'].append({'page':unit['number'],'status':'completed','candidates':len(figures)})
            for index,element in enumerate(figures):
                if not hasattr(element,'bbox'): continue
                box=pymupdf.Rect(element.bbox)
                if box.width/box.height>8 and box.height<page.rect.height*.08: continue
                if box.get_area()>page.rect.get_area()*.8:
                    unit.setdefault('warnings',[]).append('Near-full-page figure candidate withheld; figure segmentation requires review.'); continue
                existing=[r for r in unit['regions'] if r['kind']=='figure' and (pymupdf.Rect(r['bbox']) & box).get_area()>box.get_area()*.8]
                if existing: continue
                asset=(folder/element.content).absolute()
                region={'id':unit['id']+':figure-'+str(index),'kind':'figure','bbox':list(box),'text':'','asset':str(asset),
                    'metadata':{'method':'deep-doc-stage1-render-mask-spatial','mask_source':'ocr_and_table_grid' if unit.get('ocr_folder') else 'native_text_and_table_grid',
                                'requires_review':True,'source_sha256':ir.get('source_sha256')},'warnings':['Figure grouping is heuristic; review crop boundaries.']}
                unit['regions'].append(region)
                record={'region_id':region['id'],'page':unit['number'],'bbox':region['bbox'],'path':str(asset),'method':region['metadata']['method']}
                ir.setdefault('assets',[]).append(record); evidence.append(record)
    (folder/'figure-extraction.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    provenance.update(status='partial_failure' if any(p['status']=='failed' for p in provenance['pages']) else 'completed',assets=len(evidence))
    return ir
