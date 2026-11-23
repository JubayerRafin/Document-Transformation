"""Join Chinese and Japanese text that a narrow column wrapped into separate one-line paragraphs.

A word in a narrow table cell wraps one or two characters per line ("热 / 敏 / 标 / 签"). BabelDOC 0.6.4
made each line its own paragraph (layout "fallback_line"), and each was translated alone: "safety
gloves" (安全手套) came out as "All / Hand / Set" and "thermal labels" as "Hot / 敏 / Label / Sign"
(Chinese newsletter, 2026-11-21). Lines are joined back into one paragraph when they are stacked in
one column: left edges within half a character, the same size, the gap between them under 0.8 of a
character's height (the gap between table rows or paragraphs is larger), Han, kana or Hangul text
only, and the upper line not ending a sentence. Korean narrow cells wrap the same way ("품 / 목" was
translated as "Product / Neck", Korean newsletter, 2026-11-21); its lines are joined without a space,
which may join two words, as the model reads "열감지라벨" correctly but not "Heat / 라 / Bell".
"""

import re
import unicodedata

SENTENCE_END = set('。．！？!?：:；;')
GAP = 0.8          # largest gap between stacked lines, in character heights
ALIGN = 0.5        # largest difference of their left edges, in character heights
SAME_SIZE = 0.10   # largest relative difference of their sizes


def is_compact_cjk(text):
    """Han, kana, Hangul and CJK punctuation only (no Latin or other letters)."""
    letters = [c for c in text if not c.isspace()]
    if not letters:
        return False
    for c in letters:
        code = ord(c)
        if not (0x3000 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
                or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFF65 or 0x31F0 <= code <= 0x31FF
                or 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F):
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


def _same_cell(upper, lower, rules):
    """Whether two stacked lines lie in one ruled table cell: a vertical rule on each side covering
    both lines, and no horizontal rule between them."""
    a, b = upper, lower
    top, bottom = max(a.y2, b.y2), min(a.y, b.y)
    left, right = min(a.x, b.x), max(a.x2, b.x2)
    vertical, horizontal = rules[0], rules[1]
    # A cell of a table found on the page (PyMuPDF, babeldoc_worker.source_table_cells) holding both
    # lines: cell borders drawn as boxes are no thin rules. A Hindi item name wrapped in its cell
    # ("सुरक्षा" / "दस्ताने") was two paragraphs, translated "Security" and "Safety Gloves" and drawn
    # over each other (2026-11-23).
    cells = rules[2] if len(rules) > 2 else ()
    if any(c.x - 1 <= left and right <= c.x2 + 1 and c.y - 1 <= bottom and top <= c.y2 + 1 for c in cells):
        return True
    covering = [r for r in vertical if r.y <= bottom + 1 and r.y2 >= top - 1]
    if not any(r.x2 <= left + 1 for r in covering) or not any(r.x >= right - 1 for r in covering):
        return False
    return not any(r.x <= left and r.x2 >= right and b.y2 - 1 <= (r.y + r.y2) / 2 <= a.y + 1 for r in horizontal)


def drawn_boxes(page):
    """Boxes of the page's drawings, each cut to the frame of the picture (form XObject) it belongs to.

    A drawing inside a picture can reach past the picture's frame, where it is clipped and never seen.
    A line of a printer drawing ran 160 pt below its picture, through a sentence above the next
    picture, and was taken for a table border that cut "Tray" into "Tr" | "ay" (HP manual,
    2026-11-23)."""
    import copy
    frames = {getattr(x, 'xobj_id', None): getattr(x, 'box', None) for x in getattr(page, 'pdf_xobject', None) or []}
    out = []
    for curve in getattr(page, 'pdf_curve', None) or []:
        box = getattr(curve, 'box', None)
        if box is None:
            continue
        frame = frames.get(getattr(curve, 'xobj_id', None))
        if frame is not None and frame.x2 > frame.x and frame.y2 > frame.y:
            x, y, x2, y2 = max(box.x, frame.x), max(box.y, frame.y), min(box.x2, frame.x2), min(box.y2, frame.y2)
            if x2 < x or y2 < y:
                continue
            if (x, y, x2, y2) != (box.x, box.y, box.x2, box.y2):
                box = copy.copy(box)
                box.x, box.y, box.x2, box.y2 = x, y, x2, y2
        out.append(box)
    return out


def horizontal_rules(page):
    """Boxes of thin horizontal lines on the page (table row borders)."""
    rules = []
    for box in drawn_boxes(page):
        if box.y2 - box.y <= RULE_WIDTH and box.x2 - box.x > 2 * (box.y2 - box.y):
            rules.append(box)
    return rules


def mergeable(upper, lower, rules=None):
    """Whether `lower` continues `upper` as the next line of the same narrow-column text: Chinese,
    Japanese or Korean text anywhere, other scripts only inside one ruled table cell ("परामर्श /
    घंटे" came out as "Consultation" and "Hours" drawn at two sizes, Hindi report, 2026-11-21)."""
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
    if not upper_text or not lower_text or upper_text[-1:] in SENTENCE_END:
        return False
    # A line that ends in a hyphen goes on below: a code wrapped in a narrow table cell
    # ("GQX-" / "2024-" / "99135") was three paragraphs, the cell could not be laid out and its
    # row came out in tiny type, in every language (newsletter, 2026-11-23).
    if upper_text.endswith('-') and not upper_text.endswith(' -') and upper_text[-2:-1].isalnum():
        return True
    # A list item below starts a paragraph of its own, in a cell too (split_at_list_markers).
    if _item_number(lower_text) is not None or (lower_text[:1] in TEXT_BULLETS and lower_text[1:2].isspace()):
        return False
    in_cell = bool(rules) and _same_cell(last, first, rules)
    if is_compact_cjk(upper_text) and is_compact_cjk(lower_text):
        return in_cell or not rules or not any(
            r.x <= min(last.x, first.x) and r.x2 >= max(last.x2, first.x2)
            and first.y2 - 1 <= (r.y + r.y2) / 2 <= last.y + 1 for r in rules[1])
    return in_cell and upper_text[-1:] != '.'


def join_stacked_lines(paragraphs, update, rules=None):
    """Merge stacked one-column lines in place; `update(paragraph)` recomputes a merged paragraph's
    box and text; rules: (vertical, horizontal) rule boxes of the page. Returns how many paragraphs
    were joined into others."""
    joined = 0
    changed = True
    while changed:
        changed = False
        for upper in list(paragraphs):
            if upper not in paragraphs:
                continue
            below = [p for p in paragraphs if mergeable(upper, p, rules)]
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
    for box in drawn_boxes(page):
        if box.x2 - box.x <= RULE_WIDTH and box.y2 - box.y > 2 * (box.x2 - box.x):
            rules.append(box)
    return rules


def _split_index(chars, rules, line_box):
    """Index of the first character after a vertical rule that crosses the line between two of its
    characters, with letters on both sides; or None."""
    height = line_box.y2 - line_box.y
    # Neighbouring visible characters: BabelDOC's space between two cells spans the whole gap, border
    # included, so a border lies between two letters, never between a letter and that space.
    visible = [i for i, c in enumerate(chars) if (c.char_unicode or '').strip() and getattr(c, 'visual_bbox', None)]
    for a, b in zip(visible, visible[1:]):
        left, right = chars[a].visual_bbox.box, chars[b].visual_bbox.box
        for rule in rules:
            centre = (rule.x + rule.x2) / 2
            covers = min(rule.y2, line_box.y2) - max(rule.y, line_box.y) >= 0.8 * height
            if covers and left.x2 - 0.5 <= centre <= right.x + 0.5:
                return a + 1
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


BADGE_SIZE = (6.0, 40.0)  # a callout circle or badge around a number or letter, in points


def badges(page):
    """Boxes of small, roughly square shapes that can hold a character: callout circles, numbered
    badges, keycaps."""
    out = []
    for box in drawn_boxes(page):
        w, h = box.x2 - box.x, box.y2 - box.y
        if BADGE_SIZE[0] <= w <= BADGE_SIZE[1] and BADGE_SIZE[0] <= h <= BADGE_SIZE[1] and 0.6 <= w / h <= 1.6:
            out.append(box)
    return out


def _badge_of(char, shapes):
    """The index of the shape a character sits inside (its centre in the shape, at least a third of its
    height), or None."""
    box = char.visual_bbox.box
    cx, cy, ch = (box.x + box.x2) / 2, (box.y + box.y2) / 2, box.y2 - box.y
    for i, s in enumerate(shapes):
        if s.x <= cx <= s.x2 and s.y <= cy <= s.y2 and ch >= 0.33 * (s.y2 - s.y):
            return i
    return None


def split_at_badges(page, update, make_id):
    """Split one-line paragraphs whose characters sit in different callout circles or badges.

    BabelDOC joined two callout numbers on one line ("3" and "4", each in its own circle) into one
    paragraph and drew it where the "3" was in the page's drawing order, before the second circle,
    which then covered the "4" (HP manual control-panel figure, 2026-11-21). Each badge's characters
    become their own paragraph and keep their own drawing order. Returns how many were split off."""
    import copy
    shapes = badges(page)
    if not shapes:
        return 0
    added = 0
    index = 0
    while index < len(page.pdf_paragraph):
        paragraph = page.pdf_paragraph[index]
        lines = _lines(paragraph)
        compositions = paragraph.pdf_paragraph_composition or []
        index += 1
        if len(lines) != 1 or len(compositions) != 1 or getattr(paragraph, 'vertical', False):
            continue
        chars = [c for c in (lines[0].pdf_character or []) if getattr(c, 'visual_bbox', None)]
        visible = [(i, _badge_of(c, shapes)) for i, c in enumerate(chars) if (c.char_unicode or '').strip()]
        if len({b for _, b in visible if b is not None}) < 2:
            continue
        cuts, previous = [], visible[0][1]
        for i, b in visible[1:]:
            if b != previous:
                cuts.append(i)
            previous = b
        groups, start = [], 0
        for cut in cuts + [len(chars)]:
            group = [c for c in chars[start:cut]]
            while group and not (group[-1].char_unicode or '').strip():
                group.pop()
            if group:
                groups.append(group)
            start = cut
        if len(groups) < 2:
            continue
        template = copy.deepcopy(paragraph)
        pieces = [paragraph] + [copy.deepcopy(template) for _ in groups[1:]]
        for piece in pieces[1:]:
            piece.debug_id = make_id(paragraph)
        for piece, group in zip(pieces, groups):
            line = copy.copy(compositions[0].pdf_line)
            line.pdf_character = group
            composition = copy.copy(compositions[0])
            composition.pdf_line = line
            piece.pdf_paragraph_composition = [composition]
            update(piece, line)
        page.pdf_paragraph[index:index] = pieces[1:]
        index += len(pieces) - 1
        added += len(pieces) - 1
    return added


MARKER_SIZE = 7.0  # a bullet drawn as a shape is at most this wide and high, in points


def list_markers(page):
    """Boxes of small, roughly square shapes: bullets drawn as graphics (a dot, a square)."""
    markers = []
    for box in drawn_boxes(page):
        w, h = box.x2 - box.x, box.y2 - box.y
        if 0.8 <= w <= MARKER_SIZE and 0.8 <= h <= MARKER_SIZE and 0.5 <= w / h <= 2:
            markers.append(box)
    return markers


def _visual_lines(chars):
    """Consecutive characters grouped into the lines they are drawn on (a new line where the text
    moves down by more than half the font size). The font size, not the drawn height: a period or an
    Arabic letter's dot is a fraction of it, and an Arabic list's "1." became a line of its own apart
    from its item's text (2026-11-23)."""
    lines = []
    for char in chars:
        box = char.visual_bbox.box
        font = getattr(getattr(char, 'pdf_style', None), 'font_size', 0) or 0
        middle, size = (box.y + box.y2) / 2, max(box.y2 - box.y, font, 1.0)
        if lines and abs(middle - lines[-1]['middle']) <= 0.5 * size:
            lines[-1]['chars'].append(char)
            lines[-1]['x'] = min(lines[-1]['x'], box.x)
            lines[-1]['x2'] = max(lines[-1]['x2'], box.x2)
            lines[-1]['y'], lines[-1]['y2'] = min(lines[-1]['y'], box.y), max(lines[-1]['y2'], box.y2)
        else:
            lines.append({'chars': [char], 'middle': middle, 'x': box.x, 'x2': box.x2, 'y': box.y, 'y2': box.y2})
    return lines


TEXT_BULLETS = set('•●○◦▪■□–—‣⁃∙')


# Followed by a space or directly by a letter: BabelDOC often stores no space after "1." (German
# manual, items stayed one paragraph, 2026-11-23). A digit after the dot is a decimal, not an item.
# A gap before the period may read as a space when letters are set apart ("1 . Die Prüfung", German
# manual, 2026-11-23), and the space after it may be missing ("1.Die").
ITEM_NUMBER = re.compile(r'(\d{1,2}|[a-z]) ?[.)](?=\s|[^\W\d_]|$)')


def _item_number(text):
    """The number of a numbered or lettered list item ("3. Revenue ..." -> 3, "b. Open ..." -> 2), or
    None. Lettered sub-steps of a manual ("a. Click Add ... b. Open ...") were joined into one
    paragraph like numbered ones (HP manual, 2026-11-23)."""
    match = ITEM_NUMBER.match(text)
    if not match:
        return None
    mark = match.group(1)
    return int(mark) if mark.isdigit() else ord(mark) - ord('a') + 1


def _line_text(line):
    """A visual line's text with spaces between its characters where BabelDOC stored none."""
    chars = line['chars']
    out = ''
    for i, char in enumerate(chars):
        text = getattr(char, 'char_unicode', '') or ''
        if i and out and not out[-1].isspace() and not text.isspace():
            gap = char.visual_bbox.box.x - chars[i - 1].visual_bbox.box.x2
            if gap > 0.25 * max(char.visual_bbox.box.y2 - char.visual_bbox.box.y, 1.0):
                out += ' '
        out += text
    return out.strip()


def split_at_list_markers(page, update, make_id):
    """Start a new paragraph at each line with a bullet shape just to its left.

    BabelDOC 0.6.4 made a bulleted list one paragraph when its items were set close together; the
    translation then flowed across the items and the later bullets were left empty (German and Hindi
    reports, 2026-11-21). Bullets drawn as small shapes mark where each item starts. Returns how many
    paragraphs were split off."""
    import copy
    markers = list_markers(page)
    added = 0
    index = 0
    while index < len(page.pdf_paragraph):
        paragraph = page.pdf_paragraph[index]
        compositions = paragraph.pdf_paragraph_composition or []
        index += 1
        if getattr(paragraph, 'vertical', False) or not compositions or any(not getattr(c, 'pdf_line', None) for c in compositions):
            continue
        chars = [ch for c in compositions for ch in (c.pdf_line.pdf_character or []) if getattr(ch, 'visual_bbox', None)]
        lines = _visual_lines(chars)
        if len(lines) < 2:
            continue
        starts = []
        for number, line in enumerate(lines):
            size = line['y2'] - line['y']
            for m in markers:
                centre = (m.y + m.y2) / 2
                # The bullet before the line, or after it for right-to-left text (Arabic report).
                beside = ((m.x2 <= line['x'] + 1 and line['x'] - m.x2 <= 3 * size)
                          or (m.x >= line['x2'] - 1 and m.x - line['x2'] <= 3 * size))
                if beside and line['y'] - 0.2 * size <= centre <= line['y2'] + 0.2 * size:
                    starts.append(number)
                    break
        # Bullets written as characters ("– Some servers ...", "○ From the drop-down ..."): when the
        # paragraph's first line starts with one, so does each later item. Three dash items of an HP
        # manual became one paragraph, failed its checks and stayed English (2026-11-21).
        first = _line_text(lines[0])
        if first[:1] in TEXT_BULLETS and first[1:2].isspace():
            starts.extend(n for n, line in enumerate(lines)
                          if _line_text(line)[:1] in TEXT_BULLETS and _line_text(line)[1:2].isspace())
        # Numbered items ("1. Any change ...", "2. Customer ..."): when the first line starts with a
        # number, each later line that starts at the same left edge with the next number in sequence
        # starts an item. A numbered list became one paragraph and its translation ran the items
        # together ("1. ... 2. ... 3. ..." on one block, 2026-11-21 competition set).
        number = _item_number(first)
        if number is not None:
            from transformation.pdf.unmapped_text import RTL
            # Right-to-left items line up on their right edge; their left ends are ragged (Arabic
            # manual: four numbered items stayed one paragraph, 2026-11-23).
            edge = 'x2' if RTL.search(first) else 'x'
            expected = number + 1
            for n, line in enumerate(lines[1:], 1):
                if (_item_number(_line_text(line)) == expected
                        and abs(line[edge] - lines[0][edge]) <= max(1.5, 0.25 * (lines[0]['y2'] - lines[0]['y']))):
                    starts.append(n)
                    expected += 1
        cuts = sorted(set(n for n in starts if n > 0))
        if not cuts:
            continue
        groups, previous = [], 0
        for cut in cuts + [len(lines)]:
            groups.append([ch for line in lines[previous:cut] for ch in line['chars']])
            previous = cut
        template = copy.deepcopy(paragraph)
        pieces = [paragraph]
        for _ in groups[1:]:
            piece = copy.deepcopy(template)
            piece.debug_id = make_id(paragraph)
            pieces.append(piece)
        for piece, group in zip(pieces, groups):
            line = copy.copy(compositions[0].pdf_line)
            line.pdf_character = group
            composition = copy.copy(compositions[0])
            composition.pdf_line = line
            piece.pdf_paragraph_composition = [composition]
            update(piece, line)
        page.pdf_paragraph[index:index] = pieces[1:]
        index += len(pieces) - 1
        added += len(pieces) - 1
    return added


def _shaped_script(text):
    from transformation.pdf.unmapped_text import _JOINED_SCRIPTS, RTL
    return bool(_JOINED_SCRIPTS.search(text) or RTL.search(text))


def _char_size(paragraph):
    """The font size of a paragraph's first character with one, or 0."""
    return next((getattr(getattr(c, 'pdf_style', None), 'font_size', 0) or 0 for c in _characters(paragraph)
                 if getattr(getattr(c, 'pdf_style', None), 'font_size', 0)), 0)


def join_overlapping_fragments(page, update):
    """Join one-line pieces of one Indic or Arabic line whose boxes overlap side by side.

    BabelDOC 0.6.4 split "परामर्श घंटे" in a table cell into three overlapping paragraphs
    ("परामर्श", "र्श घंट", "घंटे"); each was translated alone ("Consultation", "Sure", "Hours")
    and drawn over the others (Hindi report, 2026-11-21). Pieces of these scripts whose boxes
    overlap horizontally and share most of their height are parts of one line: they are joined and
    the line is read again as a whole (unmapped_text.repair_page). Text of other scripts, and pieces
    that only touch (0.1 pt or less), are left alone, except a right-to-left piece of at most three
    letters next to another one of the line: "ساعات استشا" | "ر" | "ة" (consulting hours, in an Arabic
    report's table cell) came apart where "ر" dips below the line, 1.6 pt between them, and "رة" was
    translated alone as "Rate" (2026-11-23). Such a short piece within a third of the font size is
    part of the same line, even when it is a word of its own ("في"), and the joined line is read
    again whole; it is not joined across a table rule.
    Returns how many pieces were joined into others."""
    from transformation.pdf.unmapped_text import RTL
    rules = None
    joined = 0
    changed = True
    while changed:
        changed = False
        items = [p for p in page.pdf_paragraph if getattr(p, 'box', None) and not getattr(p, 'vertical', False)
                 and _shaped_script(''.join(getattr(c, 'char_unicode', '') or '' for c in _characters(p)))]
        items.sort(key=lambda p: p.box.x)
        for a in items:
            for b in items:
                if a is b or a not in page.pdf_paragraph or b not in page.pdf_paragraph:
                    continue
                if getattr(a, 'xobj_id', None) != getattr(b, 'xobj_id', None) or b.box.x < a.box.x:
                    continue
                height = min(a.box.y2 - a.box.y, b.box.y2 - b.box.y)
                # A paragraph without a style of its own takes its characters' size; with 1 pt
                # instead, no piece of a Hindi invoice counted as one line, and "प्रबंधक" (manager)
                # came out as "Management" and "Mortgage" drawn over each other (2026-11-23).
                size = max(_size(a) or _char_size(a), _size(b) or _char_size(b), 1.0)
                # Pieces overlapping by more than a hair are one word ("कु" | "ल" of "कुल": 0.3 pt).
                if (a.box.y2 - a.box.y > 1.8 * size or b.box.y2 - b.box.y > 1.8 * size
                        or min(a.box.y2, b.box.y2) - max(a.box.y, b.box.y) < 0.5 * height):
                    continue
                overlap = min(a.box.x2, b.box.x2) - max(a.box.x, b.box.x)
                if overlap <= 0.1:
                    texts = [''.join(getattr(c, 'char_unicode', '') or '' for c in _characters(p)) for p in (a, b)]
                    if not (overlap >= -0.3 * size and all(RTL.search(t) for t in texts)
                            and min(sum(ch.isalpha() for ch in t) for t in texts) <= 3):
                        continue
                    if rules is None:
                        rules = vertical_rules(page)
                    if any(a.box.x2 - 0.5 <= (r.x + r.x2) / 2 <= b.box.x + 0.5 and r.y <= max(a.box.y, b.box.y) + 1
                           and r.y2 >= min(a.box.y2, b.box.y2) - 1 for r in rules):
                        continue
                    # Right to left: the right piece is read first.
                    a.pdf_paragraph_composition[:0] = b.pdf_paragraph_composition
                    page.pdf_paragraph.remove(b)
                    update(a)
                    joined += 1
                    changed = True
                    continue
                a.pdf_paragraph_composition.extend(b.pdf_paragraph_composition)
                page.pdf_paragraph.remove(b)
                update(a)
                joined += 1
                changed = True
    return joined


def _plain_chars(paragraph):
    return [c for c in _characters(paragraph) if getattr(c, 'visual_bbox', None)]


def join_broken_line(page, update):
    """Join the pieces of one line that the page layout cut apart, often letter by letter.

    In an HP manual's contents, "Appendix B Environmental product stewardship program" came out as 28
    paragraphs ("A", "ppen", "di", "x", "B E", "nv", ...), each translated alone into nonsense
    (2026-11-21): the layout model's boxes crossed the line. Pieces on one baseline, of one size, are
    joined where a word runs on across the cut (letters on both sides, a gap under 0.2 of the
    size), and, on a line found broken that way, also across word spaces (a gap under 0.6 of the
    size). Leader dots and numbers stay apart, as on unbroken contents lines; nothing is joined
    across a table rule. Returns how many pieces were joined into others."""
    rules = vertical_rules(page)
    single = []
    for p in page.pdf_paragraph:
        chars = _plain_chars(p)
        size = _size(p) or next((getattr(getattr(c, 'pdf_style', None), 'font_size', 0) for c in chars), 0)
        if (not chars or not size or getattr(p, 'vertical', False) or not getattr(p, 'box', None)
                or p.box.y2 - p.box.y > 1.8 * size):
            continue
        text = ''.join(c.char_unicode or '' for c in chars).strip()
        if not text or re.fullmatch(r'[\d.,:;·…\s-]+', text):
            continue  # leader dots, page numbers
        base = min(c.visual_bbox.box.y for c in chars if (c.char_unicode or '').strip()) if any(
            (c.char_unicode or '').strip() for c in chars) else p.box.y
        single.append({'p': p, 'chars': chars, 'size': float(size), 'base': base, 'text': text})
    joined = 0
    lines = []
    for item in sorted(single, key=lambda i: i['base']):
        for line in lines:
            ref = line[0]
            if (abs(item['base'] - ref['base']) <= 0.25 * ref['size'] and abs(item['size'] - ref['size']) <= 0.1 * ref['size']
                    and getattr(item['p'], 'xobj_id', None) == getattr(ref['p'], 'xobj_id', None)):
                line.append(item)
                break
        else:
            lines.append([item])
    for line in lines:
        if len(line) < 2:
            continue
        line.sort(key=lambda i: i['p'].box.x)
        def ruled(a, b):
            return any(a['p'].box.x2 - 0.5 <= (r.x + r.x2) / 2 <= b['p'].box.x + 0.5
                       and r.y <= a['p'].box.y + 1 and r.y2 >= a['p'].box.y2 - 1 for r in rules)
        def ink_gap(a, b):
            # Between the drawn characters: paragraph boxes of rebuilt scan lines are padded, and a
            # whole form row was joined by them ("김민준 EMP-2026-14033 Employee ID ...", 2026-11-21).
            right = max(c.visual_bbox.box.x2 for c in a['chars'] if (c.char_unicode or '').strip())
            left = min(c.visual_bbox.box.x for c in b['chars'] if (c.char_unicode or '').strip())
            return left - right

        def advances_touch(a, b):
            # The characters' advance boxes meet exactly: the two glyphs were set one after the other
            # in one run of text. "You ca" | "n register ..." (Canon manual, 2026-11-23) was cut by the
            # layout model with 1.5 pt of ink between "a" and "n", over the ink rule's 1.35 pt, and
            # "You ca" was answered with "Sorry, no text was given to translate" in Japanese.
            last = [c for c in a['chars'] if (c.char_unicode or '').strip()][-1]
            first = [c for c in b['chars'] if (c.char_unicode or '').strip()][0]
            return (getattr(last, 'box', None) is not None and getattr(first, 'box', None) is not None
                    and abs(first.box.x - last.box.x2) <= 0.05 * a['size'])

        def fragment(item):
            return sum(ch.isalpha() for ch in item['text']) <= 4

        pairs = []
        for a, b in zip(line, line[1:]):
            gap = ink_gap(a, b)
            size = a['size']
            short = fragment(a) or fragment(b)
            mid_word = (short and 0 <= gap + 0.1 * size <= 0.3 * size
                        and a['text'][-1:].isalpha() and b['text'][:1].isalpha())
            # A lowercase letter touching a lowercase letter is one word cut in two, whatever the
            # pieces' lengths ("The control pa" | "nel provides ... printe" | "r.", HP manual,
            # 2026-11-23); fields of a form row do not start in lowercase.
            continuation = ((-0.1 * size <= gap <= 0.15 * size and a['text'][-1:].islower()
                             and b['text'][:1].islower())
                            or (a['text'][-1:].isalpha() and b['text'][:1].isalpha() and advances_touch(a, b)))
            pairs.append((a, b, gap, mid_word or continuation, short or continuation))
        # A line cut by the layout has many such cuts (the HP contents line had over a dozen); one or
        # two tight gaps between fields of a form row are not a broken line, unless a word runs on
        # across a cut in lowercase.
        if sum(1 for p in pairs if p[3]) < 3 and not any(
                (-0.1 * p[0]['size'] <= p[2] <= 0.15 * p[0]['size'] and p[0]['text'][-1:].islower()
                 and p[1]['text'][:1].islower())
                or (p[0]['text'][-1:].isalpha() and p[1]['text'][:1].isalpha() and advances_touch(p[0], p[1]))
                for p in pairs):
            continue
        groups, current = [], [line[0]]
        for a, b, gap, mid, short in pairs:
            if (mid or (short and gap <= 0.6 * a['size'])) and gap > -0.1 * a['size'] and not ruled(a, b):
                current.append(b)
            else:
                groups.append(current)
                current = [b]
        groups.append(current)
        for group in groups:
            if len(group) < 2:
                continue
            head = group[0]['p']
            for item in group[1:]:
                head.pdf_paragraph_composition.extend(item['p'].pdf_paragraph_composition)
                page.pdf_paragraph.remove(item['p'])
                joined += 1
            update(head)
    return joined


def _characters(paragraph):
    """All characters of a paragraph: those of its lines, formulas and same-style runs."""
    out = []
    for c in paragraph.pdf_paragraph_composition or []:
        for part in (getattr(c, 'pdf_line', None), getattr(c, 'pdf_formula', None),
                     getattr(c, 'pdf_same_style_characters', None)):
            if part is not None:
                out.extend(part.pdf_character or [])
        if getattr(c, 'pdf_character', None) is not None:
            out.append(c.pdf_character)
    return out


def _is_lone_mark(paragraph):
    import unicodedata
    text = ''.join(getattr(ch, 'char_unicode', '') or '' for ch in _characters(paragraph)).strip()
    # Combining marks, or NUL: a glyph of a shaped cluster the PDF gives no character for.
    return bool(text) and all(unicodedata.category(c) in ('Mn', 'Mc', 'Me') or c == '\x00' for c in text)


def attach_lone_marks(page, update):
    """Move paragraphs made only of combining marks (or unread glyphs) into the paragraph whose
    letters they belong to.

    A Devanagari vowel sign drawn below its consonant ("ु"), and an unread piece of a Bengali
    cluster, became paragraphs of their own; with no letter to typeset, table fitting stopped and
    whole Hindi and Bengali documents failed (2026-11-21). The mark
    joins the nearest paragraph that overlaps it horizontally and lies within a line height of it.
    Returns how many were moved."""
    moved = 0
    for lone in [p for p in page.pdf_paragraph if _is_lone_mark(p)]:
        box = getattr(lone, 'box', None)
        if box is None:
            continue
        reach = max(box.y2 - box.y, 1.0) + max((getattr(getattr(ch, 'pdf_style', None), 'font_size', 0) or 0
                                                for ch in _characters(lone)), default=0)
        best = None
        for other in page.pdf_paragraph:
            ob = getattr(other, 'box', None)
            if other is lone or ob is None or _is_lone_mark(other) or getattr(other, 'xobj_id', None) != getattr(lone, 'xobj_id', None):
                continue
            if min(ob.x2, box.x2) - max(ob.x, box.x) < 0:
                continue
            distance = max(ob.y - box.y2, box.y - ob.y2, 0)
            if distance <= reach and (best is None or distance < best[0]):
                best = (distance, other)
        if best is None:
            continue
        target = best[1]
        target.pdf_paragraph_composition.extend(lone.pdf_paragraph_composition)
        page.pdf_paragraph.remove(lone)
        update(target)
        moved += 1
    return moved


def attach_lone_punctuation(page, update):
    """Move a paragraph of one to three punctuation characters into the line of the paragraph whose
    box holds it, in the line's left-to-right order. update(paragraph, line) refreshes both.

    The hyphen of "GQX-", a code wrapped in a narrow table cell, was a paragraph of its own inside
    the code's box; the cell then held two overlapping blocks, could not be laid out, and its row
    came out in tiny type in every language (newsletter, 2026-11-23). Returns how many were moved."""
    moved = 0
    for lone in list(page.pdf_paragraph):
        text = ''.join(getattr(c, 'char_unicode', '') or '' for c in _characters(lone)).strip()
        box = getattr(lone, 'box', None)
        lines = [c.pdf_line for c in lone.pdf_paragraph_composition or [] if getattr(c, 'pdf_line', None)]
        if (not text or len(text) > 3 or any(ch.isalnum() for ch in text) or box is None or len(lines) != 1
                or getattr(lone, 'vertical', False) or any(ch in TEXT_BULLETS for ch in text)):
            continue
        height = max(box.y2 - box.y, .1)
        best = None
        for other in page.pdf_paragraph:
            ob = getattr(other, 'box', None)
            if other is lone or ob is None or getattr(other, 'xobj_id', None) != getattr(lone, 'xobj_id', None):
                continue
            if not (ob.x - .5 <= box.x and box.x2 <= ob.x2 + .5 and ob.y - .5 <= box.y and box.y2 <= ob.y2 + .5):
                continue
            for composition in other.pdf_paragraph_composition or []:
                line = getattr(composition, 'pdf_line', None)
                lb = getattr(line, 'box', None) if line is not None else None
                if lb is None:
                    continue
                overlap = min(lb.y2, box.y2) - max(lb.y, box.y)
                if overlap >= .5 * height and (best is None or overlap > best[0]):
                    best = (overlap, other, line)
        if best is None:
            continue
        _, target, line = best
        chars = list(line.pdf_character or []) + list(lines[0].pdf_character or [])
        position = lambda c: getattr(getattr(c, 'visual_bbox', None), 'box', None) or getattr(c, 'box', None)
        if all(position(c) is not None for c in chars):
            chars.sort(key=lambda c: position(c).x)
        line.pdf_character = chars
        page.pdf_paragraph.remove(lone)
        update(target, line)
        moved += 1
    return moved


def _bidi_class(char):
    text = getattr(char, 'char_unicode', '') or ''
    return unicodedata.bidirectional(text[0]) if text else ''


def _rtl_segments(chars):
    """Groups of the page's characters drawn on one line and close together: lists of indices.
    Lines come from positions, not storage order (a right-to-left line's runs can be stored apart,
    with other characters between them); a wide horizontal gap (a table cell border, another
    column) starts a new group."""
    lines = []   # [centre, size, [indices]]
    for k, c in enumerate(chars):
        box = c.visual_bbox.box
        centre, size = (box.y + box.y2) / 2, max(box.y2 - box.y, 1.0)
        for line in lines:
            if abs(line[0] - centre) <= 0.5 * max(size, line[1]):
                line[2].append(k)
                break
        else:
            lines.append([centre, size, [k]])
    groups = []
    for _, _, members in lines:
        ordered = sorted(members, key=lambda k: chars[k].visual_bbox.box.x)
        current, right = [ordered[0]], chars[ordered[0]].visual_bbox.box.x2
        for k in ordered[1:]:
            box = chars[k].visual_bbox.box
            # Word spaces and the gap between a line's runs stay under half the font size; columns
            # of a newsletter can be as close as two (Arabic newsletter, 2026-11-23).
            if box.x - right > 1.2 * max(box.y2 - box.y, 1.0):
                groups.append(sorted(current))
                current, right = [], box.x2
            current.append(k)
            right = max(right, box.x2)
        groups.append(sorted(current))
    return groups


def logical_rtl_order(page):
    """Put the characters of right-to-left lines into reading order before paragraphs are built.

    Chromium's PDFs store a line mixing Arabic with digits or Latin letters run by run, left to right
    on the page: ". شخص891 المقبل وسيوظف نحو". BabelDOC read the characters in that order, cut the
    line into pieces and sent some backwards ("بيردتلا لامكإ"): the model answered with invented
    sentences ("Welcome to our system", "I do not understand") printed in an Arabic report's English
    translation (2026-11-23). The reading order comes from the positions instead: right to left, with
    runs of digits and Latin letters left to right inside it (unmapped_text.visual_to_logical, the
    rule the checks already use). A combining mark stays after its letter. Each right-to-left line is
    placed where its first stored character was; other characters keep their places. Lines already
    stored in reading order come out unchanged. Returns how many lines were reordered."""
    from transformation.pdf.unmapped_text import RTL
    chars = getattr(page, 'pdf_character', None) or []
    if not chars or not any(RTL.search(getattr(c, 'char_unicode', '') or '') for c in chars):
        return 0
    if any(getattr(c, 'visual_bbox', None) is None for c in chars):
        return 0
    changed = 0
    placed = {}      # first stored index of a group -> its characters in reading order
    moved = set()
    for indices in _rtl_segments(chars):
        segment = [chars[k] for k in indices]
        rtl = sum(_bidi_class(c) in ('R', 'AL') for c in segment)
        ltr = sum(_bidi_class(c) == 'L' for c in segment)
        if rtl == 0 or rtl < ltr:
            continue
        clusters = []
        for c in segment:
            text = getattr(c, 'char_unicode', '') or ''
            if clusters and text and unicodedata.combining(text[0]):
                clusters[-1][1].append(c)
            else:
                clusters.append([(c.visual_bbox.box.x + c.visual_bbox.box.x2) / 2, [c]])
        order = _logical([(x, members, n) for n, (x, members) in enumerate(clusters)])
        reordered = [c for n in order for c in clusters[n][1]]
        if reordered != segment or indices != list(range(indices[0], indices[0] + len(indices))):
            changed += 1
        placed[indices[0]] = reordered
        moved.update(indices)
    if not changed:
        return 0
    out = []
    for k, c in enumerate(chars):
        if k in placed:
            out.extend(placed[k])
        elif k not in moved:
            out.append(c)
    page.pdf_character[:] = out
    return changed


def _logical(keyed):
    """Indices of clusters (x, members, index) in reading order of a right-to-left line."""
    from transformation.pdf.unmapped_text import _LTR_CLASSES, _RUN_JOINERS
    order = sorted(keyed, key=lambda k: -k[0])
    kind = lambda k: _bidi_class(k[1][0])
    out, i = [], 0
    while i < len(order):
        if kind(order[i]) not in _LTR_CLASSES:
            out.append(order[i][2]); i += 1; continue
        j = i + 1
        while j < len(order):
            if kind(order[j]) in _LTR_CLASSES:
                j += 1; continue
            if kind(order[j]) in _RUN_JOINERS:
                k = j
                while k < len(order) and kind(order[k]) in _RUN_JOINERS:
                    k += 1
                if k < len(order) and kind(order[k]) in _LTR_CLASSES:
                    j = k; continue
            break
        out.extend(k[2] for k in reversed(order[i:j]))
        i = j
    return out


def _text(paragraph):
    return ''.join(getattr(c, 'char_unicode', '') or '' for c in _characters(paragraph))


def join_lead_labels(page, update):
    """Join a short label ending in a colon ("NOTE:", "CAUTION:", "참고:") to the paragraph whose first
    line it starts.

    A note's label was a paragraph of its own when its colour differed from the text (blue "NOTE:"
    before black text); the note was translated and laid out from the top of its box while the label
    stayed at its old height, so the label sat over the note's second line (HP manual, 13 pages,
    2026-11-23). Where BabelDOC had kept label and text together the note came out right. A label is
    joined when it is one short line ending in a colon, the paragraph starts on the same line just
    to its right (within 2.5 of the font size), and no table rule lies between them. Returns how
    many labels were joined."""
    rules = vertical_rules(page)
    joined = 0
    for label in list(page.pdf_paragraph):
        if label not in page.pdf_paragraph or getattr(label, 'vertical', False) or not getattr(label, 'box', None):
            continue
        lines = _lines(label)
        text = ' '.join(_text(label).split())
        if len(lines) != 1 or not text.endswith((':', '：')) or len(text) > 24 or len(text.split()) > 3:
            continue
        if not any(ch.isalpha() for ch in text):
            continue
        size = _size(label) or 1.0
        lbox = lines[0].box
        centre = (lbox.y + lbox.y2) / 2
        best = None
        for body in page.pdf_paragraph:
            if body is label or getattr(body, 'vertical', False) or not getattr(body, 'box', None):
                continue
            if getattr(body, 'xobj_id', None) != getattr(label, 'xobj_id', None):
                continue
            first = _lines(body)[:1]
            if not first or not first[0].pdf_character:
                continue
            fbox = first[0].box
            gap = fbox.x - lbox.x2
            if not (-0.2 * size <= gap <= 2.5 * size):
                continue
            if abs((fbox.y + fbox.y2) / 2 - centre) > 0.4 * size:
                continue
            if any(lbox.x2 - 0.5 <= r.x <= fbox.x + 0.5 and min(r.y2, lbox.y2) - max(r.y, lbox.y) > 0 for r in rules):
                continue
            if best is None or gap < best[0]:
                best = (gap, body)
        if best is None:
            continue
        body = best[1]
        body.pdf_paragraph_composition[:0] = label.pdf_paragraph_composition
        page.pdf_paragraph.remove(label)
        update(body)
        joined += 1
    return joined


def table_cells(finder, page):
    """Boxes of the table cells PyMuPDF finds on this page of the input PDF (PDF coordinates), or ()."""
    try:
        from transformation.pdf.babeldoc_worker import source_table_cells
        path = finder.translation_config.input_file
        return tuple(c.box for c in source_table_cells(str(path)).get(page.page_number, []))
    except Exception:  # no cells is the old behaviour: rules only
        return ()


def trim_overhanging_spaces(page, update):
    """Cut back spaces that reach past every letter of their paragraph.

    BabelDOC adds a space at each line end, as wide as the gap to the next line's first character
    measured left to right. Right-to-left lines run the other way, so the gap was taken backwards: in
    an Arabic manual's numbered lists a space ran from 526.7 to 650.2 pt, past the page edge, the
    paragraph box grew with it, and the English translation was set into that box and ran off the
    page (2026-11-23). A space wider than the font size that sticks out beyond the paragraph's
    letters is cut to a third of the font size. Returns how many paragraphs changed."""
    changed = 0
    for paragraph in page.pdf_paragraph:
        chars = [c for c in _characters(paragraph) if getattr(c, 'box', None)]
        ink = [c.box for c in chars if (getattr(c, 'char_unicode', '') or '').strip()]
        if not ink:
            continue
        left, right = min(b.x for b in ink), max(b.x2 for b in ink)
        trimmed = False
        for c in chars:
            if getattr(c, 'char_unicode', None) != ' ':
                continue
            size = getattr(getattr(c, 'pdf_style', None), 'font_size', 0) or _size(paragraph) or 0
            box = c.box
            if not size or box.x2 - box.x <= size or (box.x2 <= right + 0.5 and box.x >= left - 0.5):
                continue
            width = size / 3
            if box.x2 > right + 0.5:      # sticks out on the right: keep its left end
                box.x = min(box.x, right)
                box.x2 = box.x + width
            else:                         # sticks out on the left: keep its right end
                box.x2 = max(box.x2, left)
                box.x = box.x2 - width
            visual = getattr(getattr(c, 'visual_bbox', None), 'box', None)
            if visual is not None and visual is not box:
                visual.x, visual.x2 = box.x, box.x2
            if getattr(c, 'advance', None) is not None:
                c.advance = box.x2 - box.x
            trimmed = True
        if trimmed:
            update(paragraph)
            changed += 1
    return changed


def split_step_numbers(page, update, make_id):
    """Keep a large step number apart from the sentence it starts.

    Manuals print step numbers larger than the step ("2" in large red type before "Select the paper
    source."). BabelDOC put the number in the sentence's paragraph and the translation redrew it in
    the sentence's small black type, with the sentence wrapped around it (Canon manual, 2026-11-23).
    A paragraph whose first characters are a number of one or two digits (with "." or ")") set at
    least 1.3 times the size of its other characters, which hold letters, is split into the number,
    kept as it is, and the sentence. Returns how many numbers were split off."""
    import copy
    from statistics import median
    added = 0
    for paragraph in list(page.pdf_paragraph):
        compositions = paragraph.pdf_paragraph_composition or []
        if getattr(paragraph, 'vertical', False) or not compositions or not getattr(compositions[0], 'pdf_line', None):
            continue
        first = compositions[0].pdf_line.pdf_character or []
        visible = [i for i, c in enumerate(first) if (c.char_unicode or '').strip()]
        lead = []
        for i in visible:
            if first[i].char_unicode.isdigit() and len(lead) < 2 and (not lead or i == lead[-1] + 1):
                lead.append(i)
            else:
                break
        if not lead or (lead[-1] + 1 < len(first) and (first[lead[-1] + 1].char_unicode or '').isdigit()):
            continue      # no number, or one of three digits or more ("2024")
        end = lead[-1] + 1
        if end < len(first) and first[end].char_unicode in '.)':
            end += 1
        size = lambda c: getattr(getattr(c, 'pdf_style', None), 'font_size', None) or 0
        rest = [c for c in first[end:] if (c.char_unicode or '').strip()] + [
            c for comp in compositions[1:] if getattr(comp, 'pdf_line', None) for c in comp.pdf_line.pdf_character or []
            if (c.char_unicode or '').strip()]
        if not any(c.char_unicode.isalpha() for c in rest):
            continue
        body = median(size(c) for c in rest)
        if not body or min(size(first[i]) for i in lead) < 1.3 * body:
            continue
        number = copy.deepcopy(paragraph)
        number.debug_id = make_id(paragraph)
        number_line = copy.copy(compositions[0].pdf_line)
        number_line.pdf_character = first[:end]
        number.pdf_paragraph_composition = [copy.copy(compositions[0])]
        number.pdf_paragraph_composition[0].pdf_line = number_line
        remaining = first[end:]
        while remaining and not (remaining[0].char_unicode or '').strip():
            remaining = remaining[1:]
        compositions[0].pdf_line.pdf_character = remaining
        if not remaining:
            paragraph.pdf_paragraph_composition = compositions[1:]
        update(number, number_line)
        if paragraph.pdf_paragraph_composition:
            update(paragraph, paragraph.pdf_paragraph_composition[0].pdf_line)
        page.pdf_paragraph.insert(page.pdf_paragraph.index(paragraph), number)
        added += 1
    return added


def install(ParagraphFinder):
    if getattr(ParagraphFinder, '_transformx_stacked_lines', False):
        return
    original = ParagraphFinder.process_page

    def process_page(self, page):
        logical_rtl_order(page)
        result = original(self, page)
        trim_overhanging_spaces(page, lambda p: self.update_paragraph_data(p, update_unicode=True))
        counter = [0]

        def make_id(paragraph):
            counter[0] += 1
            return f'{paragraph.debug_id}-c{counter[0]}'

        def update(paragraph, line):
            self.update_line_data(line)
            self.update_paragraph_data(paragraph, update_unicode=True)

        changed = join_broken_line(page, lambda p: self.update_paragraph_data(p, update_unicode=True))
        changed += split_at_rules(page, update, make_id)
        changed += split_at_badges(page, update, make_id)
        changed += split_at_list_markers(page, update, make_id)
        changed += split_step_numbers(page, update, make_id)
        # A piece cut off the end of a list item's line ("... click the Email Setup lin" | "k.", HP
        # manual, 2026-11-23) is found only once the list is split into one-line items.
        changed += join_broken_line(page, lambda p: self.update_paragraph_data(p, update_unicode=True))
        changed += join_overlapping_fragments(page, lambda p: self.update_paragraph_data(p, update_unicode=True))
        changed += join_stacked_lines(page.pdf_paragraph, lambda p: self.update_paragraph_data(p, update_unicode=True),
                                      (vertical_rules(page), horizontal_rules(page), table_cells(self, page)))
        changed += attach_lone_marks(page, lambda p: self.update_paragraph_data(p, update_unicode=True))
        changed += attach_lone_punctuation(page, update)
        # A mark attached above can make two pieces of one word overlap ("प्रब" | "ं" + "धक").
        changed += join_overlapping_fragments(page, lambda p: self.update_paragraph_data(p, update_unicode=True))
        changed += join_lead_labels(page, lambda p: self.update_paragraph_data(p, update_unicode=True))
        if changed:
            self._set_paragraph_render_order(page)
        return result

    ParagraphFinder.process_page = process_page
    ParagraphFinder._transformx_stacked_lines = True
