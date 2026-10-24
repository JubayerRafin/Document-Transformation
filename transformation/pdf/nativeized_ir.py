"""Refresh nativeized glyph geometry without rerunning source extraction models."""
from copy import deepcopy
from extraction.coordinator import extract_pdf, content_ir


def refresh(path, folder, source_ir, analysis=None):
    # The source IR is authoritative for untouched digital pages. OCR pages
    # changed during raster cleanup and need fresh native glyph boxes, not a
    # second semantic table/figure prediction over our own generated text layer.
    result=content_ir(extract_pdf(path,folder,'native',analysis,defer_figures=True))
    original={u['number']:u for u in source_ir['units']}
    if len(original)!=len(source_ir['units']) or set(original)!={u['number'] for u in result['units']}:
        raise ValueError('Nativeized IR page inventory differs from source extraction.')
    for index,unit in enumerate(result['units']):
        source=original[unit['number']]
        if any(abs(unit[key]-source[key])>.01 for key in ('width','height')):
            raise ValueError('Nativeized page geometry differs from source extraction.')
        if not source.get('ocr_folder'):
            result['units'][index]=deepcopy(source)
    result['settings']['nativeization_refresh']={
        'method':'native_glyph_geometry_with_reused_source_units',
        'source_backend':source_ir.get('backend'),
        'semantic_models_rerun':False,
        'limitations':'OCR uncertainty and source raster table evidence must be retained by the caller; new outputs require review.'}
    return result
