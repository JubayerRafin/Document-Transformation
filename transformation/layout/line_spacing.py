"""Translated paragraphs set with the line spacing of the source paragraph, when that is tighter.

BabelDOC sets every translated paragraph at 1.5 line heights for Chinese, Japanese and Korean and
1.3 otherwise. A manual set at about 1.2 then needed a quarter more height for the same lines, and
the text was shrunk to fit its box: the Korean HP printer manual came out at 70% of the source size
(2026-11-19). The source's own line pitch is measured before translation and used instead, never
looser than BabelDOC's choice and never tighter than a floor that keeps lines apart.
"""
import statistics

CJK_FLOOR = 1.25
OTHER_FLOOR = 1.15
PITCHES = {}


def _characters(paragraph):
    for composition in paragraph.pdf_paragraph_composition or []:
        if composition.pdf_character is not None:
            yield composition.pdf_character
        for name in ('pdf_line', 'pdf_same_style_characters'):
            group = getattr(composition, name, None)
            if group is not None:
                yield from group.pdf_character or []


def line_pitch(paragraph):
    """Distance between the source lines' baselines in font sizes, or None for one line."""
    style = paragraph.pdf_style
    size = getattr(style, 'font_size', None) if style else None
    if not size or paragraph.vertical:
        return None
    bottoms = sorted({round(c.box.y, 1) for c in _characters(paragraph) if c.box and (c.char_unicode or '').strip()},
                     reverse=True)
    lines = []
    for y in bottoms:
        if not lines or lines[-1] - y > 0.5 * size:
            lines.append(y)
    if len(lines) < 2:
        return None
    pitch = statistics.median(a - b for a, b in zip(lines, lines[1:]))
    return pitch / size if pitch > 0 else None


def measure(document):
    """Record the line pitch of every multi-line paragraph, by paragraph id."""
    PITCHES.clear()
    for page in document.page:
        for paragraph in page.pdf_paragraph:
            ratio = line_pitch(paragraph)
            if ratio and paragraph.debug_id:
                PITCHES[paragraph.debug_id] = ratio


def spacing(line_skip, paragraph, cjk):
    ratio = PITCHES.get(getattr(paragraph, 'debug_id', None))
    if not ratio:
        return line_skip
    return min(line_skip, max(CJK_FLOOR if cjk else OTHER_FLOOR, ratio))


def install(Typesetting):
    if getattr(Typesetting, '_transformx_source_spacing', False):
        return
    original = Typesetting._layout_typesetting_units

    def layout(self, units, box, scale, line_skip, paragraph, use_english_line_break=True):
        return original(self, units, box, scale, spacing(line_skip, paragraph, getattr(self, 'is_cjk', False)),
                        paragraph, use_english_line_break=use_english_line_break)

    Typesetting._layout_typesetting_units = layout
    Typesetting._transformx_source_spacing = True
