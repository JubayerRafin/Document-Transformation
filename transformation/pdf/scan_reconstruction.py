"""Conservative raster reconstruction using existing translations, with review fallbacks.

Not PII redaction: uncertain regions retain the original source pixels.
"""
import json
import hashlib
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pymupdf


def background(crop):
    edge = np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]])
    color = np.median(edge, axis=0)
    agreement = float(np.mean(np.max(abs(edge.astype(float) - color), axis=1) < 45))
    return color.astype(np.uint8), agreement


def fit_line(box, text, font, max_size=None, min_size=8):
    """Fit one OCR line using actual font metrics, not textbox padding."""
    if not text.strip() or '\n' in text.strip():
        return None
    size = min(box.height / (font.ascender-font.descender),
               box.width / max(font.text_length(text, fontsize=1), .01)) * .95
    if max_size is not None:
        size = min(size,max_size)
    if size < min_size:
        return None
    return size, (box.x0, box.y0 + font.ascender*size)


def fit_text(box, text, font, max_size=None, min_size=8):
    """Bounded wrapping inside the proven clear region; never shorten content."""
    single = fit_line(box, text, font, max_size, min_size)
    if single:
        size, baseline = single
        return [(baseline, text.strip(), size)]
    # Keep words/identifiers intact. CJK characters permit character boundaries.
    from transformation.layout.line_breaks import atomic_tokens
    tokens = atomic_tokens(text.strip())
    ceiling=min(24,max_size if max_size is not None else 24)
    sizes=[step/2 for step in range(int(ceiling*2),math.ceil(min_size*2)-1,-1)]
    if ceiling>=min_size and (not sizes or abs(sizes[-1]-min_size)>1e-6):sizes.append(min_size)
    for size in sizes:
        line_height = (font.ascender-font.descender)*size
        lines, line = [], ''
        invalid = False
        for token in tokens:
            if token == '\n':
                lines.append(line.rstrip())
                line = ''
                continue
            if font.text_length((line+token).rstrip(), fontsize=size) <= box.width*.95:
                line += token
            else:
                if line.strip():
                    lines.append(line.rstrip())
                line = token.lstrip()
                if font.text_length(line,fontsize=size) > box.width*.95:
                    invalid = True
                    break
        if line.strip():
            lines.append(line.rstrip())
        if not invalid and lines and len(lines)*line_height <= box.height*.95:
            return [((box.x0,box.y0+font.ascender*size+i*line_height),t,size) for i,t in enumerate(lines)]
    return None


def available_text_box(region, regions, pixels, rules):
    """Grow into verified background to the right, bounded by artwork and cells."""
    box=available_line_box(region,regions,pixels,rules)
    original=pymupdf.Rect(region['bbox'])
    height,width=pixels.shape[:2]
    x0,y0,x1,y1=map(int,(original.x0,original.y0,original.x1,original.y1))
    color,_=background(pixels[max(0,y0):min(height,y1),max(0,x0):min(width,x1)])
    top,bottom=max(0,math.floor(box.y0)),min(height,math.ceil(box.y1))
    limit=min(width-2,math.floor(original.x1+original.height*8))
    edges=vertical_edges(region,pixels,limit)
    if edges:
        limit=min(limit,edges[0]-2)
        if limit<box.x1:
            box.x1=max(box.x0+1,limit)
    for right in range(math.ceil(box.x1)+1,limit+1):
        candidate=pymupdf.Rect(box.x0,box.y0,right,box.y1)
        if any(r['id']!=region['id'] and (candidate & pymupdf.Rect(r['bbox'])).get_area()>0 for r in regions):
            break
        strip=pixels[top:bottom,right-1:right]
        if not strip.size or rules[top:bottom,right-1:right].any():
            break
        if np.mean(np.max(abs(strip.astype(float)-color),axis=2)<25)<.99:
            break
        box.x1=right
    # Leave a small margin before a detected obstacle.
    box.x1=max(box.x0+1,min(box.x1-2,limit)) if edges else max(original.x1,box.x1-2)
    return box


def vertical_edges(region,pixels,limit=None):
    """Detect short cell borders near the OCR edge using a taller corridor."""
    box=pymupdf.Rect(region['bbox'])
    height,width=pixels.shape[:2]
    pad=max(3,math.ceil(box.height*.6))
    top,bottom=max(0,math.floor(box.y0)-pad),min(height,math.ceil(box.y1)+pad)
    first=max(2,math.floor(box.x1-box.height*.7))
    last=min(width-3,math.ceil(limit if limit is not None else box.x1+2))
    edges=[]
    for x in range(first,last+1):
        strip=pixels[top:bottom,x].astype(float)
        if not strip.size:
            continue
        median=np.median(strip,axis=0)
        uniform=np.mean(np.max(abs(strip-median),axis=1)<12)
        if uniform<.7:
            continue
        left=np.median(pixels[top:bottom,x-2].astype(float),axis=0)
        right=np.median(pixels[top:bottom,x+2].astype(float),axis=0)
        ridge=np.max(abs(median-left))>=14 and np.max(abs(median-right))>=14
        if ridge:
            edges.append(x)
    return edges


def validate_placements(box,placements,font):
    """Verify the glyph bounds produced by the PDF writer before clearing pixels."""
    with pymupdf.open() as scratch:
        page=scratch.new_page(width=max(100,box.x1+20),height=max(100,box.y1+20))
        page.insert_font(fontname='verify',fontbuffer=font.buffer)
        for baseline,text,size in placements:
            page.insert_text(baseline,text,fontname='verify',fontsize=size)
        spans=[span for block in page.get_text('dict')['blocks'] for line in block.get('lines',[]) for span in line['spans']]
        allowed=pymupdf.Rect(box.x0-.25,box.y0-.25,box.x1+.25,box.y1+.25)
        expected=Counter(c for _,text,_ in placements for c in text if not c.isspace())
        observed=Counter(c for span in spans for c in span['text'] if not c.isspace())
        return bool(spans) and expected==observed and all(allowed.contains(pymupdf.Rect(span['bbox'])) for span in spans)


def available_line_box(region, regions, pixels, rules):
    """Use a small clear vertical margin without crossing other text or rules."""
    box = pymupdf.Rect(region['bbox'])
    height, width = pixels.shape[:2]
    x0,x1 = max(0,math.floor(box.x0)), min(width,math.ceil(box.x1))
    for pad in (4,3,2,1):
        candidate = pymupdf.Rect(box.x0,max(0,box.y0-pad),box.x1,min(height,box.y1+pad))
        if any(r['id'] != region['id'] and (candidate & pymupdf.Rect(r['bbox'])).get_area() > 0 for r in regions):
            continue
        top, bottom = math.floor(candidate.y0), math.ceil(candidate.y1)
        strips = [pixels[top:math.floor(box.y0),x0:x1],pixels[math.ceil(box.y1):bottom,x0:x1]]
        masks = [rules[top:math.floor(box.y0),x0:x1],rules[math.ceil(box.y1):bottom,x0:x1]]
        if any(m.any() for m in masks):
            continue
        sample = pixels[math.floor(box.y0):math.ceil(box.y1),x0:x1]
        color,_ = background(sample)
        if all(s.size and np.mean(np.max(abs(s.astype(float)-color),axis=2)<30) > .98 for s in strips):
            return candidate
    return box


def rule_pixels(pixels):
    """Protect long thin dark rules, including a border crossing an OCR box."""
    gray = pixels.astype(float).mean(axis=2)
    result = np.zeros(gray.shape, dtype=bool)
    for vertical in (True, False):
        a = gray if vertical else gray.T
        candidates = np.zeros(a.shape, dtype=bool)
        candidates[:, 2:-2] = (a[:, 2:-2] + 25 < a[:, :-4]) & (a[:, 2:-2] + 25 < a[:, 4:])
        mask = np.zeros(a.shape, dtype=bool)
        for x in np.flatnonzero(candidates.sum(axis=0) >= 70):
            padded = np.r_[False, candidates[:, x], False].astype(int)
            starts = np.flatnonzero(np.diff(padded) == 1)
            ends = np.flatnonzero(np.diff(padded) == -1)
            segments = [(int(s), int(e)) for s,e in zip(starts,ends) if e-s >= 8]
            groups = []
            for start, end in segments:
                if groups and start-groups[-1][1] <= 40:
                    groups[-1][1] = end
                    groups[-1][2] = max(groups[-1][2], end-start)
                else:
                    groups.append([start, end, end-start])
            for start, end, longest in groups:
                if longest >= 70:
                    # Restore only detected rule pixels, not all pixels in the
                    # surrounding strip (which can include old letters).
                    mask[start:end, x] = candidates[start:end, x]
        result |= mask if vertical else mask.T
    return result


def validate_corrections(data, source_bytes, regions):
    """Reject corrections for a different source or changed OCR transcription."""
    if data.get('source_sha256') != hashlib.sha256(source_bytes).hexdigest():
        raise ValueError('Correction source hash mismatch.')
    known = {r['id']: r for r in regions}
    result = {}
    for item in data.get('corrections', []):
        key = item['id']
        if key in result or key not in known or item.get('expected_ocr') != known[key]['text']:
            raise ValueError('Correction region mismatch or duplicate: ' + key)
        if item.get('action') not in {'replace', 'preserve_source'}:
            raise ValueError('Unsupported correction action.')
        if item['action'] == 'replace' and not item.get('output_text', '').strip():
            raise ValueError('Correction replacement must not be empty.')
        result[key] = item
    return result


def reconstruct(run, translated_pdf, corrections_path=None, force_native=False):
    run = Path(run)
    ir = json.loads((run / 'ocr-document.json').read_text(encoding='utf-8'))
    manifest_path=run/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    entity_path=run/'entity-proposals.json'
    proposed=json.loads(entity_path.read_text(encoding='utf-8')).get('product_names',[]) if entity_path.exists() else []
    regions = ir['pages'][0]['regions']
    mapping_path = run / 'region-translations.json'
    mapping = json.loads(mapping_path.read_text(encoding='utf-8')) if mapping_path.exists() else None
    corrections = {}
    if corrections_path:
        corrections = validate_corrections(json.loads(Path(corrections_path).read_text(encoding='utf-8')),
                                           (run / 'source.pdf').read_bytes(), regions)
    with pymupdf.open(run / 'source.pdf') as source, pymupdf.open(translated_pdf) as translated:
        if len(source) != 1 or len(translated) != 1 or source[0].rect != translated[0].rect:
            raise ValueError('Scan reconstruction requires matching single-page geometry.')
        pix = source[0].get_pixmap(alpha=False)
        pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()
        original_pixels = pixels.copy()
        erase_mask=np.zeros(pixels.shape[:2],dtype=bool)
        rules = rule_pixels(pixels)
        grouped = [[] for _ in regions]
        unmatched = []
        line_number = 0
        for block in translated[0].get_text('dict')['blocks']:
            for line in block.get('lines', []):
                line_number += 1
                for span in line['spans']:
                    if not span['text']:
                        continue
                    rect = pymupdf.Rect(span['bbox'])
                    scores = [(rect & pymupdf.Rect(r['bbox'])).get_area() / max(rect.get_area(), .001) for r in regions]
                    best = max(range(len(scores)), key=scores.__getitem__)
                    if scores[best] < .45:
                        unmatched.append(span['text'])
                    else:
                        grouped[best].append(dict(span, source_line=line_number))
        font = pymupdf.Font('cjk')
        records, placements = [], []
        for region, spans in zip(regions, grouped):
            correction = corrections.get(region['id'])
            box = pymupdf.Rect(region['bbox'])
            x0, y0, x1, y1 = math.floor(box.x0), math.floor(box.y0), math.ceil(box.x1), math.ceil(box.y1)
            color, agreement = background(pixels[y0:y1, x0:x1])
            record = {'id': region['id'], 'source_text': region['text'], 'bbox': region['bbox'],
                      'background_agreement': round(agreement, 3), 'status': 'original_preserved'}
            reason = None
            if correction:
                record['explicit_correction'] = correction
            if correction and correction['action'] == 'preserve_source':
                record['reason'] = 'Explicit correction: retain exact source pixels instead of uncertain transcription.'
                records.append(record)
                continue
            if not correction and region.get('metadata', {}).get('requires_review', False):
                reason = 'Uncertain OCR or recognition variants disagree; preserve source, review transcription.'
            elif agreement < .75:
                reason = 'Nonuniform background; preserve source artwork.'
            elif not correction and mapping is None and (not spans or any(s['size'] < 6 for s in spans)):
                reason = 'Missing or undersized translation; preserve original text.'
            # A loose OCR box could contain non-text artwork even with a uniform perimeter.
            # These are candidate replacements, never automatically approved.
            line_text = {}
            for span in spans:
                line_text.setdefault(span['source_line'], []).append(span['text'])
            text = '\n'.join(''.join(parts).strip() for parts in line_text.values())
            if mapping is not None:
                entry = mapping.get(region['id'])
                if entry and entry['source_text'] == region['text']:
                    text = entry['text']
                else:
                    reason = reason or 'No exact region translation; preserve source.'
            if correction:
                text = correction['output_text']
            if text.strip() == region['text'].strip() and not reason and not force_native:
                record['reason'] = 'Translation unchanged; keep original typography and pixels.'
                from transformation.llm.policy import expected_unchanged
                if expected_unchanged(text,manifest.get('protected_values',[])+proposed):
                    record['expected_preservation']=True
                elif mapping is not None and re.fullmatch(r'[A-Z]{2,4}:\s*.+',region['text']):
                    value=region['text'].split(':',1)[1].strip()
                    audits=mapping.get(region['id'],{}).get('stats',{}).get('translation_audits',[])
                    if expected_unchanged(value,manifest.get('protected_values',[])+proposed) and audits and all(a.get('faithful') for a in audits):
                        record['expected_preservation']=True
                        record['preservation_basis']='Conventional abbreviation and exact literal; model reviewed.'
                records.append(record)
                continue
            fitting_box = available_text_box(region, regions, original_pixels, rules)
            for edge in vertical_edges(region,original_pixels):
                top=max(0,math.floor(box.y0)-3)
                bottom=min(pix.height,math.ceil(box.y1)+3)
                rules[top:bottom,edge]=True
            from transformation.layout.ocr_fitting import source_fit_policy
            font_policy=source_fit_policy(region,source[0])
            record['font_policy']=font_policy
            fitted = fit_text(fitting_box, text.strip(), font, max_size=font_policy['maximum'],min_size=font_policy['minimum'])
            if fitted and not validate_placements(fitting_box,fitted,font):
                reason=reason or 'Rendered glyphs exceed the safe region; preserve source.'
            if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):
                reason = reason or 'Output font lacks required characters; preserve original text.'
            if fitted is None:
                reason = reason or f"Translation cannot fit inside the available region at minimum {font_policy['minimum']:.2f} pt; preserve original text."
            else:
                best_size = fitted[0][2]
            if reason:
                record['reason'] = reason
            else:
                # Clear antialiased glyph edges just outside the detector box only
                # when the margin is uniform and cannot touch another OCR region.
                clear = pymupdf.Rect(max(0,x0-3), max(0,y0-3), min(pix.width,x1+3), min(pix.height,y1+3))
                neighbors = any(other['id'] != region['id'] and (clear & pymupdf.Rect(other['bbox'])).get_area() > 0 for other in regions)
                cx0,cy0,cx1,cy1 = map(int,clear)
                expanded_color, expanded_agreement = background(original_pixels[cy0:cy1,cx0:cx1])
                if not neighbors and expanded_agreement >= .9 and np.max(abs(expanded_color.astype(float)-color)) < 15:
                    pixels[cy0:cy1,cx0:cx1] = expanded_color
                    erase_mask[cy0:cy1,cx0:cx1]=True
                else:
                    pixels[y0:y1, x0:x1] = color
                    erase_mask[y0:y1,x0:x1]=True
                record.update(status='replaced_requires_review', translated_text=text, font_size=best_size, fitting_bbox=list(fitting_box))
                ink = (1, 1, 1) if float(np.mean(color)) < 125 else (0, 0, 0)
                placements.extend((baseline, line, size, ink) for baseline,line,size in fitted)
            records.append(record)
        pixels[rules] = original_pixels[rules]
        erase_mask[rules]=False
        with pymupdf.open() as result:
            p = result.new_page(width=source[0].rect.width, height=source[0].rect.height)
            from tools.ocr.raster_policy import preserve_background
            cleaned,raster = preserve_background(source[0],pixels,erase_mask)
            p.insert_image(p.rect, pixmap=cleaned)
            p.insert_font(fontname='scanfont', fontbuffer=font.buffer)
            for baseline, text, size, ink in placements:
                p.insert_text(baseline, text, fontname='scanfont', fontsize=size, color=ink)
            output = run / 'output' / ('scan-corrected.pdf' if corrections_path else 'scan-reconstructed.pdf')
            result.save(output, garbage=4, deflate=True)
        report = {'review_status': 'requires_human_review', 'model_requests': 0,'raster':raster,
                  'acceptance_blockers': [dict(id=r['id'], reason=r['reason']) for r in records
                                          if r.get('reason') and not r.get('expected_preservation')],
                  'correction_source': str(corrections_path) if corrections_path else None,
                  'assisted_corrections': len(corrections),
                  'replaced': sum(r['status']=='replaced_requires_review' for r in records),
                  'original_preserved': sum(r['status']!='replaced_requires_review' for r in records),
                  'unmatched_translation_spans': unmatched, 'regions': records,
                  'limitations': 'Uniform-background heuristic only. Original text remains in rejected or undetected regions. OCR/translation accuracy and artwork preservation require visual review. Not PII redaction.'}
        (run / ('scan-corrected-review.json' if corrections_path else 'scan-reconstruction.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return output, report
