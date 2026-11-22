"""Source-relative font limits for verified, detailed raster text."""
import math
import pymupdf


def source_fit_policy(region,page):
    box=pymupdf.Rect(region['bbox'])
    fallback={'minimum':8.0,'maximum':max(8.0,box.height*.95),'basis':'default_readability_floor'}
    if region.get('metadata',{}).get('requires_review') or box.is_empty:return fallback
    density=1.0
    for image in page.get_image_info():
        rect=pymupdf.Rect(image['bbox'])
        if not rect.is_empty and (rect&box).get_area()/box.get_area()>=.95:
            density=max(density,min(image['width']/rect.width,image['height']/rect.height))
    # Small PDF coordinates alone are not evidence of legible source glyphs.
    # Keep the existing floor for low-resolution scans and sparse evidence.
    if density<1.5 or box.height*density<12:return fallback
    import numpy as np
    import cv2
    scale=min(4.0,density)
    clip=box&page.rect
    if clip.is_empty or clip.get_area()*scale*scale>2_000_000:return fallback
    pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=clip,colorspace=pymupdf.csRGB,alpha=False)
    pixels=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
    edge=np.concatenate((pixels[0],pixels[-1],pixels[:,0],pixels[:,-1]))
    color=np.median(edge,axis=0)
    if np.mean(np.max(abs(edge.astype(float)-color),axis=1)<35)<.75:return fallback
    ink=(np.max(abs(pixels.astype(float)-color),axis=2)>35).astype(np.uint8)
    _,_,stats,_=cv2.connectedComponentsWithStats(ink)
    heights=[h for x,y,w,h,area in stats[1:] if area>=3 and h>=3 and w<pix.width*.6 and h<pix.height*.95]
    if len(heights)<2:return fallback
    # Detector boxes contain padding. Actual glyph components, not box height,
    # supply the font estimate; thin rules and large artwork are excluded.
    measured_ink_height=float(np.median(heights))/scale
    text=region.get('text','')
    # Latin letters are measured on a Latin font even with a non-ASCII symbol in the line ("Japan ·
    # March-April" was measured on CJK letter shapes and estimated at 5.9 pt instead of ~13).
    latin=all(ord(c)<0x250 for c in text if c.isalpha())
    font=pymupdf.Font('helv') if latin else pymupdf.Font('cjk')
    glyphs=[font.glyph_bbox(ord(c)) for c in text if c.isalnum() and font.has_glyph(ord(c))]
    if not glyphs:return fallback
    ink_height=float(np.median([g.height for g in glyphs]))
    hangul=[c for c in text if '가'<=c<='힣']
    if hangul:
        # A Hangul syllable is drawn as separate parts ("매" is ㅁ and ㅐ), so the median ink blob is a
        # fragment and estimated Korean lines at under half their size (16 pt bullets as 6.6 pt,
        # 2026-11-19). Korean lines are measured by the height of the line's ink against a full
        # syllable's height instead.
        rows=np.nonzero(ink.sum(axis=1)>max(1,ink.shape[1]*.005))[0]
        boxes=[font.glyph_bbox(ord(c)) for c in hangul if font.has_glyph(ord(c))]
        if len(rows)<3 or not boxes:return fallback
        measured_ink_height=float(rows[-1]-rows[0]+1)/scale
        ink_height=float(max(g.height for g in boxes))
    if ink_height<=0:return fallback
    estimate=measured_ink_height/ink_height
    if not math.isfinite(estimate) or estimate<4:return fallback
    minimum=max(4.0,min(8.0,estimate*.85))
    return {'minimum':minimum,'maximum':max(minimum,estimate),
            'source_font_estimate':estimate,'source_pixels_per_point':density,
            'measured_source_ink_height':measured_ink_height,
            'basis':'source_relative_ink_height_estimate','requires_visual_review':True}


def neighbour_policy(region,policy,measured):
    """A line whose own size could not be measured takes the size measured for lines of about its box
    height in the same picture, instead of the 8 pt readability floor.

    In a screenshot of an HP manual most labels measured 4-6 pt, but "Supplies", "Plain" and "Media"
    fell back to the floor and were drawn at 8 pt, twice their neighbours (2026-11-23). measured:
    (region, policy) for every line of the picture. A line with no comparable neighbour keeps its
    policy."""
    if policy.get('basis')!='default_readability_floor' or region.get('metadata',{}).get('requires_review'):
        return policy
    box=pymupdf.Rect(region['bbox'])
    if box.is_empty:return policy
    estimates=[p['source_font_estimate'] for r,p in measured
               if r is not region and p.get('source_font_estimate')
               and 0.75<=pymupdf.Rect(r['bbox']).height/box.height<=1.33]
    if not estimates:return policy
    from statistics import median
    estimate=median(estimates)
    minimum=max(4.0,min(8.0,estimate*.85))
    return {'minimum':minimum,'maximum':max(minimum,estimate),'source_font_estimate':estimate,
            'basis':'neighbouring_lines_estimate','neighbours':len(estimates),'requires_visual_review':True}
