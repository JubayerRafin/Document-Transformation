"""Explicit, experimental handoff of saved PaddleOCR lines to BabelDOC.

No models run here. The scanned background is preserved, not redacted.
"""
import hashlib
import json
from pathlib import Path

import pymupdf
from extraction.models import Document, Page, Region


# Fonts for the invisible OCR text layer, tried in order: MuPDF's CJK font, then fonts covering
# Arabic and the Indic scripts. With the CJK font alone every Arabic and Hindi scan failed once those
# languages got their own recognizers (2026-11-21). TRANSFORMX_OCR_FONTS adds font files (":"-separated).
_FONT_FILES = ['~/.cache/babeldoc/fonts/GoNotoKurrent-Regular.ttf',
               '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']


def handoff_fonts():
    import os
    fonts = [('ocrfont', pymupdf.Font('cjk'))]
    extra = [p for p in os.environ.get('TRANSFORMX_OCR_FONTS', '').split(os.pathsep) if p]
    for number, path in enumerate(extra + _FONT_FILES):
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            try:
                fonts.append((f'ocrfont{number + 1}', pymupdf.Font(fontfile=path)))
            except Exception:
                continue
    return fonts


def drawing_order(text):
    """text as a PDF draws it, left to right. Right-to-left text is drawn in visual order (runs of
    digits and Latin letters left to right inside it), as real PDFs do, so that the reader that
    puts right-to-left lines back into reading order (unmapped_text.py) reads it correctly."""
    import unicodedata
    from transformation.pdf.unmapped_text import RTL, visual_to_logical
    if not RTL.search(text):
        return text
    clusters = []
    for c in text:
        if clusters and unicodedata.combining(c):
            clusters[-1] += c
        else:
            clusters.append(c)
    return visual_to_logical([(i, c) for i, c in enumerate(clusters)])


def prepare(data, recognition_dir, output_dir, source_name, *, allow_unreadable=False):
    """Build verified OCR IR; verification-only mode retains unreadable boxes.

    The default source/translation handoff remains strict. Empty verification
    regions carry no invented text and always require review; they cannot count
    as successful privacy inspection.
    """
    root = Path(recognition_dir).resolve()
    meta = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if meta.get('status') != 'completed':
        raise ValueError('Select a completed PaddleOCR recognition run.')
    detector = Path(meta['detection_run'])
    for filename, key in [('page-1.png', 'image_sha256'), ('page-1_res.json', 'detection_sha256')]:
        if hashlib.sha256((detector / filename).read_bytes()).hexdigest() != meta[key]:
            raise ValueError('Saved OCR evidence hash mismatch.')
    raw = (root / 'lines.jsonl').read_bytes()
    lines = [json.loads(s) for s in raw.decode('utf-8').splitlines() if s.strip()]
    if len(lines) != meta['lines'] or (not lines and not meta.get('no_text_detected')):
        raise ValueError('Incomplete OCR line evidence.')
    warnings = ['Experimental OCR handoff; human review required.',
                'Conservative scan reconstruction preserves original text in uncertain regions. Review the reconstruction report.',
                'Reading order, table structure and OCR accuracy are not validated.']
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        if len(pdf) != 1 or pdf.needs_pass:
            raise ValueError('Saved OCR handoff currently supports one unencrypted scanned page.')
        page = pdf[0]
        if any(meta.get(key) is not None and abs(float(meta[key])-actual)>.01
               for key,actual in [('page_width',page.rect.width),('page_height',page.rect.height)]):
            raise ValueError('Saved OCR geometry does not match the uploaded page dimensions.')
        if page.rotation or page.get_text().strip():
            raise ValueError('Use an unrotated, image-only page for this OCR handoff.')
        from tools.ocr.raster_policy import render_verified_page
        original = render_verified_page(page,meta.get('raster'))
        evidence = pymupdf.Pixmap(str(detector / 'page-1.png'))
        if (original.width, original.height, original.n, original.samples) != (evidence.width, evidence.height, evidence.n, evidence.samples):
            raise ValueError('Uploaded PDF does not match the saved OCR page image.')
        if not lines and not allow_unreadable:
            raise ValueError('No OCR text detected in the verified source image; preserve it for visual review.')
        # One detected line across a table rule becomes one line per cell (table_rules.py).
        from transformation.ocr.table_rules import split_at_rules
        lines = split_at_rules(page, lines)
        regions = []
        if not lines:
            regions.append(Region('unreadable_page', 'raster_page', list(page.rect),
                metadata={'requires_review': True, 'ocr_unreadable': True},
                warnings=['No recognized text; privacy inspection is incomplete.']))
        for line in lines:
            line=dict(line)
            text = line['text']
            if not text.strip():
                if allow_unreadable and line.get('requires_review') and line.get('recognition_decision')=='unreadable_source_region':
                    regions.append(Region(line['id'], 'figure', list(line['bbox']),
                        metadata={'requires_review': True, 'ocr_unreadable': True,
                                  'recognition_decision': 'unreadable_source_region'},
                        warnings=['Detected region could not be read; privacy inspection is incomplete.']))
                    continue
                raise ValueError('OCR source text must not be empty.')
            from tools.ocr.recognize_paddle_boxes import sensitive_literal,independent_literal_evidence,single_model_literal_evidence
            candidates=line.get('candidates',[])
            transcription_candidates=[c for c in candidates if c.get('purpose')!='redaction_geometry_only']
            sensitive=sensitive_literal(text) or any(sensitive_literal(c.get('text','')) for c in transcription_candidates)
            # Two independent recognizers (English), or for a language read by one model (Korean) its
            # agreeing views plus digits confirmed by a second model.
            independent=(independent_literal_evidence(transcription_candidates,text)
                         or single_model_literal_evidence(transcription_candidates,text)) if sensitive else None
            if sensitive and not independent:
                line['requires_review']=True
                line['recognition_decision']='sensitive_literal_independent_evidence_not_established'
            bbox=list(line['bbox'])
            label=line.get('confirmed_label')
            if label and line.get('requires_review'):
                from tools.ocr.recognize_paddle_boxes import confirmed_label_prefix
                primary=next((c.get('words',[]) for c in line.get('candidates',[]) if c.get('variant')=='word_gap_primary'),[])
                secondary=next((c.get('words',[]) for c in line.get('candidates',[]) if c.get('variant')=='word_gap_english'),[])
                proof=confirmed_label_prefix(primary,secondary)
                label_box=pymupdf.Rect(label['bbox'])
                parent_box=pymupdf.Rect(bbox)
                if not proof or proof['text']!=label['text'] or not parent_box.contains(label_box) or not parent_box.x0<label_box.x1<parent_box.x1:
                    raise ValueError('Invalid field-label recognition evidence.')
                regions.append(Region(line['id']+'_label','text',list(label_box),text=label['text'],
                    metadata={'recognition_score':proof['score'],'requires_review':False,
                              'recognition_decision':'cross_model_field_label','parent_line_id':line['id']},
                    warnings=['Field value remains unreviewed and is preserved as source pixels.']))
                bbox[0]=label_box.x1
                candidate=next(c['text'] for c in line['candidates'] if c.get('variant')=='word_gap_english')
                text=candidate.split(':',1)[1].strip() if ':' in candidate else text
            from transformation.validation.value_redaction import verified_ocr_words
            word_boxes=verified_ocr_words(line,text,bbox)
            regions.append(Region(line['id'], 'text', bbox, text=text,
                                  metadata={'recognition_score': line['recognition_score'],
                                            'original_line_text':line['text'],
                                            'source_value_preserved':bool(label and line.get('requires_review')),
                                            'recognition_decision': line.get('recognition_decision','legacy'),
                                            'independent_literal_verified':independent,
                                            'redaction_words':word_boxes,
                                            'requires_review': line.get('requires_review', line['recognition_score'] < .95),
                                            'candidates': line.get('candidates', [])},
                                  warnings=['Unreviewed OCR transcription']))
        ir = Document(source_name, hashlib.sha256(data).hexdigest(), 'paddleocr-saved-lines',
                      meta['versions']['paddleocr'],
                      pages=[Page(1, page.rect.width, page.rect.height, 0, regions=regions)],
                      settings={'recognition_manifest': meta, 'lines_sha256': hashlib.sha256(raw).hexdigest()},
                      warnings=warnings)
        ir.validate()
        # Invisible text lets the existing PDF parser read the OCR. Never erase pixels.
        fonts = handoff_fonts()
        inserted = set()
        for region in regions:
            if region.metadata.get('requires_review'):
                # Keep uncertain evidence in IR, but do not feed it to translation.
                continue
            choice = next(((name, font) for name, font in fonts
                           if all(font.has_glyph(ord(c)) for c in region.text if not c.isspace())), None)
            if choice is None:
                raise ValueError("OCR text contains a character unsupported by the handoff font.")
            fontname, font = choice
            if fontname not in inserted:
                page.insert_font(fontname=fontname, fontbuffer=font.buffer)
                inserted.add(fontname)
            drawn = drawing_order(region.text)
            box = pymupdf.Rect(region.bbox)
            size = min(box.height / 1.5, box.width / max(font.text_length(drawn, fontsize=1), .01))
            if size < 3:
                # High-resolution recognition can correctly recover text that is
                # physically too small for the parser's invisible text layer.
                # Keep that evidence and source pixels; one tiny label must not
                # abort the other regions or become a fake OCR-confidence error.
                region.metadata['source_layer_omitted']=True
                region.warnings.append('Source text is below the 3 pt handoff limit; retained in OCR IR and original pixels.')
                continue
            page.insert_text((box.x0, box.y0 + size), drawn, fontsize=size, fontname=fontname, render_mode=3)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        pdf.save(out / 'ocr-input.pdf')
        (out / 'ocr-document.json').write_text(json.dumps(ir.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
    return {'input_pdf': str(out / 'ocr-input.pdf'), 'document_ir': str(out / 'ocr-document.json'),
            'lines': len(lines), 'review_status': 'requires_human_review', 'warnings': warnings}
