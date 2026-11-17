"""Correct a source font's style flags from its name before BabelDOC chooses an output font.

BabelDOC 0.6.4 picks a serif or sans-serif, bold or regular output font from the flags in the PDF's
font description. Those flags are often wrong: Helvetica, Helvetica-Bold and Microsoft's Aptos were
all marked serif and Helvetica-Bold not bold (2026-11-17), so sans-serif slides and headings came
out in a regular serif font. When the font's name states the family or weight, the name wins;
unknown names keep their flags.
"""
import re

SANS = ('helvetica', 'arial', 'aptos', 'calibri', 'segoe', 'verdana', 'tahoma', 'trebuchet', 'roboto',
        'opensans', 'open sans', 'notosans', 'noto sans', 'lato', 'montserrat', 'gill', 'futura', 'sourcesans',
        'source sans', 'inter', 'avenir', 'frutiger', 'myriad', 'franklin', 'centurygothic', 'century gothic',
        'candara', 'corbel', 'geneva', 'dejavusans', 'liberationsans', 'arimo', 'ubuntu', 'poppins', 'nunito',
        'raleway', 'ptsans', 'pt sans', 'sourcehansans', 'malgun', 'meiryo', 'yugothic', 'gothic', 'simhei',
        'heiti', 'dengxian', 'microsoftyahei', 'sans')
SERIF = ('times', 'georgia', 'garamond', 'cambria', 'palatino', 'bookantiqua', 'book antiqua', 'baskerville',
         'minion', 'mincho', 'songti', 'simsun', 'mingliu', 'batang', 'myeongjo', 'sourcehanserif', 'notoserif',
         'noto serif', 'serif', 'didot', 'bodoni', 'caslon', 'constantia', 'century schoolbook')
BOLD = re.compile(r'bold|black|heavy|semibold|demibold|extrabold|ultrabold', re.I)
ITALIC = re.compile(r'italic|oblique', re.I)


def style_from_name(name):
    """(serif or None, bold or None, italic or None) as far as the font's name says."""
    base = (name or '').split('+', 1)[-1].lower()       # drop a subset prefix ("NHCQMT+")
    compact = re.sub(r'[\s,_-]', '', base)
    # Families are matched at the start of the name ("Palatino" is not "Lato"); "Sans"/"Serif"
    # anywhere in it ("Noto Sans CJK", "Microsoft Sans Serif") decide first.
    family = lambda names: any(compact.startswith(n.replace(' ', '')) for n in names)
    serif = None
    if 'sans' in compact or family(SANS):
        serif = False
    elif 'serif' in compact or family(SERIF):
        serif = True
    return serif, (True if BOLD.search(base) else None), (True if ITALIC.search(base) else None)


def correct(font):
    """Apply style_from_name to a BabelDOC PdfFont in place; returns True when a flag changed."""
    serif, bold, italic = style_from_name(getattr(font, 'name', ''))
    changed = False
    for attribute, value in (('serif', serif), ('bold', bold), ('italic', italic)):
        if value is not None and bool(getattr(font, attribute, None)) != value:
            setattr(font, attribute, value); changed = True
    return changed


def install(mapper=None):
    """Patch BabelDOC's FontMapper.map to correct each source font's flags first."""
    if mapper is None:
        from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper as mapper
    if getattr(mapper, '_transformx_font_flags', False): return True
    original = mapper.map

    def map(self, original_font, char_unicode):
        if hasattr(original_font, 'serif') and hasattr(original_font, 'font_id'):
            correct(original_font)
        return original(self, original_font, char_unicode)

    mapper.map = map
    mapper._transformx_font_flags = True
    return True
