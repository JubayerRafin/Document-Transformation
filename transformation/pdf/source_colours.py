"""Keep the source text colour where BabelDOC's layout data loses it.

Some PDFs set a text colour that BabelDOC's parser does not carry into its paragraphs: the white
header row of a table on a dark fill came out black on dark blue in every target language
(2026-11-16, Sample04). PyMuPDF reads those source spans with their real colour. Before typesetting,
a paragraph whose recorded fill is plain black takes the colour of the source text in its box when all
of that text has one other colour. Nothing else changes.
"""
from transformation.pdf.ir_json import ir_copy

BLACK = {'', '0 g', '0 0 0 rg', '0 0 0 1 k'}


def _instruction(style):
    state = style.graphic_state if style is not None else None
    from transformation.pdf.complex_script import instruction_text
    return ' '.join(instruction_text(state.passthrough_per_char_instruction).split()) if state is not None else ''


def source_colour(page, box):
    """The single non-black sRGB colour of the source text spans inside box (PDF coordinates), else None."""
    import pymupdf
    rect = (pymupdf.Rect(box.x, box.y, box.x2, box.y2) * page.transformation_matrix).normalize()
    colours = set()
    for block in page.get_text('dict', clip=rect)['blocks']:
        for line in block.get('lines', ()):
            for span in line['spans']:
                if not span['text'].strip(): continue
                inside = pymupdf.Rect(span['bbox']) & rect
                if inside.get_area() < 0.5 * pymupdf.Rect(span['bbox']).get_area(): continue
                colours.add(span['color'])
    if len(colours) != 1: return None
    colour = colours.pop()
    return None if colour == 0 else colour


def restore(document, input_pdf):
    """Returns the paragraphs recoloured as [(page number from 1, paragraph id, '#rrggbb')]."""
    import pymupdf
    changed = []
    with pymupdf.open(input_pdf) as pdf:
        for page in document.page or ():
            if not 0 <= page.page_number < len(pdf): continue
            source = pdf[page.page_number]
            for paragraph in page.pdf_paragraph or ():
                if paragraph.box is None or paragraph.pdf_style is None: continue
                if _instruction(paragraph.pdf_style) not in BLACK: continue
                colour = source_colour(source, paragraph.box)
                if colour is None: continue
                r, g, b = ((colour >> 16) & 255) / 255, ((colour >> 8) & 255) / 255, (colour & 255) / 255
                instruction = f'{r:.3f} {g:.3f} {b:.3f} rg'
                styles = [paragraph.pdf_style] + [c.pdf_same_style_unicode_characters.pdf_style
                                                  for c in paragraph.pdf_paragraph_composition or ()
                                                  if c.pdf_same_style_unicode_characters is not None]
                for style in styles:
                    if style is None or style.graphic_state is None or _instruction(style) not in BLACK: continue
                    # Styles can be shared between paragraphs: give this one its own copy.
                    style.graphic_state = ir_copy(style.graphic_state)
                    style.graphic_state.passthrough_per_char_instruction = instruction
                changed.append((page.page_number + 1, paragraph.debug_id, f'#{colour:06x}'))
    return changed
