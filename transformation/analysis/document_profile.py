"""Read-only page inventory; routing hints are not extraction-quality scores."""
import pymupdf


def profile_pdf(data):
    pages=[]
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        for i,p in enumerate(pdf):
            text=p.get_text().strip()
            images=p.get_image_info()
            if text:
                kind='mixed' if images else 'digital'
            else:
                kind='image_only' if images else 'vector_or_empty'
            pages.append(dict(page=i+1,kind=kind,native_characters=len(text),image_count=len(images),
                              rotation=p.rotation, raster_pixels_72dpi=int(p.rect.width+0.999)*int(p.rect.height+0.999),
                              requires_region_review=kind!='digital'))
    return {'schema_version':'1','pages':pages,
            'limitations':'Native text presence does not prove reliable extraction. Mixed and vector-only OCR routing is not yet implemented.'}


def plan_route(profile, ocr_selected=False):
    """Validate existing pipeline capabilities without claiming OCR coverage."""
    pages = profile['pages']
    errors, warnings = [], []
    if ocr_selected:
        if len(pages) != 1 or any(p['kind'] != 'image_only' or p['rotation'] for p in pages):
            errors.append('OCR currently requires one unrotated image-only PDF page. Mixed, digital and multi-page OCR are not implemented.')
        if any(p['raster_pixels_72dpi'] > 20_000_000 for p in pages):
            errors.append('OCR page exceeds the 20-million-pixel prototype limit at 72 DPI.')
        route = 'scan_regions'
    else:
        route = 'native_pdf'
        unsupported = [str(p['page']) for p in pages if p['kind'] in {'image_only', 'vector_or_empty'}]
        if unsupported:
            errors.append('Pages ' + ', '.join(unsupported) + ' have no extractable native text. Select OCR for a supported single scanned page; vector-only and mixed multi-page routing still require implementation.')
        mixed = [str(p['page']) for p in pages if p['kind'] == 'mixed']
        if mixed:
            warnings.append('Pages ' + ', '.join(mixed) + ' contain native text and images. Only native text is translated; any text inside images remains unverified.')
    return {'route': route, 'supported': not errors, 'errors': errors, 'warnings': warnings,
            'quality_validated': False}
