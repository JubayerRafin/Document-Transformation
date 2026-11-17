"""Spatial, page-aware mapping from content IR to BabelDOC paragraphs.

External geometry is evidence for association, never a replacement for native
glyph geometry. Ambiguous matches are recorded rather than assigned arbitrarily.
"""
import re
from extraction.coordinator import text_regions


def normalized(text):
    return re.sub(r'\s+','',text or '').casefold()


def lock_ocr_regions(document,ir,evidence=None):
    """Keep verified OCR fields separate when BabelDOC merges neighboring lines."""
    from copy import deepcopy
    from collections import Counter
    from babeldoc.format.pdf.document_il.il_version_1 import Box,PdfParagraphComposition,PdfSameStyleCharacters
    overrides={}; slots={}; report=[] if evidence is None else evidence
    for page in document.page:
        units=[u for u in ir['units'] if u.get('number')==page.page_number+1]
        if len(units)!=1 or not units[0].get('locked_regions'): continue
        unit=units[0]; height=unit['height']; originals=list(page.pdf_paragraph)
        characters=[]
        for paragraph in originals:
            for composition in paragraph.pdf_paragraph_composition:
                direct=composition.pdf_character
                if direct: characters.append((direct,paragraph))
                for name in ('pdf_line','pdf_formula','pdf_same_style_characters'):
                    group=getattr(composition,name,None)
                    if group is not None: characters.extend((c,paragraph) for c in group.pdf_character)
        assigned=set(); locked=[]
        for index,region in enumerate(unit['locked_regions']):
            bounds=region['slot_bbox']; left,top,right,bottom=bounds
            matches=[(c,p) for c,p in characters if id(c) not in assigned and c.box and
                left-.5<=(c.box.x+c.box.x2)/2<=right+.5 and top-.5<=height-(c.box.y+c.box.y2)/2<=bottom+.5]
            expected=Counter(c for c in region['text'] if not c.isspace())
            observed=Counter(letter for c,_ in matches for letter in (c.char_unicode or '') if not letter.isspace())
            if expected!=observed or not matches:
                report.append({'region_id':region['id'],'status':'blocked','reason':'Native glyph inventory does not match OCR source.'}); continue
            paragraph=deepcopy(matches[0][1]); paragraph.debug_id='tx_ocr_'+str(index)
            paragraph.box=Box(x=left,y=height-bottom,x2=right,y2=height-top)
            paragraph.unicode=region['text']; paragraph.vertical=False
            paragraph.pdf_style=deepcopy(matches[0][0].pdf_style)
            paragraph.pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_characters=
                PdfSameStyleCharacters(box=deepcopy(paragraph.box),pdf_style=deepcopy(paragraph.pdf_style),pdf_character=[c for c,_ in matches]))]
            paragraph.layout_label='text'
            overrides[id(paragraph)]=region['text']; locked.append(paragraph)
            assigned.update(id(c) for c,_ in matches)
            cb=region.get('metadata',{}).get('cell_bbox')
            if cb:
                # Grow only inside the proven cell, away from other source lines.
                right=min(cb[2]-6,max(right,region['bbox'][2])); left=max(left,cb[0]+6)
            minimum=region.get('font_policy',{}).get('minimum',8.0)
            if not 4<=minimum<=8:raise ValueError('Invalid source-relative OCR font floor.')
            slots[(page.page_number+1,paragraph.debug_id)]={'bbox':[left,height-bottom,right,height-top],'min_font_size':minimum,
                'source_locked_ocr':True}
            report.append({'region_id':region['id'],'paragraph_id':paragraph.debug_id,'status':'locked','source_text':region['text']})
        # Any native source glyph not safely associated remains in its original
        # paragraph. Mixed partially assigned paragraphs must not duplicate glyphs.
        if any(r['status']=='blocked' for r in report):
            raise ValueError('OCR paragraph association needs review; see locked-region evidence.')
        for paragraph in originals:
            chars=[]
            for c,p in characters:
                if p is paragraph: chars.append(c)
            if chars and any(id(c) not in assigned for c in chars):
                raise ValueError('Unassociated native glyphs remain after OCR field separation.')
        page.pdf_paragraph[:]=sorted(locked,key=lambda p:(-p.box.y2,p.box.x))
    return overrides,slots,report


def map_document(document,ir):
    report=[]
    for page in document.page:
        number=page.page_number+1
        units=[u for u in ir['units'] if u.get('number')==number]
        if len(units)!=1: continue
        unit=units[0]; height=unit['height']
        regions=unit.get('locked_regions') or text_regions({'units':[unit]})
        for paragraph in page.pdf_paragraph:
            box=paragraph.box
            if box is None: continue
            bbox=[box.x,height-box.y2,box.x2,height-box.y]
            source=getattr(paragraph,'unicode','') or ''
            candidates=[]
            for region in regions:
                rb=region.get('bbox')
                if not rb: continue
                overlap=max(0,min(bbox[2],rb[2])-max(bbox[0],rb[0]))*max(0,min(bbox[3],rb[3])-max(bbox[1],rb[1]))
                area=max(.001,(bbox[2]-bbox[0])*(bbox[3]-bbox[1]))
                exact=normalized(source)==normalized(region['text'])
                if overlap/area>=.5 or (exact and overlap>0):
                    candidates.append((exact,overlap/area,region))
            candidates.sort(key=lambda c:(c[0],c[1]),reverse=True)
            unique=bool(candidates) and (len(candidates)==1 or candidates[0][0]>candidates[1][0] or candidates[0][1]-candidates[1][1]>.2)
            row={'page':number,'paragraph_id':paragraph.debug_id,'bbox':bbox,'source_text':source,
                 'status':'mapped' if unique else 'ambiguous' if candidates else 'unmatched',
                 'candidate_region_ids':[c[2]['id'] for c in candidates]}
            if unique:
                region=candidates[0][2]; row['region_id']=region['id']; row['external_bbox']=region['bbox']
                # BabelDOC's IL dataclasses use slots. Associations live in the
                # sidecar rather than adding fields to vendor objects.
            report.append(row)
    return report


def collect_translations(ir,engine_runs):
    """Return stable external IDs with translations from associated native IL."""
    from pathlib import Path
    import json
    result=[]
    for folder in engine_runs:
        folder=Path(folder)
        mapping_file=folder/'ir-mapping.json'; transformed_file=folder/'babeldoc-transformed-ir.json'
        if not mapping_file.exists() or not transformed_file.exists(): continue
        mapping=json.loads(mapping_file.read_text(encoding='utf-8'))
        transformed=json.loads(transformed_file.read_text(encoding='utf-8'))
        paragraphs={(p['page_number']+1,r['debug_id']):r for p in transformed['page'] for r in p['pdf_paragraph']}
        for row in mapping:
            if row['status']!='mapped': continue
            paragraph=paragraphs.get((row['page'],row['paragraph_id']))
            if paragraph:
                result.append({'region_id':row['region_id'],'native_paragraph_id':row['paragraph_id'],
                               'source_text':row['source_text'],'translated_text':paragraph.get('unicode',''),
                               'engine_run':str(folder),'association':'spatial_and_text_checked'})
    return result
