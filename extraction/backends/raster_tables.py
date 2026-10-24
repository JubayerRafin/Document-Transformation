"""Ruled-table geometry from source pixels, with OCR evidence assigned to cells.

No document vocabulary, fixed coordinates or model guesses define a table.
Broken/merged grids need a structural model; this detector only accepts a full
grid supported by long horizontal and vertical separators.
"""
import numpy as np
import pymupdf


def grid_boxes(pixels):
    import cv2
    gray=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY)
    binary=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY_INV,15,5)
    height,width=gray.shape
    horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(20,width//30)),np.uint8))
    vertical=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((max(20,height//30),1),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(cv2.bitwise_or(horizontal,vertical))
    def positions(values):
        return [float(np.mean(group)) for group in np.split(values,np.where(np.diff(values)>2)[0]+1) if len(group)]
    result=[]
    for x,y,w,h,area in stats[1:]:
        if w<width*.15 or h<height*.04: continue
        xs=positions(np.flatnonzero((vertical[y:y+h,x:x+w]>0).mean(axis=0)>.5))
        ys=positions(np.flatnonzero((horizontal[y:y+h,x:x+w]>0).mean(axis=1)>.5))
        if len(xs)<3 or len(ys)<3: continue
        if min(np.diff(xs))<8 or min(np.diff(ys))<8: continue
        # Reject fragmented dividers rather than asserting invented merged cells.
        if any(np.mean(vertical[y+int(ys[0]):y+int(ys[-1]),x+round(v)]>0)<.8 for v in xs): continue
        result.append(([x+v for v in xs],[y+v for v in ys]))
    return result


def enrich(data,ir):
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        for unit in ir['units']:
            if not unit.get('ocr_folder'): continue
            page=pdf[unit['number']-1]; pix=page.get_pixmap(alpha=False)
            pixels=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
            for index,(xs,ys) in enumerate(grid_boxes(pixels)):
                xs=[x*page.rect.width/pix.width for x in xs]; ys=[y*page.rect.height/pix.height for y in ys]
                table_id=unit['id']+':raster-table-'+str(index)
                matrix=[['']*(len(xs)-1) for _ in range(len(ys)-1)]
                records=[]; members=[]
                for row in range(len(ys)-1):
                    for column in range(len(xs)-1):
                        box=[xs[column],ys[row],xs[column+1],ys[row+1]]
                        contained=[]
                        for region in unit['regions']:
                            if not region.get('text') or not region.get('bbox'): continue
                            rb=pymupdf.Rect(region['bbox']); center=(rb.x0+rb.x1)/2,(rb.y0+rb.y1)/2
                            if pymupdf.Rect(box).contains(center): contained.append(region)
                        contained.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
                        matrix[row][column]='\n'.join(r['text'] for r in contained)
                        records.append({'row':row,'column':column,'bbox':box,'text':matrix[row][column],
                                        'region_ids':[r['id'] for r in contained]})
                        members.extend(contained)
                if sum(bool(v) for row in matrix for v in row)<4: continue
                by_id={r['id']:r for r in members}
                for record in records:
                    for region_id in record['region_ids']:
                        by_id[region_id].setdefault('metadata',{}).update(table_parent=table_id,
                            row=record['row'],column=record['column'],cell_bbox=record['bbox'])
                for region in members:
                    meta=region['metadata']; row=meta['row']; column=meta['column']
                    meta['row_context']={'column_header':matrix[0][column],'row_values':matrix[row],
                                         'column_values':[r[column] for r in matrix[1:]][:8]}
                unit['regions'].append({'id':table_id,'kind':'table','bbox':[xs[0],ys[0],xs[-1],ys[-1]],'text':'',
                    'cells':matrix,'metadata':{'method':'source_raster_ruled_grid','cell_records':records,
                    'in_content_regions':True,'merge_structure':'unmerged_grid_verified'},'warnings':[]})
    return ir
