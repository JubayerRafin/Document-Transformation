"""Review queue: places in a redacted PDF that a person should look at.

Two states:
  masked    - removed as a precaution and possibly more than needed (an unverifiable model
              answer masked as a whole unit; a scanned line masked whole because the value
              could not be separated from its label).
  suggested - possibly personal data that was NOT removed (the model flagged it but a rule
              overruled it; scanned text the OCR could not read).

Items carry only a page number, a box as fractions of the displayed page (the same frame
manual redaction uses), a category and a reason code. They never carry the text itself, so
they can leave the GPU server without exposing personal data.
"""
import pymupdf

MAX_ITEMS = 200
REASONS = {'unverified_answer', 'whole_line', 'rule_overruled_model', 'unreadable_text', 'final_check'}


def _locations(data, ir):
    """region id -> (page number, displayed-page bbox) for every region detection can use."""
    from extraction.coordinator import text_regions
    from transformation.validation.value_redaction import native_pattern_regions
    places = {}
    for unit in ir.get('units', []):
        for region in text_regions({'units': [unit]}):
            if region.get('bbox'): places[region['id']] = (unit['number'], region['bbox'])
    for block in native_pattern_regions(data):
        places[block['id']] = (block['metadata']['pii_native_page'], block['bbox'])
    return places


def _value_boxes(page, region_bbox, value):
    """Boxes of a value inside a native region, in displayed coordinates; the region box if not found."""
    clip = pymupdf.Rect(region_bbox) * page.derotation_matrix
    found = [r * page.rotation_matrix for r in page.search_for(value, clip=clip)] if value else []
    return found or [pymupdf.Rect(region_bbox)]


def build(data, ir, findings, evidence, audit):
    """Return review items (no text) for a redacted PDF."""
    places = _locations(data, ir)
    masked_regions = {f['region_id'] for f in findings}
    raw = []  # (state, reason, category, region_id, value or None)
    for f in findings:
        if f.get('method') == 'unverifiable_model_answer':
            raw.append(('masked', 'unverified_answer', f['category'], f['region_id'], None))
    for a in audit:
        if a.get('localization') == 'whole_line_fallback':
            category = next((f['category'] for f in findings if f['region_id'] == a['region_id']), 'Identifiers')
            raw.append(('masked', 'whole_line', category, a['region_id'], None))
    from transformation.validation.value_redaction import compact
    masked_values = {compact(f['value']) for f in findings}
    for s in evidence.get('suppressed', []):
        if compact(s.get('value', '')) in masked_values: continue  # removed anyway by another method
        raw.append(('suggested', 'rule_overruled_model', s['category'], s['region_id'], s.get('value')))
    for unit in ir.get('units', []):
        if not unit.get('ocr_folder'): continue
        for region in unit.get('regions', []):
            if region.get('metadata', {}).get('requires_review') and region['id'] not in masked_regions:
                raw.append(('suggested', 'unreadable_text', 'Unknown', region['id'], None))
    items, seen = [], set()
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        for state, reason, category, region_id, value in raw:
            place = places.get(region_id)
            if not place or not 1 <= place[0] <= len(pdf): continue
            page = pdf[place[0] - 1]; view = page.rect
            boxes = _value_boxes(page, place[1], value) if value and region_id.startswith('pii-native-') else [pymupdf.Rect(place[1])]
            for box in boxes:
                box = box & view
                if box.is_empty: continue
                item = {'page': place[0], 'x': round(box.x0 / view.width, 4), 'y': round(box.y0 / view.height, 4),
                        'width': round(box.width / view.width, 4), 'height': round(box.height / view.height, 4),
                        'category': category, 'reason': reason, 'state': state}
                key = (item['page'], item['x'], item['y'], item['width'], item['height'], state)
                if key in seen or item['width'] <= 0 or item['height'] <= 0: continue
                seen.add(key); items.append(item)
    items.sort(key=lambda i: (i['state'] != 'suggested', i['page'], i['y'], i['x']))
    return items[:MAX_ITEMS]


# Why a box was drawn, most informative first when several methods found the same value.
METHODS = ('field_label', 'pattern', 'credential_label_pattern', 'address_block', 'model_proposal', 'document_name_sweep',
           'name_variant', 'document_consistency', 'unverifiable_model_answer')
MAX_BOXES = 400


def redaction_map(data, findings, boxes):
    """Every drawn box with its category and the method that selected it (no text).
    boxes: (page number, displayed-page rect, value) as collected by redact_pdf."""
    best = {}
    for f in findings:
        method = f.get('method') if f.get('method') in METHODS else 'model_proposal'
        current = best.get(f['value'])
        if current is None or METHODS.index(method) < METHODS.index(current[1]):
            best[f['value']] = (f['category'], method)
    result, seen = [], set()
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        for number, rect, value in boxes:
            if value not in best or not 1 <= number <= len(pdf): continue
            view = pdf[number - 1].rect; box = pymupdf.Rect(rect) & view
            if box.is_empty: continue
            item = {'page': number, 'x': round(box.x0 / view.width, 4), 'y': round(box.y0 / view.height, 4),
                    'width': round(box.width / view.width, 4), 'height': round(box.height / view.height, 4),
                    'category': best[value][0], 'method': best[value][1]}
            key = tuple(item.values())
            if key in seen: continue
            seen.add(key); result.append(item)
    result.sort(key=lambda i: (i['page'], i['y'], i['x']))
    return result[:MAX_BOXES]


def final_check_regions(reports, keep=()):
    """Lines the final privacy check found with personal data still readable in the output.

    Returns [{'region': manual-redaction area, 'item': review-queue entry, 'box': audit-map entry}]
    as fractions of the displayed page. Values on the job's keep-visible list are left alone.
    """
    from transformation.validation.pii_document import split_kept
    found, seen = [], set()
    for report in reports:
        width, height = (report.get('page_size') or [0, 0])[:2]
        detected = [d for d in report.get('detected_pii', []) if d.get('bbox') and d.get('value')]
        detected, _ = split_kept(detected, keep)
        for d in detected:
            if not width or not height: continue
            x0, y0, x1, y1 = d['bbox']
            # A point of padding keeps descenders and anti-aliased edges inside the box.
            x0, y0, x1, y1 = max(0, x0 - 1), max(0, y0 - 1), min(width, x1 + 1), min(height, y1 + 1)
            if x1 <= x0 or y1 <= y0: continue
            area = {'page': report['page'], 'x': round(x0 / width, 4), 'y': round(y0 / height, 4),
                    'width': round((x1 - x0) / width, 4), 'height': round((y1 - y0) / height, 4)}
            key = tuple(area.values())
            if key in seen: continue
            seen.add(key)
            found.append({'region': area, 'item': dict(area, category=d['category'], reason='final_check', state='masked'),
                          'box': dict(area, category=d['category'], method='final_check')})
    return found
