"""Text style read from scanned pixels, for rebuilding a scanned line as native text.

OCR gives words and boxes but no style, so rebuilt scan text was always black, regular and set in
the CJK fallback font, whose Latin letters are much wider than the source's: lines were fitted far
smaller than printed ("Japan · March-April" at 5.9 pt instead of about 12; scanned slides,
2026-11-18). This module measures what the pixels show (ink colour, stroke weight, slant) and picks
a Latin font with real Latin metrics when the text allows it.

Calibration (2026-11-18):
- slant, on synthetic scans of known Helvetica and Times text (9-36 pt, 110-300 dpi, blurred,
  JPEG-compressed): italic lines scored 1.00-1.47, upright lines at most 0.91; upright lines of a
  real scanned page (Sample01) at most 0.94.
- weight, on Sample01's scanned page, whose bold and regular lines were labelled by eye: with
  letters at least 28 px tall at 4x, regular lines measured at most 0.184 and bold lines 0.181-0.241.
  Smaller letters are too blurred to judge (regular reached 0.225), so they stay regular. A darker
  scan thickens every stroke (plain table cells of another scan measured about 0.19), so a line must
  also be clearly heavier than the page's typical line (1.15x its median), which kept every regular
  line regular on both scans. Missing a bold line keeps the earlier behaviour; marking regular text
  bold would be a new error.
"""
import numpy as np
import pymupdf

ITALIC_SHEAR = -0.21      # undo a slant of about 12 degrees
ITALIC_GAIN = 1.0         # sharper column profile after undoing it: italic
BOLD_STROKE = 0.19        # stroke width over letter height at or above this: bold
BOLD_LETTER_PX = 28       # weight is judged only on letters at least this tall at 4x
BOLD_RELATIVE = 1.15      # and on lines clearly heavier than the page's typical line
BOLD_PAGE_LINES = 5       # which needs this many measurable lines on the page
MIN_LETTER_PX = 12        # letters smaller than this at 4x are too blurred to judge slant

LATIN = {(False, False): 'helv', (True, False): 'hebo', (False, True): 'heit', (True, True): 'hebi'}
# Written into the page as embedded font files: the PDF's built-in Helvetica only encodes Latin-1,
# so a dash or curly quote would come out as another character.
EMBEDDED = {'helv': 'TXSansR', 'hebo': 'TXSansB', 'heit': 'TXSansI', 'hebi': 'TXSansBI'}
_FONTS = {}


def font(name):
    if name not in _FONTS:
        _FONTS[name] = pymupdf.Font(name)
    return _FONTS[name]


def latin_font(text, bold=False, italic=False):
    """(name, Font) of the Helvetica variant for text it can draw entirely, else None."""
    name = LATIN[(bool(bold), bool(italic))]
    chosen = font(name)
    if text.strip() and all(c.isspace() or (ord(c) < 0x2200 and chosen.has_glyph(ord(c))) for c in text):
        return name, chosen
    return None


SCALE = 4.0
WHOLE_PAGE_PIXELS = 40_000_000  # render the page once at this size or below, else line by line


def render(page, scale=SCALE):
    """The whole page as an RGB array at scale, or None when it would be too large."""
    if page.rect.get_area() * scale * scale > WHOLE_PAGE_PIXELS:
        return None
    # Rendered from a separate copy of the document: a full-page render changes how MuPDF caches the
    # decoded scan, which shifted the size measurements made afterwards on the same page (2026-11-19).
    with pymupdf.open(stream=page.parent.tobytes(), filetype='pdf') as copy:
        pix = copy[page.number].get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)


def _crop(page, bbox, scale=SCALE, rendered=None):
    clip = pymupdf.Rect(bbox) & page.rect
    if clip.is_empty or clip.get_area() * scale * scale > 3_000_000:
        return None
    if rendered is not None:
        x0, y0 = int(round((clip.x0 - page.rect.x0) * scale)), int(round((clip.y0 - page.rect.y0) * scale))
        x1, y1 = int(round((clip.x1 - page.rect.x0) * scale)), int(round((clip.y1 - page.rect.y0) * scale))
        return rendered[y0:y1, x0:x1].astype(float)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(float)


def _profile_peak(mask, shear):
    """Sharpness of the column profile after shifting each row by shear * its height above the bottom."""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    shifted = np.round(xs + shear * (h - 1 - ys)).astype(int)
    shifted -= shifted.min()
    return float(np.sum(np.bincount(shifted).astype(float) ** 2))


def measure(page, bbox, background, rendered=None):
    """Ink colour (0-255 RGB), stroke-width ratio and slant gain of one text box, or None.
    rendered: the page from render(), to cut the box from instead of rendering it again."""
    pixels = _crop(page, bbox, rendered=rendered)
    if pixels is None or min(pixels.shape[:2]) < 6:
        return None
    distance = np.max(np.abs(pixels - np.asarray(background, float)), axis=2)
    if np.percentile(distance, 99) < 40:
        return None
    ink = distance >= max(40.0, 0.5 * float(np.percentile(distance, 99)))
    if ink.sum() < 30:
        return None
    core = ink & (distance >= np.percentile(distance[ink], 60))
    color = np.median(pixels[core], axis=0)
    rows = np.nonzero(ink.any(axis=1))[0]
    trimmed = ink[rows[0]:rows[-1] + 1]
    upright = _profile_peak(trimmed, 0.0)
    slant = _profile_peak(trimmed, ITALIC_SHEAR) / upright if upright else 1.0
    # A thin stroke of length L and width W has area L*W and an outline about 2L long.
    padded = np.pad(trimmed, 1)
    outline = trimmed & ~(padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:])
    width = 2.0 * float(trimmed.sum()) / max(1.0, float(outline.sum()))
    try:
        import cv2
        _, _, stats, _ = cv2.connectedComponentsWithStats(trimmed.astype(np.uint8))
        heights = [h for x, y, w, h, area in stats[1:] if area >= 6 and h >= 4]
    except ImportError:          # without OpenCV the letter height is unknown: weight is not judged
        return {'color': [round(float(c), 1) for c in color], 'width_ratio': 0.0,
                'slant_gain': round(slant, 3), 'letter_px': 0.0}
    letter = float(np.median(heights)) if heights else float(trimmed.shape[0])
    return {'color': [round(float(c), 1) for c in color], 'width_ratio': round(width / letter, 3),
            'slant_gain': round(slant, 3), 'letter_px': letter}


def ink_color(color, background):
    """Drawing colour (0-1 RGB) for measured ink. Dark ink that is neutral, or only a darker shade of a
    coloured background (black text on yellow measures olive), is black; light neutral ink is white;
    clearly coloured ink keeps its colour."""
    color = np.asarray(color, float); background = np.asarray(background, float)
    chroma = float(color.max() - color.min())
    luminance = float(color @ (0.299, 0.587, 0.114))
    shade = False
    if float(background.max() - background.min()) >= 40 and np.linalg.norm(color) and np.linalg.norm(background):
        shade = float(color @ background / (np.linalg.norm(color) * np.linalg.norm(background))) > 0.95
    if (chroma < 40 or shade) and luminance < 110:
        return (0, 0, 0)
    if chroma < 40 and color.min() > 150:
        return (1, 1, 1)
    return tuple(round(float(c) / 255, 3) for c in color)


def page_styles(page, items):
    """Style for each (bbox, background, text) of one page: {'ink', 'bold', 'italic', 'evidence'}."""
    # One render of the page for all lines (about 50 separate renders took 3.5 s on a scanned page,
    # 2026-11-19).
    rendered = render(page) if items else None
    found = [measure(page, bbox, background, rendered) for bbox, background, _ in items]
    usable = lambda m, text: m and m['letter_px'] >= MIN_LETTER_PX and sum(c.isalnum() for c in text) >= 3
    weighable = [m['width_ratio'] for m, (_, _, text) in zip(found, items)
                 if usable(m, text) and m['letter_px'] >= BOLD_LETTER_PX]
    typical = float(np.median(weighable)) if len(weighable) >= BOLD_PAGE_LINES else None
    result = []
    for m, (bbox, background, text) in zip(found, items):
        dark = float(np.mean(background)) < 125
        style = {'ink': (1, 1, 1) if dark else (0, 0, 0), 'bold': False, 'italic': False, 'evidence': m}
        if m:
            style['ink'] = ink_color(m['color'], background)
            if usable(m, text):
                style['italic'] = sum(c.isalnum() for c in text) >= 5 and m['slant_gain'] >= ITALIC_GAIN
                style['bold'] = (typical is not None and m['letter_px'] >= BOLD_LETTER_PX
                                 and m['width_ratio'] >= max(BOLD_STROKE, BOLD_RELATIVE * typical))
        result.append(style)
    return result
