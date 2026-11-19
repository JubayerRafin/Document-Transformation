"""Every page is processed upright: no /Rotate, and its text running left to right.

BabelDOC 0.6.4 placed a rotated page's translated text and graphics outside the visible page (a
form page with rotation 270 came out blank, 2026-11-19), and it leaves text that runs sideways on
the page untranslated (it treats it as vertical writing). So:

1. Pages with a /Rotate value become unrotated pages that look the same (PyMuPDF's
   remove_rotation(): pixel for pixel, checked for 90, 180 and 270, text kept).
2. A page whose text mostly runs sideways or upside down is turned so it reads left to right. The
   output page is then upright. Vertical CJK writing (top to bottom, upright characters) is left
   as it is.
"""
import collections

import pymupdf

TURN = {(0, -1): 90, (0, 1): 270, (-1, 0): 180}     # line direction -> page turn that makes it (1, 0)
SHARE = 0.6        # share of a page's characters that must run that way
MINIMUM = 20       # characters a page needs before its direction is judged


def _cjk(character):
    code = ord(character)
    return 0x3040 <= code <= 0x30ff or 0x3400 <= code <= 0x9fff or 0xac00 <= code <= 0xd7af or 0xf900 <= code <= 0xfaff


def text_turn(page):
    """Degrees to turn the page so most of its text runs left to right, or 0."""
    counts, cjk, total = collections.Counter(), 0, 0
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(span['text'] for span in line['spans'])
            letters = sum(not c.isspace() for c in text)
            if not letters:
                continue
            counts[tuple(round(v) for v in line['dir'])] += letters
            cjk += sum(_cjk(c) for c in text)
            total += letters
    if total < MINIMUM:
        return 0
    direction, count = counts.most_common(1)[0]
    if direction not in TURN or count < SHARE * total:
        return 0
    if direction == (0, 1) and cjk >= 0.5 * total:
        return 0    # Japanese or Chinese vertical writing
    return TURN[direction]


def unrotated(data):
    """PDF bytes with every page upright; the same bytes when nothing changes or the file cannot be
    read here (later checks report that)."""
    if not isinstance(data, (bytes, bytearray)) or data[:5] != b'%PDF-':
        return data   # later checks refuse anything that is not PDF bytes
    try:
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            if pdf.needs_pass:
                return data
            changed = False
            for page in pdf:
                if page.rotation:
                    page.remove_rotation()
                    changed = True
            for page in pdf:
                turn = text_turn(page)
                if turn:
                    page.set_rotation(turn)
                    page.remove_rotation()
                    changed = True
            return pdf.tobytes(garbage=1, deflate=True) if changed else data
    except Exception:
        return data
