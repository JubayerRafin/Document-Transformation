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


def install(ParagraphFinder):
    if getattr(ParagraphFinder, '_transformx_stacked_lines', False):
        return
    original = ParagraphFinder.process_page

    def process_page(self, page):
        result = original(self, page)
        if join_stacked_lines(page.pdf_paragraph, lambda p: self.update_paragraph_data(p, update_unicode=True)):
            self._set_paragraph_render_order(page)
        return result

    ParagraphFinder.process_page = process_page
    ParagraphFinder._transformx_stacked_lines = True
