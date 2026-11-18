"""Whether a source font is serif or sans-serif, from the font itself.

BabelDOC picks the output typeface from the PDF's font flags, which are often wrong: HP's printer
manual set in its sans-serif Forma DJR came out in a serif font (2026-11-19). Each embedded font is
classified once: by its OS/2 table (family class, PANOSE serif style) when it has one, else by the
shape of a few letters, where serif fonts widen into feet at the end of a stem ("I", "l", "H").
Fonts that cannot be classified keep their flags.
"""
import struct

LETTERS = 'IlHdnri'


def _os2_class(buffer):
    """True (serif), False (sans) or None from a TrueType/OpenType font's OS/2 table."""
    if len(buffer) < 12 or buffer[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
        return None
    try:
        count = struct.unpack('>H', buffer[4:6])[0]
        for index in range(count):
            tag, _, offset, length = struct.unpack('>4sIII', buffer[12 + 16 * index:28 + 16 * index])
            if tag == b'OS/2' and length >= 42:
                family_class = buffer[offset + 30]
                family_type, serif_style = buffer[offset + 32], buffer[offset + 33]
                if family_class in (1, 2, 3, 4, 5, 7):
                    return True
                if family_class == 8:
                    return False
                if family_type == 2 and 2 <= serif_style <= 10:
                    return True
                if family_type == 2 and 11 <= serif_style <= 13:
                    return False
                return None
    except struct.error:
        return None
    return None


def _stem_feet(font, letter):
    """Ratio of the ink width at a stem's bottom to its middle, or None when it cannot be measured."""
    import numpy as np
    import pymupdf
    if not font.has_glyph(ord(letter)):
        return None
    with pymupdf.open() as scratch:
        page = scratch.new_page(width=200, height=200)
        page.insert_font(fontname='probe', fontbuffer=font.buffer)
        page.insert_text((60, 150), letter, fontname='probe', fontsize=120)
        pix = page.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY)
    ink = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 128
    rows = np.flatnonzero(ink.any(axis=1))
    if len(rows) < 30:
        return None
    widths = ink[rows[0]:rows[-1] + 1].sum(axis=1).astype(float)
    height = len(widths)
    middle = np.median(widths[int(height * 0.35):int(height * 0.65)])
    bottom = widths[int(height * 0.9):].max()
    return float(bottom / middle) if middle > 0 else None


def serif(buffer):
    """True, False or None for a font program's bytes."""
    by_table = _os2_class(buffer)
    if by_table is not None:
        return by_table
    try:
        import pymupdf
        font = pymupdf.Font(fontbuffer=buffer)
    except Exception:
        return None
    ratios = [r for r in (_stem_feet(font, letter) for letter in LETTERS) if r is not None]
    if len(ratios) < 2:
        return None
    feet = sum(r > 1.6 for r in ratios)
    if feet * 2 > len(ratios):
        return True
    if feet == 0:
        return False
    return None


def _feet_on_page(page, box):
    """Bottom-to-middle ink width ratio of one rendered letter on the page, or None."""
    import numpy as np
    import pymupdf
    rect = pymupdf.Rect(box)
    if rect.height < 4:
        return None
    zoom = 120 / rect.height
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, colorspace=pymupdf.csGRAY, annots=False)
    ink = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 128
    rows = np.flatnonzero(ink.any(axis=1))
    if len(rows) < 30:
        return None
    widths = ink[rows[0]:rows[-1] + 1].sum(axis=1).astype(float)
    height = len(widths)
    middle = np.median(widths[int(height * 0.35):int(height * 0.65)])
    return float(widths[int(height * 0.9):].max() / middle) if middle > 0 else None


def _decide(ratios):
    if len(ratios) < 2:
        return None
    feet = sum(r > 1.6 for r in ratios)
    if feet * 2 > len(ratios):
        return True
    return False if feet == 0 else None


def classify_pdf(path, pages=12):
    """{font base name: True/False} for the embedded fonts of a PDF that can be classified. Fonts
    whose program cannot be read (symbolic subsets without a character map) are measured where
    their stem letters appear on the first pages."""
    import pymupdf
    result, unknown = {}, set()
    with pymupdf.open(path) as pdf:
        seen = set()
        for page in pdf:
            for xref, _, _, name, *_ in page.get_fonts():
                base = name.split('+', 1)[-1]
                if base in seen:
                    continue
                seen.add(base)
                try:
                    buffer = pdf.extract_font(xref)[3]
                except Exception:
                    buffer = None
                style = serif(buffer) if buffer else None
                if style is None:
                    unknown.add(base)
                else:
                    result[base] = style
        samples = {base: [] for base in unknown}
        for page in pdf.pages(0, min(pages, len(pdf))):
            if all(len(v) >= 6 for v in samples.values()):
                break
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines', []):
                    if tuple(round(v) for v in line['dir']) != (1, 0):
                        continue
                    for span in line['spans']:
                        base = span['font'].split('+', 1)[-1]
                        if base not in samples or len(samples[base]) >= 6:
                            continue
                        for char in span['chars']:
                            if char['c'] in 'IlH' and len(samples[base]) < 6:
                                ratio = _feet_on_page(page, char['bbox'])
                                if ratio is not None:
                                    samples[base].append(ratio)
        for base, ratios in samples.items():
            style = _decide(ratios)
            if style is not None:
                result[base] = style
    return result
