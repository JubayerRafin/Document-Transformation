"""Independent artifact inspection, including non-visible package content."""
from io import BytesIO
import re
import zipfile
import xml.etree.ElementTree as ET
import pymupdf


def normalized(value):
    from urllib.parse import unquote
    return re.sub(r'\s+','',unquote(value)).casefold()


def residual_in(value,text):
    """True when value is still in text, spaces and letter case aside (a name split by a line break
    counts). A value that starts and ends with a letter or digit of a script written with spaces
    must stand as a word: the name "Mat" was found in "Format" and blocked a Samsung guide
    (2026-11-23). Other scripts (Arabic words take prefixes; CJK has no word spaces) are found anywhere."""
    from urllib.parse import unquote
    key=normalized(value)
    if not key:return False
    bounded=lambda ch:ch.isalnum() and ord(ch)<0x0590      # Latin, Greek, Cyrillic letters and digits
    if not (bounded(key[0]) and bounded(key[-1])):
        return key in normalized(text)
    # Only a neighbouring letter or digit of the same kind of script ends the word: "EMP-2026-14033은"
    # (a Korean particle attached) still holds the value.
    near=r'[0-9a-zÀ-ɏͰ-ϿЀ-ӿ]'
    pattern=r'(?<!'+near+r')'+r'\s*'.join(re.escape(c) for c in key)+r'(?!'+near+r')'
    return re.search(pattern,unquote(text).casefold()) is not None


def partial_name_residuals(source,output,names):
    """Words of a selected multi-word name left behind while other words were removed.

    Compares whole-word counts between the source and output text layers: a word whose
    count did not drop while a sibling word's count did is a likely half-masked name.
    """
    def words(data):
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            return re.findall(r'\w+',' '.join(page.get_text() for page in pdf))
    before,after=words(source),words(output)
    count=lambda tokens,word:sum(1 for t in tokens if t==word)
    residual=set()
    for name in names:
        parts=[p for p in re.findall(r'\w+',name) if len(p)>=3]
        if len(parts)<2:continue
        removed=[p for p in parts if count(after,p)<count(before,p)]
        kept=[p for p in parts if count(before,p) and count(after,p)==count(before,p)]
        if removed and kept:residual.update(kept)
    return sorted(residual)


def source_floor(span_box,sources):
    """The floor for an output line drawn where source text was: 70% of that text's size, never below
    4 pt (None when less than 30% of the line lies over source text). Translation shrinks text to
    70% of its source size before it takes more room (BabelDOC's typesetting); a scanned invoice whose
    own table text is 7 pt was reported as 31 layout problems against a flat 8 pt floor (2026-11-23).
    sources: (rect, size) of the source page's text."""
    area=span_box.get_area()
    if area<=0:return None
    best=max(sources,key=lambda s:(s[0]&span_box).get_area(),default=None)
    if best is None or (best[0]&span_box).get_area()<area*.3:return None
    return max(4.0,.7*best[1])


def inspect_output(data,kind,values=(),min_font_size=None,font_regions=(),intended_overlays=(),source_text=None):
    """source_text: {page number: [(rect, size), ...]} of the source document; with it, text below
    min_font_size is accepted down to source_floor() where it covers source text."""
    residual=[]; uninspected=[]; geometry=[]; candidates=[]
    import math
    for region in font_regions:
        if not isinstance(region.get('minimum'),(int,float)) or not math.isfinite(region['minimum']) or not 4<=region['minimum']<=8:
            raise ValueError('Invalid source-relative font validation region.')
    def check(text,location):
        for value in values:
            if residual_in(value,text):
                residual.append({'location':location,'value':value})
    if kind=='pdf':
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            check(str(pdf.metadata),'PDF metadata'); check(pdf.get_xml_metadata(),'PDF XML metadata')
            for number,page in enumerate(pdf,1):
                check(page.get_text(),'page '+str(number))
                text_spans=[]
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines',[]):
                        for span in line['spans']:
                            text_spans.append(span)
                            minimum=min_font_size
                            matching=[r['minimum'] for r in font_regions if r['page']==number and
                                      (pymupdf.Rect(r['bbox'])+(-.25,-.25,.25,.25)).contains(pymupdf.Rect(span['bbox']))]
                            if minimum is not None and matching:minimum=min(minimum,min(matching))
                            if minimum is not None and source_text:
                                floor=source_floor(pymupdf.Rect(span['bbox']),source_text.get(number,()))
                                if floor is not None:minimum=min(minimum,floor)
                            # A quarter point of rounding: 7.9 pt text against an 8 pt floor was
                            # reported as a layout problem on most scanned pages (2026-11-23).
                            if minimum is not None and span['text'].strip() and span['size']<minimum-.25:
                                geometry.append({'page':number,'reason':'text below configured font limit',
                                                 'text':span['text'],'font_size':span['size'],'minimum':minimum})
                            if not page.rect.contains(pymupdf.Rect(span['bbox'])):
                                geometry.append({'page':number,'reason':'text outside page','text':span['text']})
                for image in page.get_image_info():
                    uninspected.append({'page':number,'kind':'raster','bbox':image['bbox']})
                    rect=pymupdf.Rect(image['bbox'])
                    if rect.get_area()<page.rect.get_area()*.85:
                        for span in text_spans:
                            overlap=(rect & pymupdf.Rect(span['bbox'])).get_area()
                            if overlap>pymupdf.Rect(span['bbox']).get_area()*.2:
                                row={'page':number,'kind':'image_text_overlap','text':span['text'],'bbox':span['bbox'],'image_bbox':image['bbox'],
                                     'classification':'unverified_overlap'}
                                for proof in intended_overlays:
                                    if (proof['page']==number and proof['text']==span['text'] and
                                        max(abs(a-b) for a,b in zip(proof['bbox'],span['bbox']))<=.5 and
                                        (pymupdf.Rect(proof['image_bbox'])+(-.5,-.5,.5,.5)).contains(rect)):
                                        row.update(classification='intended_raster_text_placement_requires_review',region_id=proof['region_id'])
                                        break
                                candidates.append(row)
                from transformation.validation.overlap_index import candidate_pairs
                rectangles=[pymupdf.Rect(span['bbox']) for span in text_spans]
                areas=[rect.get_area() for rect in rectangles]
                for index,other_index in candidate_pairs(rectangles):
                    overlap=(rectangles[index] & rectangles[other_index]).get_area()
                    if overlap>min(areas[index],areas[other_index])*.35:
                        candidates.append({'page':number,'kind':'text_text_overlap',
                                           'text':[text_spans[index]['text'],text_spans[other_index]['text']]})
                # A link's address and file, not its record: "105088" matched a link's object number
                # and blocked a Cisco manual (2026-11-23).
                for link in page.get_links(): check(' '.join(str(link.get(k) or '') for k in ('uri','file')),'page '+str(number)+' link')
                for annot in page.annots() or []: check(str(annot.info),'page '+str(number)+' annotation')
                # annots() excludes form fields; their values are not page text.
                for widget in page.widgets() or []: check(str(widget.field_value or '')+' '+str(widget.field_name or ''),'page '+str(number)+' form field')
            check(' '.join(str(entry[1]) for entry in pdf.get_toc()),'PDF bookmarks')
            for index in range(pdf.embfile_count()):
                name=pdf.embfile_names()[index]; check(name,'attachment name')
                uninspected.append({'kind':'attachment','name':name})
    else:
        def package(payload,prefix='',depth=0):
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                if sum(i.file_size for i in archive.infolist())>300_000_000: raise ValueError('Validation package exceeds size limit.')
                for item in archive.infolist():
                    part=archive.read(item); location=prefix+item.filename
                    if item.filename.endswith(('.xml','.rels')):
                        root=ET.fromstring(part)
                        check(''.join(root.itertext()),location)
                        check(' '.join(v for node in root.iter() for v in node.attrib.values()),location+' attributes')
                    elif '/embeddings/' in item.filename:
                        if zipfile.is_zipfile(BytesIO(part)) and depth<2: package(part,location+'/',depth+1)
                        else:
                            check(part.decode('utf-8',errors='ignore'),location)
                            check(part.decode('utf-16-le',errors='ignore'),location)
                            uninspected.append({'kind':'embedded_binary','part':location})
                    elif '/media/' in item.filename:
                        uninspected.append({'kind':'image','part':location})
        package(data)
    return {'known_sensitive_residuals':residual,'geometry_blockers':geometry,
            'layout_review_candidates':candidates,
            'uninspected_content':uninspected,'privacy_complete':False if values else None,
            'acceptable':not residual and not geometry and not uninspected and not candidates}


INTENDED_PLACEMENT = 'intended_raster_text_placement_requires_review'


def set_aside_expected(independent, redact):
    """Move findings that are expected, not problems, out of the counts that mark a result for review.

    Every result was marked for review (2026-11-23), also when nothing was wrong: translated text drawn
    into a picture where its source text was is the intended placement, not an overlap
    (kept as picture_text_placements); and a picture in a document that is only translated holds no
    selected value whose removal needs checking (kept as pictures). Changes `independent` in place."""
    candidates = independent.get('layout_review_candidates') or []
    intended = [c for c in candidates if c.get('classification') == INTENDED_PLACEMENT]
    if intended:
        independent['picture_text_placements'] = intended
        independent['layout_review_candidates'] = [c for c in candidates if c.get('classification') != INTENDED_PLACEMENT]
    if not redact:
        content = independent.get('uninspected_content') or []
        independent['pictures'] = [c for c in content if c.get('kind') in {'image', 'raster'}]
        independent['uninspected_content'] = [c for c in content if c.get('kind') not in {'image', 'raster'}]
    return independent
