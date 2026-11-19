"""Split OCR lines that run across a table rule in a scan.

The text detector sometimes returns one box for two neighbouring table cells when their text is
close ("QRF-2020-34513 | 479", "Quantity | Unit price"). Translated as one line, the cells' text
is redrawn together in the first cell and the next cell's header disappears (scanned invoices,
2026-11-19). A line is split where a vertical rule crosses it: a column of ink that covers nearly
the whole line height and continues well above or below it (a table border, not a letter). The
words are divided where the share of characters best matches the share of ink on each side; when
no division matches, the line is left whole.
"""
import re

import numpy as np
import pymupdf

COVER = 0.85        # share of the line's rows the rule must cover
EXTEND = 0.3        # the rule continues at least this many line heights above or below
EDGE = 0.04         # rules this close to a line's edge are its cell border, not inside it
MATCH = 0.18        # largest difference between character share and ink share of a division


def _render(page, scale):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False, annots=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _rule_columns(gray, box, scale):
    """Pixel columns inside the line box (relative to it) crossed by a vertical rule."""
    x0, y0 = max(0, int(box.x0 * scale)), max(0, int(box.y0 * scale))
    x1, y1 = min(gray.shape[1], int(np.ceil(box.x1 * scale))), min(gray.shape[0], int(np.ceil(box.y1 * scale)))
    height = y1 - y0
    if height < 4 or x1 - x0 < 8:
        return [], None
    crop = gray[y0:y1, x0:x1]
    background = float(np.median(np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]])))
    dark = (background - crop.astype(np.int16)) > 60
    reach = max(3, int(EXTEND * height))
    columns = []
    for x in np.flatnonzero(dark.mean(axis=0) >= COVER):
        if not int(EDGE * (x1 - x0)) <= x < (x1 - x0) - int(EDGE * (x1 - x0)):
            continue
        column = gray[:, x0 + x].astype(np.int16)
        above = column[max(0, y0 - reach):y0]
        below = column[y1:y1 + reach]
        continues = any(len(part) >= reach and np.mean((background - part) > 60) >= COVER for part in (above, below))
        if continues:
            columns.append(int(x))
    # One rule may be several pixels wide: keep the middle of each run.
    rules, run = [], []
    for x in columns:
        if run and x != run[-1] + 1:
            rules.append(run[len(run) // 2]); run = []
        run.append(x)
    if run:
        rules.append(run[len(run) // 2])
    ink = dark.copy()
    for x in columns:
        ink[:, max(0, x - 1):x + 2] = False
    return rules, ink


def _division(tokens, ink, rule):
    """Index i: tokens[:i] lie left of the rule, or None when no division matches the ink."""
    columns = ink.any(axis=0)
    left, total = int(columns[:rule].sum()), int(columns.sum())
    if total == 0 or left == 0 or left == total:
        return None
    share = left / total
    lengths = [len(t) for t in tokens]
    best = min(range(1, len(tokens)), key=lambda i: abs(sum(lengths[:i]) / sum(lengths) - share))
    return best if abs(sum(lengths[:best]) / sum(lengths) - share) <= MATCH else None


def _ink_box(box, ink, start, end, scale):
    columns = np.flatnonzero(ink[:, start:end].any(axis=0))
    if not len(columns):
        return None
    return pymupdf.Rect(box.x0 + (start + columns[0]) / scale, box.y0, box.x0 + (start + columns[-1] + 1) / scale, box.y1)


def split_at_rules(page, lines):
    """lines (OCR line dicts with 'id', 'text', 'bbox' in page units) with each line that a table
    rule crosses divided into one line per cell. Lines without such a rule are returned unchanged."""
    if not lines:
        return lines
    scale = min(4.0, 3000 / max(page.rect.width, page.rect.height))
    gray = None
    result = []
    for line in lines:
        tokens = re.findall(r'\S+', line.get('text') or '')
        if len(tokens) < 2 or line.get('confirmed_label'):
            result.append(line); continue
        if gray is None:
            gray = _render(page, scale)
        box = pymupdf.Rect(line['bbox'])
        rules, ink = _rule_columns(gray, box, scale)
        pieces, rest, offset = [], tokens, 0
        for rule in rules:
            if len(rest) < 2:
                break
            index = _division(rest, ink[:, offset:], rule - offset)
            if index is None:
                continue
            pieces.append((rest[:index], offset, rule))
            rest, offset = rest[index:], rule + 1
        if not pieces:
            result.append(line); continue
        pieces.append((rest, offset, ink.shape[1]))
        parts = []
        for number, (words, start, end) in enumerate(pieces, 1):
            part_box = _ink_box(box, ink, start, end, scale)
            if part_box is None:
                parts = []; break
            part = dict(line, id=f"{line['id']}_cell{number}", text=' '.join(words), bbox=list(part_box),
                        split_from=line['id'])
            # The line's recognition evidence was for the whole line: values in a part are kept as
            # source pixels unless read again; ordinary text is translated cell by cell.
            part['candidates'] = []
            part.pop('polygon_pixels', None)
            parts.append(part)
        result.extend(parts or [line])
    return result
