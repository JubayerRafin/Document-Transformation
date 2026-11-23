"""Source-backed value spans and PDF geometry; no proportional character guesses."""
from collections import Counter
import re
import pymupdf

from transformation.validation.scripts import changes as script_changes


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


_NO_SPACE_SCRIPT=re.compile(r'[぀-ヿ㐀-鿿가-힯]')  # Kana, Han, Hangul
_HAN_KANA=re.compile(r'[぀-ヿ㐀-鿿]')


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
        # Sentence punctuation after a value ("... d.lee@corp.example.") is not part of
        # the token: a '.', '-' or '+' followed by whitespace or the end of text ends it.
        trailing = b<len(text) and re.match(token_chars,text[b]) and not (
            text[b] in '.-+' and (b+1==len(text) or text[b+1].isspace()))
        # Korean, Japanese and Chinese attach particles and endings without a space
        # ("정시우에게", "田中さん"), so a following character in those scripts does not
        # extend the value. Han/Kana text has no spaces at all, so the same holds before.
        if trailing and _NO_SPACE_SCRIPT.match(needle[-1]) and _NO_SPACE_SCRIPT.match(text[b]): trailing=False
        if trailing and script_changes(needle[-1], text[b]): trailing=False
        leading = a and re.match(token_chars,text[a-1]) and not (_HAN_KANA.match(needle[0]) and _HAN_KANA.match(text[a-1]))             and not script_changes(text[a-1], needle[0])
        if needle[0].isalnum() and leading: continue
        if needle[-1].isalnum() and trailing: continue
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


def visual_length(token):
    """Width of a word in ordinary letters: combining marks and format characters take no width of
    their own (a Bengali or Hindi word has many), wide CJK characters take two."""
    import unicodedata
    return sum(0 if unicodedata.category(c) in ('Mn', 'Me', 'Cf') else 2 if unicodedata.east_asian_width(c) in 'WF' else 1
               for c in token)


def space_tokens(text):
    return [(m.start(), m.end()) for m in re.finditer(r'\S+', text)]


def script_tokens(text):
    """Words of an OCR line, also split where the writing system changes ("請求書INV-2022-75013")."""
    spans = []
    for a, b in space_tokens(text):
        start = a
        for i in range(a + 1, b):
            if script_changes(text[i - 1], text[i]):
                spans.append((start, i)); start = i
        spans.append((start, b))
    return spans


def pixel_word_boxes(page, bbox, text, cache=None, tokens=None):
    """Split one OCR line into its whitespace-separated words using ink gaps.

    Works on the rendered pixels inside the line box, so it needs no second OCR
    model and no per-character width guesses. The k widest gaps (k = words - 1)
    are taken as word gaps only when they are strictly wider than every other
    gap, are a visible space, and give words whose widths are consistent with
    their lengths (within a factor of two). Otherwise the split is ambiguous and
    None is returned. Returns one display-coordinate Rect per word, or None.
    """
    import numpy as np
    tokens=[text[a:b] for a,b in (tokens if tokens is not None else space_tokens(text))]
    rect=pymupdf.Rect(bbox)&page.rect
    if not tokens or rect.is_empty:return None
    # Render the page as displayed (rotation applied) once, then crop the line in
    # display coordinates; this works for rotated scans as well as upright pages.
    # cache: optional dict owned by one redaction pass over one open document.
    key=(page.number,page.rotation)
    if cache is None or key not in cache:
        scale=min(4.0,4000/max(page.rect.width,page.rect.height))
        pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),colorspace=pymupdf.csGRAY,alpha=False,annots=False)
        rendered=(scale,np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width).copy())
        if cache is not None:cache[key]=rendered
    else:rendered=cache[key]
    scale,full=rendered
    image=full[int(rect.y0*scale):int(np.ceil(rect.y1*scale)),int(rect.x0*scale):int(np.ceil(rect.x1*scale))]
    if image.size==0 or min(image.shape)<2:return None
    border=np.concatenate([image[0],image[-1],image[:,0],image[:,-1]])
    background=float(np.median(border))
    ink=np.abs(image.astype(np.int16)-background)>64
    columns=np.flatnonzero(ink.any(axis=0))
    if not len(columns):return None
    runs=[];start=prev=columns[0]
    for c in columns[1:]:
        if c!=prev+1:runs.append((start,prev));start=c
        prev=c
    runs.append((start,prev))
    gaps=[(runs[i+1][0]-runs[i][1]-1,i) for i in range(len(runs)-1)]
    k=len(tokens)-1
    if k>len(gaps):return None
    ordered=sorted(gaps,reverse=True)
    rows=np.flatnonzero(ink.any(axis=1));ink_height=rows[-1]-rows[0]+1
    if k:
        narrowest=ordered[k-1][0]; next_gap=ordered[k][0] if k<len(ordered) else 0
        # Word gaps must be wider than every letter gap and a visible space.
        if narrowest<=next_gap or narrowest<max(2,.15*ink_height):return None
    cuts=sorted(i for _,i in ordered[:k])
    segments=[];first=0
    for i in cuts+[len(runs)-1]:
        segments.append((runs[first][0],runs[i][1]));first=i+1
    # Word widths against their visual lengths: counted in code points, a Bengali word with its
    # vowel signs looked twice as long as its ink, the split was refused and the whole line,
    # label included, was masked (Bengali scan, 2026-11-23).
    widths=[b-a+1 for a,b in segments];lengths=[max(1,visual_length(t)) for t in tokens]
    for width,length in zip(widths,lengths):
        actual=width/sum(widths);expected=length/sum(lengths)
        if length>=2 and not .5<=actual/expected<=2:return None
    left=int(rect.x0*scale)
    return [pymupdf.Rect((left+a)/scale,rect.y0,(left+b+1)/scale,rect.y1) for a,b in segments]




def _ocr_rectangles(region, values, page=None, localization=None, renders=None):
    """Locate selected values inside an OCR line, preserving labels where possible.

    In order: the whole line is the value; word boxes verified by two OCR
    models; words split at ink gaps in the line's own pixels (whole words are
    covered when a value starts or ends inside a word). If none of these can
    place the value, the whole line is covered and the audit records that the
    label was not preserved. A line box with no visible ink fails closed.
    """
    text=region['text']; matches=[span for value in values for span in literal_spans(text,value)]
    if not matches:return []
    record=localization if localization is not None else {}
    if any(compact(text)==compact(value) for value in values):
        record['localization']='whole_region_value';return [pymupdf.Rect(region['bbox'])]
    words=region.get('metadata',{}).get('redaction_words',[])
    if words:
        try:
            result=_verified_word_rectangles(region,text,matches,words)
            record['localization']='verified_words';return result
        except RedactionLocalizationError:
            if page is None:raise
    elif page is None:
        raise RedactionLocalizationError([{'region_id':region['id'],'reason':'missing_verified_word_geometry'}])
    # Words split at spaces first. When a value starts or ends inside such a word, the split at
    # changes of script is tried too: a label stored against its value ("請求書INV-2022-75013")
    # was trimmed by character counts and the mask covered part of the label (2026-11-23).
    fallback=None
    for tokenizer in (space_tokens,script_tokens):
        tokens=tokenizer(text)
        boxes=pixel_word_boxes(page,region['bbox'],text,renders,tokens)
        if not boxes:continue
        result=[];aligned=True
        for a,b in matches:
            chosen=[(span,box) for span,box in zip(tokens,boxes) if span[0]<b and span[1]>a]
            if chosen:
                aligned=aligned and chosen[0][0][0]>=a and chosen[-1][0][1]<=b
                union=None
                for span,box in chosen:
                    box=_trim_to_span(box,_Span(*span),a,b)
                    union=box if union is None else union|box
                result.append(union+(-.75,-.5,.75,.5))
        if len(result)!=len(matches):continue
        if aligned:
            record['localization']='pixel_word_gaps';return result
        fallback=fallback or result
    if fallback:
        record['localization']='pixel_word_gaps';return fallback
    if pixel_word_boxes(page,region['bbox'],'x',renders) is None:
        # Nothing visible in the line box: its geometry does not match the page.
        raise RedactionLocalizationError([{'region_id':region['id'],'reason':'missing_verified_word_geometry'}])
    record['localization']='whole_line_fallback';record['label_preserved']=False
    return [pymupdf.Rect(region['bbox'])+(-1,-1,1,1)]


def _clip_padding(base,padded,neighbours):
    """Keep a padded OCR box out of the text lines directly above and below it.

    The padding covers descenders and accents of the value's own line; on closely spaced
    lines it would otherwise black out the bottom or top of a neighbouring line. Only the
    padding is limited: the box never becomes smaller than the value's own line box.
    """
    padded=pymupdf.Rect(padded)
    for n in neighbours:
        n=pymupdf.Rect(n)
        if n.is_empty or n.x1<=base.x0 or n.x0>=base.x1:continue
        if n.y1<=base.y0+.25*base.height:padded.y0=max(padded.y0,min(base.y0,n.y1))
        elif n.y0>=base.y1-.25*base.height:padded.y1=min(padded.y1,max(base.y1,n.y0))
    return padded


class _Span:
    def __init__(self, start, end): self._start, self._end = start, end
    def start(self): return self._start
    def end(self): return self._end


def _trim_to_span(box,token,a,b,margin=.75):
    """Narrow a word box to the part of the word inside [a, b).

    OCR may read a label and its value as one word ("Device:WP-0042"). The box then
    covers only the value's characters, estimated from character positions and widened
    by `margin` characters toward the rest of the word, so no part of the value stays
    visible while most of the label does.
    """
    box=pymupdf.Rect(box);length=token.end()-token.start()
    if length<=0 or (a<=token.start() and b>=token.end()):return box
    step=box.width/length
    left=box.x0+max(0,a-token.start()-margin)*step if a>token.start() else box.x0
    right=box.x0+min(length,b-token.start()+margin)*step if b<token.end() else box.x1
    return pymupdf.Rect(left,box.y0,max(right,left+step),box.y1)


def _verified_word_rectangles(region,text,matches,words):
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


def remove_hidden_values(pdf,values):
    """Selected values outside page content: form fields, comments, links, bookmarks.

    Form fields are flattened so their visible values become page text and are
    redacted with it. Comments and links whose text contains a selected value are
    deleted; matching bookmark titles are replaced. Runs before glyph accounting.
    """
    needles=[compact(v) for v in values if compact(v)]
    def contains(text):
        text=compact(str(text or ''))
        return any(n in text for n in needles)
    if any(True for page in pdf for _ in (page.widgets() or [])):
        pdf.bake(annots=False,widgets=True)
    for page in pdf:
        for annot in list(page.annots() or []):
            if contains(' '.join(str(v) for v in annot.info.values())):page.delete_annot(annot)
        for link in page.get_links():
            if contains(link.get('uri')) or contains(link.get('file')):page.delete_link(link)
    toc=pdf.get_toc(simple=False)
    if any(contains(entry[1]) for entry in toc):
        pdf.set_toc([[entry[0],'[REDACTED]' if contains(entry[1]) else entry[1],*entry[2:]] for entry in toc])


STYLES={'box','label','pseudonym'}
_LABEL={'Names':'NAME','Email addresses':'EMAIL','Phone numbers':'PHONE','Addresses':'ADDRESS','Identifiers':'ID','Bank accounts':'ACCOUNT','Dates of birth':'BIRTH DATE'}
_PSEUDO={'Names':'Person','Email addresses':'Email','Phone numbers':'Phone','Addresses':'Address','Identifiers':'ID','Bank accounts':'Account','Dates of birth':'Birth date'}


def replacement_labels(findings,style='box'):
    """value -> text drawn inside its black box. 'label': the category ([NAME]); 'pseudonym': a
    consistent placeholder per distinct value (Person A, Email 1, ...). A single word of a full
    name reuses that person's placeholder, so 'Becker' and 'Thomas Becker' read as one person."""
    if style not in STYLES:raise ValueError('Unknown masking style.')
    if style=='box':return {}
    category={}
    for f in findings:category.setdefault(f['value'],f['category'])
    if style=='label':return {v:'['+_LABEL.get(c,'REDACTED')+']' for v,c in category.items()}
    labels,by_key,counters={}, {}, {}
    def key(v):return compact(v).casefold()
    names=[v for v,c in category.items() if c=='Names']
    for v in sorted(names,key=lambda v:(-len(v.split()),names.index(v))):
        if key(v) in by_key:labels[v]=by_key[key(v)];continue
        owner=next((labels[o] for o in labels if len(v.split())==1 and len(o.split())>1
                    and v.casefold() in [w.strip(',.').casefold() for w in o.split()]),None)
        if owner is None:
            n=counters.get('Names',0);counters['Names']=n+1
            owner='Person '+(chr(65+n) if n<26 else chr(65+n//26-1)+chr(65+n%26))
        by_key[key(v)]=labels[v]=owner
    for v,c in category.items():
        if c=='Names':continue
        if key(v) not in by_key:
            counters[c]=counters.get(c,0)+1;by_key[key(v)]=f"{_PSEUDO.get(c,'Item')} {counters[c]}"
        labels[v]=by_key[key(v)]
    return labels


def _label_annotation(text,rect):
    """Keyword arguments that write text into a black redaction box, if it fits legibly."""
    if not text:return {}
    rect=pymupdf.Rect(rect);size=min(rect.height*.72,rect.width/(.56*len(text)+.6),11)
    if size<3.5:return {}
    return {'text':text,'fontname':'helv','fontsize':size,'text_color':(1,1,1),'align':pymupdf.TEXT_ALIGN_CENTER,'cross_out':False}


def redact_pdf(data,ir,findings,style='box',boxes=None):
    """boxes: optional list that receives (page number, displayed-page rect, value) per drawn box."""
    from extraction.coordinator import text_regions
    regions={r['id']:r for r in text_regions(ir)+ir.get('pii_source_regions',[])}
    for finding in findings:
        if finding['region_id'] not in regions or not finding.get('value') or finding['value'] not in regions[finding['region_id']]['text']:
            raise ValueError('PII redaction requires an exact source-region finding.')
    values=sorted({f['value'] for f in findings},key=len,reverse=True)
    labels=replacement_labels(findings,style)
    audit=[]; unselected={}; selected_boxes=[]
    renders={}  # page renders, valid only for this open document
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        # Native supplemental findings must be re-derived from these exact bytes.
        actual={r['id']:r for r in native_pattern_regions(data)}
        for finding in findings:
            region=regions[finding['region_id']]
            if region.get('metadata',{}).get('pii_detection_only'):
                if actual.get(region['id'],{}).get('text')!=region['text']:
                    raise ValueError('PII native source evidence does not match the PDF.')
        remove_hidden_values(pdf,values)
        for unit in ir['units']:
            index=unit['number']-1;page=pdf[index];blocks=native_blocks(page)
            native_rects=[]; native_labels=[]; native_values=[]; boxes_by_value={v:[] for v in values}; page_unselected=[]
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
                        native_rects.extend(line_boxes.values()); native_labels.extend(labels.get(value,'') for _ in line_boxes)
                        native_values.extend(value for _ in line_boxes)
                        boxes_by_value[value].extend(line_boxes.values())
                unselected.setdefault(index,Counter()).update(_glyph_key(c) for i,c in enumerate(block['chars'])
                    if c is not None and not c['c'].isspace() and i not in selected)
                page_unselected.extend(c for i,c in enumerate(block['chars'])
                                       if c is not None and not c['c'].isspace() and i not in selected)
            # A character drawn mostly on top of a removed value (a value overflowing its table cell
            # under the next column's letter, Korean roster 2026-11-23) cannot stay without the value
            # under it showing around it; it is removed with the value and reported for review,
            # instead of failing the whole document.
            covered=Counter()
            for char in page_unselected:
                box=pymupdf.Rect(char['bbox'])
                if box.get_area()>0 and any((box&rect).get_area()>.5*box.get_area() for rect in native_rects):
                    covered[_glyph_key(char)]+=1
            if covered:
                unselected[index]=unselected.get(index,Counter())-covered
                audit.append({'page':index+1,'method':'covered_by_removed_value','glyphs_removed_with_value':sum(covered.values()),
                              'review_required':True})
            for rect,label,value in zip(native_rects,native_labels,native_values):
                page.add_redact_annot(rect,fill=(0,0,0),**_label_annotation(label,rect))
                if boxes is not None:boxes.append((index+1,pymupdf.Rect(rect)*page.rotation_matrix,value))
            selected_boxes.extend((index,rect) for rect in native_rects)
            # A page with a text layer is redacted from its own glyphs: every occurrence of a
            # selected value on the page was located above. Extractor boxes are not used there,
            # because some extractors report coordinates in another frame (e.g. media box on
            # pages with an offset crop box). OCR geometry is used only for OCR'd/image pages.
            ocr_page=bool(unit.get('ocr_folder')) or not any(c is not None for b in blocks for c in b['chars'])
            for region in text_regions({'units':[unit]}):
                selected=[v for v in values if literal_spans(region['text'],v)]
                if not selected:continue
                region_box=pymupdf.Rect(region['bbox'])*page.derotation_matrix
                # A value is covered where it, or a longer selected value containing it, was boxed.
                # A table cell can clip a value that overflows it ("ann@host.exa" of
                # "ann@host.example"); the clipped text is not a word on the page, but the whole
                # value it belongs to is removed there.
                native_covered=all(any((region_box & rect).get_area()>0 for w in values if v in w for rect in boxes_by_value[w])
                                   for v in selected)
                if not ocr_page and not native_covered:
                    missing=[v for v in selected if not boxes_by_value[v]]
                    if missing:
                        raise RedactionLocalizationError([{'region_id':region['id'],'reason':'native_value_not_fully_located'}])
                    native_covered=True
                if native_covered:
                    audit.append({'region_id':region['id'],'method':'native_glyph_spans','review_required':True})
                else:
                    if any(c is not None and (region_box & pymupdf.Rect(c['bbox'])).get_area()>0
                           for block in blocks for c in block['chars']):
                        raise RedactionLocalizationError([{'region_id':region['id'],'reason':'native_value_not_fully_located'}])
                    localization={}
                    rects=_ocr_rectangles(region,selected,page,localization,renders)
                    for rect in rects:
                        if not (pymupdf.Rect(rect)&page.rect).is_empty and page.rect.contains(rect):
                            # OCR boxes are tight to the text body; pad so descenders/accents
                            # inside the cleared image area are removed too (kept on the page).
                            pad=max(.5,.12*min(rect.height,rect.width))
                            rect=_clip_padding(pymupdf.Rect(rect),(pymupdf.Rect(rect)+(-.5,-pad,.5,pad))&page.rect,
                                               [r['bbox'] for r in text_regions({'units':[unit]}) if r['id']!=region['id'] and r.get('bbox')])
                        rect=rect*page.derotation_matrix
                        # Page coordinates start at the crop box corner; page.cropbox is
                        # measured from the media box, so compare with the crop box size.
                        if not pymupdf.Rect(0,0,page.cropbox.width,page.cropbox.height).contains(rect):raise RedactionLocalizationError([{'region_id':region['id'],'reason':'outside_page'}])
                        page.add_redact_annot(rect,fill=(0,0,0),**_label_annotation(labels.get(selected[0],''),rect));selected_boxes.append((index,rect))
                        if boxes is not None:boxes.append((index+1,pymupdf.Rect(rect)*page.rotation_matrix,selected[0]))
                    audit.append({'region_id':region['id'],'method':'ocr_value_spans',
                        'selected_value_occurrences':sum(len(literal_spans(region['text'],v)) for v in selected),
                        'propagated_exact_value':not any(f['region_id']==region['id'] for f in findings),'review_required':True,
                        **localization})
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
            missing=_missing_glyphs(unselected.get(index,Counter()),remaining)
            if missing:
                # A mask that also removes some neighbouring characters costs layout, not privacy: the
                # page is delivered and listed for review. It failed the whole 302-page Samsung guide
                # (2026-11-23), where glyphs of different lines overlap.
                audit.append({'region_id':None,'method':'collateral_glyph_check','page':index+1,
                              'removed_unselected_characters':sum(missing.values()),'review_required':True})
    return result,audit

