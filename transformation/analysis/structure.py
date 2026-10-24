"""Conservative spatial relationships; no guessed or rewritten source text."""


def structure_regions(regions):
    usable = [r for r in regions if r.get('bbox') and not r.get('metadata',{}).get('requires_review')]
    rows = []
    for region in sorted(usable,key=lambda r:(r['bbox'][1],r['bbox'][0])):
        box=region['bbox']
        height=box[3]-box[1]
        if height <= 0:
            continue
        center=(box[1]+box[3])/2
        candidates=[row for row in rows if abs(center-row['center'])<=min(height,row['height'])*.4]
        if candidates:
            row=min(candidates,key=lambda row:abs(center-row['center']))
            row['regions'].append(region)
        else:
            rows.append(dict(center=center,height=height,regions=[region]))
    relationships=[]
    for row in rows:
        ordered=sorted(row['regions'],key=lambda r:r['bbox'][0])
        if len(ordered)>1:
            relationships.append({'type':'same_row_candidate','region_ids':[r['id'] for r in ordered]})
    for a in usable:
        x,y,right,bottom=a['bbox']
        height=bottom-y
        following=[]
        for b in usable:
            if a['id']==b['id']:
                continue
            bx,by,br,bb=b['bbox']
            # Reject side-by-side cells and only propose the closest aligned line.
            if 0 <= by-bottom <= height*.8 and abs(bx-x)<=height*.35 and abs((bb-by)-height)<=height*.3:
                following.append(b)
        if following:
            b=min(following,key=lambda r:r['bbox'][1])
            relationships.append({'type':'paragraph_continuation_candidate','region_ids':[a['id'],b['id']]})
    return {'schema_version':'1','relationships':relationships,
            'limitations':'Spatial candidates only. Borders, merged cells and semantic reading order are not inferred. Translation retains region identity.'}
