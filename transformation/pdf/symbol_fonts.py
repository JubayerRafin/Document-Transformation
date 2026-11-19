"""A last fallback font for form symbols in every target language.

BabelDOC 0.6.4 draws each character with the target language's fonts and drops a character none
of them has. Its Noto, Source Han and Go Noto fonts have no check boxes (☐ ☑) or ✓, so the boxes in
front of form options disappeared in every translation (2026-11-19). BabelDOC's own assets include
fonts that have them (MaruBuri, LXGW WenKai); the first one present is added as the last
fallback, used only for characters no other font has.
"""
SYMBOL_FONTS = ('MaruBuri-Regular.ttf', 'LXGWWenKaiGB-Regular.1.520.ttf', 'LXGWWenKaiTC-Regular.1.520.ttf')


def add_symbol_fallback(mapper):
    """Append the first available symbol font to an initialised FontMapper's fallback list."""
    import functools
    import pymupdf
    from babeldoc.assets import assets
    if any(font_id in mapper.fallback_font_ids for font_id in SYMBOL_FONTS):
        return None
    for name in SYMBOL_FONTS:
        if name in mapper.fontid2font:
            # Already loaded for another role (MaruBuri is the Korean script font): reuse it.
            font = mapper.fontid2font[name]
            if font.has_glyph(0x2610):
                mapper.fallback_font_ids.append(name)
                mapper.fallback_fonts.append(font)
                return name
            continue
        try:
            path, metadata = assets.get_font_and_metadata(name)
            font = pymupdf.Font(fontfile=str(path))
        except Exception:
            continue
        if not font.has_glyph(0x2610):
            continue
        font.has_glyph = functools.lru_cache(maxsize=10240, typed=True)(font.has_glyph)
        font.char_lengths = functools.lru_cache(maxsize=10240, typed=True)(font.char_lengths)
        font.font_id, font.font_path = name, path
        font.ascent_fontmap, font.descent_fontmap = metadata['ascent'], metadata['descent']
        font.encoding_length = metadata['encoding_length']
        mapper.fonts[name] = font
        mapper.fontid2fontpath[name] = path
        mapper.fontid2font[name] = font
        mapper.fallback_font_ids.append(name)
        mapper.fallback_fonts.append(font)     # the same list type2font['fallback'] holds
        return name
    return None


def install(mapper=None):
    """Patch FontMapper so every instance gets the symbol fallback."""
    if mapper is None:
        from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper as mapper
    if getattr(mapper, '_transformx_symbol_fallback', False):
        return True
    original = mapper.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        try:
            add_symbol_fallback(self)
        except Exception:
            pass      # never stop a translation for a missing optional font

    mapper.__init__ = init
    mapper._transformx_symbol_fallback = True
    return True
