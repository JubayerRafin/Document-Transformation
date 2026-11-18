"""Draw translated paragraphs in scripts that need text shaping with MuPDF's shaping engine.

BabelDOC places translated text one character at a time. That is right for Latin, Cyrillic and CJK,
but Arabic, Hebrew and the Indic scripts (Hindi, Bengali, ...) need shaping: Bengali "ো" is drawn on
both sides of its consonant, Hindi "ि" before it, Arabic letters join and run right to left. Placed
character by character these came out broken, and lines also broke in the middle of words
(checked 2026-11-16 on rendered en->bn/hi/ar translations).

Here BabelDOC still decides where every paragraph goes and how large its text is. For a paragraph
in such a script, the logical text is taken before typesetting, the box, font size, colour and
weight after it, and BabelDOC's own glyphs for that paragraph are removed. After BabelDOC writes the
PDF, the paragraph is drawn into the same box with MuPDF's HTML layout (HarfBuzz shaping,
bidirectional text, line breaks only at word boundaries, its bundled Noto fonts), shrinking the
text if it would not fit. The result is real, searchable text.
"""
import html
import json
import re
from collections import Counter

# Arabic (with Syriac/Thaana/N'Ko neighbours and presentation forms), Hebrew, and the Indic block.
SHAPED = re.compile(r'[֐-ࣿיִ-﷿ﹰ-﻿ऀ-෿]')
RTL = re.compile(r'[֐-ࣿיִ-﷿ﹰ-﻿]')


# MuPDF's HTML layout (PyMuPDF 1.28.2) mirrors text-align inside right-to-left text: "right" puts the
# lines at the left edge (Arabic paragraphs came out left-aligned, 2026-11-17); "start" is the right edge.
RTL_ALIGN = 'start'


def needs_shaping(text):
    return bool(text) and bool(SHAPED.search(text))


def logical_text(paragraph):
    """The paragraph's text in reading order, before typesetting splits it into placed glyphs."""
    parts = []
    for composition in paragraph.pdf_paragraph_composition or ():
        unicode_run = composition.pdf_same_style_unicode_characters
        if unicode_run is not None and unicode_run.unicode:
            parts.append(unicode_run.unicode); continue
        for holder in (composition.pdf_formula, composition.pdf_line, composition.pdf_same_style_characters):
            if holder is not None:
                parts.append(''.join(c.char_unicode or '' for c in holder.pdf_character or ()))
                break
        else:
            if composition.pdf_character is not None:
                parts.append(composition.pdf_character.char_unicode or '')
    return ''.join(parts) or (paragraph.unicode or '')


def _styles(paragraph):
    for composition in paragraph.pdf_paragraph_composition or ():
        if composition.pdf_same_style_unicode_characters is not None:
            yield composition.pdf_same_style_unicode_characters.pdf_style
        for holder in (composition.pdf_line, composition.pdf_formula, composition.pdf_same_style_characters):
            if holder is not None: yield from (c.pdf_style for c in holder.pdf_character or ())
        if composition.pdf_character is not None: yield composition.pdf_character.pdf_style
    yield paragraph.pdf_style


def colour(instruction):
    """RGB (0-1) of the fill colour a PDF instruction string leaves in effect: operators apply in
    order, so in '0.85 0.88 0.95 rg 0 g' the text is black. Fill operators: g (gray), rg (RGB),
    k (CMYK), sc/scn (1, 3 or 4 components); stroke operators (G, RG, K, SC, SCN) do not colour text."""
    fill, operands = (0.0, 0.0, 0.0), []
    for token in (instruction or '').split():
        try:
            operands.append(float(token)); continue
        except ValueError:
            pass
        values = operands[-4:]
        if token == 'g' and operands:
            fill = (operands[-1],) * 3
        elif token == 'rg' and len(operands) >= 3:
            fill = tuple(operands[-3:])
        elif token == 'k' and len(operands) >= 4:
            c, m, y, k = operands[-4:]; fill = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
        elif token in ('sc', 'scn') and operands:
            n = len(operands)
            if n >= 4 and len(values) == 4:
                c, m, y, k = values; fill = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
            elif n >= 3: fill = tuple(operands[-3:])
            else: fill = (operands[-1],) * 3
        operands = []
    return [round(min(1.0, max(0.0, v)), 4) for v in fill]


def take(document, slots=None):
    """Just before typesetting: record each paragraph that needs shaping (logical text, box, font
    size, colour, weight) and remove it from BabelDOC's typesetting. BabelDOC would measure it with
    unshaped glyph widths, so its fitting would be wrong and could fail the document.
    slots: {(page number from 1, paragraph id): [x0, y0, x1, y1]} for paragraphs that must stay in a
    fixed table cell, heading or panel slot; others keep their source box."""
    records = []; slots = slots or {}
    for page in document.page or ():
        for paragraph in page.pdf_paragraph or ():
            if paragraph.vertical or paragraph.box is None: continue
            if not needs_shaping(logical_text(paragraph)): continue
            records.append(take_paragraph(page, paragraph, slots.get((page.page_number + 1, paragraph.debug_id))))
    return records


def take_paragraph(page, paragraph, box=None, reason='shaping'):
    """Record one paragraph for drawing after the PDF is written (into box, default its own box) and
    remove it from BabelDOC's typesetting."""
    font_names = {f.font_id: (f.name or '') for f in page.pdf_font or ()}
    text = logical_text(paragraph)
    styles = [s for s in _styles(paragraph) if s is not None]
    sizes = Counter(round(s.font_size, 2) for s in styles if s.font_size)
    instructions = Counter((s.graphic_state.passthrough_per_char_instruction or '') for s in styles if s.graphic_state)
    fonts = Counter(font_names.get(s.font_id, s.font_id or '').lower() for s in styles)
    own = paragraph.box
    record = {'page': page.page_number, 'box': list(box) if box else [own.x, own.y, own.x2, own.y2], 'text': text,
              'font_size': float(sizes.most_common(1)[0][0]) if sizes else 10.0,
              'color': colour(instructions.most_common(1)[0][0]) if instructions else [0.0, 0.0, 0.0],
              'bold': bool(fonts) and 'bold' in fonts.most_common(1)[0][0], 'rtl': bool(RTL.search(text)),
              'paragraph_id': paragraph.debug_id, 'reason': reason}
    paragraph.pdf_paragraph_composition = []
    return record


# A run of Latin letters/digits joined by separators that contains a digit: phone numbers, dates,
# IDs, amounts, versions (010-2345-6789, 2024-03-18, HR-2024-00417, 1,250.00).
_LTR_RUN = re.compile(r'(?=[A-Za-z0-9*/.:,+_-]*[0-9])[A-Za-z0-9*]+(?:[-/.:,+_][A-Za-z0-9*]+)+')


def _keep_left_to_right(text):
    """Wrap number-like runs in left-to-right embedding marks (U+202A … U+202C). MuPDF's HTML layout
    (PyMuPDF 1.28.2) reverses digit groups joined by hyphens inside right-to-left lines
    (010-2345-6789 drawn as 6789-2345-010), against the Unicode bidirectional algorithm;
    dir="ltr" spans and isolates have no effect there, embedding marks do."""
    return _LTR_RUN.sub(lambda m: '‪' + m.group(0) + '‬', text)


def _visual_order(text):
    """Each line of right-to-left text in display order (Unicode bidirectional algorithm)."""
    try:
        from bidi import get_display
    except ImportError:  # requirements-transformation.txt installs python-bidi; without it, keep logical order
        return text
    return '\n'.join(get_display(line, base_dir='R') for line in text.split('\n'))


def _outlined(rect, markup, css):
    """Lay the paragraph out with MuPDF's HTML engine (shaping, bidi, word breaks) on a scratch page
    the size of its box, and return it as a one-page PDF of vector outlines plus the scale used.
    Outlines, because MuPDF's text layer for shaped Indic/Arabic glyphs maps back to the wrong
    characters (checked with PyMuPDF 1.28.2); the correct text is added separately, invisibly."""
    import pymupdf
    with pymupdf.open() as scratch:
        page = scratch.new_page(width=rect.width, height=rect.height)
        spare, scale = page.insert_htmlbox(page.rect, markup, css=css, scale_low=0)
        svg = page.get_svg_image(text_as_path=True)
    with pymupdf.open('svg', svg.encode('utf-8')) as vector:
        return pymupdf.open('pdf', vector.convert_to_pdf()), spare, scale


def fit_shaped(rect, text, max_size, min_size, color=(0.0, 0.0, 0.0)):
    """Lay text out shaped inside rect at the largest size up to max_size that fits.

    For text drawn into images (transformation/pdf/scan_reconstruction.py), which otherwise writes
    glyphs one by one with a CJK font: Arabic, Hindi and Bengali image text was never drawn
    (2026-11-17; the glyphs could not be shaped and failed the fit check). Returns
    (one-page PDF of vector outlines, font size used), or None when even min_size does not fit."""
    rtl = bool(RTL.search(text))
    r, g, b = color
    css = ('* {font-size: %.2fpt; line-height: 1.15; margin: 0; padding: 0; color: rgb(%d,%d,%d); '
           'direction: %s; text-align: %s;}' % (max_size, r * 255, g * 255, b * 255,
                                                 'rtl' if rtl else 'ltr', RTL_ALIGN if rtl else 'left'))
    body = html.escape(_keep_left_to_right(text) if rtl else text).replace('\n', '<br>')
    outlines, spare, scale = _outlined(rect, f'<div dir="{"rtl" if rtl else "ltr"}">{body}</div>', css)
    if spare < 0 or max_size * scale < min_size - 1e-6:
        outlines.close()
        return None
    return outlines, max_size * scale


def _invisible_text(page, rect, text, size, text_font, rtl):
    """Write the paragraph's text invisibly inside rect, smaller if needed: its unshaped widths differ
    from the drawn outlines, and text placed past the page edge is dropped by PDF readers. A single
    long line written when the box was too small ran off the page, so values at the end of Hindi,
    Bengali and Arabic paragraphs could not be found or copied (checked 2026-11-19)."""
    import pymupdf
    options = dict(fontname='TxShaped', fontfile=str(text_font), render_mode=3)
    align = pymupdf.TEXT_ALIGN_RIGHT if rtl else pymupdf.TEXT_ALIGN_LEFT
    for factor in (1.0, 0.85, 0.7, 0.55, 0.42, 0.32, 0.24, 0.18):
        if page.insert_textbox(rect, text, fontsize=max(0.5, size * factor), align=align, **options) >= 0:
            return
    # Still too long for the box (a tiny cell): one line, sized to the box width, inside the page.
    line = ' '.join(text.split())
    width = pymupdf.Font(fontfile=str(text_font)).text_length(line, fontsize=1) or 1
    fitted = max(0.5, min(size, rect.width / width))
    page.insert_text((rect.x0, min(rect.y1, rect.y0 + fitted)), line, fontsize=fitted, **options)


def _grown(rect, record, page_rect):
    """A box under a point wide or tall (a lone danda "।" measured as 0.9 pt) grown around its centre
    to hold the text at its font size, kept on the page. Such a box once failed a whole Bengali
    document (mass test, 2026-11-19)."""
    import pymupdf
    size = max(1.0, record.get('font_size') or 10)
    width = max(rect.width, 0.7 * size * max(1, len(record.get('text') or '')))
    height = max(rect.height, 1.2 * size)
    centre = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    grown = pymupdf.Rect(centre.x - width / 2, centre.y - height / 2, centre.x + width / 2, centre.y + height / 2)
    if grown.x1 > page_rect.x1: grown += (page_rect.x1 - grown.x1, 0, page_rect.x1 - grown.x1, 0)
    if grown.x0 < page_rect.x0: grown += (page_rect.x0 - grown.x0, 0, page_rect.x0 - grown.x0, 0)
    if grown.y1 > page_rect.y1: grown += (0, page_rect.y1 - grown.y1, 0, page_rect.y1 - grown.y1)
    if grown.y0 < page_rect.y0: grown += (0, page_rect.y0 - grown.y0, 0, page_rect.y0 - grown.y0)
    return grown & page_rect


def draw(pdf_path, records, text_font=None):
    """Draw recorded paragraphs into the written PDF; returns per-paragraph results.
    text_font: a font file covering these scripts, for the invisible searchable text layer."""
    import os
    import pymupdf
    results = []
    pdf_path = str(pdf_path); temporary = pdf_path + '.shaped.tmp'
    with pymupdf.open(pdf_path) as pdf:
        for record in records:
            if not 0 <= record['page'] < len(pdf):
                results.append({'paragraph_id': record['paragraph_id'], 'status': 'page_missing'}); continue
            page = pdf[record['page']]
            x0, y0, x1, y1 = record['box']
            rect = pymupdf.Rect(x0, y0, x1, y1) * page.transformation_matrix
            rect.normalize()
            if rect.width < 1 or rect.height < 1:
                rect = _grown(rect, record, page.rect)
            if rect.is_empty or rect.width < 1 or rect.height < 1:
                results.append({'paragraph_id': record['paragraph_id'], 'status': 'empty_box'}); continue
            # Slots are as tall as the source line (a 10 pt cell for 10 pt text): allow the extra height
            # shaped scripts need above and below, so one-line cells are not shrunk while two-line
            # cells keep full size (seen in translated tables).
            rect = rect + (0, -0.2 * record['font_size'], 0, 0.2 * record['font_size'])
            r, g, b = record['color']
            css = ('* {font-size: %.2fpt; line-height: 1.15; margin: 0; padding: 0; color: rgb(%d,%d,%d); '
                   'font-weight: %s; direction: %s; text-align: %s;}'
                   % (record['font_size'], r * 255, g * 255, b * 255, 'bold' if record['bold'] else 'normal',
                      'rtl' if record['rtl'] else 'ltr', RTL_ALIGN if record['rtl'] else 'left'))
            text = _keep_left_to_right(record['text']) if record['rtl'] else record['text']
            body = html.escape(text).replace('\n', '<br>')
            markup = f'<div dir="{"rtl" if record["rtl"] else "ltr"}">{body}</div>'
            outlines, spare, scale = _outlined(rect, markup, css)
            with outlines:
                page.show_pdf_page(rect, outlines, 0)
            if text_font:
                # Searchable, copyable text, invisible (render mode 3), in the box. PDF readers and MuPDF
                # read right-to-left text from visual order, so RTL lines are stored that way.
                size = max(1.0, record['font_size'] * scale)
                stored = _visual_order(record['text']) if record['rtl'] else record['text']
                _invisible_text(page, rect, stored, size, text_font, record['rtl'])
            results.append({'paragraph_id': record['paragraph_id'], 'status': 'drawn' if spare >= 0 else 'not_fitted',
                            'scale': round(scale, 3)})
        pdf.save(temporary, garbage=1, deflate=True)
    os.replace(temporary, pdf_path)
    return results


def save_records(folder, records):
    from pathlib import Path
    Path(folder, 'complex-script-paragraphs.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
