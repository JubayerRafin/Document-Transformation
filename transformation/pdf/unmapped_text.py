"""Paragraphs whose characters BabelDOC could not read, re-read with MuPDF.

Word, LibreOffice and browsers write complex-script clusters (Bengali "ক্ষে", Hindi conjuncts, Arabic
ligatures) as glyphs without their own character-map entry, with the real text in an ActualText tag.
BabelDOC's PDF reader ignores ActualText, so those glyphs arrive as "(cid:12)" and the paragraph is
sent to the model as garbage: Bengali slides came back half-translated and one page failed
(mass test, 2026-11-19). MuPDF honours ActualText, so the paragraph's area is read again there and
used as the translation input when it agrees with the characters BabelDOC did read.

Right-to-left paragraphs are re-read the same way: BabelDOC's reader returns Arabic and Hebrew in
the order the glyphs are drawn (reversed), and the model, given reversed Arabic, invented English
letters instead of translating (every Arabic document of the same mass test). MuPDF returns them in
reading order.
"""
from collections import Counter
import re
import unicodedata

CID = re.compile(r'\(cid:\d+\)')
# Brahmic scripts (Devanagari to Sinhala) and Thai/Lao: clusters written with ActualText report boxes
# narrower than the drawn glyphs, so pieces of one word on a line seem to be separated by a gap.
_JOINED_SCRIPTS = re.compile(r'[ऀ-෿฀-໿]')
RTL = re.compile(r'[֐-ࣿיִ-﷿ﹰ-﻿]')


_PRESENTATION_FORMS = re.compile(r'[ﭐ-﷿ﹰ-﻿]')


def fold(text):
    """Arabic presentation forms (the shaped glyph variants some PDFs map to) as base letters."""
    return _PRESENTATION_FORMS.sub(lambda m: unicodedata.normalize('NFKC', m.group()), text)


def _alnum(text):
    return Counter(c for c in fold(text) if c.isalnum())


def join_pieces(pieces):
    """Text of (baseline, x0, x1, text, size) pieces in reading order. Pieces on the same baseline
    are joined without a space when they touch, or when both sides are Indic letters (see above)."""
    out, previous = '', None
    for base, x0, x1, text, size in pieces:
        if previous is None:
            out = text
        else:
            same_line = abs(base - previous[0]) < 0.3 * size
            gap = x0 - previous[2]
            glued = (same_line and -0.5 * size < gap < 0.15 * size) or (
                same_line and -0.5 * size < gap < 1.2 * size and out[-1:] and text[:1]
                and _JOINED_SCRIPTS.match(out[-1]) and _JOINED_SCRIPTS.match(text[0]))
            out = out + text if glued else out.rstrip() + ' ' + text.lstrip()
        previous = (base, x0, x1)
    return ' '.join(out.split())


def read_area(page, rect):
    """MuPDF's text inside rect (MuPDF coordinates), horizontal lines only, or ''."""
    pieces = []
    for block in page.get_text('dict', clip=rect, sort=True)['blocks']:
        for line in block.get('lines', []):
            if tuple(round(v) for v in line['dir']) != (1, 0) or not line['spans']:
                continue
            text = ''.join(span['text'] for span in line['spans'])
            if text.strip():
                size = max(span['size'] for span in line['spans']) or 1
                pieces.append((line['spans'][0]['origin'][1], line['bbox'][0], line['bbox'][2], text, size))
    return fold(join_pieces(pieces))


# Parts of a garbled paragraph that BabelDOC still reads reliably: Latin words and longer numbers,
# drawn with ordinary fonts. Shaped-script glyphs are also read as wrong letters and single digits
# ("কতির্মটি" for "কমিটি", "চা7লি8র"), so those are not compared.
_RELIABLE = re.compile(r'[A-Za-z][A-Za-z0-9@._-]{2,}|\d[\d,.:/-]{3,}\d')


def _main_script(text):
    names = Counter(unicodedata.name(c, ' ').split(' ')[0] for c in fold(text) if c.isalpha() and not c.isascii())
    return names.most_common(1)[0][0] if names else None


def acceptable(babeldoc_text, mupdf_text):
    """Whether mupdf_text can stand in for babeldoc_text: readable, and the same paragraph.

    Right-to-left text that BabelDOC read in full (only reversed) must hold the same letters. Text
    with unread glyphs must keep the Latin words and longer numbers BabelDOC read, be in the same
    script, and have a plausible length (no neighbouring text pulled in)."""
    if not mupdf_text or CID.search(mupdf_text) or '�' in mupdf_text:
        return False
    unmapped = len(CID.findall(babeldoc_text))
    readable = CID.sub(' ', babeldoc_text)
    known, found = _alnum(readable), _alnum(mupdf_text)
    total, length = sum(known.values()), sum(found.values())
    if not unmapped:
        return sum((known & found).values()) >= 0.8 * total and length <= 1.3 * total + 4
    flat = ' '.join(fold(mupdf_text).split())
    if any(token not in flat for token in _RELIABLE.findall(readable)):
        return False
    if _main_script(readable) and _main_script(readable) != _main_script(mupdf_text):
        return False
    # An unmapped glyph stands for up to four characters (a conjunct with its vowel sign).
    return 0.4 * total <= length <= 1.6 * (total + 4 * unmapped) + 8


def repair_page(page, source):
    """Translation inputs for the BabelDOC paragraphs of page that contain unread characters,
    read from source (the same page opened with MuPDF). Returns (overrides by id, records)."""
    import pymupdf
    overrides, records = {}, []
    height = source.rect.height
    for paragraph in page.pdf_paragraph:
        text = paragraph.unicode or ''
        unread, reversed_order = bool(CID.search(text)), bool(RTL.search(text))
        if not paragraph.box or not (unread or reversed_order):
            continue
        box = paragraph.box
        rect = pymupdf.Rect(box.x - 1, height - box.y2 - 1, box.x2 + 1, height - box.y + 1)
        replacement = read_area(source, rect)
        record = {'page': page.page_number + 1, 'paragraph_id': paragraph.debug_id,
                  'unread_characters': len(CID.findall(text)), 'right_to_left': reversed_order}
        if acceptable(text, replacement):
            paragraph.unicode = replacement
            overrides[id(paragraph)] = replacement
            records.append({**record, 'status': 'reread'})
        else:
            records.append({**record, 'status': 'kept', 'reason': 'MuPDF text did not match the paragraph',
                            'babeldoc_text': text[:120], 'mupdf_text': replacement[:120]})
    return overrides, records
