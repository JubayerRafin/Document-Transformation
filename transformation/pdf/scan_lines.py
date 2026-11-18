"""Join OCR pieces of one printed line before a scanned page is rebuilt as native text.

The OCR detector often splits one line into pieces: letter-spaced capitals ("BEFORE" / "YOU" / "GO"),
a heading over a photo ("Celebrations" / "Worth Planning" / "Around"), a subtitle at a style change.
Each piece became its own native line, so BabelDOC translated each alone ("YOU" -> "귀하") and set
them at different sizes (scanned slides, 2026-11-18). Pieces are joined only when they share a
line, are close together with clean background between them, are not separated by a ruled line,
and were each read reliably; table cells (wide gaps or cell borders) and lines with an unread
symbol between the pieces ("2026 · Global Edition") stay apart.
"""
import re

import numpy as np
import pymupdf

GAP = 1.5          # at most this many line heights between pieces (spaced capitals reach about 0.9)
OVERLAP = 0.6      # vertical overlap, as a share of the shorter piece
HEIGHTS = 1.6      # height ratio of the pieces (descenders and capitals vary)


def _clean_gap(a, b, pixels, rules):
    """True when nothing but background lies between two pieces on the same line."""
    x0, x1 = int(np.ceil(a.x1)), int(np.floor(b.x0))
    y0, y1 = int(np.ceil(max(a.y0, b.y0))), int(np.floor(min(a.y1, b.y1)))
    if x1 - x0 < 1 or y1 - y0 < 2:
        return True               # touching or overlapping pieces
    if rules[y0:y1, x0:x1].any():
        return False
    def busy(top, bottom):
        strip = pixels[max(0, top):max(0, bottom), x0:x1].astype(float)
        if not strip.size:
            return None
        background = np.median(strip.reshape(-1, 3), axis=0)
        return float(np.mean(np.max(np.abs(strip - background), axis=2) > 35))
    inside = busy(y0, y1)
    if inside < 0.02:
        return True
    # On a photo or gradient the gap is never plain: compare it with the image just above and below
    # the line. A missed symbol between the pieces makes the gap busier than its surroundings.
    height = y1 - y0
    around = [v for v in (busy(y0 - height - 2, y0 - 2), busy(y1 + 2, y1 + height + 2)) if v is not None]
    return bool(around) and inside <= min(around) + 0.03


def _joinable(a, b, pixels, rules):
    ra, rb = pymupdf.Rect(a['bbox']), pymupdf.Rect(b['bbox'])
    ha, hb = ra.height, rb.height
    if min(ha, hb) <= 0 or max(ha, hb) / min(ha, hb) > HEIGHTS:
        return False
    if min(ra.y1, rb.y1) - max(ra.y0, rb.y0) < OVERLAP * min(ha, hb):
        return False
    gap = rb.x0 - ra.x1
    height = min(ha, hb)
    return -0.6 * height <= gap <= GAP * height and _clean_gap(ra, rb, pixels, rules)


def join_line_pieces(regions, mapping, pixels, rules, reliable):
    """(regions, mapping) with same-line pieces joined left to right. reliable(region) says whether
    a piece was read well enough to join; others are left as they are."""
    order = sorted(range(len(regions)), key=lambda i: (pymupdf.Rect(regions[i]['bbox']).x0))
    used, joined = set(), []
    for i in order:
        if i in used:
            continue
        current = dict(regions[i])
        used.add(i)
        if current.get('text') and reliable(current) and current['id'] in mapping:
            pieces = [regions[i]]
            while True:
                tail = pieces[-1]
                candidates = [j for j in order if j not in used and regions[j].get('text') and regions[j]['id'] in mapping
                              and reliable(regions[j]) and pymupdf.Rect(regions[j]['bbox']).x0 > pymupdf.Rect(tail['bbox']).x0
                              and _joinable(tail, regions[j], pixels, rules)]
                if not candidates:
                    break
                nearest = min(candidates, key=lambda j: pymupdf.Rect(regions[j]['bbox']).x0)
                pieces.append(regions[nearest]); used.add(nearest)
            if len(pieces) > 1:
                box = pymupdf.Rect(pieces[0]['bbox'])
                for piece in pieces[1:]:
                    box |= pymupdf.Rect(piece['bbox'])
                current = dict(pieces[0], bbox=list(box), text=' '.join(p['text'].strip() for p in pieces),
                               metadata=dict(pieces[0].get('metadata', {}), joined_pieces=[p['id'] for p in pieces],
                                             joined_boxes=[list(p['bbox']) for p in pieces]))
                mapping = dict(mapping)
                mapping[current['id']] = {'source_text': current['text'],
                                          'text': ' '.join(mapping[p['id']]['text'].strip() for p in pieces)}
        joined.append(current)
    # Keep the original reading order (by the first piece's position in the input).
    position = {r['id']: k for k, r in enumerate(regions)}
    joined.sort(key=lambda r: position[r['id']])
    return joined, mapping


# A line continues on the next one only with clear evidence of a wrap: the next line starts in lower
# case, or this line ends in a word or sign that cannot end a phrase. Title-case wraps without such
# evidence ("Travel Smart, Travel" / "Kindly") stay separate: joining bullets, table rows or fields
# by mistake would be worse than translating a wrapped title line by line.
CONNECTORS = {'and', 'or', 'of', 'the', 'to', 'for', 'with', 'in', 'a', 'an', 'on', 'at', 'by', 'from', 'as', 'nor'}
LINE_GAP = 0.6     # at most this many line heights between the lines
SAME_INK = 0.25    # colours (0-1 RGB) closer than this are the same ink


HANGUL = re.compile(r'[가-힣]')
NEW_ITEM = re.compile(r'^(?:[•·▪\-–*]|\d+[.)])')


def _continues(upper, lower):
    a, b = upper['text'].strip(), lower['text'].strip()
    if not a or not b or ':' in a or ':' in b or a[-1] in '.!?;:。':
        return False
    # Korean has no lower case: a sentence line that does not end the sentence continues below, unless
    # the next line starts a new bullet or numbered item. Short lines (headings) never continue.
    if HANGUL.search(a) and HANGUL.search(b):
        return len(a) >= 12 and not NEW_ITEM.match(b)
    last = a.split()[-1].lower()
    return b[0].islower() or a[-1] in ',&-–' or last in CONNECTORS


def _wrapped(upper, lower, rules, inks):
    ra, rb = pymupdf.Rect(upper['bbox']), pymupdf.Rect(lower['bbox'])
    height = min(ra.height, rb.height)
    if height <= 0 or max(ra.height, rb.height) / height > 1.3:
        return False
    if not 0 <= rb.y0 - ra.y1 <= LINE_GAP * height:
        return False
    if abs(ra.x0 - rb.x0) > 0.6 * height and abs((ra.x0 + ra.x1) / 2 - (rb.x0 + rb.x1) / 2) > 0.6 * height:
        return False
    if np.linalg.norm(np.subtract(inks[0], inks[1])) > SAME_INK:
        return False
    x0, x1 = int(max(ra.x0, rb.x0)), int(min(ra.x1, rb.x1))
    if x1 > x0 and rules[int(ra.y1):int(np.ceil(rb.y0)) + 1, x0:x1].any():
        return False
    return _continues(upper, lower)


def join_wrapped_lines(regions, mapping, rules, reliable, styles):
    """(regions, mapping, styles) with a line and its wrapped continuation below joined into one
    paragraph. Each line keeps its own box (metadata wrapped_lines) and is drawn there as before;
    only translation and the locked paragraph area cover both."""
    order = sorted(range(len(regions)), key=lambda i: pymupdf.Rect(regions[i]['bbox']).y0)
    used, out, out_styles = set(), [], []
    ok = lambda r: r.get('text') and r['id'] in mapping and reliable(r)
    for i in order:
        if i in used:
            continue
        used.add(i)
        lines = [i]
        while ok(regions[lines[-1]]):
            tail = lines[-1]
            below = [j for j in order if j not in used and ok(regions[j])
                     and _wrapped(regions[tail], regions[j], rules, (styles[tail]['ink'], styles[j]['ink']))]
            if len(below) != 1:
                break
            lines.append(below[0]); used.add(below[0])
        current = dict(regions[i])
        if len(lines) > 1:
            members = [regions[k] for k in lines]
            box = pymupdf.Rect(members[0]['bbox'])
            for m in members[1:]:
                box |= pymupdf.Rect(m['bbox'])
            text = members[0]['text'].strip()
            for m in members[1:]:
                text += ('' if text.endswith('-') else ' ') + m['text'].strip()
            pieces = []
            for m in members:
                pieces.extend(m.get('metadata', {}).get('joined_boxes') or [list(m['bbox'])])
            current = dict(members[0], bbox=list(box), text=text,
                           metadata=dict(members[0].get('metadata', {}), joined_boxes=pieces,
                                         wrapped_lines=[{'id': m['id'], 'bbox': list(m['bbox']), 'text': m['text'].strip()} for m in members]))
            mapping = dict(mapping)
            mapping[current['id']] = {'source_text': text, 'text': '\n'.join(mapping[m['id']]['text'].strip() for m in members)}
        out.append(current); out_styles.append(styles[i])
    position = {r['id']: k for k, r in enumerate(regions)}
    paired = sorted(zip(out, out_styles), key=lambda p: position[p[0]['id']])
    return [p[0] for p in paired], mapping, [p[1] for p in paired]


def regroup_for_translations(regions, mapping):
    """Regions regrouped to match translations made for joined lines.

    The text of a scanned figure is translated per joined line (join_line_pieces, join_wrapped_lines),
    but the step that draws the translations back reads the figure's original OCR lines, so a joined
    line found no translation with the same source text and kept its source pixels (a German label
    left in English, 2026-11-18). Here the OCR lines whose texts, in reading order, make up exactly a
    translation's source text become one region again. Nothing else changes: a region with its own
    exact translation is left alone."""
    squeeze = lambda text: ''.join((text or '').split())
    by_id = {r['id']: r for r in regions}
    used, merged = set(), {}
    for key, entry in mapping.items():
        first = by_id.get(key)
        if first is None or squeeze(entry.get('source_text')) == squeeze(first.get('text')):
            continue
        target = squeeze(entry.get('source_text'))
        if not target.startswith(squeeze(first.get('text'))) or not first.get('text'):
            continue
        members, built = [first], squeeze(first['text'])
        while built != target:
            rest = target[len(built):]
            options = [r for r in regions if r['id'] not in used and r not in members and r.get('text')
                       and rest.startswith(squeeze(r['text'])) and squeeze(r['text'])
                       and (r['id'] not in mapping or squeeze(mapping[r['id']].get('source_text')) != squeeze(r['text']))]
            if not options:
                break
            # the nearest line after the last member: to the right on the same line, else just below
            last = pymupdf.Rect(members[-1]['bbox'])
            nearest = min(options, key=lambda r: (abs(pymupdf.Rect(r['bbox']).y0 - last.y0) > last.height * .5,
                                                  abs(pymupdf.Rect(r['bbox']).y0 - last.y1), abs(pymupdf.Rect(r['bbox']).x0 - last.x0)))
            members.append(nearest); built += squeeze(nearest['text'])
        if built != target or len(members) < 2:
            continue
        box = pymupdf.Rect(members[0]['bbox'])
        for m in members[1:]:
            box |= pymupdf.Rect(m['bbox'])
        merged[key] = dict(first, bbox=list(box), text=entry['source_text'],
                           metadata=dict(first.get('metadata', {}), regrouped_lines=[m['id'] for m in members]))
        used.update(m['id'] for m in members)
    return [merged.get(r['id'], r) for r in regions if r['id'] in merged or r['id'] not in used]
