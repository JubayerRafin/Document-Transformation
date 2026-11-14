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


def verify(data,kind,run,values,categories,endpoint,model,assist,options,language=None,analyzed_texts=(),pages=None,ignore=()):
    """language is the output's text language: the target if translated, else the source.

    analyzed_texts: source texts the detector already sent to the model. An output line
    whose text occurs unchanged in one of them would get the same answer again
    (deterministic model), so it is checked with format patterns only; changed or new
    lines (e.g. around removed values, or image text) still go to the model.
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
            if options.get('ocr_remote'):
                from transformation.ocr.remote_ocr import run_remote
                events=run_remote(folder/'source.pdf',folder/'paddle',options['ocr_remote'],timeout=min(options.get('timeout',1200),1200),source_language=language)
            elif options.get('ocr_python'):
                from transformation.ocr.paddle_pipeline import run_ocr
                events=run_ocr(folder/'source.pdf',folder/'paddle',options['ocr_python'],timeout=min(options.get('timeout',1200),1200),source_language=language)
            else: raise ValueError('Fresh OCR mode is required for rendered-output privacy verification.')
            for progress in events: yield {'page':index+1,'progress':progress}
            from transformation.ocr.paddle_handoff import prepare
            prepare((folder/'source.pdf').read_bytes(),progress['recognition_dir'],folder,'verification.pdf',allow_unreadable=True)
            ir=json.loads((folder/'ocr-document.json').read_text(encoding='utf-8'))
            regions=ir['pages'][0]['regions']; text=' '.join(r['text'] for r in regions)
            residual=[v for v in values if normalized(v) in normalized(text)]
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
            flagged=[r for r in regions if r.get('metadata',{}).get('requires_review')]
            with pymupdf.open(folder/'source.pdf') as rendered:
                uncertain=[r['id'] for r in flagged if _could_hide_selected_value(r,values,rendered[0])]
            # A line the model could not answer verifiably is unchecked, not a crash.
            uncertain+=[i for i in unanswered if i not in uncertain]
            # Where each finding is (rendered-page coordinates = the displayed page), so a
            # caller can remove what the check found.
            boxes={r['id']:r.get('bbox') for r in regions}
            detected=[dict(d,bbox=boxes.get(d.get('region_id'))) for d in detected]
            reports.append({'page':index+1,'page_size':[page.rect.width,page.rect.height],
                            'known_residual_values':residual,'detected_pii':detected,'uncertain_regions':uncertain,
                            'uncertain_regions_ignored':len(flagged)-len(uncertain)})
    yield {'reports':reports,'passed':not any(p['known_residual_values'] or p['detected_pii'] or p['uncertain_regions'] for p in reports),
           'limitations':'Fresh OCR can still miss content. This is an acceptance check, not a privacy guarantee.'}
