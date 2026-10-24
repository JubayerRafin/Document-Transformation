"""Conservative footer space and indivisible ISO date layout rules."""
import re
from datetime import date


def join_split_dates(document):
    """Join a two-digit date continuation only immediately below a plain block."""
    changes = []
    for page in document.page:
        removed = set()
        for p in list(page.pdf_paragraph):
            text = p.unicode or ""
            match = re.search(r"(?<!\d)(\d{4}-\d{2}-)$", text.rstrip())
            b = p.box
            if not match or not b or p.layout_label == 'table':
                continue
            candidates = [q for q in page.pdf_paragraph if q is not p and id(q) not in removed
                          and q.box and q.layout_label != 'table'
                          and re.fullmatch(r'\s*\d{2}\s*', q.unicode or '')
                          and abs(q.box.x-b.x) <= 3
                          and -1 <= b.y-q.box.y2 <= max(b.y2-b.y, 12)]
            if len(candidates) != 1:
                continue
            q = candidates[0]
            try:
                date.fromisoformat(match[1]+q.unicode.strip())
            except ValueError:
                continue
            comps = p.pdf_paragraph_composition
            from transformation.pdf.source_grouping import composition_text
            rendered=composition_text(p)
            tail=composition_text(q)
            # Preserve complete rich character groups; no mathematical/vector
            # formula is flattened into prose to make a date match.
            plain=all(getattr(c,'pdf_same_style_unicode_characters',None) is not None for c in comps)
            if rendered is None or not rendered.rstrip().endswith(match[1]):continue
            if not plain and (tail is None or tail.strip()!=q.unicode.strip()):continue
            # Only combine when the entire intervening area is clear of text.
            if any(o is not p and o is not q and o.box
                   and min(o.box.x2,b.x2)>max(o.box.x,b.x)
                   and min(o.box.y2,b.y)>max(o.box.y,q.box.y)
                   for o in page.pdf_paragraph):
                continue
            if plain:
                last=comps[-1].pdf_same_style_unicode_characters
                last.unicode=last.unicode.rstrip()+q.unicode.strip()
            else:
                from copy import deepcopy
                comps.extend(deepcopy(q.pdf_paragraph_composition))
            p.unicode = text.rstrip()+q.unicode.strip()
            b.y = min(b.y,q.box.y)
            removed.add(id(q))
            changes.append({'page':page.page_number,'paragraph_id':p.debug_id,
                            'continuation_id':q.debug_id,'reason':'join_split_iso_date'})
        page.pdf_paragraph[:] = [p for p in page.pdf_paragraph if id(p) not in removed]
    return changes

DATE = re.compile(r"(?<![A-Za-z0-9])\d{4}-\d{2}-\d{2}(?![A-Za-z0-9])")


def unit_text(unit):
    text=unit.try_get_unicode()
    if text is not None:return text
    formula=getattr(unit,'formular',None)
    if formula is not None and not getattr(formula,'pdf_curve',[]) and not getattr(formula,'pdf_form',[]):
        return ''.join(c.char_unicode or '' for c in getattr(formula,'pdf_character',[]))
    return ''


def atomic_ranges(text,values=()):
    from transformation.llm.protected_text import protect_values
    _,tokens=protect_values(text,values)
    ranges=[];cursor=0
    for value in tokens.values():
        offset=text.find(value,cursor)
        if offset>=0:ranges.append((offset,offset+len(value)));cursor=offset+len(value)
    return ranges


def atomic_tokens(text):
    """Keep literal spans, including timestamps, intact during OCR wrapping."""
    ranges=atomic_ranges(text);result=[];cursor=0
    for start,end in ranges:
        result.extend(re.findall(r'[\u3000-\u9fff\uac00-\ud7af]|[^\s\u3000-\u9fff\uac00-\ud7af]+|[ \t]+|\n',text[cursor:start]))
        result.append(text[start:end]);cursor=end
    result.extend(re.findall(r'[\u3000-\u9fff\uac00-\ud7af]|[^\s\u3000-\u9fff\uac00-\ud7af]+|[ \t]+|\n',text[cursor:]))
    return result


def heading_slots(document, changes, source_images=None):
    """Use clear horizontal space without lowering headings toward obstacles."""
    slots = {}
    for page in document.page:
        crop = getattr(getattr(page, 'cropbox', None), 'box', None)
        for change in changes:
            if change['page'] != page.page_number or change['reason'] not in {
                'text_above_image_shift', 'text_above_table_shift'
            }:
                continue
            left, bottom, right, top = change['after']
            limit = min(right + (right-left), crop.x2-2) if crop else right
            obstacles = [p for p in page.pdf_paragraph if p.debug_id != change['paragraph_id']]
            for attr in ('pdf_figure', 'pdf_curve', 'pdf_form'):
                obstacles.extend(getattr(page, attr, []) or [])
            obstacles.extend((source_images or {}).get(page.page_number, []))
            for obstacle in obstacles:
                b = getattr(obstacle, 'box', None)
                if b and min(b.y2,top)>max(b.y,bottom) and b.x2>right:
                    limit = min(limit, max(right,b.x-2))
            slots[(page.page_number+1, change['paragraph_id'])] = {
                'bbox':[left,bottom,max(right,limit),top]
            }
    return slots


def mark_date_units(units, protected_values=()):
    parts = [unit_text(u) for u in units]
    text = "".join(parts)
    ranges = atomic_ranges(text,protected_values)
    for value in protected_values:
        if value and not any(c.isspace() for c in value):
            ranges.extend(m.span() for m in re.finditer(re.escape(value), text))
    offset = 0
    for unit, part in zip(units, parts):
        if any(offset<end and offset+len(part)>start for start, end in ranges):
            unit.can_break_line_cache = False
        offset += len(part)
    return bool(ranges)


def widen_page_footers(document):
    changes = []
    for page in document.page:
        crop = getattr(getattr(page, "cropbox", None), "box", None)
        if crop is None:
            continue
        for p in page.pdf_paragraph:
            b = p.box
            if not b or not re.fullmatch(r"\s*(?:제\s*)?\d+\s*페이지\s*", p.unicode or ""):
                continue
            if b.y2 > crop.y + (crop.y2 - crop.y) * .08:
                continue
            center = (b.x + b.x2) / 2
            left, right = max(crop.x + 2, center - 36), min(crop.x2 - 2, center + 36)
            if right-left <= b.x2-b.x:
                continue
            if any(o is not p and o.box and min(o.box.y2,b.y2)>max(o.box.y,b.y)
                   and min(o.box.x2,right)>max(o.box.x,left) for o in page.pdf_paragraph):
                continue
            changes.append({"page": page.page_number, "paragraph_id": p.debug_id,
                            "reason": "page_footer_width", "before": [b.x,b.y,b.x2,b.y2],
                            "after": [left,b.y,right,b.y2]})
            b.x, b.x2 = left, right
    return changes


def install_date_line_breaks(Typesetting, protected_values=()):
    if getattr(Typesetting, "_transformx_date_breaks", False):
        return
    original = Typesetting._layout_typesetting_units

    def layout(self, units, box, scale, line_skip, paragraph, use_english_line_break=True):
        has_date = mark_date_units(units, protected_values)
        placed, fits = original(self, units, box, scale, line_skip, paragraph,
                               use_english_line_break=True if has_date else use_english_line_break)
        # Even the native character-overflow fallback must not split a date.
        if has_date:
            parts = [unit_text(u) for u in placed]
            text = ''.join(parts)
            for start,end in atomic_ranges(text,protected_values):
                offset, positions = 0, []
                for u, part in zip(placed, parts):
                    if offset<end and offset+len(part)>start:
                        positions.append(u.box.y)
                    offset += len(part)
                if positions and max(positions)-min(positions) > 1:
                    fits = False
        return placed, fits

    Typesetting._layout_typesetting_units = layout
    Typesetting._transformx_date_breaks = True
