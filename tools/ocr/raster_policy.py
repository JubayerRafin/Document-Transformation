"""Bounded, source-density-aware OCR rasterization in PDF point coordinates."""
import math
import pymupdf

VERSION='source-density-v1'
MAX_SIDE=3072
MAX_PIXELS=8_000_000


def render_page(page,clip=None):
    box=pymupdf.Rect(clip) if clip is not None else pymupdf.Rect(page.rect)
    if not page.rect.contains(box) or box.is_empty or box.is_infinite:
        raise ValueError('OCR raster bounds must be a finite region inside the page.')
    densities=[]
    for image in page.get_image_info():
        bounds=pymupdf.Rect(image['bbox'])
        if bounds.is_empty:continue
        if (bounds&box).get_area()/box.get_area()>=.5:
            densities.append(max(image['width']/bounds.width,image['height']/bounds.height))
    # Preserve a dominant raster's native density. Vector/composite regions
    # require a render scale; blindly enlarging scan pixels adds no information.
    requested=max(1.0,max(densities) if densities else 3.0)
    scale=min(requested,(MAX_SIDE-2)/max(box.width,box.height),
              math.sqrt((MAX_PIXELS-4*MAX_SIDE)/(box.width*box.height)))
    if not math.isfinite(scale) or scale<=0:raise ValueError('Invalid OCR raster scale.')
    pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=box,
                        colorspace=pymupdf.csRGB,alpha=False)
    if max(pix.width,pix.height)>MAX_SIDE or pix.width*pix.height>MAX_PIXELS:
        raise ValueError('OCR raster exceeds its bounded pixel budget.')
    metadata={'policy':VERSION,'scale':scale,'requested_scale':requested,
        'limited':scale<requested-1e-6,'image_pixels':[pix.width,pix.height],
        'point_bounds':list(box),'pixel_bounds':[pix.x,pix.y,pix.x+pix.width,pix.y+pix.height],
        'max_side':MAX_SIDE,'max_pixels':MAX_PIXELS}
    return pix,metadata


def render_verified_page(page,metadata):
    if metadata is None:
        return page.get_pixmap(matrix=pymupdf.Matrix(1,1),colorspace=pymupdf.csRGB,alpha=False)
    if metadata.get('policy')!=VERSION:raise ValueError('Unknown OCR raster evidence policy.')
    pix,actual=render_page(page)
    if actual!=metadata:raise ValueError('OCR raster policy or geometry does not match source evidence.')
    return pix


def preserve_background(page,analysis_pixels,erase_mask):
    """Apply committed point-grid erasures, preserving other native pixels."""
    import numpy as np
    pix,metadata=render_page(page)
    output=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3).copy()
    sx=pix.width/page.rect.width;sy=pix.height/page.rect.height
    xs=np.minimum(((np.arange(pix.width)+.5)/sx).astype(int),analysis_pixels.shape[1]-1)
    ys=np.minimum(((np.arange(pix.height)+.5)/sy).astype(int),analysis_pixels.shape[0]-1)
    mask=erase_mask[ys[:,None],xs[None,:]]
    colors=analysis_pixels[ys[:,None],xs[None,:]]
    output[mask]=colors[mask]
    return pymupdf.Pixmap(pymupdf.csRGB,pix.width,pix.height,output.tobytes(),False),metadata
