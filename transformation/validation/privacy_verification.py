"""Fresh OCR of rendered output, independent of processing IR and detections."""
from pathlib import Path
import json
import pymupdf
from transformation.validation.output_validation import normalized


def _could_hide_selected_value(region,values,page):
    """An unreadable/uncertain OCR region matters only if it could contain a selected value.

    Empty regions count unless they are a solid redaction box. Text regions count when
    they share a long run of characters with a selected value or look like an email or
    phone number. Ordinary text with digits (amounts, dates, headings) does not block.
    """
    import difflib,re
    from transformation.validation.value_redaction import compact
    text=region.get('text','')
    if not text.strip():
        if not region.get('bbox'):return True
        box=pymupdf.Rect(region['bbox'])&page.rect
        if box.is_empty:return True
        pix=page.get_pixmap(clip=box,colorspace=pymupdf.csGRAY,alpha=False)
        samples=pix.samples
        return not samples or sum(1 for b in samples if b<60)/len(samples)<.85
    flat=compact(text).casefold()
    for value in values:
        target=compact(value).casefold()
        if len(target)<4:continue
        match=difflib.SequenceMatcher(None,target,flat,autojunk=False).find_longest_match(0,len(target),0,len(flat))
        if match.size>=max(4,.6*len(target)):return True
    # Same phone shapes as detection (international '+' or grouped digits ending in four),
    # so ISO dates and amounts are not mistaken for phone numbers.
    return bool(re.search(r'[\w.+-]+@[\w-]+\.|\+\d[\d ()-]{7,}\d|(?<!\d)\d{2,4}[- ]\d{2,4}[- ]\d{4}(?!\d)',text))


def _read(pdf,folder,name,options,language):
    """Fresh OCR events of one rendered page in one language."""
    if options.get('ocr_remote'):
        from transformation.ocr.remote_ocr import run_remote
        yield from run_remote(pdf,folder/name,options['ocr_remote'],timeout=min(options.get('timeout',1200),1200),source_language=language)
    elif options.get('ocr_python'):
        from transformation.ocr.paddle_pipeline import run_ocr
        yield from run_ocr(pdf,folder/name,options['ocr_python'],timeout=min(options.get('timeout',1200),1200),source_language=language)
    else: raise ValueError('Fresh OCR mode is required for rendered-output privacy verification.')


def _regions(pdf,folder,progress):
    """OCR regions of a finished reading, prepared into folder."""
    from transformation.ocr.paddle_handoff import prepare
    prepare(Path(pdf).read_bytes(),progress['recognition_dir'],folder,'verification.pdf',allow_unreadable=True)
    return json.loads((Path(folder)/'ocr-document.json').read_text(encoding='utf-8'))['pages'][0]['regions']


def _inside(region,readings):
    """Readings of another pass that lie mostly inside an uncertain region's box."""
    if not region.get('bbox'):return []
    box=pymupdf.Rect(region['bbox']); found=[]
    for reading in readings:
        if not reading.get('bbox') or not reading.get('text','').strip():continue
        other=pymupdf.Rect(reading['bbox']); common=box&other
        if not common.is_empty and common.get_area()>=.5*min(box.get_area(),other.get_area()):found.append(reading)
    return found


def verify(data,kind,run,values,categories,endpoint,model,assist,options,language=None,analyzed_texts=(),pages=None,ignore=(),source_language=None):
    """language is the output's text language: the target if translated, else the source.

    analyzed_texts: source texts the detector already sent to the model. An output line
    whose text occurs unchanged in one of them would get the same answer again
    (deterministic model), so it is checked with format patterns only; changed or new
    lines (e.g. around removed values, or image text) still go to the model.

    source_language: the input's language when the output is a translation. A translated scan
    keeps the source pixels of lines it could not translate, which the output-language reader
    cannot read; those uncertain regions are read again in the source language.
    """
    from transformation.validation.value_redaction import compact as _compact
    seen_texts=[_compact(t).casefold() for t in analyzed_texts if t]
    from transformation.validation.privacy import detect
    from tools.ocr.raster_policy import render_page
    language=language or options.get('target','ko')
    if kind!='pdf':
        from transformation.office.office import to_pdf
        data=to_pdf(data,'output.'+kind)
    reports=[]
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        for index,page in enumerate(pdf):
            # Only pages with raster content need a fresh-OCR look; text layers are
            # inspected directly (pages: 1-based numbers, None = every page).
            if pages is not None and index+1 not in pages:continue
            folder=Path(run)/'privacy-verification'/str(index+1); folder.mkdir(parents=True,exist_ok=True)
            with pymupdf.open() as raster:
                target=raster.new_page(width=page.rect.width,height=page.rect.height)
                # Same bounded OCR raster policy as source OCR; the 72 dpi default loses small text.
                target.insert_image(target.rect,pixmap=render_page(page)[0])
                raster.save(folder/'source.pdf')
            progress=None
            for progress in _read(folder/'source.pdf',folder,'paddle',options,language): yield {'page':index+1,'progress':progress}
            regions=_regions(folder/'source.pdf',folder,progress); text=' '.join(r['text'] for r in regions)
            # A selected value read again inside one line is a finding with a place, masked and
            # checked again by the final-check rounds; only one that cannot be placed (read across
            # lines) blocks the result at once. A Cisco part number the model had selected, printed
            # in a picture, blocked a whole manual (2026-11-23). Values are matched as words.
            from transformation.validation.output_validation import residual_in
            residual=[v for v in values if residual_in(v,text)]
            located=[{'region_id':r['id'],'value':v,'category':'Identifiers','method':'selected_value_reread'}
                     for v in residual for r in regions if residual_in(v,r.get('text',''))]
            residual=[v for v in residual if v not in {d['value'] for d in located}]
            def analysed(region):
                key=_compact(region.get('text','')).casefold()
                return len(key)>=3 and any(key in text for text in seen_texts)
            fresh=[r for r in regions if not analysed(r)]
            repeated=[r for r in regions if analysed(r)]
            unanswered=[]
            detected=detect(fresh,categories,endpoint,model,assist,unresolved=unanswered)+detect(repeated,categories,endpoint,model,False)
            # Replacement text the redaction itself wrote ([NAME], Person A) is not personal data.
            placeholders={_compact(i).casefold() for i in ignore if i}
            detected=[d for d in detected if not any(_compact(d.get('value','')).casefold() in p for p in placeholders)]
            # The same shape rules as detection: a label next to a removed value ("API Key.")
            # is not an identifier and must not trigger masking or a second check.
            from transformation.validation.privacy import plausible
            detected=[d for d in detected if plausible(d.get('value',''),d.get('category',''))]
            detected+=located
            flagged=[r for r in regions if r.get('metadata',{}).get('requires_review')]
            with pymupdf.open(folder/'source.pdf') as rendered:
                uncertain=[r for r in flagged if _could_hide_selected_value(r,values,rendered[0])]
                if uncertain and source_language and source_language!=language:
                    # Kept source pixels (an Arabic line in an English output) read as empty or
                    # garbled in the output language: read the page in the source language and
                    # judge each uncertain region by what that reader finds inside it (2026-11-23).
                    second=folder/'source-language'; second.mkdir(exist_ok=True); progress=None
                    for progress in _read(folder/'source.pdf',second,'paddle',options,source_language):
                        yield {'page':index+1,'progress':progress}
                    readings=_regions(folder/'source.pdf',second,progress)
                    rereads=[]
                    for region in uncertain:
                        inside=_inside(region,readings)
                        # Still unreadable in the source language: it stays uncertain.
                        if not inside or any(r.get('metadata',{}).get('requires_review') for r in inside):continue
                        reread={'id':region['id'],'bbox':region.get('bbox'),'text':' '.join(r['text'] for r in inside)}
                        if not _could_hide_selected_value(reread,values,rendered[0]):rereads.append(reread)
                    if rereads:
                        # The new readings get the same detection as any other output line.
                        unread=[]
                        found=[d for d in detect(rereads,categories,endpoint,model,assist,unresolved=unread)
                               if plausible(d.get('value',''),d.get('category',''))
                               and not any(_compact(d.get('value','')).casefold() in p for p in placeholders)]
                        detected+=found
                        cleared={r['id'] for r in rereads}-{d.get('region_id') for d in found}-set(unread)
                        uncertain=[r for r in uncertain if r['id'] not in cleared]
                uncertain=[r['id'] for r in uncertain]
            # A line the model could not answer verifiably is unchecked, not a crash.
            uncertain+=[i for i in unanswered if i not in uncertain]
            # Where each finding is (rendered-page coordinates = the displayed page), so a
            # caller can remove what the check found.
            boxes={r['id']:r.get('bbox') for r in regions}
            detected=[dict(d,bbox=boxes.get(d.get('region_id'))) for d in detected]
            reports.append({'page':index+1,'page_size':[page.rect.width,page.rect.height],
                            'known_residual_values':residual,'located_residual_values':sorted({d['value'] for d in located}),
                            'detected_pii':detected,'uncertain_regions':uncertain,
                            'uncertain_regions_ignored':len(flagged)-len(uncertain)})
    yield {'reports':reports,'passed':not any(p['known_residual_values'] or p['detected_pii'] or p['uncertain_regions'] for p in reports),
           'limitations':'Fresh OCR can still miss content. This is an acceptance check, not a privacy guarantee.'}
