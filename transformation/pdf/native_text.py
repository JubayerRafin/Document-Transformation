"""Real PDF text for recognized scan text in any script, written so the PDF reader reads it back.

Scan text becomes PDF text twice: as the invisible OCR layer the translator reads (ocr/paddle_handoff.py)
and as the visible text of a scan made native for translation (scan_reconstruction.py, force_native).
MuPDF's CJK font, used for both, has no Arabic or Indic letters: every Arabic and Hindi scan failed
once those languages got their own recognizers, and native scans drew such text as outlines the
translator cannot read, so it stayed untranslated (2026-11-21).
"""
import os
import unicodedata

import pymupdf

# Fonts tried in order after MuPDF's CJK font. TRANSFORMX_OCR_FONTS adds font files (os.pathsep-separated).
FONT_FILES = ['~/.cache/babeldoc/fonts/GoNotoKurrent-Regular.ttf',
              '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']

_fonts = None


def fonts():
    """[(name, pymupdf.Font)], MuPDF's CJK font first."""
    global _fonts
    if _fonts is None:
        found = [('ocrfont', pymupdf.Font('cjk'))]
        extra = [p for p in os.environ.get('TRANSFORMX_OCR_FONTS', '').split(os.pathsep) if p]
        for number, path in enumerate(extra + FONT_FILES):
            path = os.path.expanduser(path)
            if os.path.isfile(path):
                try:
                    found.append((f'ocrfont{number + 1}', pymupdf.Font(fontfile=path)))
                except Exception:
                    continue
        _fonts = found
    return _fonts


def covering_font(text):
    """(name, font) of the first font with a glyph for every character of text, or None."""
    return next(((name, font) for name, font in fonts()
                 if all(font.has_glyph(ord(c)) for c in text if not c.isspace())), None)


def drawing_order(text):
    """text as a PDF draws it, left to right. Right-to-left text is drawn in visual order (runs of
    digits and Latin letters left to right inside it), as real PDFs do, so that the reader that
    puts right-to-left lines back into reading order (unmapped_text.py) reads it correctly."""
    from transformation.pdf.unmapped_text import RTL, visual_to_logical
    if not RTL.search(text):
        return text
    clusters = []
    for c in text:
        if clusters and unicodedata.combining(c):
            clusters[-1] += c
        else:
            clusters.append(c)
    return visual_to_logical([(i, c) for i, c in enumerate(clusters)])
