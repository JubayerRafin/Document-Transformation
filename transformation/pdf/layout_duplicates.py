"""Drop duplicate text boxes from the page-layout model before BabelDOC groups characters.

The layout model sometimes returns a second, shorter box of the same class inside a text line it
already covers (seen 2026-11-17: a header line "Document ID: SEC-2024-0093 | ... during redaction"
had a full-width "plain text" box plus one from x=138 to x=511 inside it). BabelDOC 0.6.4 starts a
new paragraph whenever a character's layout box changes, so the line became three paragraphs,
"Document ID: SEC-2024-", "0093 | ... redacti" and "on", each translated on its own in every language.

A box is a duplicate when it has the same class as a larger box, lies almost entirely inside it,
and spans nearly the same height (the same lines). Boxes of other classes, and separate paragraphs
stacked inside a larger region (each much shorter than it), are kept.
"""
INSIDE = 0.9   # share of the smaller box's area inside the larger one
SAME_BAND = 0.8  # smaller box height as a share of the larger one's
TEXT_CLASSES = {'plain text', 'title', 'text', 'paragraph_title', 'list', 'caption', 'table_caption',
                'figure_caption', 'abandon'}


def _area(box):
    return max(0.0, box.x2 - box.x) * max(0.0, box.y2 - box.y)


def _overlap(a, b):
    w = min(a.x2, b.x2) - max(a.x, b.x)
    h = min(a.y2, b.y2) - max(a.y, b.y)
    return max(0.0, w) * max(0.0, h)


def duplicates(layouts):
    """The ids of text boxes that duplicate part of a larger box of the same class."""
    found = set()
    for small in layouts:
        if small.class_name not in TEXT_CLASSES or _area(small.box) <= 0: continue
        for large in layouts:
            if large is small or large.class_name != small.class_name or large.id in found: continue
            if _area(large.box) <= _area(small.box): continue
            tall = large.box.y2 - large.box.y
            if (_overlap(small.box, large.box) >= INSIDE * _area(small.box)
                    and tall > 0 and (small.box.y2 - small.box.y) >= SAME_BAND * tall):
                found.add(small.id)
                break
    return found


def prune(page):
    """Remove duplicate text boxes from the page; returns how many."""
    drop = duplicates(page.page_layout or [])
    if drop:
        page.page_layout = [layout for layout in page.page_layout if layout.id not in drop]
    return len(drop)


def install(finder=None):
    """Patch BabelDOC's ParagraphFinder to prune each page first; returns True when installed."""
    if finder is None:
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder as finder
    if getattr(finder, '_transformx_layout_duplicates', False): return True
    original = finder.process_page

    def process_page(self, page):
        prune(page)
        return original(self, page)

    finder.process_page = process_page
    finder._transformx_layout_duplicates = True
    return True
