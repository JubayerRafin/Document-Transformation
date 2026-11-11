"""Source-backed value spans and PDF geometry; no proportional character guesses."""
from collections import Counter
import re
import pymupdf


class RedactionLocalizationError(ValueError):
    def __init__(self, regions):
        self.regions = regions
        super().__init__('Selected PII cannot be located separately from surrounding text; precise redaction review is required.')


class RedactionVerificationError(ValueError):
    """Exported-PDF check failed; the output is not released. code is a hosted reason code."""
    def __init__(self, message, code):
        self.code = code
        super().__init__(message)


def compact(text):
    return ''.join(c for c in text if not c.isspace() and c != '\u00ad')


def literal_spans(text, value):
    """Match source literals across layout whitespace, retaining source offsets.

    Meaningful punctuation and letter case remain exact. Do not propagate a
    short identifier into a longer word or token.
    """
    offsets = [i for i,c in enumerate(text) if not c.isspace() and c != '\u00ad']
    haystack = ''.join(text[i] for i in offsets); needle = compact(value)
    if not needle: return []
    result=[]; cursor=0
    while (start := haystack.find(needle,cursor)) >= 0:
        end=start+len(needle); cursor=end
        a,b=offsets[start],offsets[end-1]+1
        token_chars = r'[\w@.+-]' if '@' in needle else r'\w'
        if needle[0].isalnum() and a and re.match(token_chars,text[a-1]): continue
        if needle[-1].isalnum() and b<len(text) and re.match(token_chars,text[b]): continue
        result.append((a,b))
    return result


def native_blocks(page):
    blocks=[]
    flags=pymupdf.TEXTFLAGS_RAWDICT | pymupdf.TEXT_ACCURATE_BBOXES
    for bi,block in enumerate(page.get_text('rawdict',flags=flags)['blocks']):
        if block['type'] != 0: continue
        chars=[]; text=[]
        for li,line in enumerate(block['lines']):
            if text: text.append('\n'); chars.append(None)
            for span in line['spans']:
                for char in span['chars']:
                    # A PDF character can represent more than one Unicode scalar.
                    for c in char['c']:
                        text.append(c); chars.append(dict(char,c=c,line=li))
        blocks.append({'id':bi,'text':''.join(text),'chars':chars,'bbox':block['bbox']})
    return blocks


def native_pattern_regions(data):
    """Supplement detection with original PDF blocks, without translating duplicates."""
    regions=[]
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        for number,page in enumerate(pdf,1):
            for block in native_blocks(page):
                regions.append({'id':f'pii-native-{number}-{block["id"]}',
                    'text':block['text'],'bbox':list(pymupdf.Rect(block['bbox'])*page.rotation_matrix),
                    'metadata':{'pii_native_page':number,'pii_detection_only':True}})
    return regions


def _glyph_key(char):
    return char['c'],tuple(round(v,2) for v in char['origin'])


def _missing_glyphs(expected, remaining):
    # MuPDF serialization can round an unchanged text origin by 0.01 pt.
    # Match that numerical drift, never a different nearby character.
    shared=expected & remaining; missing=expected-shared; available=remaining-shared
    for key,count in list(missing.items()):
        char,origin=key
        for other in list(available):
            if other[0]!=char or max(abs(a-b) for a,b in zip(origin,other[1]))>.05:continue
            used=min(count,available[other]);count-=used;available[other]-=used
            if not count:break
        missing[key]=count
    return +missing


def verified_ocr_words(line, text, bbox):
    """Map existing independently recognized, axis-aligned crop words to page units."""
    candidates=line.get('candidates',[])
    first=next((c.get('words',[]) for c in candidates if c.get('variant')=='word_gap_primary'),[])
    second=next((c.get('words',[]) for c in candidates if c.get('variant')=='word_gap_english'),[])
    polygon=line.get('polygon_pixels',[])
    if not first or len(first)!=len(second) or len(polygon)!=4:return []
    import math
    if not all(math.isfinite(float(n)) for point in polygon for n in point):return []
    if any(abs(polygon[a][axis]-polygon[b][axis])>1.5 for a,b,axis in [(0,1,1),(2,3,1),(0,3,0),(1,2,0)]):return []
    width=max(p[0] for p in polygon)-min(p[0] for p in polygon)
    if width<=0 or abs(first[-1]['end']-width)>2:return []
    cursor=0
    for a,b in zip(first,second):
        if (a['text'].strip()!=b['text'].strip() or not a['text'].strip()
                or not all(math.isfinite(float(w['score'])) and .90<=w['score']<=1 for w in (a,b))
                or a['start']!=cursor or a['end']<=a['start']
                or (a['start'],a['end'])!=(b['start'],b['end'])):return []
        cursor=a['end']
    joined=' '.join(w['text'].strip() for w in first)
    if joined!=line['text'].strip() or joined.count(text)!=1:return []
    base=joined.index(text);offset=0;result=[];parent=pymupdf.Rect(line['bbox'])
    for word in first:
        token=word['text'].strip();start=offset;end=start+len(token);offset=end+1
        if start<base or end>base+len(text):continue
        box=pymupdf.Rect(parent.x0+parent.width*word['start']/width,parent.y0,
                        parent.x0+parent.width*word['end']/width,parent.y1)
        if not pymupdf.Rect(bbox).contains(box):return []
        result.append({'text':token,'start':start-base,'end':end-base,'bbox':list(box),
                       'evidence':'two_recognizers_at_detected_word_boundaries'})
    return result


def _ocr_rectangles(region, values):
    """Use complete-value boxes or verified word boxes; never erase a whole field."""
    text=region['text']; matches=[span for value in values for span in literal_spans(text,value)]
    if not matches:return []
    words=region.get('metadata',{}).get('redaction_words',[])
    if not words:
        if any(compact(text)==compact(value) for value in values):
            return [pymupdf.Rect(region['bbox'])]
        raise RedactionLocalizationError([{'region_id':region['id'],'reason':'missing_verified_word_geometry'}])
    result=[]
    for a,b in matches:
        selected=[w for w in words if a<w['end'] and b>w['start']]
        if (not selected or any(w['text']!=text[w['start']:w['end']] or
                w['start']<a or w['end']>b or w.get('evidence')!='two_recognizers_at_detected_word_boundaries'
                for w in selected)):
            raise RedactionLocalizationError([{'region_id':region['id'],'reason':'value_crosses_unverified_word_boundary'}])
        if compact(''.join(w['text'] for w in selected))!=compact(text[a:b]):
            raise RedactionLocalizationError([{'region_id':region['id'],'reason':'incomplete_word_coverage'}])
        for word in selected:
            box=pymupdf.Rect(word['bbox'])
            if box.is_empty or not pymupdf.Rect(region['bbox']).contains(box):
                raise RedactionLocalizationError([{'region_id':region['id'],'reason':'invalid_word_geometry'}])
            result.append(box)
    return result


def redact_pdf(data,ir,findings):
    from extraction.coordinator import text_regions
    regions={r['id']:r for r in text_regions(ir)+ir.get('pii_source_regions',[])}
    for finding in findings:
        if finding['region_id'] not in regions or not finding.get('value') or finding['value'] not in regions[finding['region_id']]['text']:
            raise ValueError('PII redaction requires an exact source-region finding.')
    values=sorted({f['value'] for f in findings},key=len,reverse=True)
    audit=[]; unselected={}; selected_boxes=[]
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        # Native supplemental findings must be re-derived from these exact bytes.
        actual={r['id']:r for r in native_pattern_regions(data)}
        for finding in findings:
            region=regions[finding['region_id']]
            if region.get('metadata',{}).get('pii_detection_only'):
                if actual.get(region['id'],{}).get('text')!=region['text']:
                    raise ValueError('PII native source evidence does not match the PDF.')
        for unit in ir['units']:
            index=unit['number']-1;page=pdf[index];blocks=native_blocks(page)
            native_rects=[]; boxes_by_value={v:[] for v in values}
            for block in blocks:
                selected=set()
                for value in values:
                    for a,b in literal_spans(block['text'],value):
                        selected.update(range(a,b)); line_boxes={}
                        for char in block['chars'][a:b]:
                            if char is None or char['c'].isspace():continue
                            rect=pymupdf.Rect(char['bbox'])
                            if rect.is_empty:continue
                            if char['line'] not in line_boxes:line_boxes[char['line']]=rect
                            else:line_boxes[char['line']] |= rect
                        native_rects.extend(line_boxes.values())
                        boxes_by_value[value].extend(line_boxes.values())
                unselected.setdefault(index,Counter()).update(_glyph_key(c) for i,c in enumerate(block['chars'])
                    if c is not None and not c['c'].isspace() and i not in selected)
            for rect in native_rects:page.add_redact_annot(rect,fill=(0,0,0))
            selected_boxes.extend((index,rect) for rect in native_rects)
            for region in text_regions({'units':[unit]}):
                selected=[v for v in values if literal_spans(region['text'],v)]
                if not selected:continue
                region_box=pymupdf.Rect(region['bbox'])*page.derotation_matrix
                native_covered=all(any((region_box & rect).get_area()>0 for rect in boxes_by_value[v]) for v in selected)
                if native_covered:
                    audit.append({'region_id':region['id'],'method':'native_glyph_spans','review_required':True})
                else:
                    if any(c is not None and (region_box & pymupdf.Rect(c['bbox'])).get_area()>0
                           for block in blocks for c in block['chars']):
                        raise RedactionLocalizationError([{'region_id':region['id'],'reason':'native_value_not_fully_located'}])
                    rects=_ocr_rectangles(region,selected)
                    for rect in rects:
                        rect=rect*page.derotation_matrix
                        if not page.cropbox.contains(rect):raise RedactionLocalizationError([{'region_id':region['id'],'reason':'outside_page'}])
                        page.add_redact_annot(rect,fill=(0,0,0));selected_boxes.append((index,rect))
                    audit.append({'region_id':region['id'],'method':'ocr_value_spans',
                        'selected_value_occurrences':sum(len(literal_spans(region['text'],v)) for v in selected),
                        'propagated_exact_value':not any(f['region_id']==region['id'] for f in findings),'review_required':True})
            for value in values:
                expected=[r for r in text_regions({'units':[unit]}) if literal_spans(r['text'],value)]
                if expected and not native_rects and not any(a['region_id'] in {r['id'] for r in expected} for a in audit):
                    raise RedactionLocalizationError([{'region_id':r['id'],'reason':'value_not_located'} for r in expected])
            # Preserve table rules and unrelated vector artwork. Images are physically
            # cleared only beneath selected regions, not retained under painted boxes.
            if native_rects or any(i==index for i,_ in selected_boxes):
                page.apply_redactions(images=2,graphics=0,text=0)
        pdf.set_metadata({});pdf.del_xml_metadata()
        for name in pdf.embfile_names():pdf.embfile_del(name)
        result=pdf.tobytes(garbage=4,deflate=True,clean=True)
    with pymupdf.open(stream=result,filetype='pdf') as pdf:
        for index,page in enumerate(pdf):
            blocks=native_blocks(page)
            if any(literal_spans(b['text'],v) for b in blocks for v in values):
                raise RedactionVerificationError('Selected sensitive text remains searchable after redaction.','PRIVACY_CHECK_BLOCKED')
            remaining=Counter(_glyph_key(c) for b in blocks for c in b['chars'] if c is not None and not c['c'].isspace())
            if _missing_glyphs(unselected.get(index,Counter()),remaining):
                raise RedactionVerificationError('Precise redaction would remove surrounding text; review is required.','REDACTION_REVIEW_REQUIRED')
    return result,audit
