"""Conservative grouping of aligned slide body columns from native PDF text."""
from collections import Counter

PANEL_SLOTS = {}


def panel_horizontal_bounds(block, all_blocks, drawings, page_width, padding=6):
    """Use a containing panel's padding only when no other content enters it."""
    left, top, right, bottom = block[:4]
    containers = [r for r in drawings
                  if r.x0 <= left and r.x1 >= right and r.y0 <= top and r.y1 >= bottom
                  and r.width < page_width * .5]
    if not containers:
        return left, right
    container = min(containers, key=lambda r: r.width * r.height)
    expanded_left = min(left, container.x0 + padding)
    expanded_right = max(right, container.x1 - padding)
    # Text or embedded images in the same vertical band block expansion.
    for other in all_blocks:
        if other is block:
            continue
        if min(bottom, other[3]) > max(top, other[1]) and min(expanded_right, other[2]) > max(expanded_left, other[0]):
            return left, right
    return expanded_left, expanded_right


def group_panels(page, source):
    if source.rect.width <= source.rect.height or source.rotation:
        return {}, []
    all_blocks = source.get_text('blocks')
    blocks=[b for b in all_blocks if b[6]==0
            and len(b[4].strip())>=80 and len(b[4].strip().splitlines())>=3]
    # Only a clearly aligned, disjoint set of columns is eligible.
    blocks.sort(key=lambda b:b[0])
    if len(blocks)<3 or max(b[1] for b in blocks)-min(b[1] for b in blocks)>3:
        return {}, []
    if any(a[2]+5>b[0] for a,b in zip(blocks,blocks[1:])):
        return {}, []
    drawings=[d['rect'] for d in source.get_drawings() if d.get('fill') is not None]
    overrides,records={},[]
    for b in blocks:
        left,ytop,right,ybottom,text=b[:5]
        top,bottom=source.rect.height-ytop,source.rect.height-ybottom
        candidates=[p for p in page.pdf_paragraph if p.box and not p.vertical
                    and left-2 <= (p.box.x+p.box.x2)/2 <= right+2
                    and bottom-2 <= (p.box.y+p.box.y2)/2 <= top+2]
        if not candidates:
            continue
        normal=lambda s:Counter(c for c in s if c.isalnum())
        if normal(text)!=normal(''.join(p.unicode or '' for p in candidates)):
            continue
        if any(c.pdf_formula for p in candidates for c in p.pdf_paragraph_composition):
            continue
        anchor=min(candidates,key=lambda p:(-p.box.y2,p.box.x))
        if anchor.pdf_style is None:
            continue
        # Stop above any filled decoration entering the body from below.
        original_left, original_right = left, right
        left, right = panel_horizontal_bounds(b, all_blocks, drawings, source.rect.width)
        cutoff=ybottom
        for rect in drawings:
            if rect.y0>ytop+10 and min(rect.x1,right)>max(rect.x0,left):
                cutoff=min(cutoff,rect.y0-3)
        if cutoff-ytop<25:
            continue
        bounds=[left,source.rect.height-cutoff,right,top]
        anchor.unicode=' '.join(text.split())
        anchor.box.x,anchor.box.y,anchor.box.x2,anchor.box.y2=bounds
        anchor.first_line_indent=False
        removed={id(p) for p in candidates if p is not anchor}
        page.pdf_paragraph[:]=[p for p in page.pdf_paragraph if id(p) not in removed]
        overrides[id(anchor)]=anchor.unicode
        PANEL_SLOTS[(page.page_number+1,anchor.debug_id)]={'bbox':bounds}
        records.append({'page':page.page_number+1,'paragraph_id':anchor.debug_id,
                        'merged_blocks':len(candidates),'status':'grouped','bbox':bounds,
                        'reused_panel_padding':(left,right)!=(original_left,original_right)})
    return overrides,records
