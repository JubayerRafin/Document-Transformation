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
                from transformation.pdf.ir_json import ir_copy
                comps.extend(ir_copy(q.pdf_paragraph_composition))
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


def _font_size(paragraph):
    for composition in paragraph.pdf_paragraph_composition or []:
        line = getattr(composition, 'pdf_line', None)
        for char in (getattr(line, 'pdf_character', None) or []) if line else []:
            style = getattr(char, 'pdf_style', None)
            if style and style.font_size:
                return float(style.font_size)
    style = getattr(paragraph, 'pdf_style', None)
    return float(style.font_size) if style and getattr(style, 'font_size', None) else None


def _free_edge(box, size, others, shapes, page_edge):
    """How far right box can grow: to the right edge of its column (paragraphs starting at its left
    edge within six lines above or below; twice its width without one), before anything to its
    right on its line, and inside a panel or cell drawn around it. Boxes are (x, y, x2, y2)."""
    left, bottom, right, top = box
    column = [o[2] for o in others if abs(o[0] - left) <= size and max(o[1] - top, bottom - o[3]) <= 6 * size]
    limit = min(max(column) if column else right + (right - left), page_edge)
    for x, y, x2, y2 in others:
        # Anything on its line that starts after it, also a piece it already overlaps: a Hindi word
        # split into overlapping pieces grew one piece over the next ("Sure" over "Hours", 2026-11-21).
        if min(y2, top) > max(y, bottom) and x > left + 1:
            limit = min(limit, max(right, x - 2))
    for x, y, x2, y2 in shapes:
        if not min(y2, top) > max(y, bottom):
            continue
        if x >= right - 1:                                   # something to the right on this line
            limit = min(limit, x - 2)
        elif x <= left + 1 and x2 >= right - 1 and y <= bottom + 1 and y2 >= top - 1:
            limit = min(limit, x2 - 2)                       # a panel or cell around it
        elif x2 > right:                                     # a shape crossing its line further right
            limit = min(limit, max(right, x - 2))
    return limit, bool(column)


def widen_single_lines(document, source_images=None, skip=()):
    """Let a one-line paragraph use the free space of its column.

    A translation longer than its source ("계약 조건" -> "Contract Terms", "Finanzlage" ->
    "Financial Position") was fitted into the source's short box and drawn at a third of its size
    (Korean and German reports, 2026-11-21). A left-aligned line now reaches the right edge of its
    column (_free_edge); a right-aligned one, as in Arabic documents, the left edge."""
    changes = []
    for page in document.page:
        crop = getattr(getattr(page, 'cropbox', None), 'box', None)
        paragraphs = [p for p in page.pdf_paragraph if getattr(p, 'box', None)]
        shapes = [getattr(o, 'box', None) for attr in ('pdf_figure', 'pdf_curve', 'pdf_form')
                  for o in (getattr(page, attr, None) or [])]
        shapes.extend(getattr(o, 'box', None) for o in (source_images or {}).get(page.page_number, []))
        shapes = [(b.x, b.y, b.x2, b.y2) for b in shapes if b is not None]
        for paragraph in paragraphs:
            b = paragraph.box
            size = _font_size(paragraph)
            if (paragraph.debug_id in skip or getattr(paragraph, 'vertical', False) or not size
                    or b.y2 - b.y > 1.8 * size or b.x2 - b.x <= 0):
                continue
            others = [(o.box.x, o.box.y, o.box.x2, o.box.y2) for o in paragraphs if o is not paragraph]
            box = (b.x, b.y, b.x2, b.y2)
            limit, aligned = _free_edge(box, size, others, shapes, crop.x2 - 2 if crop else float('inf'))
            if aligned and limit > b.x2 + 1:
                changes.append({'page': page.page_number, 'paragraph_id': paragraph.debug_id,
                                'reason': 'single_line_widened', 'before': list(box),
                                'after': [b.x, b.y, limit, b.y2]})
                b.x2 = limit
                continue
            # Mirrored: the same rules for a line aligned with its column's right edge.
            flip = lambda r: (-r[2], r[1], -r[0], r[3])
            edge, right_aligned = _free_edge(flip(box), size, [flip(o) for o in others], [flip(s) for s in shapes],
                                             -(crop.x + 2) if crop else float('inf'))
            if right_aligned and -edge < b.x - 1:
                changes.append({'page': page.page_number, 'paragraph_id': paragraph.debug_id,
                                'reason': 'single_line_widened_left', 'before': list(box),
                                'after': [-edge, b.y, b.x2, b.y2]})
                b.x = -edge
            elif limit > b.x2 + 1:
                changes.append({'page': page.page_number, 'paragraph_id': paragraph.debug_id,
                                'reason': 'single_line_widened', 'before': list(box),
                                'after': [b.x, b.y, limit, b.y2]})
                b.x2 = limit
    return changes


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


def install_whole_word_breaks(Typesetting):
    """Keep words whole when they fit at the end of a line.

    BabelDOC 0.6.4 checks each unit with current_x + unit_width + _get_width_before_next_break_point(units[i:])
    > box.x2, but that width already includes the current unit, so it is counted twice. At every
    letter inside a word the check becomes "word end + this letter's width", and a word ending
    within one letter of the edge was split at its widest letter without a hyphen ("seule | ment",
    "cert | ains", "do | micilio"; French and Spanish, 2026-11-17). The width here is the rest of
    the word after the current unit, as the check intends."""
    if getattr(Typesetting, '_transformx_whole_words', False):
        return

    def width_after(self, typesetting_units, scale):
        if not typesetting_units or typesetting_units[0].can_break_line:
            return 0
        total = 0
        for unit in typesetting_units[1:]:
            if unit.can_break_line:
                break
            total += unit.width
        return total * scale

    Typesetting._get_width_before_next_break_point = width_after
    Typesetting._transformx_whole_words = True


def is_hangul(character):
    """Korean letters: syllables, jamo and compatibility jamo."""
    code = ord(character) if character else 0
    return (0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F
            or 0xA960 <= code <= 0xA97F or 0xD7B0 <= code <= 0xD7FF)


def install_korean_word_breaks(TypesettingUnit):
    """Break Korean lines between words, not inside them.

    BabelDOC 0.6.4 lets a line break after any Chinese, Japanese or Korean character. Korean
    separates words with spaces like English, and a word split over two lines ("사용 가 | 능.")
    reads as two words (scanned caution label, 2026-11-21). Hangul letters are no break point now,
    like Latin letters; a space still is. A single word wider than the line still breaks: BabelDOC
    retries such a paragraph with character breaks (use_english_line_break=False)."""
    if getattr(TypesettingUnit, '_transformx_korean_words', False):
        return
    original = TypesettingUnit.calc_can_break_line

    def can_break(self):
        text = self.try_get_unicode()
        if text and len(text) == 1 and is_hangul(text):
            return False
        return original(self)

    TypesettingUnit.calc_can_break_line = can_break
    TypesettingUnit._transformx_korean_words = True


INDENT_EMS = 0.5  # a first line starting this many ems right of the paragraph is indented


def install_real_first_line_indents(ParagraphFinder):
    """Indent a paragraph's first line only when the source really indents it.

    BabelDOC 0.6.4 marks a paragraph as indented when its first character starts more than 1 pt right
    of the paragraph's left edge, and then sets the translated first line four spaces in. Lines read
    from a scan start a pixel or two apart, so a label's second line ("Badge EMP-2024-8831") came out
    pushed right inside its box (2026-11-21). An indent now needs half an em of the first
    character's size; typographic indents are about one em or more."""
    if getattr(ParagraphFinder, '_transformx_real_indents', False):
        return
    original = ParagraphFinder.update_paragraph_data

    def update(self, paragraph, *args, **kwargs):
        result = original(self, paragraph, *args, **kwargs)
        if getattr(paragraph, 'first_line_indent', False):
            try:
                first = paragraph.pdf_paragraph_composition[0].pdf_line.pdf_character[0]
                offset = first.visual_bbox.box.x - paragraph.box.x
                size = (first.pdf_style.font_size if first.pdf_style else None) or 0
            except (AttributeError, IndexError, TypeError):
                return result
            if size > 0 and offset < INDENT_EMS * size:
                paragraph.first_line_indent = False
        return result

    ParagraphFinder.update_paragraph_data = update
    ParagraphFinder._transformx_real_indents = True


ATTACHING_PUNCTUATION = set('.,:;!?)]}%’”') | set('([{“‘')


def install_attached_punctuation(TypesettingUnit):
    """No extra gap between Chinese/Japanese/Korean text and punctuation that attaches to it.

    BabelDOC 0.6.4 adds half a space wherever CJK and non-CJK units meet, except for five
    full-width marks, so Korean sentences were drawn as "했습니다 ." and labels as "직원 :" (2026-11-17)
    although the translated text had no space there. ASCII punctuation and brackets are added to
    that exception list; spaces typed in the translation are unchanged."""
    if getattr(TypesettingUnit, '_transformx_attached_punctuation', False):
        return
    original = TypesettingUnit.calc_mixed_character_blacklist

    def blacklist(self):
        return original(self) or (self.try_get_unicode() or '') in ATTACHING_PUNCTUATION

    TypesettingUnit.calc_mixed_character_blacklist = blacklist
    TypesettingUnit._transformx_attached_punctuation = True


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
                offset, positions, heights = 0, [], []
                for u, part in zip(placed, parts):
                    if offset<end and offset+len(part)>start:
                        positions.append((u.box.y + u.box.y2) / 2)
                        heights.append(u.box.y2 - u.box.y)
                    offset += len(part)
                # On another line means at least half a line lower. A protected number BabelDOC keeps
                # as a formula sits on its own baseline, a point or two off the letters around it,
                # and with "> 1 pt" a whole "HR-2024-00417 | ..." line never fitted and was drawn at
                # 3 pt in every language (2026-11-17).
                if positions and max(positions)-min(positions) > max(1.0, 0.5 * max(heights)):
                    fits = False
        return placed, fits

    Typesetting._layout_typesetting_units = layout
    Typesetting._transformx_date_breaks = True
