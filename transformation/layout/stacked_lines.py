"""Join Chinese and Japanese text that a narrow column wrapped into separate one-line paragraphs.

A word in a narrow table cell wraps one or two characters per line ("热 / 敏 / 标 / 签"). BabelDOC 0.6.4
made each line its own paragraph (layout "fallback_line"), and each was translated alone: "safety
gloves" (安全手套) came out as "All / Hand / Set" and "thermal labels" as "Hot / 敏 / Label / Sign"
(Chinese newsletter, 2026-11-21). Lines are joined back into one paragraph when they are stacked in
one column: left edges within half a character, the same size, the gap between them under 0.8 of a
character's height (the gap between table rows or paragraphs is larger), Han or kana text only (no
spaces are lost: these scripts put none between words), and the upper line not ending a sentence.
"""

SENTENCE_END = set('。．！？!?：:；;')
GAP = 0.8          # largest gap between stacked lines, in character heights
ALIGN = 0.5        # largest difference of their left edges, in character heights
SAME_SIZE = 0.10   # largest relative difference of their sizes


def is_compact_cjk(text):
    """Han, kana and CJK punctuation only (no letters of space-separated scripts)."""
    letters = [c for c in text if not c.isspace()]
    if not letters:
        return False
    for c in letters:
        code = ord(c)
        if not (0x3000 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
                or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFF65 or 0x31F0 <= code <= 0x31FF):
            return False
    return True


def _lines(paragraph):
    return [c.pdf_line for c in (paragraph.pdf_paragraph_composition or []) if getattr(c, 'pdf_line', None)]


def _size(paragraph):
    for line in _lines(paragraph):
        for char in line.pdf_character or []:
            style = getattr(char, 'pdf_style', None)
            if style and style.font_size:
                return float(style.font_size)
    return None


def _text(paragraph):
    return ''.join(getattr(ch, 'char_unicode', '') or '' for line in _lines(paragraph) for ch in (line.pdf_character or []))


def mergeable(upper, lower):
    """Whether `lower` continues `upper` as the next line of the same narrow-column text."""
    if upper is lower or getattr(upper, 'vertical', False) or getattr(lower, 'vertical', False):
        return False
    if getattr(upper, 'xobj_id', None) != getattr(lower, 'xobj_id', None):
        return False
    if getattr(upper, 'layout_label', None) != getattr(lower, 'layout_label', None):
        return False
    a, b = _size(upper), _size(lower)
    if not a or not b or abs(a - b) > SAME_SIZE * max(a, b):
        return False
    upper_lines = _lines(upper)
    if not upper_lines or not _lines(lower):
        return False
    last = upper_lines[-1].box
    first = _lines(lower)[0].box
    height = max(a, last.y2 - last.y, first.y2 - first.y)
    gap = last.y - first.y2  # PDF y grows upwards: the lower line's top is below the upper line's bottom
    if not -0.2 * height <= gap <= GAP * height:
        return False
    if abs(last.x - first.x) > ALIGN * height:
        return False
    upper_text, lower_text = _text(upper).strip(), _text(lower).strip()
    if not is_compact_cjk(upper_text) or not is_compact_cjk(lower_text) or upper_text[-1:] in SENTENCE_END:
        return False
    return True


def join_stacked_lines(paragraphs, update):
    """Merge stacked one-column lines in place; `update(paragraph)` recomputes a merged paragraph's
    box and text. Returns how many paragraphs were joined into others."""
    joined = 0
    changed = True
    while changed:
        changed = False
        for upper in list(paragraphs):
            if upper not in paragraphs:
                continue
            below = [p for p in paragraphs if mergeable(upper, p)]
            if not below:
                continue
            lower = min(below, key=lambda p: _lines(upper)[-1].box.y - _lines(p)[0].box.y2)
            upper.pdf_paragraph_composition.extend(lower.pdf_paragraph_composition)
            paragraphs.remove(lower)
            update(upper)
            joined += 1
            changed = True
    return joined


RULE_WIDTH = 1.5   # a vertical rule (cell border) is at most this wide, in points


def vertical_rules(page):
    """Boxes of thin vertical lines on the page (table cell borders)."""
    rules = []
    for curve in getattr(page, 'pdf_curve', None) or []:
        box = getattr(curve, 'box', None)
        if box is not None and box.x2 - box.x <= RULE_WIDTH and box.y2 - box.y > 2 * (box.x2 - box.x):
            rules.append(box)
    return rules


def _split_index(chars, rules, line_box):
    """Index of the first character after a vertical rule that crosses the line between two of its
    characters, with letters on both sides; or None."""
    height = line_box.y2 - line_box.y
    for i in range(len(chars) - 1):
        left, right = chars[i].visual_bbox.box, chars[i + 1].visual_bbox.box
        for rule in rules:
            centre = (rule.x + rule.x2) / 2
            covers = min(rule.y2, line_box.y2) - max(rule.y, line_box.y) >= 0.8 * height
            if covers and left.x2 - 0.5 <= centre <= right.x + 0.5:
                before = ''.join(c.char_unicode or '' for c in chars[:i + 1]).strip()
                after = ''.join(c.char_unicode or '' for c in chars[i + 1:]).strip()
                if before and after:
                    return i + 1
    return None


def split_at_rules(page, update, make_id):
    """Split one-line paragraphs that run across a table cell border into one paragraph per cell.

    BabelDOC 0.6.4 joins characters on one line when the gap between them is small, also across a
    cell border: two header cells "数量" and "単価" became one paragraph "数量 単価", translated as
    "Qty Unit Price" and drawn in the first cell (Japanese invoice, 2026-11-21). Returns how many
    paragraphs were split off."""
    import copy
    rules = vertical_rules(page)
    if not rules:
        return 0
    added = 0
    index = 0
    while index < len(page.pdf_paragraph):
        paragraph = page.pdf_paragraph[index]
        lines = _lines(paragraph)
        compositions = paragraph.pdf_paragraph_composition or []
        if len(lines) == 1 and len(compositions) == 1 and not getattr(paragraph, 'vertical', False):
            chars = lines[0].pdf_character or []
            cut = _split_index(chars, rules, lines[0].box) if chars and lines[0].box else None
            if cut:
                right = copy.deepcopy(paragraph)
                right.debug_id = make_id(paragraph)
                right_line = right.pdf_paragraph_composition[0].pdf_line
                right_line.pdf_character = [c for c in right_line.pdf_character[cut:]]
                lines[0].pdf_character = chars[:cut]
                for p in (paragraph, right):
                    line = p.pdf_paragraph_composition[0].pdf_line
                    while line.pdf_character and not (line.pdf_character[-1].char_unicode or '').strip():
                        line.pdf_character.pop()
                    while line.pdf_character and not (line.pdf_character[0].char_unicode or '').strip():
                        line.pdf_character.pop(0)
                    update(p, line)
                page.pdf_paragraph.insert(index + 1, right)
                added += 1
                continue  # the left part may cross another rule
        index += 1
    return added


def install(ParagraphFinder):
    if getattr(ParagraphFinder, '_transformx_stacked_lines', False):
        return
    original = ParagraphFinder.process_page

    def process_page(self, page):
        result = original(self, page)
        counter = [0]

        def make_id(paragraph):
            counter[0] += 1
            return f'{paragraph.debug_id}-c{counter[0]}'

        def update(paragraph, line):
            self.update_line_data(line)
            self.update_paragraph_data(paragraph, update_unicode=True)

        changed = split_at_rules(page, update, make_id)
        changed += join_stacked_lines(page.pdf_paragraph, lambda p: self.update_paragraph_data(p, update_unicode=True))
        if changed:
            self._set_paragraph_render_order(page)
        return result

    ParagraphFinder.process_page = process_page
    ParagraphFinder._transformx_stacked_lines = True
