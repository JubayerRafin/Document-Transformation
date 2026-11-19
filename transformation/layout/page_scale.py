"""Shrink translated text to the most common scale of its own page, not of the whole document.

BabelDOC finds the scale each translated paragraph needs to fit its box, takes the most common
scale over the whole document (by characters) and caps every paragraph there, so all text is set
alike. In a 50-page manual, dense table pages made that 0.7, and every page was set at 70% of the
source size, including pages whose text fitted at full size (Korean HP manual, 2026-11-19). The same
rule is applied per page: one page is still set alike, and a page that fits keeps its size.
"""
import statistics

RAW = {}


def _weight(paragraph):
    return max(1, len((paragraph.unicode or '').strip()))


def page_mode(scales):
    """Most common scale of (scale, weight) pairs; the smallest when several are equally common."""
    counts = {}
    for scale, weight in scales:
        counts[scale] = counts.get(scale, 0) + weight
    if not counts:
        return None
    top = max(counts.values())
    return min(s for s, c in counts.items() if c == top)


def recap(document):
    """Set each paragraph's scale to min(its own scale, its page's most common scale)."""
    for page in document.page:
        pairs = [(RAW.get(id(p), 1.0), p) for p in page.pdf_paragraph if p.optimal_scale is not None]
        mode = page_mode([(round(scale, 2), _weight(p)) for scale, p in pairs])
        if mode is None:
            continue
        for scale, paragraph in pairs:
            paragraph.optimal_scale = min(scale, mode)


def install(Typesetting):
    if getattr(Typesetting, '_transformx_page_scale', False):
        return
    measure = Typesetting._get_optimal_scale
    preprocess = Typesetting.preprocess_document

    def optimal_scale(self, paragraph, page, typesetting_units, use_english_line_break=True):
        scale = measure(self, paragraph, page, typesetting_units, use_english_line_break)
        RAW[id(paragraph)] = scale
        return scale

    def preprocess_document(self, document, pbar):
        RAW.clear()
        result = preprocess(self, document, pbar)
        recap(document)
        RAW.clear()
        return result

    Typesetting._get_optimal_scale = optimal_scale
    Typesetting.preprocess_document = preprocess_document
    Typesetting._transformx_page_scale = True
