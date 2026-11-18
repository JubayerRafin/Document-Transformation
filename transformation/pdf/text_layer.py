"""Make copied and searched text match what the translated PDF shows.

The CJK fonts BabelDOC embeds (Source Han Sans) draw the space and the non-breaking space with one
glyph, and the hyphen and the non-breaking hyphen with one glyph; their ToUnicode maps name the
non-breaking characters. Copying "SEC-2024-0093" from a Japanese output gave "SEC‑2024‑0093" with
U+2011 and searching for the ID found nothing (2026-11-19). A glyph mapped to exactly one of these
characters is remapped to the ordinary one; the page looks the same.
"""
import re

PLAIN = {'00a0': '0020', '2011': '002d'}
_HEX = r'<([0-9a-fA-F]+)>'


def _remap(cmap):
    changed = 0

    def bfchar(block):
        nonlocal changed
        def entry(match):
            nonlocal changed
            target = match.group(2).lower()
            if target in PLAIN:
                changed += 1
                return f'<{match.group(1)}> <{PLAIN[target]}>'
            return match.group(0)
        return re.sub(_HEX + r'\s+' + _HEX, entry, block)

    def bfrange(block):
        nonlocal changed
        def entry(match):
            nonlocal changed
            low, high, target = match.group(1), match.group(2), match.group(3).lower()
            if low.lower() == high.lower() and target in PLAIN:     # one glyph only: no range shift
                changed += 1
                return f'<{low}> <{high}> <{PLAIN[target]}>'
            return match.group(0)
        return re.sub(_HEX + r'\s+' + _HEX + r'\s+' + _HEX, entry, block)

    cmap = re.sub(r'beginbfchar(.*?)endbfchar', lambda m: 'beginbfchar' + bfchar(m.group(1)) + 'endbfchar', cmap, flags=re.S)
    cmap = re.sub(r'beginbfrange(.*?)endbfrange', lambda m: 'beginbfrange' + bfrange(m.group(1)) + 'endbfrange', cmap, flags=re.S)
    return cmap, changed


def plain_spaces_and_hyphens(path):
    """Rewrite the PDF at path in place; returns the number of glyph mappings changed."""
    import os
    import pymupdf
    total = 0
    path = str(path)
    with pymupdf.open(path) as pdf:
        streams = set()
        for xref in range(1, pdf.xref_length()):
            try:
                key = pdf.xref_get_key(xref, 'ToUnicode')
            except Exception:
                continue
            if key[0] == 'xref':
                streams.add(int(key[1].split()[0]))
        for xref in streams:
            try:
                cmap = pdf.xref_stream(xref).decode('latin-1')
            except Exception:
                continue
            fixed, changed = _remap(cmap)
            if changed:
                pdf.update_stream(xref, fixed.encode('latin-1'))
                total += changed
        if total:
            temporary = path + '.text.tmp'
            pdf.save(temporary, garbage=1, deflate=True)
    if total:
        os.replace(temporary, path)
    return total
