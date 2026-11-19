"""Annotations, form fields and links of the source, put back on the translated pages.

The translated PDF is a new document drawn from the source's content, so a reviewer's highlight and
note and a filled-in "approver" field disappeared from the output (timetable, 2026-11-19). They are
copied onto the same place of the same page. Their own text (a note's comment, a field's value) is
kept as written: it is the reader's or the form filler's, not part of the page. Translation-only
jobs: annotations can hold personal data that a redacted output must not regain.
"""
import pymupdf

_MARKUP = {
    pymupdf.PDF_ANNOT_HIGHLIGHT: 'add_highlight_annot',
    pymupdf.PDF_ANNOT_UNDERLINE: 'add_underline_annot',
    pymupdf.PDF_ANNOT_STRIKE_OUT: 'add_strikeout_annot',
    pymupdf.PDF_ANNOT_SQUIGGLY: 'add_squiggly_annot',
}
_WIDGET_FIELDS = ('field_type', 'field_name', 'field_value', 'field_label', 'field_flags', 'text_font',
                  'text_fontsize', 'text_color', 'text_maxlen', 'border_color', 'border_width', 'fill_color',
                  'choice_values', 'button_caption')


def _copy_annotation(annot, page):
    kind = annot.type[0]
    vertices = annot.vertices
    if kind in _MARKUP:
        if not vertices or len(vertices) % 4:
            return None
        quads = [pymupdf.Quad(*vertices[i:i + 4]) for i in range(0, len(vertices), 4)]
        new = getattr(page, _MARKUP[kind])(quads=quads)
    elif kind == pymupdf.PDF_ANNOT_TEXT:
        new = page.add_text_annot(annot.rect.tl, annot.info.get('content', ''), icon=annot.info.get('name') or 'Note')
    elif kind == pymupdf.PDF_ANNOT_FREE_TEXT:
        new = page.add_freetext_annot(annot.rect, annot.info.get('content', ''))
    elif kind == pymupdf.PDF_ANNOT_SQUARE:
        new = page.add_rect_annot(annot.rect)
    elif kind == pymupdf.PDF_ANNOT_CIRCLE:
        new = page.add_circle_annot(annot.rect)
    elif kind == pymupdf.PDF_ANNOT_LINE and vertices and len(vertices) >= 2:
        new = page.add_line_annot(vertices[0], vertices[1])
    elif kind == pymupdf.PDF_ANNOT_INK and vertices:
        new = page.add_ink_annot(vertices)
    elif kind == pymupdf.PDF_ANNOT_POLYGON and vertices:
        new = page.add_polygon_annot(vertices)
    elif kind == pymupdf.PDF_ANNOT_POLY_LINE and vertices:
        new = page.add_polyline_annot(vertices)
    else:
        return None
    colors = {k: v for k, v in (annot.colors or {}).items() if v}
    if colors:
        new.set_colors(**colors)
    if annot.opacity is not None and 0 <= annot.opacity < 1:
        new.set_opacity(annot.opacity)
    new.set_info({k: v for k, v in annot.info.items() if k in ('content', 'title', 'subject') and v})
    new.update()
    return new


def _copy_widget(widget, page):
    new = pymupdf.Widget()
    for name in _WIDGET_FIELDS:
        value = getattr(widget, name, None)
        if value is not None:
            setattr(new, name, value)
    new.rect = widget.rect
    page.add_widget(new)


def carry(source_bytes, output_bytes):
    """(output PDF bytes, {'annotations': n, 'fields': n, 'links': n, 'skipped': n})."""
    counts = {'annotations': 0, 'fields': 0, 'links': 0, 'skipped': 0}
    with pymupdf.open(stream=source_bytes, filetype='pdf') as source, \
            pymupdf.open(stream=output_bytes, filetype='pdf') as output:
        if len(source) != len(output):
            return output_bytes, counts
        for source_page, page in zip(source, output):
            if abs(source_page.rect.width - page.rect.width) > 1 or abs(source_page.rect.height - page.rect.height) > 1:
                continue
            for annot in source_page.annots():
                try:
                    counts['annotations' if _copy_annotation(annot, page) else 'skipped'] += 1
                except Exception:
                    counts['skipped'] += 1
            for widget in source_page.widgets():
                try:
                    _copy_widget(widget, page); counts['fields'] += 1
                except Exception:
                    counts['skipped'] += 1
            if not page.get_links():
                for link in source_page.get_links():
                    if link.get('kind') in (pymupdf.LINK_URI, pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
                        try:
                            page.insert_link(link); counts['links'] += 1
                        except Exception:
                            counts['skipped'] += 1
        if not any(counts[k] for k in ('annotations', 'fields', 'links')):
            return output_bytes, counts
        return output.tobytes(garbage=1, deflate=True), counts
